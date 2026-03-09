"""scripts/backfill_qdrant_doc_type.py

Scrolls all Qdrant points, computes doc_type from existing payload fields
(url, title, source, doi), and sets it via overwrite_payload by point ID.
No re-embedding needed.
"""

import json
import pathlib
import sys
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pipelines.common import env, classify_doc_type

QDRANT_URL = env("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = env("QDRANT_COLLECTION", "docs_chunks_v2")
BATCH_SIZE = 200

SESSION = requests.Session()


def scroll_points(offset=None):
    url = f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/scroll"
    body = {
        "limit": BATCH_SIZE,
        "with_payload": ["url", "title", "source", "doc_id"],
        "with_vector": False,
    }
    if offset:
        body["offset"] = offset
    r = SESSION.post(url, json=body, timeout=30)
    r.raise_for_status()
    data = r.json().get("result") or {}
    return data.get("points") or [], data.get("next_page_offset")


def set_payload_batch(point_ids: list, payload: dict):
    """Set payload fields for a list of point IDs."""
    url = f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/payload"
    body = {"payload": payload, "points": point_ids}
    r = SESSION.post(url, json=body, timeout=30)
    return r.status_code == 200


def main():
    print(f"Scrolling {QDRANT_COLLECTION} to backfill doc_type...")
    offset = None
    total = 0
    updated = 0
    already_set = 0

    # Group updates: {doc_type -> [point_ids]}
    # Process per-batch to avoid large payloads
    while True:
        points, next_offset = scroll_points(offset)
        if not points:
            break

        # Group by doc_type within this batch
        by_type: dict[str, list] = {}
        for pt in points:
            payload = pt.get("payload") or {}
            if payload.get("doc_type"):
                already_set += 1
                continue
            dt = classify_doc_type(
                url=str(payload.get("url") or ""),
                title=str(payload.get("title") or ""),
                doi=str(payload.get("doc_id") or ""),  # doc_id ≠ doi, but harmless
                source=str(payload.get("source") or ""),
            )
            by_type.setdefault(dt, []).append(pt["id"])

        # Push one request per doc_type group (minimizes API calls)
        for dt, ids in by_type.items():
            ok = set_payload_batch(ids, {"doc_type": dt})
            if ok:
                updated += len(ids)

        total += len(points)
        print(f"  processed {total} points — updated: {updated}, already set: {already_set}")

        if not next_offset:
            break
        offset = next_offset
        time.sleep(0.05)

    print(f"\nDone.")
    print(f"  Total points scanned : {total}")
    print(f"  doc_type set         : {updated}")
    print(f"  Already had doc_type : {already_set}")


if __name__ == "__main__":
    main()
