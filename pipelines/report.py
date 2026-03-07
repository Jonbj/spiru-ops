"""pipelines/report.py — Generate a human-readable pipeline run report
================================================================================

Functional purpose
------------------
This step produces a Markdown report summarizing the current run:
- number of candidates discovered
- ingest success/failure breakdown
- domain distribution and saturation
- Spirulina relevance stats

Why this exists
--------------
The pipeline is only as useful as its KB quality.
A report makes it easy to diagnose:
- source bias (same domains repeating)
- noise/boilerplate prevalence
- ingestion failures (403/429/timeouts)
- whether Spirulina-centric gating is working

RUN_ID
------
Report filenames are RUN_ID-scoped to avoid overwriting when running 3-4 times/day.
"""

import json
import pathlib
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from pipelines.common import day_stamp_utc, env, artifact_path, state_path


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)

STATE_DIR = pathlib.Path(env("STATE_DIR", "storage/state"))
ART_DIR = pathlib.Path(env("ART_DIR", "storage/artifacts"))


def load_state_json(suffix: str) -> Dict[str, Any]:
    """Loads run-scoped JSON if present.

    Preference order:
    - env var (CANDIDATES_PATH / INGESTED_PATH / INDEXED_PATH) is handled elsewhere
    - else use RUN_ID-scoped `state_path('<suffix>.json')`

    We keep day_stamp_utc compatibility only as a last-resort fallback.
    """
    # RUN_ID-scoped default
    p = pathlib.Path(state_path(f"{suffix}.json"))
    if not p.exists():
        # backward compat: old day-based filenames
        p = STATE_DIR / f"{day_stamp_utc()}_{suffix}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_jsonl(path: pathlib.Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    out.append(obj)
            except Exception:
                continue
    return out


def md_table(rows: List[Tuple[str, str]]) -> str:
    if not rows:
        return ""
    lines = ["| Key | Value |", "|---|---|"]
    for k, v in rows:
        lines.append(f"| {k} | {v} |")
    return "\n".join(lines)


def netloc(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def is_pdfish(url: str) -> bool:
    u = (url or "").lower()
    return (
        u.endswith(".pdf")
        or "format=pdf" in u
        or "type=pdf" in u
        or "/pdf" in u
        or "download=pdf" in u
    )


def load_seen_urls() -> set:
    """
    Reads storage/state/seen_urls.jsonl if present.
    Accepts lines like:
      {"url": "..."}  OR {"u":"..."} OR "https://..."
    """
    p = STATE_DIR / "seen_urls.jsonl"
    if not p.exists():
        return set()

    seen = set()
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                obj = None

            if isinstance(obj, str):
                seen.add(obj)
                continue
            if isinstance(obj, dict):
                u = obj.get("url") or obj.get("u") or obj.get("source_url")
                if isinstance(u, str) and u.strip():
                    seen.add(u.strip())
    return seen


def top_k(counter: Dict[str, int], k: int = 12) -> List[Tuple[str, int]]:
    return sorted(counter.items(), key=lambda kv: kv[1], reverse=True)[:k]


def pct(n: int, d: int) -> str:
    if d <= 0:
        return "0%"
    return f"{(100.0 * n / d):.1f}%"


def main() -> None:
    ART_DIR.mkdir(parents=True, exist_ok=True)

    # Optional (may not exist in your pipeline)
    discover = load_state_json("discover")
    ingested_state = load_state_json("ingested")
    indexed = load_state_json("indexed")

    candidates_path = pathlib.Path(env("CANDIDATES_PATH", state_path("candidates.jsonl")))
    strain_seeds_path = pathlib.Path(env("STRAIN_SEEDS_PATH", state_path("strain_seeds.jsonl")))

    # Backward compat: if run-scoped not found, fall back to day-based
    if not candidates_path.exists():
        candidates_path = STATE_DIR / f"{day_stamp_utc()}_candidates.jsonl"
    if not strain_seeds_path.exists():
        strain_seeds_path = STATE_DIR / f"{day_stamp_utc()}_strain_seeds.jsonl"

    candidates = load_jsonl(candidates_path)
    strain_seeds = load_jsonl(strain_seeds_path)

    # From ingest state (your current schema)
    failures_by_reason: Dict[str, int] = (ingested_state.get("failures_by_reason") or {})
    failures_total = int(ingested_state.get("failures_total") or 0)
    skipped: Dict[str, int] = (ingested_state.get("skipped") or {})

    sources_kpi = ingested_state.get("sources_kpi") or {}
    if not isinstance(sources_kpi, dict):
        sources_kpi = {}

    # Ingested docs metadata list (if present)
    ing = ingested_state.get("ingested") or []
    if not isinstance(ing, list):
        ing = []

    # ---- Candidate analytics
    cand_by_focus: Dict[str, int] = {}
    cand_by_domain: Dict[str, int] = {}
    cand_by_source: Dict[str, int] = {}
    cand_pdf = 0

    for c in candidates:
        f = (c.get("focus") or "unknown").strip()
        cand_by_focus[f] = cand_by_focus.get(f, 0) + 1

        s = (c.get("source") or "unknown").strip()
        cand_by_source[s] = cand_by_source.get(s, 0) + 1

        u = c.get("url") or ""
        d = netloc(u) or "unknown"
        cand_by_domain[d] = cand_by_domain.get(d, 0) + 1

        if is_pdfish(u):
            cand_pdf += 1

    # Novelty vs seen_urls.jsonl
    seen = load_seen_urls()
    cand_urls = [c.get("url") for c in candidates if isinstance(c.get("url"), str)]
    cand_unique = len(set(cand_urls))
    cand_new = len([u for u in set(cand_urls) if u and u not in seen])
    cand_old = cand_unique - cand_new

    # Concentration: how much top N domains account for
    total_cand = len(candidates)
    top_domains = top_k(cand_by_domain, 12)
    top5_share = 0
    if total_cand > 0:
        top5_share = sum(v for _, v in top_domains[:5])

    # ---- Ingest analytics
    ing_by_focus: Dict[str, int] = {}
    ing_by_domain: Dict[str, int] = {}
    ing_pdf = 0
    spiru_cnt = 0

    # ---- Ingest source diversity KPIs (preferred: produced by ingest.py)
    # We include this in the Markdown report so it's easy to track over time.
    diversity_rows: List[Tuple[str, str]] = []
    if sources_kpi:
        diversity_rows = [
            ("ingested_docs", str(sources_kpi.get("n_docs", ""))),
            ("unique_domain_families", str(sources_kpi.get("unique_domain_families", ""))),
            ("top5_share", str(sources_kpi.get("top5_share", ""))),
            ("top10_share", str(sources_kpi.get("top10_share", ""))),
            ("hhi", str(sources_kpi.get("hhi", ""))),
            ("entropy_norm", str(sources_kpi.get("entropy_norm", ""))),
            (f"novelty_domains_share_{sources_kpi.get('history_days','?')}d", str(sources_kpi.get("novelty_domains_share_history_days", ""))),
            ("jaccard_vs_prev_run", str(sources_kpi.get("jaccard_vs_prev_run", ""))),
        ]
    spiru_scores: List[float] = []
    term_cnt: Dict[str, int] = {}
    boiler_share: List[float] = []
    for m in ing:
        f = (m.get("focus") or "unknown").strip()
        ing_by_focus[f] = ing_by_focus.get(f, 0) + 1

        u = m.get("url") or ""
        d = netloc(u) or "unknown"
        ing_by_domain[d] = ing_by_domain.get(d, 0) + 1

        if is_pdfish(u):
            ing_pdf += 1

        s = _f(m.get("spirulina_score"), 0.0)
        if s > 0:
            spiru_scores.append(s)
        if s >= 0.30:
            spiru_cnt += 1
        for t in (m.get("spirulina_terms") or [])[:20]:
            if not isinstance(t, str) or not t:
                continue
            term_cnt[t] = term_cnt.get(t, 0) + 1
        ts = (m.get("text_stats") or {})
        if isinstance(ts, dict):
            boiler_share.append(_f(ts.get("boilerplate_share"), 0.0))

    top_failures = top_k(failures_by_reason, 12)

    out_md: List[str] = []
    out_md.append(f"# Daily report — {day_stamp_utc()}\n")

    # ---- Summary
    out_md.append("## Pipeline summary\n")
    out_md.append(
        md_table(
            [
                ("candidates_found", str(discover.get("candidates_found", len(candidates)))),
                ("candidates_unique_urls", str(cand_unique)),
                ("candidates_new_vs_seen", f"{cand_new} ({pct(cand_new, cand_unique)})"),
                ("candidates_already_seen", f"{cand_old} ({pct(cand_old, cand_unique)})"),
                ("candidates_pdf_ratio", f"{cand_pdf}/{len(candidates)} ({pct(cand_pdf, len(candidates))})"),
                ("strain_seeds", str(len(strain_seeds))),
                ("docs_ingested", str(len(ing))),
                ("ingested_pdf_ratio", f"{ing_pdf}/{len(ing)} ({pct(ing_pdf, len(ing))})" if len(ing) else "0/0 (0%)"),
                ("spirulina_share_ingested", f"{spiru_cnt}/{len(ing)} ({pct(spiru_cnt, len(ing))})" if len(ing) else "0/0 (0%)"),
                ("spirulina_avg_score", f"{(sum(spiru_scores)/len(spiru_scores)):.3f}" if spiru_scores else "0.000"),
                ("boilerplate_share_avg", f"{(sum(boiler_share)/len(boiler_share)):.2%}" if boiler_share else "n/a"),
                ("failures_total", str(failures_total)),
                ("skipped_already_seen", str(skipped.get("already_seen", 0))),
                ("skipped_denied_domain", str(skipped.get("denied_domain", 0))),
                ("indexed_docs", str(indexed.get("docs_indexed", 0))),
                ("indexed_docs_skipped_low_relevance", str(indexed.get("docs_skipped_low_relevance", 0))),
                ("indexed_points", str(indexed.get("points_upserted", 0))),
                ("qdrant_collection", str(indexed.get("collection", ""))),
                ("embed_model", str(indexed.get("embed_model", ""))),
            ]
        )
        + "\n"
    )

    # ---- Candidate health checks
    out_md.append("## Candidate health\n")
    out_md.append(f"- **Unique domains (candidates):** {len(cand_by_domain)}")
    out_md.append(f"- **Top-5 domains share:** {top5_share}/{total_cand} ({pct(top5_share, total_cand)})")
    out_md.append("")

    # Candidates: by source
    if cand_by_source:
        out_md.append("### Candidates by source\n")
        for k, v in top_k(cand_by_source, 12):
            out_md.append(f"- **{k}**: {v} ({pct(v, total_cand)})")
        out_md.append("")

    # Candidates: by focus
    if cand_by_focus:
        out_md.append("### Candidates by focus (top)\n")
        for k, v in top_k(cand_by_focus, 12):
            out_md.append(f"- **{k}**: {v} ({pct(v, total_cand)})")
        out_md.append("")

    # Candidates: by domain
    if cand_by_domain:
        out_md.append("### Candidates by domain (top)\n")
        for k, v in top_domains:
            out_md.append(f"- `{k}`: {v} ({pct(v, total_cand)})")
        out_md.append("")

    # ---- Failures
    if top_failures:
        out_md.append("## Failure categories (top)\n")
        out_md.append("| Reason | Count |")
        out_md.append("|---|---:|")
        for reason, cnt in top_failures:
            out_md.append(f"| `{reason}` | {cnt} |")
        out_md.append("")

    examples = ingested_state.get("failures_examples") or {}
    if isinstance(examples, dict) and examples:
        out_md.append("## Failure examples (up to 2 per category)\n")
        for reason, exs in examples.items():
            if not exs:
                continue
            out_md.append(f"### `{reason}`\n")
            for ex in (exs or [])[:2]:
                if not isinstance(ex, dict):
                    continue
                url = ex.get("url", "")
                extra = {k: v for k, v in ex.items() if k != "url"}
                if extra:
                    out_md.append(f"- {url} — {json.dumps(extra, ensure_ascii=False)}")
                else:
                    out_md.append(f"- {url}")
            out_md.append("")

    # ---- Ingest distribution
    if ing:
        out_md.append("## Ingest distribution\n")

        if diversity_rows:
            out_md.append("### Source diversity KPIs (ingested)\n")
            out_md.append(md_table([(k, v) for k, v in diversity_rows if v not in ("", "None")]) + "\n")

        out_md.append("### By focus\n")
        total_ing = len(ing)
        for k, v in top_k(ing_by_focus, 12):
            out_md.append(f"- **{k}**: {v} ({pct(v, total_ing)})")

        out_md.append("\n### By domain\n")
        for k, v in top_k(ing_by_domain, 12):
            out_md.append(f"- `{k}`: {v} ({pct(v, total_ing)})")
        out_md.append("")

        # Spirulina terms overview
        if term_cnt:
            out_md.append("### Spirulina signal terms (top)\n")
            for k, v in top_k(term_cnt, 12):
                out_md.append(f"- `{k}`: {v}")
            out_md.append("")

    # ---- Quick interpretation hints (actionable)
    out_md.append("## Notes / signals\n")
    out_md.append("- Se **Top-5 domains share** > ~60–70%, la discovery sta concentrando troppo: conviene aumentare varietà query o pesare la diversity per-domain.")
    out_md.append("- Se **candidates_new_vs_seen** scende molto giorno su giorno, stai “ricercando gli stessi URL”: serve rotazione query, finestra temporale, o penalità per domini ricorrenti.")
    out_md.append("- Se `docs_ingested` è molto più basso di `candidates_found`, guarda `Failure categories` per capire il collo di bottiglia (fetch/parsing/pdf/robots/timeout).")
    out_md.append("")

    report_path = pathlib.Path(env("REPORT_PATH", artifact_path("report.md")))
    report_path.write_text("\n".join(out_md).strip() + "\n", encoding="utf-8")
    print(str(report_path))


if __name__ == "__main__":
    main()