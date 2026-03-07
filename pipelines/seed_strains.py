"""pipelines/seed_strains.py — spiru-ops (documented version)

This file is part of the spiru-ops project, which builds a Spirulina/Arthrospira
knowledge base and a RAG Copilot.

The repository is intentionally documented with verbose comments so that:
- humans can quickly understand intent and invariants
- AI tools (agents, refactoring assistants) can reason about the code safely

This header is *documentation-only*; the runtime logic below is preserved.
"""

import json
import pathlib
import time
from typing import Any, Dict, List, Optional
import requests

from pipelines.common import (
    env,
    day_stamp_utc,
    state_path,
    utc_now_iso,
    safe_id,
    load_yaml,
    normalize_url,
)

USER_AGENT = env("USER_AGENT", "spiru-ops-bot/0.3")
BRAVE_API_KEY = env("BRAVE_API_KEY", required=False)

STATE_DIR = pathlib.Path(env("STATE_DIR", "storage/state"))
# Output is RUN_ID-scoped (set by daily.sh)
OUT_PATH = pathlib.Path(env("STRAIN_SEEDS_PATH", state_path("strain_seeds.jsonl")))

CONFIG_PATH = env("STRAIN_SEEDS_CONFIG", "configs/strain_seeds.yaml")

# Seed score: very high so you can always prioritize these in downstream selection
SEED_SCORE = 999


def brave_search(query: str, count: int = 10) -> List[Dict[str, Any]]:
    """
    Brave Web Search API. Returns list of result objects (dicts).
    If BRAVE_API_KEY missing, returns empty list.
    """
    if not BRAVE_API_KEY:
        return []

    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": BRAVE_API_KEY,
        "User-Agent": USER_AGENT,
    }
    params = {"q": query, "count": str(count)}

    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()

    data = r.json()
    return (data.get("web") or {}).get("results") or []


def mk_row(
    *,
    url: str,
    title: str = "",
    snippet: str = "",
    focus: str = "spirulina_strains_eu_collections",
    source: str = "seed",
    score: int = SEED_SCORE,
    tag: Optional[str] = None,
) -> Dict[str, Any]:
    url = normalize_url(url)
    t = (title or "").strip()
    sn = (snippet or "").strip()

    # If we have a tag, enrich title/snippet a bit without being too noisy
    if tag and not t:
        t = tag
    if tag and sn and tag not in sn:
        sn = f"{tag} — {sn}"
    elif tag and not sn:
        sn = tag

    return {
        "id": safe_id(f"strain|{source}|{url}"),
        "focus": focus,
        "source": source,
        "url": url,
        "title": t,
        "snippet": sn,
        "published_at": None,
        "score": score,
        "discovered_at": utc_now_iso(),
    }


def load_config(path: str) -> Dict[str, Any]:
    cfg = load_yaml(path) or {}
    if not isinstance(cfg, dict):
        return {}
    return cfg


def add_must_urls(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in cfg.get("must_urls", []) or []:
        try:
            url = item.get("url")
            if not url:
                continue
            rows.append(
                mk_row(
                    url=url,
                    title=item.get("tag") or "",
                    snippet=item.get("tag") or "",
                    focus=item.get("focus") or "spirulina_strains_eu_collections",
                    source=item.get("source") or "seed:doc",
                    score=SEED_SCORE,
                    tag=item.get("tag"),
                )
            )
        except Exception:
            continue
    return rows


def add_collection_discovery(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Use Brave queries per collection to discover strain pages and catalog pages.
    """
    rows: List[Dict[str, Any]] = []
    collections = cfg.get("collections", []) or []
    for col in collections:
        cname = col.get("name") or "UNKNOWN"
        queries = col.get("brave_queries", []) or []
        for q in queries:
            try:
                results = brave_search(q, count=10)
                for r in results:
                    url = r.get("url") or ""
                    url = normalize_url(url)
                    if not url:
                        continue
                    title = (r.get("title") or "").strip()
                    snippet = (r.get("description") or "").strip()

                    rows.append(
                        mk_row(
                            url=url,
                            title=title,
                            snippet=snippet,
                            focus="spirulina_strains_eu_collections",
                            source=f"seed:{cname}",
                            score=SEED_SCORE,
                        )
                    )
                # be polite to API / avoid burst
                time.sleep(0.35)
            except Exception:
                continue
    return rows


def dedup_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for r in rows:
        u = r.get("url")
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(r)
    return out


def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    cfg = load_config(CONFIG_PATH)

    rows: List[Dict[str, Any]] = []

    # 1) Always include must_urls (doc-driven gold seeds)
    rows.extend(add_must_urls(cfg))

    # 2) Optionally include Brave-based discovery
    if BRAVE_API_KEY:
        rows.extend(add_collection_discovery(cfg))
    else:
        # still output must_urls only; that's fine
        pass

    rows = dedup_rows(rows)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(str(OUT_PATH))
    print(f"strain seed urls: {len(rows)}")
    if not BRAVE_API_KEY:
        print("Note: BRAVE_API_KEY not set, only must_urls were emitted.")


if __name__ == "__main__":
    main()