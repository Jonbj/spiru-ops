"""scripts/dedup_qdrant.py

Removes duplicate documents from Qdrant by content_hash.

content_hash is document-level (SHA-256 of raw file), so all chunks of the
same document share the same hash.  A "duplicate" is when the SAME document
was ingested under multiple doc_ids (e.g. after a re-download or URL change).

Logic:
  - Group points by (content_hash, doc_id) → each (hash, doc_id) pair is one
    document copy with N chunks.
  - For each content_hash that maps to more than one doc_id, pick the best
    doc_id (highest avg spirulina_score, tiebreak lowest min chunk_i).
  - Delete ALL chunks of the non-winning doc_ids.

This preserves every chunk of the winning copy while removing exact-content
duplicate document copies.

Safe: read-only scroll first, then batch deletes with confirmation count.
"""

import json
import pathlib
import sys
import time
from collections import defaultdict

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pipelines.common import env

QDRANT_URL = env("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = env("QDRANT_COLLECTION", "docs_chunks_v2")
BATCH_SIZE = 200

SESSION = requests.Session()


def scroll_all():
    """Scroll all points, yielding (id, payload) for each."""
    offset = None
    total = 0
    while True:
        body = {
            "limit": BATCH_SIZE,
            "with_payload": ["content_hash", "spirulina_score", "chunk_i", "doc_id", "url"],
            "with_vector": False,
        }
        if offset:
            body["offset"] = offset
        r = SESSION.post(
            f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/scroll",
            json=body, timeout=30,
        )
        r.raise_for_status()
        data = r.json().get("result") or {}
        points = data.get("points") or []
        for pt in points:
            yield pt
        total += len(points)
        offset = data.get("next_page_offset")
        if not offset:
            break
    return total


def delete_points(ids: list) -> bool:
    r = SESSION.post(
        f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/delete",
        json={"points": ids},
        timeout=30,
    )
    return r.status_code == 200


def main():
    print("Scanning Qdrant for duplicate documents (same content_hash, different doc_id)...")

    # by_hash[content_hash][doc_id] = list of {id, spirulina_score, chunk_i}
    by_hash: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    total = 0
    for pt in scroll_all():
        payload = pt.get("payload") or {}
        ch = str(payload.get("content_hash") or "")
        doc_id = str(payload.get("doc_id") or "")
        if ch and doc_id:
            by_hash[ch][doc_id].append({
                "id": pt["id"],
                "spirulina_score": float(payload.get("spirulina_score") or 0.0),
                "chunk_i": int(payload.get("chunk_i") or 0),
            })
        total += 1
        if total % 5000 == 0:
            print(f"  scanned {total}...")

    print(f"\nTotal points scanned: {total}")

    # Find content_hashes that appear under more than one doc_id
    dup_hashes = {h: docs for h, docs in by_hash.items() if len(docs) > 1}
    print(f"Content hashes with multiple doc_ids (true duplicates): {len(dup_hashes)}")

    if not dup_hashes:
        print("No duplicate documents found.")
        return

    # For each duplicate hash: pick the doc_id with highest avg spirulina_score
    # (tiebreak: lowest min chunk_i). Delete all chunks of the other doc_ids.
    to_delete = []
    for ch, docs_by_id in dup_hashes.items():
        def doc_sort_key(item):
            did, pts = item
            avg_score = sum(p["spirulina_score"] for p in pts) / len(pts)
            min_chunk = min(p["chunk_i"] for p in pts)
            return (-avg_score, min_chunk)

        sorted_docs = sorted(docs_by_id.items(), key=doc_sort_key)
        winner_did = sorted_docs[0][0]
        winner_chunks = len(sorted_docs[0][1])
        loser_chunks = sum(len(pts) for _, pts in sorted_docs[1:])
        print(f"  hash {ch[:16]}…: keep doc_id={winner_did[:12]} ({winner_chunks} chunks), "
              f"delete {len(sorted_docs)-1} doc copies ({loser_chunks} chunks)")
        for did, pts in sorted_docs[1:]:
            to_delete.extend(p["id"] for p in pts)

    print(f"\nTotal chunks to delete: {len(to_delete)}")
    if not to_delete:
        return

    deleted = 0
    for i in range(0, len(to_delete), 100):
        batch = to_delete[i:i+100]
        ok = delete_points(batch)
        if ok:
            deleted += len(batch)
        else:
            print(f"  [warn] delete batch {i//100} failed")
        time.sleep(0.05)

    print(f"\nDone.")
    print(f"  Deleted: {deleted} duplicate-document chunks")
    print(f"  Remaining in collection: {total - deleted}")


if __name__ == "__main__":
    main()
