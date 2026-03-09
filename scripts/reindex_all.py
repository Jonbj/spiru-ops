"""scripts/reindex_all.py — Re-index all parsed documents into a new Qdrant collection.

Used to migrate from docs_chunks (all-MiniLM-L6-v2, 384d, unnamed vectors)
to docs_chunks_v2 (BAAI/bge-m3, 1024d dense + sparse, hybrid retrieval).

Usage:
    source .env
    .venv/bin/python scripts/reindex_all.py [--collection docs_chunks_v2] [--dry-run]

The script reads every .meta.json in PARSED_DIR, loads the companion .txt,
chunks + embeds, and upserts into the target collection. Existing points are
overwritten (upsert is idempotent — safe to re-run).

After the script completes successfully:
    1. Set QDRANT_COLLECTION=docs_chunks_v2 in .env
    2. Set EMBED_MODEL=BAAI/bge-m3 in .env  (if not already set)
    3. The next cron run will write to the new collection automatically.
    4. Keep docs_chunks around for a few days, then drop it:
         curl -X DELETE http://localhost:6333/collections/docs_chunks
"""

import argparse
import json
import pathlib
import sys
import time
from urllib.parse import urlparse

from tqdm import tqdm

# Add project root to path so pipelines.* imports work
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from pipelines.common import env, chunk_text, safe_id, normalize_url
from pipelines.qdrant_rest import (
    QdrantConfig,
    ensure_collection,
    upsert_points,
    ensure_collection_hybrid,
    upsert_points_hybrid,
)

# ── Config ────────────────────────────────────────────────────────────────────
QDRANT_URL = env("QDRANT_URL", "http://localhost:6333")
EMBED_MODEL = env("EMBED_MODEL", "BAAI/bge-m3")
PARSED_DIR = pathlib.Path(env("PARSED_DIR", "storage/parsed"))
CHUNK_MAX = int(env("CHUNK_MAX_CHARS", 2200))
CHUNK_OVERLAP = int(env("CHUNK_OVERLAP", 240))
INDEX_MIN_SPIRULINA_SCORE = float(env("INDEX_MIN_SPIRULINA_SCORE", "0.25"))
UPSERT_BATCH = int(env("QDRANT_UPSERT_BATCH", 128))  # larger batch → fewer SST files → avoid "too many open files"

_BGE_M3_NAMES = {"baai/bge-m3", "bge-m3"}
IS_BGE_M3 = EMBED_MODEL.lower() in _BGE_M3_NAMES


def _payload(meta, url, domain, doc_id, content_hash, spiru_score, is_spiru, ch, i):
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
        "text_boilerplate_share": float(
            ((meta.get("text_stats") or {}) or {}).get("boilerplate_share") or 0.0
        ),
        "chunk_i": i,
        "chunk_idx": i,
        "text": ch[:6000],
    }


def main():
    ap = argparse.ArgumentParser(description="Re-index all parsed docs into a new Qdrant collection.")
    ap.add_argument("--collection", default="docs_chunks_v2", help="Target collection name")
    ap.add_argument("--dry-run", action="store_true", help="Parse + chunk but do not upsert")
    ap.add_argument("--limit", type=int, default=0, help="Max docs to process (0 = all)")
    args = ap.parse_args()

    meta_files = sorted(PARSED_DIR.glob("*.meta.json"))
    if not meta_files:
        print(f"[reindex] No .meta.json files found in {PARSED_DIR}")
        sys.exit(1)

    print(f"[reindex] Found {len(meta_files)} documents in {PARSED_DIR}")
    print(f"[reindex] Model: {EMBED_MODEL} | Collection: {args.collection} | dry-run: {args.dry_run}")

    if args.limit > 0:
        meta_files = meta_files[: args.limit]
        print(f"[reindex] Limiting to {args.limit} docs")

    qcfg = QdrantConfig(url=QDRANT_URL, collection=args.collection)

    if not args.dry_run:
        if IS_BGE_M3:
            ensure_collection_hybrid(qcfg, dense_dim=1024)
            print(f"[reindex] Collection '{args.collection}' ready (dense=1024d + sparse)")
        else:
            # need model for dim; load first
            from sentence_transformers import SentenceTransformer
            _tmp = SentenceTransformer(EMBED_MODEL)
            ensure_collection(qcfg, dim=_tmp.get_sentence_embedding_dimension())
            print(f"[reindex] Collection '{args.collection}' ready (dim={_tmp.get_sentence_embedding_dimension()})")

    # Load model
    if IS_BGE_M3:
        from FlagEmbedding import BGEM3FlagModel  # type: ignore
        model = BGEM3FlagModel(EMBED_MODEL, use_fp16=True)
        print("[reindex] bge-m3 model loaded (fp16)")
    else:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(EMBED_MODEL)
        print(f"[reindex] SentenceTransformer loaded: {EMBED_MODEL}")

    t0 = time.time()
    docs_indexed = 0
    docs_skipped = 0
    docs_empty = 0
    points_total = 0

    for meta_path in tqdm(meta_files, desc="Reindex"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[WARN] Failed to read {meta_path.name}: {e}")
            continue

        # companion text file — prefer parsed_path from meta (same as index.py)
        parsed_path_str = meta.get("parsed_path")
        if parsed_path_str:
            txt_path = pathlib.Path(parsed_path_str)
        else:
            txt_path = meta_path.parent / meta_path.name.replace(".meta.json", ".txt")
        if not txt_path.exists():
            docs_empty += 1
            continue

        spiru_score = float(meta.get("spirulina_score") or 0.0)
        is_spiru = spiru_score >= INDEX_MIN_SPIRULINA_SCORE
        if not is_spiru:
            docs_skipped += 1
            continue

        text = txt_path.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            docs_empty += 1
            continue

        chunks = chunk_text(text, CHUNK_MAX, CHUNK_OVERLAP)
        if not chunks:
            docs_empty += 1
            continue

        raw_url = meta.get("url") or ""
        url = normalize_url(raw_url) or raw_url
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
            for i, (ch, dvec, svec) in enumerate(
                zip(chunks, enc["dense_vecs"], enc["lexical_weights"])
            ):
                pid = base + i
                sindices = [int(k) for k in svec.keys()]
                svalues = [float(v) for v in svec.values()]
                points_batch.append({
                    "id": pid,
                    "vector": {
                        "dense": dvec.tolist(),
                        "sparse": {"indices": sindices, "values": svalues},
                    },
                    "payload": _payload(meta, url, domain, doc_id, content_hash, spiru_score, is_spiru, ch, i),
                })
                if len(points_batch) >= UPSERT_BATCH:
                    if not args.dry_run:
                        upsert_points_hybrid(qcfg, points_batch, wait=True, timeout=90)
                    points_total += len(points_batch)
                    points_batch = []
        else:
            vectors = model.encode(chunks, normalize_embeddings=True)
            for i, (ch, vec) in enumerate(zip(chunks, vectors)):
                pid = base + i
                points_batch.append({
                    "id": pid,
                    "vector": vec.tolist(),
                    "payload": _payload(meta, url, domain, doc_id, content_hash, spiru_score, is_spiru, ch, i),
                })
                if len(points_batch) >= UPSERT_BATCH:
                    if not args.dry_run:
                        upsert_points(qcfg, points_batch, wait=True, timeout=90)
                    points_total += len(points_batch)
                    points_batch = []

        if points_batch:
            if not args.dry_run:
                if IS_BGE_M3:
                    upsert_points_hybrid(qcfg, points_batch, wait=True, timeout=90)
                else:
                    upsert_points(qcfg, points_batch, wait=True, timeout=90)
            points_total += len(points_batch)

        docs_indexed += 1

    elapsed = time.time() - t0
    print(f"\n[reindex] Done in {elapsed:.0f}s")
    print(f"  docs indexed : {docs_indexed}")
    print(f"  docs skipped (low relevance): {docs_skipped}")
    print(f"  docs empty/missing txt: {docs_empty}")
    print(f"  points upserted: {points_total}")
    if args.dry_run:
        print("  (dry-run — nothing written to Qdrant)")
    else:
        print(f"\n  Collection '{args.collection}' is ready.")
        print("  Next steps:")
        print("    1. Verify: curl -s http://localhost:6333/collections/" + args.collection + " | python3 -m json.tool | grep points_count")
        print("    2. Test:   source .env && .venv/bin/python -m pipelines.query 'spirulina pH' --topk 5")
        print("    3. Switch: edit .env → QDRANT_COLLECTION=" + args.collection)
        print("    4. Drop old (after verification):")
        print("         curl -X DELETE http://localhost:6333/collections/docs_chunks")


if __name__ == "__main__":
    main()
