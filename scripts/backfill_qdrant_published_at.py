"""scripts/backfill_qdrant_published_at.py

Scrolls all Qdrant points that have no published_at payload,
looks up the value from the matching .meta.json file (matched by doc_id or url),
and sets it via overwrite_payload by point ID.
"""

import json
import pathlib
import sys
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pipelines.common import env

QDRANT_URL = env("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = env("QDRANT_COLLECTION", "docs_chunks_v2")
PARSED_DIR = pathlib.Path(env("PARSED_DIR", "storage/parsed"))
BATCH_SIZE = 200

SESSION = requests.Session()


def build_lookup() -> tuple[dict, dict]:
    """Build doc_id→published_at and url→published_at lookup from meta files."""
    by_doc_id: dict[str, str] = {}
    by_url: dict[str, str] = {}
    for f in PARSED_DIR.glob("*.meta.json"):
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
            pub = m.get("published_at")
            if not pub:
                continue
            doc_id = m.get("doc_id")
            url = m.get("url") or ""
            if doc_id:
                by_doc_id[str(doc_id)] = pub
            if url:
                by_url[url.rstrip("/")] = pub
        except Exception:
            continue
    return by_doc_id, by_url


def scroll_points(offset=None):
    url = f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/scroll"
    body = {
        "limit": BATCH_SIZE,
        "with_payload": ["doc_id", "url", "source_url", "published_at"],
        "with_vector": False,
    }
    if offset:
        body["offset"] = offset
    r = SESSION.post(url, json=body, timeout=30)
    r.raise_for_status()
    data = r.json().get("result") or {}
    return data.get("points") or [], data.get("next_page_offset")


def set_payload_batch(point_ids: list, payload: dict) -> bool:
    url = f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/payload"
    body = {"payload": payload, "points": point_ids}
    r = SESSION.post(url, json=body, timeout=30)
    return r.status_code == 200


def main():
    print("Building lookup from meta files...")
    by_doc_id, by_url = build_lookup()
    print(f"  doc_id lookup: {len(by_doc_id)} entries")
    print(f"  url lookup:    {len(by_url)} entries")

    print(f"\nScrolling {QDRANT_COLLECTION}...")
    offset = None
    total = 0
    updated = 0
    already_set = 0
    not_found = 0

    while True:
        points, next_offset = scroll_points(offset)
        if not points:
            break

        # Group points to update by their published_at value
        to_update: dict[str, list] = {}  # pub_date -> [point_ids]
        for pt in points:
            payload = pt.get("payload") or {}
            if payload.get("published_at"):
                already_set += 1
                continue

            # Try matching by doc_id first, then url
            doc_id = str(payload.get("doc_id") or "")
            url = str(payload.get("url") or payload.get("source_url") or "").rstrip("/")

            pub = by_doc_id.get(doc_id) or by_url.get(url)
            if pub:
                to_update.setdefault(pub, []).append(pt["id"])
            else:
                not_found += 1

        # Push updates grouped by date (minimizes API calls)
        for pub, ids in to_update.items():
            ok = set_payload_batch(ids, {"published_at": pub})
            if ok:
                updated += len(ids)

        total += len(points)
        if total % 2000 == 0:
            print(f"  {total} scanned — updated: {updated}, already set: {already_set}, not found: {not_found}")

        if not next_offset:
            break
        offset = next_offset
        time.sleep(0.02)

    print(f"\nDone.")
    print(f"  Total scanned    : {total}")
    print(f"  published_at set : {updated}")
    print(f"  Already had it   : {already_set}")
    print(f"  Not in lookup    : {not_found}")


if __name__ == "__main__":
    main()
