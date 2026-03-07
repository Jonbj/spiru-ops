"""pipelines/analyze_sources_by_run.py — Source analysis per run (DOI/content-hash level)
================================================================================

Goal
----
Analyze which *sources* were effectively added by each pipeline run.

We define a "source" primarily by:
- DOI (when available) — preferred
- else content_hash (sha256 of downloaded bytes) — stable within the pipeline
- else normalized URL fallback

This helps:
- understand novelty per run
- detect re-ingestion of the same paper via different URLs
- monitor domain saturation and OA/paywall behavior

Inputs
------
- storage/state/<RUN_ID>_ingested.json

Outputs
-------
- storage/artifacts/<DAY>_sources_by_run.md

Usage
-----
  python -m pipelines.analyze_sources_by_run

Optional env:
- DAY=YYYY-MM-DD     # default: today's UTC day
- LIMIT_RUNS=10      # only analyze latest N runs

"""

from __future__ import annotations

import json
import pathlib
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pipelines.common import env, normalize_url


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


def _domain(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _source_key(meta: Dict[str, Any]) -> str:
    """Return a stable key for a document meta."""
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


@dataclass
class RunSources:
    run_id: str
    total_docs: int
    source_keys: List[str]
    domains: Counter
    avg_spirulina_score: float
    share_spirulina_ge_050: float


def _summarize_run_ingested(rid: str) -> Optional[RunSources]:
    p = STATE_DIR / f"{rid}_ingested.json"
    data = _load_json(p)
    if not data:
        return None

    ing = data.get("ingested") or []
    if not isinstance(ing, list):
        ing = []

    keys: List[str] = []
    dom = Counter()
    scores: List[float] = []

    for m in ing:
        if not isinstance(m, dict):
            continue
        keys.append(_source_key(m))
        u = str(m.get("url") or "")
        d = _domain(u)
        if d:
            dom[d] += 1
        scores.append(float(m.get("spirulina_score") or 0.0))

    # de-dup keys within run
    uniq_keys = sorted(set([k for k in keys if k and k != "unknown"]))
    avg_score = (sum(scores) / max(1, len(scores))) if scores else 0.0
    share_050 = (sum(1 for s in scores if s >= 0.50) / max(1, len(scores))) if scores else 0.0

    return RunSources(
        run_id=rid,
        total_docs=len(ing),
        source_keys=uniq_keys,
        domains=dom,
        avg_spirulina_score=float(avg_score),
        share_spirulina_ge_050=float(share_050),
    )


def _pct(x: float) -> str:
    return f"{100.0 * x:.1f}%"


def main() -> None:
    day = (env("DAY", "") or "").strip() or _today_utc_day()
    limit_runs = int(env("LIMIT_RUNS", "0") or 0)

    ART_DIR.mkdir(parents=True, exist_ok=True)

    # Find run ids for day by ingested.json
    rids = []
    for p in sorted(STATE_DIR.glob(f"{day}T*_ingested.json")):
        rid = p.name.replace("_ingested.json", "")
        rids.append(rid)

    if limit_runs and len(rids) > limit_runs:
        rids = rids[-limit_runs:]

    runs: List[RunSources] = []
    for rid in rids:
        rs = _summarize_run_ingested(rid)
        if rs:
            runs.append(rs)

    out: List[str] = []
    out.append(f"# spiru-ops — Sources by run ({day})\n")

    if not runs:
        out.append("No RUN_ID-scoped runs found for this day (expected files like YYYY-MM-DDT*_ingested.json).")
        out_path = ART_DIR / f"{day}_sources_by_run.md"
        out_path.write_text("\n".join(out).strip() + "\n", encoding="utf-8")
        print(str(out_path))
        return

    # cumulative novelty across the day
    seen_sources: set[str] = set()

    out.append("## Per-run novelty (DOI > content_hash > URL)\n")
    out.append("| RUN_ID | ingested docs | unique sources | new sources vs previous runs | avg spiru | spiru>=0.50 | top domains |")
    out.append("|---|---:|---:|---:|---:|---:|---|")

    for r in runs:
        uniq = set(r.source_keys)
        new = sorted([k for k in uniq if k not in seen_sources])
        seen_sources.update(uniq)

        top_dom = ", ".join([f"{d}({n})" for d, n in r.domains.most_common(3)])
        out.append(
            "| {rid} | {docs} | {u} | {newn} | {avg:.3f} | {share} | {top} |".format(
                rid=r.run_id,
                docs=r.total_docs,
                u=len(uniq),
                newn=len(new),
                avg=r.avg_spirulina_score,
                share=_pct(r.share_spirulina_ge_050),
                top=top_dom,
            )
        )

    out.append("")

    # Aggregate domain counts across all runs
    out.append("## Domain distribution (ingested docs, aggregate)\n")
    dom_all = Counter()
    for r in runs:
        dom_all.update(r.domains)
    for d, n in dom_all.most_common(20):
        out.append(f"- `{d}`: {n}")

    out.append("")

    # List new sources per run (expanded)
    out.append("## New sources per run (expanded)\n")
    seen_sources2: set[str] = set()
    for r in runs:
        uniq = set(r.source_keys)
        new = sorted([k for k in uniq if k not in seen_sources2])
        seen_sources2.update(uniq)
        out.append(f"### {r.run_id} — new sources: {len(new)}")
        for k in new[:120]:
            out.append(f"- {k}")
        if len(new) > 120:
            out.append(f"- … ({len(new)-120} more)")
        out.append("")

    out_path = ART_DIR / f"{day}_sources_by_run.md"
    out_path.write_text("\n".join(out).strip() + "\n", encoding="utf-8")

    # convenience
    (ART_DIR / "latest_sources_by_run.md").write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")

    print(str(out_path))


if __name__ == "__main__":
    main()
