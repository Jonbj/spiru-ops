"""pipelines/evaluate.py — Automated Quality Checks (QC) for spiru-ops
================================================================================

Functional purpose
------------------
This step decides whether a pipeline run is "good enough" to keep and schedule.
It prints metrics and returns:
- exit code 0: PASS
- exit code 2: FAIL

Why QC matters
--------------
A RAG Copilot is only as good as its evidence.
If the KB is noisy or dominated by irrelevant sources, the Copilot will either:
- hallucinate
- output many TBDs
- cite irrelevant microalgae content

QC is therefore designed to detect:
- too few candidates (discovery broken)
- too few indexed points (ingest/index broken)
- domain saturation (low diversity)
- too many paywalled/metadata-poor docs (missing publication info)
- Spirulina relevance too low

RUN_ID correctness
------------------
This file must not compute paths from "today" at runtime.
It should read the same run artifacts produced by discover/ingest/index/report.
Therefore it uses:
- CANDIDATES_PATH, INGESTED_PATH (exported by daily.sh)
or falls back to RUN_ID-based defaults.

Implementation notes
--------------------
- prefer_share/penal_share are computed by matching ingested domains against
  configs/domains.yaml. We do *not* rely on a 'domain_tier' field being present.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any, Dict, List, Optional, Tuple

from pipelines.common import env, run_id, state_path, load_domains

STATE_DIR = pathlib.Path(env("STATE_DIR", "storage/state"))
QDRANT_URL = env("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = env("QDRANT_COLLECTION", "docs_chunks")

# ---- QC thresholds (tunable via env) ----
MIN_CANDIDATES = int(env("QC_MIN_CANDIDATES", "200"))
MIN_INDEXED_POINTS = int(env("QC_MIN_INDEXED_POINTS", "1500"))

MAX_PENAL_SHARE = float(env("QC_MAX_PENAL_SHARE", "0.35"))
MAX_MISSING_PUB_SHARE = float(env("QC_MAX_MISSING_PUB_SHARE", "0.60"))

MIN_PREFER_SHARE = float(env("QC_MIN_PREFER_SHARE", "0.10"))
MAX_TOP5_DOMAIN_SHARE = float(env("QC_MAX_TOP5_DOMAIN_SHARE", "0.70"))
MIN_UNIQUE_DOMAINS = int(env("QC_MIN_UNIQUE_DOMAINS", "60"))
# When a run ingests few docs, an absolute unique-domains threshold is impossible.
# Use a dynamic floor: unique_domains >= min(MIN_UNIQUE_DOMAINS, ceil(n_ing * share))
MIN_UNIQUE_DOMAINS_SHARE = float(env("QC_MIN_UNIQUE_DOMAINS_SHARE", "0.55"))

MIN_SPIRULINA_SHARE = float(env("QC_MIN_SPIRULINA_SHARE", "0.35"))
MIN_AVG_SPIRULINA_SCORE = float(env("QC_MIN_AVG_SPIRULINA_SCORE", "0.28"))

_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


def _extract_year(s: Any) -> Optional[int]:
    if not isinstance(s, str):
        return None
    m = _YEAR_RE.search(s)
    if not m:
        return None
    try:
        y = int(m.group(1))
        if 1900 <= y <= 2100:
            return y
    except Exception:
        return None
    return None


def _domain(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _matches_any_suffix(domain: str, suffixes: List[str]) -> bool:
    d = (domain or "").lower()
    for s in suffixes or []:
        s2 = (s or "").lower().strip()
        if not s2:
            continue
        if d == s2 or d.endswith("." + s2) or d.endswith(s2):
            return True
    return False


def _qdrant_points_count(url: str, collection: str) -> int:
    import requests

    r = requests.get(f"{url}/collections/{collection}", timeout=10)
    r.raise_for_status()
    j = r.json()
    return int(j.get("result", {}).get("points_count", 0))


def _load_json(path: pathlib.Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _pct(x: float) -> str:
    return f"{100.0*x:.2f}%"


def main() -> None:
    rid = (env("RUN_ID", "") or "").strip() or run_id()

    # Resolve run-scoped artifact paths.
    cand_path = pathlib.Path(env("CANDIDATES_PATH", state_path("candidates.jsonl")))
    ing_path = pathlib.Path(env("INGESTED_PATH", state_path("ingested.json")))

    if not cand_path.exists():
        raise SystemExit(f"Missing candidates state: {cand_path}")
    if not ing_path.exists():
        raise SystemExit(f"Missing ingested state: {ing_path}")

    # Count candidates (JSONL)
    candidates = 0
    with open(cand_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                candidates += 1

    data = _load_json(ing_path)
    ingested: List[Dict[str, Any]] = data.get("ingested", [])

    # Per-run indexed points (from indexed.json written by index.py this run).
    # This is the correct signal: did THIS run index enough data?
    # (The cumulative Qdrant count would always pass on an established KB.)
    idx_path = pathlib.Path(env("INDEXED_PATH", state_path("indexed.json")))
    indexed_points = 0
    if idx_path.exists():
        try:
            idx_data = _load_json(idx_path)
            indexed_points = int(idx_data.get("points_upserted") or 0)
        except Exception:
            pass

    # Qdrant collection health (informational only)
    try:
        collection_points = _qdrant_points_count(QDRANT_URL, QDRANT_COLLECTION)
    except Exception as e:
        collection_points = 0
        print(f"[qc] WARN: could not reach Qdrant: {e}")

    # Domain distribution on ingested docs
    domains: List[str] = []
    for m in ingested:
        u = (m.get("url") or "").strip()
        if u:
            domains.append(_domain(u))

    from collections import Counter

    c = Counter([d for d in domains if d])
    unique_domains = len(set([d for d in domains if d]))
    top5 = c.most_common(5)
    top5_share = sum(v for _, v in top5) / max(len(domains), 1)

    # Prefer / penal shares from domain config
    dom_cfg = load_domains("configs/domains.yaml") or {}
    prefer_domains = dom_cfg.get("prefer_domains", []) or []
    penal_domains = dom_cfg.get("penalize_domains", []) or []

    prefer = sum(1 for d in domains if _matches_any_suffix(d, prefer_domains))
    penal = sum(1 for d in domains if _matches_any_suffix(d, penal_domains))

    prefer_share = prefer / max(len(ingested), 1)
    penal_share = penal / max(len(ingested), 1)

    # Missing publication info: publication_year OR year(published_at)
    missing_pub = 0
    for m in ingested:
        if m.get("publication_year"):
            continue
        y = _extract_year(m.get("published_at"))
        if y is None:
            missing_pub += 1
    missing_pub_share = missing_pub / max(len(ingested), 1)

    # Spirulina relevance
    scores = [float(m.get("spirulina_score") or 0.0) for m in ingested]
    spirulina_share = sum(1 for s in scores if s >= 0.50) / max(len(scores), 1)
    avg_spirulina_score = sum(scores) / max(len(scores), 1)

    print(f"[qc] run_id={rid}")
    print(f"[qc] candidates={candidates}")
    print(f"[qc] indexed_points(this_run)={indexed_points}")
    print(f"[qc] collection_points_total(qdrant)={collection_points}")
    print(f"[qc] prefer_share={_pct(prefer_share)} penal_share={_pct(penal_share)} missing_pub_share={_pct(missing_pub_share)}")
    print(f"[qc] top5_domain_share={_pct(top5_share)} unique_domains={unique_domains}")
    print(f"[qc] spirulina_share_ingested={_pct(spirulina_share)} avg_spirulina_score={avg_spirulina_score:.3f} (n_ing={len(ingested)})")

    print("[qc] top_domains:")
    for d, v in c.most_common(10):
        print(f"  - {d}: {v}")

    # Focus distribution
    foc = Counter([(m.get("focus") or "unknown") for m in ingested])
    print("[qc] focus_distribution(top 10):")
    for k, v in foc.most_common(10):
        print(f"  - {k}: {v}")

    # ---- Checks ----
    checks: List[Tuple[bool, str]] = []

    checks.append((candidates >= MIN_CANDIDATES, f"candidates>=min: {candidates} >= {MIN_CANDIDATES}"))
    checks.append((indexed_points >= MIN_INDEXED_POINTS, f"indexed_points>=min: {indexed_points} >= {MIN_INDEXED_POINTS}"))

    checks.append((penal_share <= MAX_PENAL_SHARE, f"penal_share<=max: {_pct(penal_share)} <= {_pct(MAX_PENAL_SHARE)}"))
    checks.append((missing_pub_share <= MAX_MISSING_PUB_SHARE, f"missing_pub<=max: {_pct(missing_pub_share)} <= {_pct(MAX_MISSING_PUB_SHARE)}"))
    checks.append((prefer_share >= MIN_PREFER_SHARE, f"prefer_share>=min: {_pct(prefer_share)} >= {_pct(MIN_PREFER_SHARE)}"))

    checks.append((top5_share <= MAX_TOP5_DOMAIN_SHARE, f"top5_domain_share<=max: {_pct(top5_share)} <= {_pct(MAX_TOP5_DOMAIN_SHARE)}"))
    # Dynamic unique-domains threshold (prevents impossible FAIL when n_ing is small)
    import math

    min_ud_dyn = min(MIN_UNIQUE_DOMAINS, int(math.ceil(len(ingested) * MIN_UNIQUE_DOMAINS_SHARE)))
    checks.append((unique_domains >= min_ud_dyn, f"unique_domains>=min: {unique_domains} >= {min_ud_dyn} (dyn, n_ing={len(ingested)} share={MIN_UNIQUE_DOMAINS_SHARE})"))

    checks.append((spirulina_share >= MIN_SPIRULINA_SHARE, f"spirulina_share_ingested>=min: {_pct(spirulina_share)} >= {_pct(MIN_SPIRULINA_SHARE)}"))
    checks.append((avg_spirulina_score >= MIN_AVG_SPIRULINA_SCORE, f"avg_spirulina_score>=min: {avg_spirulina_score:.3f} >= {MIN_AVG_SPIRULINA_SCORE:.3f}"))

    print("[qc] checks:")
    ok_all = True
    for ok, msg in checks:
        print(f"  - {'PASS' if ok else 'FAIL'} {msg}")
        if not ok:
            ok_all = False

    if ok_all:
        print("[qc] ✅ QUALITY PASS")
        raise SystemExit(0)

    print("[qc] ❌ QUALITY FAIL")
    raise SystemExit(2)


if __name__ == "__main__":
    main()
