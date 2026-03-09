"""scripts/inject_manual_seeds.py

Reads configs/manual_seeds.yaml and appends each seed URL to
storage/state/candidates.jsonl so the next ingest.py run picks them up.

Already-seen URLs (in storage/state/seen_urls.jsonl) are skipped.
Run this before a manual or scheduled pipeline run.
"""

import json
import pathlib
import sys
import time

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pipelines.common import env

STATE_DIR = pathlib.Path(env("STATE_DIR", "storage/state"))
SEEDS_CONFIG = pathlib.Path("configs/manual_seeds.yaml")
CANDIDATES_PATH = STATE_DIR / "candidates.jsonl"


def load_seen_urls() -> set:
    seen_path = STATE_DIR / "seen_urls.jsonl"
    if not seen_path.exists():
        return set()
    seen = set()
    for line in seen_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            seen.add(json.loads(line).get("url", ""))
        except Exception:
            pass
    return seen


def main():
    if not SEEDS_CONFIG.exists():
        print(f"Seeds config not found: {SEEDS_CONFIG}")
        return

    cfg = yaml.safe_load(SEEDS_CONFIG.read_text(encoding="utf-8"))
    seeds_by_focus = cfg.get("seeds", {})

    seen = load_seen_urls()

    added = 0
    skipped_seen = 0
    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(CANDIDATES_PATH, "a", encoding="utf-8") as f:
        for focus, entries in seeds_by_focus.items():
            for entry in (entries or []):
                url = entry.get("url", "").strip()
                if not url:
                    continue
                if url in seen:
                    skipped_seen += 1
                    continue
                record = {
                    "url": url,
                    "title": entry.get("title", ""),
                    "focus": focus,
                    "source": "manual_seed",
                    "discovered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                added += 1
                print(f"  + [{focus}] {url[:70]}")

    print(f"\nDone. Added {added} candidates, skipped {skipped_seen} already seen.")
    if added > 0:
        print(f"  -> Run 'python -m pipelines.ingest' to process them.")


if __name__ == "__main__":
    main()
