"""pipelines/analyze_sources_global.py — Global source novelty (DOI/content-hash level)
================================================================================

Goal
----
Compute *global* novelty of sources across the whole KB history.

A "source" key is defined as:
- DOI when available (preferred)
- else content_hash
- else normalized URL

This script scans ALL `storage/state/*_ingested.json` files and builds a global
"first seen" index:
  source_key -> first_run_id

Then it can produce:
- per-run new sources vs global
- per-day new sources vs global

Outputs
-------
- storage/artifacts/sources_global_index.json          (cache, can be rebuilt)
- storage/artifacts/<DAY>_sources_global_new.md        (daily report)
- storage/artifacts/latest_sources_global_new.md

Usage
-----
  python -m pipelines.analyze_sources_global

Env
---
- DAY=YYYY-MM-DD          # default: today's UTC day
- REBUILD_INDEX=0|1       # force rebuild cache
- LIMIT_RUNS=0            # if >0, only report last N runs for that DAY

"""

from __future__ import annotations

import json
import pathlib
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from pipelines.common import env, normalize_url


STATE_DIR = pathlib.Path(env("STATE_DIR", "storage/state"))
ART_DIR = pathlib.Path(env("ARTIFACTS_DIR", env("ART_DIR", "storage/artifacts")))
INDEX_PATH = ART_DIR / "sources_global_index.json"


def _today_utc_day() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_json(path: pathlib.Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _domain(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _source_key(meta: Dict[str, Any]) -> str:
    doi = (meta.get("doi") or "").strip()
    if doi:
        return f"doi:{doi.lower()}"

    ch = (meta.get("content_hash") or "").strip()
    if ch:
        return f"hash:{ch}"

    url = normalize_url(str(meta.get("url") or "")) or str(meta.get("url") or "").strip()
    if url:
        return f"url:{url}"

    return "unknown"


def build_or_load_global_index(*, rebuild: bool = False) -> Dict[str, str]:
    """Return mapping source_key -> first_run_id."""
    ART_DIR.mkdir(parents=True, exist_ok=True)

    if INDEX_PATH.exists() and not rebuild:
        d = _load_json(INDEX_PATH)
        if isinstance(d, dict):
            # ensure str->str
            return {str(k): str(v) for k, v in d.items()}

    # rebuild
    first_seen: Dict[str, str] = {}

    # Sort by filename so earlier RUN_IDs get precedence (lexicographically works for YYYY-MM-DDTHHMMSSZ)
    ing_files = sorted(STATE_DIR.glob("*_ingested.json"))

    for p in ing_files:
        rid = p.name.replace("_ingested.json", "")
        data = _load_json(p)
        if not data:
            continue
        ing = data.get("ingested") or []
        if not isinstance(ing, list):
            continue

        for m in ing:
            if not isinstance(m, dict):
                continue
            k = _source_key(m)
            if not k or k == "unknown":
                continue
            if k not in first_seen:
                first_seen[k] = rid

    INDEX_PATH.write_text(json.dumps(first_seen, ensure_ascii=False, indent=2), encoding="utf-8")
    return first_seen


@dataclass
class RunReport:
    run_id: str
    ingested_docs: int
    unique_sources: int
    new_sources_global: int
    top_domains: List[Tuple[str, int]]


def main() -> None:
    day = (env("DAY", "") or "").strip() or _today_utc_day()
    rebuild = (env("REBUILD_INDEX", "0") or "0").strip() in ("1", "true", "TRUE", "yes", "YES")
    limit_runs = int(env("LIMIT_RUNS", "0") or 0)

    ART_DIR.mkdir(parents=True, exist_ok=True)

    first_seen = build_or_load_global_index(rebuild=rebuild)

    # runs for this day
    rids = [p.name.replace("_ingested.json", "") for p in sorted(STATE_DIR.glob(f"{day}T*_ingested.json"))]
    if limit_runs and len(rids) > limit_runs:
        rids = rids[-limit_runs:]

    out: List[str] = []
    out.append(f"# spiru-ops — Global-new sources ({day})\n")
    out.append("This report counts sources as NEW if their first occurrence in the whole KB history is within the selected run/day.\n")

    if not rids:
        out.append("No RUN_ID-scoped ingested runs found for this day.")
        out_path = ART_DIR / f"{day}_sources_global_new.md"
        out_path.write_text("\n".join(out).strip() + "\n", encoding="utf-8")
        (ART_DIR / "latest_sources_global_new.md").write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(str(out_path))
        return

    # Per-run section
    out.append("## Per-run (new vs global)\n")
    out.append("| RUN_ID | ingested docs | unique sources | NEW sources vs global | top domains |")
    out.append("|---|---:|---:|---:|---|")

    run_reports: List[RunReport] = []
    new_sources_all: List[Tuple[str, str]] = []  # (source_key, first_run_id)
    # store a representative meta for each NEW source_key (from the run it first appears)
    new_meta: Dict[str, Dict[str, Any]] = {}
    dom_all = Counter()

    for rid in rids:
        p = STATE_DIR / f"{rid}_ingested.json"
        data = _load_json(p)
        if not data:
            continue
        ing = data.get("ingested") or []
        if not isinstance(ing, list):
            ing = []

        keys = []
        dom = Counter()
        for m in ing:
            if not isinstance(m, dict):
                continue
            k = _source_key(m)
            if k and k != "unknown":
                keys.append(k)
            d = _domain(str(m.get("url") or ""))
            if d:
                dom[d] += 1

        uniq = sorted(set(keys))
        # New vs global means: first_seen[k] == this rid
        new = [k for k in uniq if first_seen.get(k) == rid]
        for k in new:
            new_sources_all.append((k, rid))

        # Capture representative metadata for each NEW source_key from this run
        if new:
            new_set = set(new)
            for m in ing:
                if not isinstance(m, dict):
                    continue
                k = _source_key(m)
                if k in new_set and k not in new_meta:
                    new_meta[k] = m

        dom_all.update(dom)
        top_dom = ", ".join([f"{d}({n})" for d, n in dom.most_common(3)])
        run_reports.append(RunReport(rid, len(ing), len(uniq), len(new), dom.most_common(3)))

        out.append(f"| {rid} | {len(ing)} | {len(uniq)} | {len(new)} | {top_dom} |")

    out.append("")

    # Aggregate domain highlights for the day
    out.append("## Domain highlights (ingested docs, aggregate for day)\n")
    for d, n in dom_all.most_common(20):
        out.append(f"- `{d}`: {n}")
    out.append("")

    # Expanded new sources
    out.append("## Expanded — sources first seen today\n")
    out.append("Each entry shows the source key plus a representative document metadata snapshot from the run that first saw it.\n")

    # group by rid
    by_rid: Dict[str, List[str]] = {}
    for k, rid in new_sources_all:
        by_rid.setdefault(rid, []).append(k)

    def _fmt_meta(k: str) -> List[str]:
        m = new_meta.get(k) or {}
        url = str(m.get("url") or "").strip()
        title = str(m.get("title") or "").strip()
        focus = str(m.get("focus") or "").strip()
        src = str(m.get("source") or "").strip()
        puby = m.get("publication_year")
        puba = str(m.get("published_at") or "").strip()
        doi = str(m.get("doi") or "").strip()
        spiru = m.get("spirulina_score")
        dom = _domain(url)

        lines = [f"- **{k}**"]
        if title:
            lines.append(f"  - title: {title}")
        if url:
            lines.append(f"  - url: {url}")
        if dom:
            lines.append(f"  - domain: `{dom}`")
        if doi and not k.startswith("doi:"):
            lines.append(f"  - doi: {doi}")
        if puby:
            lines.append(f"  - publication_year: {puby}")
        elif puba:
            lines.append(f"  - published_at: {puba}")
        if focus:
            lines.append(f"  - focus: {focus}")
        if src:
            lines.append(f"  - source: {src}")
        if spiru is not None:
            try:
                lines.append(f"  - spirulina_score: {float(spiru):.3f}")
            except Exception:
                pass
        return lines

    for rid in rids:
        ks = sorted(by_rid.get(rid, []))
        out.append(f"### {rid} — new sources: {len(ks)}")
        for k in ks[:120]:
            out.extend(_fmt_meta(k))
        if len(ks) > 120:
            out.append(f"- … ({len(ks)-120} more)")
        out.append("")

    out_path = ART_DIR / f"{day}_sources_global_new.md"
    out_path.write_text("\n".join(out).strip() + "\n", encoding="utf-8")
    (ART_DIR / "latest_sources_global_new.md").write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(str(out_path))


if __name__ == "__main__":
    main()
