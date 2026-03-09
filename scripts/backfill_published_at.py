"""scripts/backfill_published_at.py

For docs missing published_at:
1. If has DOI → query Crossref API → extract published date
2. If openalex source + has openalex URL → try OpenAlex works API
Updates both .meta.json and Qdrant payload.
"""

import json
import pathlib
import sys
import time
from typing import Optional

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pipelines.common import env

QDRANT_URL = env("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = env("QDRANT_COLLECTION", "docs_chunks_v2")
PARSED_DIR = pathlib.Path(env("PARSED_DIR", "storage/parsed"))
CROSSREF_MAILTO = env("CROSSREF_MAILTO", "stefano.delgobbo@gmail.com")
OPENALEX_EMAIL = env("OPENALEX_EMAIL", CROSSREF_MAILTO)
USER_AGENT = env("USER_AGENT", "spiru-ops-bot/0.3")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


def crossref_published_at(doi: str) -> Optional[str]:
    """Query Crossref for published date. Returns ISO date string or None."""
    doi = doi.strip().lstrip("https://doi.org/").lstrip("http://dx.doi.org/")
    url = f"https://api.crossref.org/works/{doi}"
    params = {"mailto": CROSSREF_MAILTO}
    try:
        r = SESSION.get(url, params=params, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json().get("message") or {}
        # Try published-print > published-online > issued
        for key in ("published-print", "published-online", "issued"):
            dp = (data.get(key) or {}).get("date-parts")
            if dp and dp[0]:
                parts = dp[0]
                if len(parts) >= 3:
                    return f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"
                elif len(parts) == 2:
                    return f"{parts[0]:04d}-{parts[1]:02d}"
                elif len(parts) == 1:
                    return f"{parts[0]:04d}"
    except Exception:
        pass
    return None


def openalex_published_at(url: str) -> Optional[str]:
    """Query OpenAlex by landing page URL. Returns ISO date or None."""
    api_url = "https://api.openalex.org/works"
    params = {"filter": f"primary_location.landing_page_url:{url}", "mailto": OPENALEX_EMAIL}
    try:
        r = SESSION.get(api_url, params=params, timeout=15)
        if r.status_code != 200:
            return None
        results = (r.json().get("results") or [])
        if results:
            return results[0].get("publication_date")
    except Exception:
        pass
    return None


def qdrant_set_payload_by_doc_id(doc_id: str, payload: dict) -> bool:
    url = f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/payload"
    body = {
        "payload": payload,
        "filter": {"must": [{"key": "doc_id", "match": {"value": doc_id}}]},
    }
    try:
        r = SESSION.post(url, json=body, timeout=15)
        return r.status_code == 200
    except Exception:
        return False


def main():
    meta_files = sorted(PARSED_DIR.glob("*.meta.json"))
    candidates = []
    for f in meta_files:
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
            if not m.get("published_at"):
                candidates.append((f, m))
        except Exception:
            continue

    print(f"Docs missing published_at: {len(candidates)}")
    resolved = 0
    failed = 0

    for i, (meta_path, meta) in enumerate(candidates, 1):
        pub = None
        method = ""

        doi = str(meta.get("doi") or "").strip()
        if doi:
            pub = crossref_published_at(doi)
            method = "crossref"
            time.sleep(0.12)  # Crossref polite pool: ~10 req/s

        if not pub and meta.get("source") == "openalex":
            pub = openalex_published_at(str(meta.get("url") or ""))
            method = "openalex"
            time.sleep(0.1)

        if pub:
            meta["published_at"] = pub
            try:
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                print(f"  [write error] {meta_path.name}: {e}")
                continue

            doc_id = meta.get("doc_id")
            if doc_id:
                qdrant_set_payload_by_doc_id(doc_id, {"published_at": pub})

            resolved += 1
            if resolved % 20 == 0 or i % 50 == 0:
                print(f"  [{i}/{len(candidates)}] resolved: {resolved} — last: {pub} via {method}")
        else:
            failed += 1

    print(f"\nDone.")
    print(f"  Resolved published_at: {resolved}")
    print(f"  Not found: {failed}")


if __name__ == "__main__":
    main()
