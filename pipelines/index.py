"""pipelines/index.py — Chunk + embed + upsert into Qdrant
================================================================================

Functional purpose
------------------
This step takes ingested documents (clean text + metadata) and builds the vector
index used by the RAG Copilot.

High-level flow
---------------
1) Read the ingested run summary (`INGESTED_PATH`).
2) For each ingested doc:
   - load parsed text from storage/parsed/<...>.txt
   - chunk into overlapping segments
   - embed each chunk using a sentence-transformers model
3) Upsert the chunk vectors into Qdrant (vector DB) via REST.
4) Write an indexed run summary (`INDEXED_PATH`).

Notes on correctness
--------------------
- Chunk payload must include stable fields (url, title, focus) for retrieval.
- IDs should be stable so re-runs/upserts behave predictably.

RUN_ID
------
All IO is RUN_ID-scoped to avoid overwriting and midnight-split issues.
"""

import json
import pathlib
from urllib.parse import urlparse

from tqdm import tqdm

from pipelines.common import env, chunk_text, safe_id, normalize_url, state_path
from pipelines.qdrant_rest import (
    QdrantConfig,
    ensure_collection,
    upsert_points,
    ensure_collection_hybrid,
    upsert_points_hybrid,
)

QDRANT_URL = env("QDRANT_URL", required=True)
COLLECTION = env("QDRANT_COLLECTION", "docs_chunks")

STATE_DIR = pathlib.Path(env("STATE_DIR", "storage/state"))

EMBED_MODEL = env("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
CHUNK_MAX = int(env("CHUNK_MAX_CHARS", 2200))
CHUNK_OVERLAP = int(env("CHUNK_OVERLAP", 240))

# Detect bge-m3 mode: uses FlagEmbedding, produces dense+sparse hybrid vectors
_BGE_M3_NAMES = {"baai/bge-m3", "bge-m3"}
IS_BGE_M3 = EMBED_MODEL.lower() in _BGE_M3_NAMES

INDEX_MIN_SPIRULINA_SCORE = float(env("INDEX_MIN_SPIRULINA_SCORE", "0.25"))
INDEX_ALLOW_NON_SPIRULINA = env("INDEX_ALLOW_NON_SPIRULINA", "0").strip() in ("1", "true", "TRUE", "yes", "YES")

UPSERT_BATCH = int(env("QDRANT_UPSERT_BATCH", 64))
MAX_CHUNKS_PER_DOC = int(env("MAX_CHUNKS_PER_DOC", "50"))  # cap per-doc chunks to prevent single page bloat


def _build_payload(meta: dict, url: str, domain: str, doc_id: str, content_hash: str, spiru_score: float, is_spiru: bool, chunk_text_val: str, chunk_i: int) -> dict:
    return {
        "url": url,
        "source_url": url,
        "domain": domain,
        "doc_id": doc_id,
        "focus": meta.get("focus"),
        "source": meta.get("source"),
        "title": meta.get("title"),
        "published_at": meta.get("published_at"),
        "discovered_at": meta.get("discovered_at"),
        "fetched_at": meta.get("fetched_at"),
        "content_hash": content_hash,
        "spirulina_score": spiru_score,
        "spirulina_terms": (meta.get("spirulina_terms") or [])[:12],
        "is_spirulina": bool(is_spiru),
        "doc_type": meta.get("doc_type") or "webpage",
        "text_boilerplate_share": float(((meta.get("text_stats") or {}) or {}).get("boilerplate_share") or 0.0),
        "chunk_i": chunk_i,
        "chunk_idx": chunk_i,
        "text": chunk_text_val[:6000],
    }


def main() -> None:
    # Read the run-scoped ingest summary
    ingested_path = pathlib.Path(env("INGESTED_PATH", state_path("ingested.json")))
    if not ingested_path.exists():
        raise SystemExit(f"Missing ingested: {ingested_path} (run ingest.py first)")

    data = json.loads(ingested_path.read_text(encoding="utf-8"))
    docs = data.get("ingested", [])

    qcfg = QdrantConfig(url=QDRANT_URL, collection=COLLECTION)

    if IS_BGE_M3:
        from FlagEmbedding import BGEM3FlagModel  # type: ignore
        model = BGEM3FlagModel(EMBED_MODEL, use_fp16=True)
        ensure_collection_hybrid(qcfg, dense_dim=1024)
    else:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(EMBED_MODEL)
        dim = model.get_sentence_embedding_dimension()
        ensure_collection(qcfg, dim=dim)

    points_total = 0
    docs_indexed = 0
    docs_skipped_low_relevance = 0

    for meta in tqdm(docs, desc="Index"):
        parsed_path = meta.get("parsed_path")
        if not parsed_path:
            continue

        spiru_score = float(meta.get("spirulina_score") or 0.0)
        is_spiru = spiru_score >= INDEX_MIN_SPIRULINA_SCORE
        if (not is_spiru) and (not INDEX_ALLOW_NON_SPIRULINA):
            docs_skipped_low_relevance += 1
            continue

        text = pathlib.Path(parsed_path).read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            continue

        chunks = chunk_text(text, CHUNK_MAX, CHUNK_OVERLAP)
        if not chunks:
            continue
        if MAX_CHUNKS_PER_DOC > 0 and len(chunks) > MAX_CHUNKS_PER_DOC:
            chunks = chunks[:MAX_CHUNKS_PER_DOC]

        raw_url = meta.get("url") or ""
        url = normalize_url(raw_url) or raw_url
        domain = ""
        try:
            domain = (urlparse(url).netloc or "").lower()
        except Exception:
            domain = ""

        content_hash = meta.get("content_hash") or ""
        doc_id = safe_id(f"doc|{url}|{content_hash}")[:16]
        base = int(safe_id(url + content_hash), 16) % (10**12)

        points_batch = []

        if IS_BGE_M3:
            enc = model.encode(
                chunks,
                batch_size=12,
                max_length=512,
                return_dense=True,
                return_sparse=True,
                return_colbert_vecs=False,
            )
            dense_vecs = enc["dense_vecs"]
            sparse_vecs = enc["lexical_weights"]

            for i, (ch, dvec, svec) in enumerate(zip(chunks, dense_vecs, sparse_vecs)):
                pid = base + i
                sindices = [int(k) for k in svec.keys()]
                svalues = [float(v) for v in svec.values()]
                points_batch.append({
                    "id": pid,
                    "vector": {
                        "dense": dvec.tolist(),
                        "sparse": {"indices": sindices, "values": svalues},
                    },
                    "payload": _build_payload(meta, url, domain, doc_id, content_hash, spiru_score, is_spiru, ch, i),
                })
                if len(points_batch) >= UPSERT_BATCH:
                    upsert_points_hybrid(qcfg, points_batch, wait=True, timeout=60)
                    points_total += len(points_batch)
                    points_batch = []
        else:
            vectors = model.encode(chunks, normalize_embeddings=True)
            for i, (ch, vec) in enumerate(zip(chunks, vectors)):
                pid = base + i
                points_batch.append({
                    "id": pid,
                    "vector": vec.tolist(),
                    "payload": _build_payload(meta, url, domain, doc_id, content_hash, spiru_score, is_spiru, ch, i),
                })
                if len(points_batch) >= UPSERT_BATCH:
                    upsert_points(qcfg, points_batch, wait=True, timeout=60)
                    points_total += len(points_batch)
                    points_batch = []

        if points_batch:
            if IS_BGE_M3:
                upsert_points_hybrid(qcfg, points_batch, wait=True, timeout=60)
            else:
                upsert_points(qcfg, points_batch, wait=True, timeout=60)
            points_total += len(points_batch)

        docs_indexed += 1

    out_path = pathlib.Path(env("INDEXED_PATH", state_path("indexed.json")))
    out_path.write_text(
        json.dumps(
            {
                "collection": COLLECTION,
                "embed_model": EMBED_MODEL,
                "docs_indexed": docs_indexed,
                "docs_skipped_low_relevance": docs_skipped_low_relevance,
                "points_upserted": points_total,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(str(out_path))


if __name__ == "__main__":
    main()