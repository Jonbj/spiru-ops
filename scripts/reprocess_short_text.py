"""scripts/reprocess_short_text.py

Re-fetches docs with short_text=True and tries harder extraction.
Skips: Walmart blocked, YouTube, already-denied domains.
On success: updates .txt parsed file + meta, clears short_text flag,
            re-indexes in Qdrant via index.py logic.
"""

import json
import pathlib
import sys
import time
from typing import Optional

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pipelines.common import env, extract_html_text, clean_text_with_stats, normalize_url
from pipelines.relevance import compute_spirulina_relevance

QDRANT_URL = env("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = env("QDRANT_COLLECTION", "docs_chunks_v2")
PARSED_DIR = pathlib.Path(env("PARSED_DIR", "storage/parsed"))
USER_AGENT = env("USER_AGENT", "spiru-ops-bot/0.3")
MIN_TEXT_CHARS = 500

# Domains known to be unscrapable or irrelevant
SKIP_DOMAINS = {
    "www.walmart.com", "walmart.com",
    "www.youtube.com", "youtube.com",
    "twitter.com", "x.com",
    "www.instagram.com",
    "www.facebook.com",
}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
})


def domain_of(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def fetch_html(url: str, timeout: int = 20) -> Optional[str]:
    try:
        r = SESSION.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code == 200 and "text/html" in r.headers.get("Content-Type", ""):
            return r.text
    except Exception:
        pass
    return None


def main():
    meta_files = sorted(PARSED_DIR.glob("*.meta.json"))
    candidates = []
    for f in meta_files:
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
            if m.get("short_text"):
                candidates.append((f, m))
        except Exception:
            continue

    print(f"Docs with short_text flag: {len(candidates)}")
    recovered = 0
    skipped = 0
    still_short = 0

    for meta_path, meta in candidates:
        url = str(meta.get("url") or "")
        dom = domain_of(url)

        if dom in SKIP_DOMAINS or not url.startswith("http"):
            print(f"  [skip] {dom} — {url[:80]}")
            skipped += 1
            continue

        print(f"  [fetch] {url[:90]}")
        html = fetch_html(url)
        time.sleep(1.0)

        if not html:
            print(f"    → fetch failed")
            still_short += 1
            continue

        text = extract_html_text(html)
        stats = clean_text_with_stats(text)
        clean = stats.clean_text if hasattr(stats, "clean_text") else text

        if len(clean) < MIN_TEXT_CHARS:
            print(f"    → still too short ({len(clean)} chars)")
            still_short += 1
            continue

        # Update parsed text file
        txt_name = meta_path.name.replace(".meta.json", ".txt")
        txt_path = PARSED_DIR / txt_name
        try:
            txt_path.write_text(clean, encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"    → write error: {e}")
            still_short += 1
            continue

        # Re-compute spirulina relevance on the new text
        rel = compute_spirulina_relevance(
            url=url,
            title=str(meta.get("title") or ""),
            text=clean[:120000],
        )

        # Update meta
        meta.pop("short_text", None)
        meta["text_stats"] = {
            "raw_chars": stats.raw_chars if hasattr(stats, "raw_chars") else len(text),
            "clean_chars": stats.clean_chars if hasattr(stats, "clean_chars") else len(clean),
            "boilerplate_share": stats.boilerplate_share if hasattr(stats, "boilerplate_share") else 0.0,
        }
        meta["spirulina_score"] = round(float(rel.score), 4)
        meta["spirulina_terms"] = rel.positive_terms[:20]
        meta["spirulina_reasons"] = rel.reasons[:10]
        meta["reprocessed"] = True

        try:
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"    → meta write error: {e}")
            continue

        print(f"    → recovered: {len(clean)} chars")
        recovered += 1

    print(f"\nDone.")
    print(f"  Recovered  : {recovered}")
    print(f"  Skipped    : {skipped}")
    print(f"  Still short: {still_short}")
    if recovered > 0:
        print(f"\n  ⚠ Run 'python -m pipelines.index' to re-index recovered documents.")


if __name__ == "__main__":
    main()
