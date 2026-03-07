"""pipelines/aggregate_daily.py — Daily aggregated report
================================================================================

Purpose
-------
Your pipeline can run multiple times per day (e.g. 4 runs at 02/08/14/20).
This script aggregates per-run artifacts into ONE daily Markdown summary.

It is designed to be:
- cron-safe (no interactive input)
- tolerant to partial failures (missing files)
- RUN_ID-aware (runs are identified by the RUN_ID prefix YYYY-MM-DD)

Inputs
------
- Per-run state JSON files written by the pipeline:
  - storage/state/<RUN_ID>_ingested.json
  - storage/state/<RUN_ID>_indexed.json
  - (optional) storage/state/<RUN_ID>_candidates.jsonl

Output
------
- storage/artifacts/<YYYY-MM-DD>_daily_aggregate.md
- plus an optional convenience copy: storage/artifacts/latest_daily_aggregate.md

Usage
-----
  python -m pipelines.aggregate_daily

You can override the day:
  AGG_DAY=2026-02-26 python -m pipelines.aggregate_daily

"""

from __future__ import annotations

import json
import pathlib
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from pipelines.common import env


STATE_DIR = pathlib.Path(env("STATE_DIR", "storage/state"))
ART_DIR = pathlib.Path(env("ARTIFACTS_DIR", env("ART_DIR", "storage/artifacts")))


def _today_utc_day() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_json(path: pathlib.Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _count_jsonl_lines(path: pathlib.Path) -> int:
    if not path.exists():
        return 0
    n = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def _pct(x: float) -> str:
    return f"{100.0 * x:.1f}%"


@dataclass
class RunSummary:
    run_id: str
    candidates: int
    ingested: int
    failures_total: int
    openalex_fallback_used: int
    openalex_fallback_success: int
    docs_indexed: int
    points_upserted: int
    spirulina_share_ge_050: float
    avg_spirulina_score: float
    unique_domains: int
    top_domains: List[Tuple[str, int]]


def _domain(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _summarize_run(rid: str) -> Optional[RunSummary]:
    ing_path = STATE_DIR / f"{rid}_ingested.json"
    idx_path = STATE_DIR / f"{rid}_indexed.json"
    cand_path = STATE_DIR / f"{rid}_candidates.jsonl"

    ing = _load_json(ing_path)
    idx = _load_json(idx_path)

    if not ing or not idx:
        return None

    ingested_list = ing.get("ingested") or []
    if not isinstance(ingested_list, list):
        ingested_list = []

    # Candidates
    candidates = _count_jsonl_lines(cand_path)

    # Failures
    failures_total = int(ing.get("failures_total") or 0)

    # OpenAlex fallback
    fb = ing.get("openalex_fallback") or {}
    openalex_used = int((fb.get("used") or 0) if isinstance(fb, dict) else 0)
    openalex_succ = int((fb.get("success") or 0) if isinstance(fb, dict) else 0)

    # Relevance
    scores = [float(m.get("spirulina_score") or 0.0) for m in ingested_list if isinstance(m, dict)]
    avg_score = (sum(scores) / max(1, len(scores))) if scores else 0.0
    share_050 = (sum(1 for s in scores if s >= 0.50) / max(1, len(scores))) if scores else 0.0

    # Domain stats
    domains = [_domain(str(m.get("url") or "")) for m in ingested_list if isinstance(m, dict)]
    domains = [d for d in domains if d]
    c = Counter(domains)
    unique_domains = len(set(domains))
    top_domains = c.most_common(8)

    return RunSummary(
        run_id=rid,
        candidates=int(candidates),
        ingested=len(ingested_list),
        failures_total=failures_total,
        openalex_fallback_used=openalex_used,
        openalex_fallback_success=openalex_succ,
        docs_indexed=int(idx.get("docs_indexed") or 0),
        points_upserted=int(idx.get("points_upserted") or 0),
        spirulina_share_ge_050=float(share_050),
        avg_spirulina_score=float(avg_score),
        unique_domains=int(unique_domains),
        top_domains=top_domains,
    )


def main() -> None:
    day = (env("AGG_DAY", "") or "").strip() or _today_utc_day()

    ART_DIR.mkdir(parents=True, exist_ok=True)

    # Find run ids for this day (RUN_ID prefix)
    # We anchor on ingested.json existence because it's the most informative state.
    rids = []
    for p in sorted(STATE_DIR.glob(f"{day}T*_ingested.json")):
        name = p.name
        rid = name.replace("_ingested.json", "")
        rids.append(rid)

    runs: List[RunSummary] = []
    for rid in rids:
        rs = _summarize_run(rid)
        if rs:
            runs.append(rs)

    out: List[str] = []
    out.append(f"# spiru-ops — Daily aggregate report ({day})\n")

    if not runs:
        out.append("No runs found (no *_ingested.json for this day).")
        out_path = ART_DIR / f"{day}_daily_aggregate.md"
        out_path.write_text("\n".join(out).strip() + "\n", encoding="utf-8")
        (ART_DIR / "latest_daily_aggregate.md").write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(str(out_path))
        return

    # Summary totals
    out.append("## Summary\n")
    out.append(f"- Runs: **{len(runs)}**")
    out.append(f"- Total candidates: **{sum(r.candidates for r in runs)}**")
    out.append(f"- Total ingested docs: **{sum(r.ingested for r in runs)}**")
    out.append(f"- Total failures: **{sum(r.failures_total for r in runs)}**")
    out.append(f"- Total indexed points upserted: **{sum(r.points_upserted for r in runs)}**")

    # Averages
    avg_spiru = sum(r.avg_spirulina_score for r in runs) / max(1, len(runs))
    avg_share = sum(r.spirulina_share_ge_050 for r in runs) / max(1, len(runs))
    out.append(f"- Avg spirulina score (ingested): **{avg_spiru:.3f}**")
    out.append(f"- Avg share spirulina>=0.50 (ingested): **{_pct(avg_share)}**\n")

    # Per-run table (Markdown)
    out.append("## Runs (per RUN_ID)\n")
    out.append("| RUN_ID | candidates | ingested | failures | openalex fb (used/success) | indexed docs | points | avg spiru | spiru>=0.50 | uniq domains |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in runs:
        out.append(
            "| {rid} | {cand} | {ing} | {fail} | {fb_u}/{fb_s} | {didx} | {pts} | {avg:.3f} | {share} | {ud} |".format(
                rid=r.run_id,
                cand=r.candidates,
                ing=r.ingested,
                fail=r.failures_total,
                fb_u=r.openalex_fallback_used,
                fb_s=r.openalex_fallback_success,
                didx=r.docs_indexed,
                pts=r.points_upserted,
                avg=r.avg_spirulina_score,
                share=_pct(r.spirulina_share_ge_050),
                ud=r.unique_domains,
            )
        )
    out.append("")

    # Domain highlights (aggregate)
    out.append("## Domain highlights (aggregate)\n")
    dom_all = Counter()
    for r in runs:
        dom_all.update(dict(r.top_domains))
    for d, n in dom_all.most_common(12):
        out.append(f"- `{d}`: {n}")
    out.append("")

    out_path = ART_DIR / f"{day}_daily_aggregate.md"
    out_path.write_text("\n".join(out).strip() + "\n", encoding="utf-8")

    # Convenience: keep a copy to latest_*.md (copy, not symlink, to be FS/Windows-safe)
    (ART_DIR / "latest_daily_aggregate.md").write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")

    print(str(out_path))


if __name__ == "__main__":
    main()
