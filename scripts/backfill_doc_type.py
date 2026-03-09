"""scripts/backfill_doc_type.py

Backfill doc_type in:
1. All existing .meta.json files (storage/parsed/)
2. Qdrant payload (set_payload by doc_id filter) for docs_chunks_v2
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
PARSED_DIR = pathlib.Path(env("PARSED_DIR", "storage/parsed"))

SESSION = requests.Session()


def qdrant_set_payload_by_doc_id(doc_id: str, payload: dict) -> bool:
    url = f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/payload"
    body = {
        "payload": payload,
        "filter": {
            "must": [{"key": "doc_id", "match": {"value": doc_id}}]
        },
    }
    try:
        r = SESSION.post(url, json=body, timeout=15)
        return r.status_code == 200
    except Exception as e:
        print(f"  [qdrant error] {e}")
        return False


def main():
    meta_files = sorted(PARSED_DIR.glob("*.meta.json"))
    total = len(meta_files)
    updated_meta = 0
    updated_qdrant = 0
    skipped = 0

    print(f"Found {total} meta files. Backfilling doc_type...")

    for i, meta_path in enumerate(meta_files, 1):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [skip] {meta_path.name}: {e}")
            continue

        # Skip if already classified
        if "doc_type" in meta:
            skipped += 1
            continue

        doc_type = classify_doc_type(
            url=str(meta.get("url") or ""),
            title=str(meta.get("title") or ""),
            doi=str(meta.get("doi") or ""),
            source=str(meta.get("source") or ""),
        )
        meta["doc_type"] = doc_type

        # Update meta.json
        try:
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            updated_meta += 1
        except Exception as e:
            print(f"  [write error] {meta_path.name}: {e}")
            continue

        # Update Qdrant payload if doc_id is available
        doc_id = meta.get("doc_id")
        if doc_id:
            ok = qdrant_set_payload_by_doc_id(doc_id, {"doc_type": doc_type})
            if ok:
                updated_qdrant += 1

        if i % 100 == 0:
            print(f"  {i}/{total} — meta updated: {updated_meta}, qdrant: {updated_qdrant}, skipped: {skipped}")
        time.sleep(0.01)  # avoid hammering Qdrant

    print(f"\nDone.")
    print(f"  Meta files updated : {updated_meta}")
    print(f"  Qdrant points updated: {updated_qdrant}")
    print(f"  Already had doc_type: {skipped}")


if __name__ == "__main__":
    main()
