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
    m = re.search(r"\b(19[9]\d|20[0-2]\d|203[0-5])\b", blob)
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
            dt = datetime.utcfromtimestamp(v)
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
                        # if DOI already seen globally, down-rank heavily
                        if doi_norm and doi_norm.lower() in seen_doi:
                            base_score = max(0.0, base_score - 25.0)

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

                    hint = compute_spirulina_relevance(url=url, title=title, text=snippet).score
                    score = enrich_score(url, base_score, dom_cfg, is_doi=is_doi) + (8.0 * float(hint))

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
            if CORE_API_KEY:
                try:
                    works = core_search(q, limit=15)
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
                    time.sleep(0.5)
                except Exception as e:
                    print(f"[discover] WARN: core search failed for query '{q[:60]}': {e}", flush=True)

    candidates = dedup(candidates)

    # Domain saturation penalty (soft)
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