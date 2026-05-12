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
import logging
import pathlib
from urllib.parse import urlparse

from tqdm import tqdm

from pipelines.common import env, env_bool, chunk_text, safe_id, normalize_url, state_path
from pipelines.qdrant_rest import (
    QdrantConfig,
    ensure_collection,
    upsert_points,
    ensure_collection_hybrid,
    upsert_points_hybrid,
)

# Configura il logger
logger = logging.getLogger(__name__)

QDRANT_URL = env("QDRANT_URL", required=True)
COLLECTION = env("QDRANT_COLLECTION", "docs_chunks_v2")

STATE_DIR = pathlib.Path(env("STATE_DIR", "storage/state"))

EMBED_MODEL = env("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
# 2200 chars ≈ 350-400 tokens at ~5.5 chars/token — fits inside BGE-M3's 512-token
# encoding window with room for special tokens. Using chars (not tokens) avoids a
# tokenizer dependency at chunking time and is fast enough for our throughput.
CHUNK_MAX = int(env("CHUNK_MAX_CHARS", 2200))
# 240 chars ≈ 11% overlap. Enough to preserve sentence-boundary context across
# adjacent chunks so retrieval doesn't miss passages that span a chunk boundary.
CHUNK_OVERLAP = int(env("CHUNK_OVERLAP", 240))

# Detect bge-m3 mode: uses FlagEmbedding, produces dense+sparse hybrid vectors
_BGE_M3_NAMES = {"baai/bge-m3", "bge-m3"}
IS_BGE_M3 = EMBED_MODEL.lower() in _BGE_M3_NAMES

# Device for embedding model. Default "cuda" if available, else "cpu".
# Set EMBED_DEVICE=cpu to force CPU and avoid VRAM conflict with LLM server.
EMBED_DEVICE = env("EMBED_DEVICE", "").strip().lower() or None

# Documents below this spirulina relevance score are skipped at index time.
# 0.25 is deliberately lower than the QC threshold (0.35) and the copilot filter
# (0.22) — it accepts borderline documents into the KB so the copilot can still
# retrieve them when queried with a very specific focus, while the QC step checks
# the aggregate share remains healthy.
INDEX_MIN_SPIRULINA_SCORE = float(env("INDEX_MIN_SPIRULINA_SCORE", "0.25"))
INDEX_ALLOW_NON_SPIRULINA = env_bool("INDEX_ALLOW_NON_SPIRULINA", False)

# 64 points per Qdrant upsert request — balances HTTP payload size and round-trip
# overhead. Too small = many round trips; too large = risk of timeout on slow networks.
UPSERT_BATCH = int(env("QDRANT_UPSERT_BATCH", "64"))
# Hard cap on chunks per document. Prevents a single very-long PDF (e.g. a thesis or
# textbook) from flooding the KB and dominating retrieval results. At 2200 chars/chunk,
# 50 chunks ≈ 110 KB of text — more than enough for any scientific paper.
MAX_CHUNKS_PER_DOC = int(env("MAX_CHUNKS_PER_DOC", "50"))

# Validazione dei parametri di chunking
CHUNK_MAX = int(env("CHUNK_MAX_CHARS", 2200))
if CHUNK_MAX <= 0:
    raise ValueError("CHUNK_MAX_CHARS must be positive")

CHUNK_OVERLAP = int(env("CHUNK_OVERLAP", 240))
if CHUNK_OVERLAP < 0:
    raise ValueError("CHUNK_OVERLAP cannot be negative")
if CHUNK_OVERLAP >= CHUNK_MAX:
    raise ValueError("CHUNK_OVERLAP must be less than CHUNK_MAX_CHARS")


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
        model = BGEM3FlagModel(
            EMBED_MODEL,
            use_fp16=(EMBED_DEVICE != "cpu"),
            devices=[EMBED_DEVICE] if EMBED_DEVICE else None,
        )
        ensure_collection_hybrid(qcfg, dense_dim=1024)
    else:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(EMBED_MODEL, device=EMBED_DEVICE)
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
        # Point IDs must be unsigned 64-bit integers in Qdrant. We derive a stable
        # base from url+content_hash so re-indexing the same document produces the
        # same IDs (idempotent upsert). Modulo 10^12 keeps the number human-readable
        # in logs while staying well within u64 range.
        base = int(safe_id(url + content_hash), 16) % (10**12)

        points_batch = []

        if IS_BGE_M3:
            enc = model.encode(
                chunks,
                batch_size=32,
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