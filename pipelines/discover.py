"""pipelines/discover.py — Candidate discovery for the KB build
================================================================================

Functional purpose
------------------
This step produces a *candidate list* of URLs to ingest.

- It queries:
  - Brave Search API (web results)
  - OpenAlex (papers/works)
- It applies heuristic scoring to prioritize:
  - Spirulina/Arthrospira relevance
  - Prefer domains (configs/domains.yaml)
  - PDFs / open-access where possible
- It de-duplicates and writes one JSONL record per candidate.

Outputs
-------
Writes JSONL to `CANDIDATES_PATH` (preferred) or to the default
`storage/state/<RUN_ID>_candidates.jsonl`.

Why RUN_ID matters
------------------
The pipeline can run multiple times per day and can cross midnight.
All steps must agree on the same candidates list for a run.
Therefore discover MUST write to a RUN_ID-scoped file.

AI/tool-friendly notes
----------------------
This file contains explicit sections and verbose comments because discovery is a
high-leverage place to improve KB quality (domain diversity, Spirulina-centricity).
"""

import json
import pathlib
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from pipelines.common import (
    env,
    day_stamp_utc,
    run_id,
    state_path,
    utc_now_iso,
    safe_id,
    normalize_url,
    load_yaml,
    load_domains,
)

from pipelines.relevance import compute_spirulina_relevance

USER_AGENT = env("USER_AGENT", "spiru-ops-bot/0.4")
BRAVE_API_KEY = env("BRAVE_API_KEY", required=False)
OPENALEX_EMAIL = env("OPENALEX_EMAIL", required=False)
CORE_API_KEY = env("CORE_API_KEY", required=False)
# Seconds to sleep between CORE requests (free tier ~10-20 req/min → 3s ≈ 20 req/min)
CORE_SLEEP_S = float(env("CORE_SLEEP_S", "3.0"))
# After this many unrecovered 429s, CORE is disabled for the rest of the run
CORE_MAX_429 = int(env("CORE_MAX_429", "3"))

# Mutable run-level state for CORE rate-limit tracking (dict avoids `global`)
_core_state: dict = {"streak_429": 0, "disabled": False}

STATE_DIR = pathlib.Path(env("STATE_DIR", "storage/state"))
# Output path is RUN_ID-scoped for determinism; daily.sh exports CANDIDATES_PATH
OUT_PATH = pathlib.Path(env("CANDIDATES_PATH", state_path("candidates.jsonl")))

SCORING_PATH = env("SCORING_CONFIG", "configs/scoring.yaml")

# De-saturation controls
DENY_RESEARCHGATE = env("DENY_RESEARCHGATE", "1").strip() in ("1", "true", "TRUE", "yes", "YES")
RESOLVE_DOI_REDIRECTS = env("RESOLVE_DOI_REDIRECTS", "1").strip() in ("1", "true", "TRUE", "yes", "YES")
RESOLVE_DOI_TIMEOUT_S = int(env("RESOLVE_DOI_TIMEOUT_S", "8"))

# Prefer OA/pdf URLs from OpenAlex to reduce paywall/403 problems
OPENALEX_OA_FIRST = env("OPENALEX_OA_FIRST", "1").strip() in ("1", "true", "TRUE", "yes", "YES")

DOI_DOMAINS = {"doi.org", "dx.doi.org"}
RESEARCHGATE_DOMAINS = {"researchgate.net", "www.researchgate.net"}

_SESSION = requests.Session()


def domain_of(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def looks_like_pdf_url(url: str) -> bool:
    u = (url or "").lower()
    if u.endswith(".pdf"):
        return True
    if "pdf" in u and any(x in u for x in ["/pdf", "download=1", "format=pdf", "type=pdf", "content.pdf"]):
        return True
    return False


def matches_any_suffix(domain: str, suffixes: List[str]) -> bool:
    d = (domain or "").lower()
    for s in suffixes or []:
        s = (s or "").lower().strip()
        if not s:
            continue
        if s.startswith("."):
            if d.endswith(s):
                return True
        else:
            if d == s or d.endswith("." + s):
                return True
    return False


def is_denied_domain(url: str, dom_cfg: Dict[str, Any]) -> bool:
    d = domain_of(url)
    deny = dom_cfg.get("deny_domains", []) or []
    return matches_any_suffix(d, deny)


def resolve_final_url(url: str) -> str:
    """
    Resolve redirects (esp. doi.org) to reduce doi.org saturation and improve parse quality.
    Keep it cheap: HEAD first, fallback to GET (streamed).
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    try:
        r = _SESSION.head(url, allow_redirects=True, timeout=RESOLVE_DOI_TIMEOUT_S, headers=headers)
        if r.status_code >= 200 and r.url:
            return r.url
    except Exception:
        pass
    try:
        r = _SESSION.get(url, allow_redirects=True, timeout=RESOLVE_DOI_TIMEOUT_S, stream=True, headers=headers)
        if r.status_code >= 200 and r.url:
            return r.url
    except Exception:
        pass
    return url


def brave_search(query: str, count: int = 10) -> List[Dict[str, Any]]:
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


def openalex_search(query: str, per_page: int = 15) -> List[Dict[str, Any]]:
    url = "https://api.openalex.org/works"
    params = {"search": query, "per-page": str(per_page)}
    if OPENALEX_EMAIL:
        params["mailto"] = OPENALEX_EMAIL
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data.get("results") or []


def core_search(query: str, limit: int = 15) -> List[Dict[str, Any]]:
    """Search CORE.ac.uk for open-access papers.

    CORE is the largest EU open-access aggregator (200M+ docs). Particularly
    useful for European/Italian institutional papers not well covered by OpenAlex.
    Register for a free API key at: https://core.ac.uk/services/api
    """
    if not CORE_API_KEY:
        return []
    url = "https://api.core.ac.uk/v3/search/works"
    headers = {
        "Authorization": f"Bearer {CORE_API_KEY}",
        "User-Agent": USER_AGENT,
    }
    params = {"q": query, "limit": str(limit)}
    r = requests.get(url, headers=headers, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    return data.get("results") or []


def _core_best_url(work: Dict[str, Any]) -> str:
    """Pick best URL from a CORE result: prefer downloadUrl (direct PDF), then fulltext."""
    download = (work.get("downloadUrl") or "").strip()
    if download:
        return download
    fulltext = work.get("sourceFulltextUrls") or []
    if isinstance(fulltext, list) and fulltext:
        return (fulltext[0] or "").strip()
    doi = (work.get("doi") or "").strip()
    if doi:
        return "https://doi.org/" + doi.lstrip("/")
    return ""


def load_scoring(path: str) -> Dict[str, Any]:
    cfg = load_yaml(path) or {}
    return cfg if isinstance(cfg, dict) else {}


def _extract_year_best_effort(*texts: str) -> Optional[str]:
    blob = " ".join([t for t in texts if t]).strip()
    if not blob:
        return None
    m = re.search(r"\b(19[89]\d|20[0-2]\d|203[0-5])\b", blob)
    if not m:
        return None
    y = m.group(1)
    return f"{y}-01-01"


def _parse_date_any(x: Any) -> Optional[str]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        try:
            v = float(x)
            if v > 10_000_000_000:
                v = v / 1000.0
            dt = datetime.fromtimestamp(v, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return None
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        s2 = s.replace("/", "-")
        if re.match(r"^\d{4}-\d{2}-\d{2}", s2):
            return s2[:10]
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.date().isoformat()
        except Exception:
            return None
    return None


def mk_candidate(
    *,
    url: str,
    title: str,
    snippet: str,
    focus: str,
    source: str,
    score: float,
    published_at: Optional[str] = None,
    is_pdf: Optional[bool] = None,
    doi: Optional[str] = None,
) -> Dict[str, Any]:
    url = normalize_url(url)
    return {
        "id": safe_id(f"cand|{source}|{focus}|{url}"),
        "focus": focus,
        "source": source,
        "url": url,
        "title": (title or "").strip() or url,
        "snippet": (snippet or "").strip(),
        "published_at": published_at,
        "doi": doi,
        "score": float(score or 0),
        "spirulina_hint": None,
        "is_pdf": bool(is_pdf) if is_pdf is not None else looks_like_pdf_url(url),
        "discovered_at": utc_now_iso(),
    }


_SPIRU_QUERY_SUFFIX = " (Spirulina OR Arthrospira OR Limnospira)"


def ensure_spirulina_in_query(q: str) -> str:
    qn = (q or "").lower()
    if any(t in qn for t in ("spirulina", "arthrospira", "limnospira")):
        return q
    return (q or "").strip() + _SPIRU_QUERY_SUFFIX


def add_temporal_rotation(q: str, run_date: Optional[datetime] = None) -> str:
    """
    Add dynamic temporal filter to Brave queries for rotation across runs.
    Rotates between 30/60/90/120 day windows based on date ordinal.
    """
    if run_date is None:
        run_date = datetime.now(timezone.utc)
    
    # Rotate window: 30/60/90/120 days based on day modulo 4
    days_back_options = [30, 60, 90, 120]
    idx = run_date.toordinal() % len(days_back_options)
    days_back = days_back_options[idx]
    
    from datetime import timedelta
    cutoff = run_date - timedelta(days=days_back)
    after_clause = f' after:{cutoff.strftime("%Y-%m-%d")}'
    
    return (q or "").strip() + after_clause


FOCUS_STRICT_TERMS: Dict[str, List[str]] = {
    "cosmetic_market_entry_barriers": ["spirulina", "arthrospira", "limnospira"],
    "certifications_protocols_food_cosmetic": ["spirulina", "arthrospira", "limnospira"],
    "public_grants_funding_agrifood_algae": ["spirulina", "arthrospira", "microalgae"],
    "water_treatment_well_mains": ["spirulina", "arthrospira", "limnospira"],
    "sales_channels_italy_b2b_b2c": ["spirulina", "arthrospira"],
    "marketing_branding_consumer_perception": ["spirulina", "arthrospira"],
    "diy_home_cultivation_kits": ["spirulina", "arthrospira"],
}


def focus_gate_adjustment(*, focus: str, url: str, title: str, snippet: str, hint: float) -> float:
    """Apply extra penalty to noisy focuses when they lack focus-specific anchors.

    This is a soft guardrail: candidates are not dropped, but strongly de-ranked if
    they come from historically noisy focuses and don't mention enough target terms.
    """
    strict_terms = FOCUS_STRICT_TERMS.get((focus or "").strip())
    if not strict_terms:
        return 0.0

    blob = f"{url} {title} {snippet}".lower()
    matches = sum(1 for t in strict_terms if t in blob)

    if hint >= 0.55:
        return 0.0
    if matches >= 2:
        return 0.0
    if matches == 1:
        return -8.0
    return -18.0


def enrich_score(url: str, base_score: float, dom_cfg: Dict[str, Any], *, is_doi: bool = False) -> float:
    d = domain_of(url)
    score = float(base_score or 0)

    prefer = dom_cfg.get("prefer_domains", []) or []
    if matches_any_suffix(d, prefer):
        score += 5.0

    penalize = dom_cfg.get("penalize_domains", []) or []
    if matches_any_suffix(d, penalize):
        score -= 8.0

    if looks_like_pdf_url(url):
        score += 4.0

    pdf_bonus = dom_cfg.get("pdf_bonus_domains", []) or []
    if matches_any_suffix(d, pdf_bonus):
        score += 2.0

    if is_doi:
        score += 3.0

    # De-saturate doi.org and researchgate explicitly
    if d in DOI_DOMAINS:
        score -= 2.5
    if d in RESEARCHGATE_DOMAINS:
        score -= 4.0

    # Penalize common 403-prone hosts a bit (soft, not deny)
    if d.endswith("onlinelibrary.wiley.com"):
        score -= 2.0
    if d.endswith("medium.com"):
        score -= 2.0

    return score


def dedup(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for r in rows:
        u = normalize_url(r.get("url", ""))
        if not u or u in seen:
            continue
        seen.add(u)
        r["url"] = u
        out.append(r)
    return out


def load_seen_doi(path: str = "storage/state/seen_doi.jsonl") -> set[str]:
    """Load globally seen DOIs (written by ingest.py) to reduce rediscovery."""
    p = pathlib.Path(path)
    out: set[str] = set()
    if not p.exists():
        return out
    try:
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                d = (obj.get("doi") or "").strip().lower()
                if d:
                    out.add(d)
            except Exception:
                continue
    except Exception:
        return out
    return out


def brave_published_at(result: Dict[str, Any], title: str, snippet: str) -> Optional[str]:
    for k in ("published", "published_at", "date", "page_age", "age", "created", "timestamp"):
        if k in result:
            d = _parse_date_any(result.get(k))
            if d:
                return d
    return _extract_year_best_effort(title, snippet)


def _openalex_best_url(work: Dict[str, Any]) -> str:
    """
    Prefer open access URL/pdf URL when possible to avoid paywall 403.
    """
    if not OPENALEX_OA_FIRST:
        loc = work.get("primary_location") or {}
        return (loc.get("landing_page_url") or "").strip()

    oa = work.get("open_access") or {}
    oa_url = (oa.get("oa_url") or "").strip()
    if oa_url:
        return oa_url

    loc = work.get("primary_location") or {}
    pdf_url = (loc.get("pdf_url") or "").strip()
    if pdf_url:
        return pdf_url

    landing = (loc.get("landing_page_url") or "").strip()
    if landing:
        return landing

    return ""


def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    scoring = load_scoring(SCORING_PATH)
    dom_cfg = load_domains("configs/domains.yaml")

    # Novelty-aware: avoid re-discovering already ingested DOIs
    seen_doi = load_seen_doi(str(STATE_DIR / "seen_doi.jsonl"))

    focuses = scoring.get("focuses") or []
    if not isinstance(focuses, list) or not focuses:
        raise SystemExit(f"No focuses found in {SCORING_PATH}. Expected key 'focuses' list.")

    candidates: List[Dict[str, Any]] = []

    for f in focuses:
        focus = f.get("name") or "unknown"
        queries = f.get("queries") or []
        base_score = float(f.get("base_score") or 10)

        for q in queries:
            q = (q or "").strip()
            if not q:
                continue
            q = ensure_spirulina_in_query(q)

            # 1) Brave
            if BRAVE_API_KEY:
                try:
                    # Apply temporal rotation for query diversity
                    q_rotated = add_temporal_rotation(q)
                    results = brave_search(q_rotated, count=20)
                    for r in results:
                        url = r.get("url") or ""
                        if not url:
                            continue
                        url = normalize_url(url)
                        if not url:
                            continue

                        d = domain_of(url)
                        if DENY_RESEARCHGATE and d in RESEARCHGATE_DOMAINS:
                            continue
                        if is_denied_domain(url, dom_cfg):
                            continue

                        if RESOLVE_DOI_REDIRECTS and d in DOI_DOMAINS:
                            resolved = resolve_final_url(url)
                            resolved_n = normalize_url(resolved)
                            if resolved_n:
                                url = resolved_n

                        title = (r.get("title") or "").strip()
                        snippet = (r.get("description") or "").strip()
                        pub = brave_published_at(r, title, snippet)

                        hint = compute_spirulina_relevance(url=url, title=title, text=snippet).score
                        score = enrich_score(url, base_score, dom_cfg) + (8.0 * float(hint))
                        score += focus_gate_adjustment(focus=focus, url=url, title=title, snippet=snippet, hint=float(hint))

                        c = mk_candidate(
                            url=url,
                            title=title,
                            snippet=snippet,
                            focus=focus,
                            source="brave",
                            score=score,
                            published_at=pub,
                            is_pdf=looks_like_pdf_url(url),
                        )
                        c["spirulina_hint"] = round(float(hint), 4)
                        candidates.append(c)
                    time.sleep(0.35)
                except Exception as e:
                    print(f"[discover] WARN: brave search failed for query '{q[:60]}': {e}", flush=True)

            # 2) OpenAlex
            try:
                works = openalex_search(q, per_page=15)
                for w in works:
                    doi = (w.get("doi") or "").strip()
                    doi_norm: Optional[str] = None
                    if doi:
                        doi_norm = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
                        doi_norm = doi_norm or None

                    url = _openalex_best_url(w)

                    is_doi = False
                    if not url and doi_norm:
                        url = "https://doi.org/" + doi_norm
                        is_doi = True

                    url = normalize_url(url)
                    if not url:
                        continue

                    d = domain_of(url)
                    if DENY_RESEARCHGATE and d in RESEARCHGATE_DOMAINS:
                        continue
                    if is_denied_domain(url, dom_cfg):
                        continue

                    if RESOLVE_DOI_REDIRECTS and d in DOI_DOMAINS:
                        resolved = resolve_final_url(url)
                        resolved_n = normalize_url(resolved)
                        if resolved_n:
                            url = resolved_n

                    title = (w.get("display_name") or "").strip() or url
                    pub = _parse_date_any(w.get("publication_date")) or w.get("publication_date")

                    snippet = ""
                    try:
                        host = (w.get("host_venue") or {}).get("display_name")
                        if host:
                            snippet = f"Venue: {host}"
                    except Exception:
                        snippet = ""

                    # if DOI already seen globally, down-rank this work only (don't mutate base_score)
                    work_score = base_score
                    if doi_norm and doi_norm.lower() in seen_doi:
                        work_score = max(0.0, work_score - 25.0)

                    hint = compute_spirulina_relevance(url=url, title=title, text=snippet).score
                    score = enrich_score(url, work_score, dom_cfg, is_doi=is_doi) + (8.0 * float(hint))
                    score += focus_gate_adjustment(focus=focus, url=url, title=title, snippet=snippet, hint=float(hint))

                    c = mk_candidate(
                        url=url,
                        title=title,
                        snippet=snippet,
                        focus=focus,
                        source="openalex",
                        score=score,
                        published_at=pub,
                        is_pdf=looks_like_pdf_url(url),
                        doi=doi_norm,
                    )
                    c["spirulina_hint"] = round(float(hint), 4)
                    candidates.append(c)
            except Exception as e:
                print(f"[discover] WARN: openalex search failed for query '{q[:60]}': {e}", flush=True)

            # 3) CORE
            if CORE_API_KEY and not _core_state["disabled"]:
                time.sleep(CORE_SLEEP_S)
                try:
                    works = core_search(q, limit=15)
                    _core_state["streak_429"] = 0  # reset streak on success
                    for w in works:
                        doi = (w.get("doi") or "").strip()
                        doi_norm: Optional[str] = None
                        if doi:
                            doi_norm = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip() or None
                            if doi_norm and doi_norm.lower() in seen_doi:
                                continue  # already ingested

                        url = _core_best_url(w)
                        url = normalize_url(url)
                        if not url:
                            continue

                        d = domain_of(url)
                        if DENY_RESEARCHGATE and d in RESEARCHGATE_DOMAINS:
                            continue
                        if is_denied_domain(url, dom_cfg):
                            continue

                        if RESOLVE_DOI_REDIRECTS and d in DOI_DOMAINS:
                            resolved = resolve_final_url(url)
                            resolved_n = normalize_url(resolved)
                            if resolved_n:
                                url = resolved_n

                        title = (w.get("title") or "").strip() or url
                        year = w.get("yearPublished") or w.get("year")
                        pub = f"{year}-01-01" if year else None
                        snippet = (w.get("abstract") or "")[:400]

                        hint = compute_spirulina_relevance(url=url, title=title, text=snippet).score
                        score = enrich_score(url, base_score, dom_cfg, is_doi=bool(doi_norm)) + (8.0 * float(hint))
                        score += focus_gate_adjustment(focus=focus, url=url, title=title, snippet=snippet, hint=float(hint))

                        c = mk_candidate(
                            url=url,
                            title=title,
                            snippet=snippet,
                            focus=focus,
                            source="core",
                            score=score,
                            published_at=pub,
                            is_pdf=looks_like_pdf_url(url),
                            doi=doi_norm,
                        )
                        c["spirulina_hint"] = round(float(hint), 4)
                        candidates.append(c)
                except requests.HTTPError as e:
                    if e.response is not None and e.response.status_code == 429:
                        # Respect Retry-After header and retry once before counting as failure
                        retry_after = 0
                        try:
                            retry_after = int(e.response.headers.get("Retry-After", 0))
                        except (ValueError, TypeError):
                            retry_after = 0
                        if 0 < retry_after <= 120:
                            print(
                                f"[discover] WARN: CORE 429 — sleeping {retry_after}s (Retry-After) then retrying '{q[:60]}'",
                                flush=True,
                            )
                            time.sleep(retry_after)
                            try:
                                works2 = core_search(q, limit=15)
                                _core_state["streak_429"] = 0
                                for w in works2:
                                    _doi = (w.get("doi") or "").strip()
                                    _doi_norm: Optional[str] = None
                                    if _doi:
                                        _doi_norm = _doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip() or None
                                        if _doi_norm and _doi_norm.lower() in seen_doi:
                                            continue
                                    _url = normalize_url(_core_best_url(w))
                                    if not _url or is_denied_domain(_url, dom_cfg):
                                        continue
                                    _title = (w.get("title") or "").strip() or _url
                                    _year = w.get("yearPublished") or w.get("year")
                                    _snippet = (w.get("abstract") or "")[:400]
                                    _hint = compute_spirulina_relevance(url=_url, title=_title, text=_snippet).score
                                    _score = enrich_score(_url, base_score, dom_cfg, is_doi=bool(_doi_norm)) + (8.0 * float(_hint))
                                    _score += focus_gate_adjustment(focus=focus, url=_url, title=_title, snippet=_snippet, hint=float(_hint))
                                    _c = mk_candidate(url=_url, title=_title, snippet=_snippet, focus=focus, source="core",
                                                      score=_score, published_at=f"{_year}-01-01" if _year else None,
                                                      is_pdf=looks_like_pdf_url(_url), doi=_doi_norm)
                                    _c["spirulina_hint"] = round(float(_hint), 4)
                                    candidates.append(_c)
                                time.sleep(CORE_SLEEP_S)
                            except Exception:
                                _core_state["streak_429"] += 1
                        else:
                            _core_state["streak_429"] += 1
                            print(
                                f"[discover] WARN: CORE 429 #{_core_state['streak_429']}/{CORE_MAX_429} for query '{q[:60]}'",
                                flush=True,
                            )
                        if _core_state["streak_429"] >= CORE_MAX_429:
                            _core_state["disabled"] = True
                            print(
                                f"[discover] WARN: CORE API disabled after {_core_state['streak_429']} unrecovered 429s — skipping for this run",
                                flush=True,
                            )
                    else:
                        print(f"[discover] WARN: core search failed for query '{q[:60]}': {e}", flush=True)
                except Exception as e:
                    print(f"[discover] WARN: core search failed for query '{q[:60]}': {e}", flush=True)

    candidates = dedup(candidates)

    # Domain saturation penalty (soft)
    # Deduplicate by URL keeping the candidate with the highest score.
    # When the same URL is found by queries from different focus areas, this
    # ensures it gets assigned to the most relevant focus (highest scoring query)
    # rather than whichever focus happened to find it first.
    url_best: Dict[str, Dict[str, Any]] = {}
    for c in candidates:
        u = c.get("url") or ""
        if not u:
            continue
        existing = url_best.get(u)
        if existing is None or float(c.get("score") or 0) > float(existing.get("score") or 0):
            url_best[u] = c
    candidates = list(url_best.values())

    dom_counts: Dict[str, int] = {}
    for c in candidates:
        d = domain_of(c.get("url") or "")
        if d:
            dom_counts[d] = dom_counts.get(d, 0) + 1
    for c in candidates:
        d = domain_of(c.get("url") or "")
        n = dom_counts.get(d, 0)
        if n >= 25:
            c["score"] = float(c.get("score") or 0) - (1.2 * (n ** 0.5))

    # Hard diversify: cap per-domain and round-robin to improve unique_domains + top5 share
    MAX_CAND_PER_DOMAIN = int(env("DISCOVER_MAX_CAND_PER_DOMAIN", "12"))

    candidates.sort(key=lambda x: float(x.get("score") or 0), reverse=True)

    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for c in candidates:
        d = domain_of(c.get("url") or "") or "unknown"
        buckets.setdefault(d, []).append(c)

    # Round-robin pick
    diversified: List[Dict[str, Any]] = []
    per_dom: Dict[str, int] = {k: 0 for k in buckets.keys()}
    more = True
    i = 0
    keys = list(buckets.keys())
    while more:
        more = False
        for d in keys:
            if per_dom.get(d, 0) >= MAX_CAND_PER_DOMAIN:
                continue
            if i < len(buckets.get(d, [])):
                diversified.append(buckets[d][i])
                per_dom[d] = per_dom.get(d, 0) + 1
                more = True
        i += 1

    candidates = diversified

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        for c in candidates:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(str(OUT_PATH))
    print(f"candidates: {len(candidates)}")
    if not BRAVE_API_KEY:
        print("Note: BRAVE_API_KEY not set, only OpenAlex candidates were emitted.")


if __name__ == "__main__":
    main()