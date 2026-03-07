"""pipelines/ingest.py — Fetch + parse candidates into KB-ready text
================================================================================

Functional purpose
------------------
Given a list of candidate URLs (from discover), this step:

1) Downloads the content (HTML or PDF) into `storage/raw/`.
2) Extracts readable text into `storage/parsed/*.txt`.
3) Writes per-document metadata to `storage/parsed/*.meta.json`.
4) Writes a run summary JSON to `INGESTED_PATH`.

Why this is the "risk center"
------------------------------
Ingest is where most production failures happen:
- paywalls / 403 / 429
- timeouts
- non-PDF files masquerading as PDF
- huge PDFs causing Unstructured to OOM
- corrupted PDFs causing parser exceptions

Therefore the code is designed with:
- conservative download limits
- retries/backoff
- circuit-breaker style domain cooldowns
- fallbacks (Unstructured -> local PDF parser)

RUN_ID and artifact naming
--------------------------
This module must read candidates from `CANDIDATES_PATH` and write its summary to
`INGESTED_PATH`.
Those env vars are exported by daily.sh using the stable RUN_ID.

AI/tool-friendly notes
----------------------
This file contains verbose comments because it is high-leverage and complex.
"""

# pipelines/ingest.py
#
# Full file (copy/paste safe).
# Changes vs previous:
# - Extract DOI from HTML/PDF text when missing
# - Use OpenAlex to fill publication_year when DOI exists
# - OpenAlex fallback on 403/429 when DOI exists (download alternate OA/PDF/landing)
# - Telemetry: openalex_fallback + domain_403_top/domain_429_top in state JSON

import os
import json
import pathlib
import random
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import requests
from tqdm import tqdm

from pipelines.common import (
    env,
    day_stamp_utc,
    run_id,
    state_path,
    utc_now_iso,
    normalize_url,
    load_domains,
    denied,
    soup_text,
    clean_text_with_stats,
)
from pipelines.relevance import compute_spirulina_relevance

USER_AGENT = env("USER_AGENT", "spiru-ops-bot/0.6")
OPENALEX_EMAIL = env("OPENALEX_EMAIL", required=False)

UNSTRUCTURED_URL = env("UNSTRUCTURED_URL", required=True)
UNSTRUCTURED_ENABLE = env("UNSTRUCTURED_ENABLE", default="1").strip().lower() in ("1", "true", "yes")
UNSTRUCTURED_MAX_MB = int(env("UNSTRUCTURED_MAX_MB", 25))

GROBID_URL = env("GROBID_URL", default="http://localhost:8070", required=False)
GROBID_ENABLE = env("GROBID_ENABLE", default="0").strip().lower() in ("1", "true", "yes")
GROBID_FULLTEXT = env("GROBID_FULLTEXT", default="0").strip().lower() in ("1", "true", "yes")

RAW_DIR = pathlib.Path(env("RAW_DIR", "storage/raw"))
PARSED_DIR = pathlib.Path(env("PARSED_DIR", "storage/parsed"))
STATE_DIR = pathlib.Path(env("STATE_DIR", "storage/state"))

MAX_DOWNLOAD_MB = int(env("MAX_DOWNLOAD_MB", 50))
MAX_DOWNLOAD_BYTES = MAX_DOWNLOAD_MB * 1024 * 1024

PDF_REQUEST_TIMEOUT_S = int(env("PDF_REQUEST_TIMEOUT_S", 90))
HTML_REQUEST_TIMEOUT_S = int(env("HTML_REQUEST_TIMEOUT_S", 40))
HEAD_TIMEOUT_S = int(env("HEAD_TIMEOUT_S", 20))

MAX_403_PER_DOMAIN = int(env("MAX_403_PER_DOMAIN", 5))
MAX_429_PER_DOMAIN = int(env("MAX_429_PER_DOMAIN", 3))

SPIRULINA_MIN_TEXT_CHARS = int(env("SPIRULINA_MIN_TEXT_CHARS", 600))

CURRENT_YEAR = int(env("CURRENT_YEAR", str(time.gmtime().tm_year)))
MIN_YEAR = int(env("MIN_YEAR", "1980"))

PAPER_URL_HINTS = [
    "doi.org",
    "mdpi.com",
    "frontiersin.org",
    "springer",
    "elsevier",
    "sciencedirect",
    "wiley",
    "tandfonline",
    "nature.com",
    "sagepub",
    "ieee",
    "acm.org",
    "oup.com",
    "cell.com",
    "acs.org",
]

_SESSION = requests.Session()

# --- DOI extraction regex (robust) ---
# Matches typical Crossref DOI patterns; we normalize later.
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)\b", re.IGNORECASE)
_TRAIL_PUNCT = ".,;:)]}>\"'"

# --- Year regex ---
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


def _domain(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _domain_family(dom: str) -> str:
    """Normalize technical subdomains to a 'publisher family' to improve diversity.

    This is intentionally conservative (a few high-impact mappings).
    """
    d = (dom or "").lower().strip()
    if d.startswith("www."):
        d = d[4:]

    # Frontiers PDFs often come from a CDN-like subdomain
    if d.endswith(".frontiersin.org"):
        return "frontiersin.org"

    # Elsevier PDF CDN vs ScienceDirect landing
    if d.endswith(".els-cdn.com"):
        return "sciencedirect.com"

    # PubMed / PMC are both NCBI
    if d in ("pubmed.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov"):
        return "ncbi.nlm.nih.gov"

    return d


def _looks_like_pdf_url(url: str) -> bool:
    u = (url or "").lower()
    if u.endswith(".pdf"):
        return True
    if any(x in u for x in ("/pdf", "download=1", "format=pdf", "type=pdf", "content.pdf", "pdfdirect")):
        return True
    return False


def _base_headers(url: str) -> Dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.8,*/*;q=0.7",
        "Accept-Language": "en-US,en;q=0.9,it-IT;q=0.7,it;q=0.6",
        "Connection": "keep-alive",
        "Referer": "https://scholar.google.com/",
    }


def safe_filename(url: str) -> str:
    import hashlib
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    p = urlparse(url)
    base = re.sub(r"[^a-zA-Z0-9._-]+", "_", (p.netloc + p.path).strip("_"))
    if not base:
        base = "doc"
    return f"{base[:140]}__{h}"


def http_head(url: str) -> requests.Response:
    return _SESSION.head(url, headers=_base_headers(url), timeout=HEAD_TIMEOUT_S, allow_redirects=True)


def _request_timeout_for(url: str, hinted_pdf: bool) -> int:
    if hinted_pdf or _looks_like_pdf_url(url):
        return PDF_REQUEST_TIMEOUT_S
    return HTML_REQUEST_TIMEOUT_S


def http_get_stream(url: str, *, timeout_s: int) -> requests.Response:
    r = _SESSION.get(url, headers=_base_headers(url), timeout=timeout_s, allow_redirects=True, stream=True)
    r.raise_for_status()
    return r


def _looks_like_pdf_bytes(prefix: bytes) -> bool:
    return prefix.startswith(b"%PDF")


def download_stream_to_file(
    url: str,
    out_path: pathlib.Path,
    max_bytes: int,
    *,
    timeout_s: int,
) -> Tuple[str, int, str, bytes, str]:
    import hashlib
    r = http_get_stream(url, timeout_s=timeout_s)
    h = hashlib.sha256()
    n = 0
    prefix = b""
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=256 * 1024):
            if not chunk:
                continue
            if not prefix:
                prefix = chunk[:8]
            n += len(chunk)
            if n > max_bytes:
                raise RuntimeError("download_exceeded_max_bytes")
            h.update(chunk)
            f.write(chunk)
    return r.url, n, h.hexdigest(), prefix, (r.headers.get("content-type") or "")


def get_content_length(url: str) -> Optional[int]:
    try:
        r = http_head(url)
        if r.status_code >= 400:
            return None
        cl = r.headers.get("content-length")
        if cl and cl.isdigit():
            return int(cl)
    except Exception:
        return None
    return None


def unstructured_partition(file_path: str) -> str:
    with open(file_path, "rb") as f:
        files = {"files": (os.path.basename(file_path), f)}
        r = requests.post(f"{UNSTRUCTURED_URL}/general/v0/general", files=files, timeout=240)
    r.raise_for_status()
    data = r.json()
    texts: List[str] = []
    for el in data:
        t = (el.get("text") or "").strip()
        if t:
            texts.append(t)
    return "\n\n".join(texts)


def fallback_pdf_extract_text(pdf_path: str) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as e:
        raise RuntimeError("pypdf_not_installed") from e

    reader = PdfReader(pdf_path)
    parts: List[str] = []
    for page in reader.pages[:200]:
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        t = (t or "").strip()
        if t:
            parts.append(t)
    return "\n\n".join(parts)


def parse_pdf_with_fallback(pdf_path: pathlib.Path, *, content_length: int) -> Tuple[str, str]:
    if content_length > (UNSTRUCTURED_MAX_MB * 1024 * 1024):
        try:
            return fallback_pdf_extract_text(str(pdf_path)), "pypdf"
        except Exception:
            return "", "none"

    if UNSTRUCTURED_ENABLE:
        for i in range(3):
            try:
                return unstructured_partition(str(pdf_path)), "unstructured"
            except requests.exceptions.Timeout:
                time.sleep(0.8 * (2**i))
                continue
            except Exception:
                time.sleep(0.8 * (2**i))
                continue

    try:
        return fallback_pdf_extract_text(str(pdf_path)), "pypdf"
    except Exception:
        return "", "none"


def looks_like_paper_url(url: str) -> bool:
    u = (url or "").lower()
    return any(x in u for x in PAPER_URL_HINTS)


def grobid_process(endpoint: str, pdf_path: str, timeout: int) -> str:
    if not GROBID_URL:
        raise RuntimeError("GROBID_URL not set")
    with open(pdf_path, "rb") as f:
        files = {"input": (os.path.basename(pdf_path), f, "application/pdf")}
        r = requests.post(f"{GROBID_URL}/api/{endpoint}", files=files, timeout=timeout)
    r.raise_for_status()
    return r.text


def extract_doi_from_tei(tei_xml: str) -> Optional[str]:
    m = re.search(r'<idno[^>]*type="DOI"[^>]*>\s*([^<\s]+)\s*</idno>', tei_xml, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m2 = _DOI_RE.search(tei_xml or "")
    return m2.group(1) if m2 else None


def extract_title_from_tei(tei_xml: str) -> Optional[str]:
    m = re.search(r"<title[^>]*>\s*([^<]{8,300})\s*</title>", tei_xml, flags=re.IGNORECASE)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    return None


def load_seen(seen_path: pathlib.Path) -> set[str]:
    seen: set[str] = set()
    if not seen_path.exists():
        return seen
    with open(seen_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                u = normalize_url(obj.get("url", ""))
                if u:
                    seen.add(u)
            except Exception:
                continue
    return seen


def record_failure(
    failures_by_reason: Dict[str, int],
    failures_examples: Dict[str, List[Dict[str, Any]]],
    reason: str,
    url: str,
    extra: Optional[Dict[str, Any]] = None,
    keep_examples: int = 8,
) -> None:
    failures_by_reason[reason] = failures_by_reason.get(reason, 0) + 1
    if reason not in failures_examples:
        failures_examples[reason] = []
    if len(failures_examples[reason]) < keep_examples:
        payload: Dict[str, Any] = {"url": url}
        if extra:
            payload.update(extra)
        failures_examples[reason].append(payload)


def _year_ok(y: int) -> bool:
    return MIN_YEAR <= y <= (CURRENT_YEAR + 1)


def _extract_year_from_str(s: str) -> Optional[int]:
    if not s:
        return None
    m = _YEAR_RE.search(s)
    if not m:
        return None
    try:
        y = int(m.group(1))
        return y if _year_ok(y) else None
    except Exception:
        return None


def extract_pub_year_from_html(html: str) -> Optional[int]:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        return None
    soup = BeautifulSoup(html or "", "html.parser")
    keys = [
        ("name", "citation_publication_date"),
        ("name", "citation_date"),
        ("name", "citation_year"),
        ("name", "dc.date"),
        ("name", "dc.date.issued"),
        ("name", "DC.Date"),
        ("name", "DCTERMS.issued"),
        ("name", "dcterms.issued"),
        ("property", "article:published_time"),
    ]
    for attr, key in keys:
        tag = soup.find("meta", attrs={attr: key})
        if tag and tag.get("content"):
            y = _extract_year_from_str(tag["content"])
            if y:
                return y
    text = soup.get_text(" ", strip=True)
    return _extract_year_from_str(text[:4000])


def extract_pub_year_from_pdf_path(pdf_path: str) -> Optional[int]:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return None
    try:
        r = PdfReader(pdf_path)
        info = r.metadata or {}
        for k in ("/CreationDate", "/ModDate"):
            v = info.get(k)
            if isinstance(v, str):
                y = _extract_year_from_str(v)
                if y:
                    return y
        try:
            if r.pages:
                t = (r.pages[0].extract_text() or "")[:2500]
                y2 = _extract_year_from_str(t)
                if y2:
                    return y2
        except Exception:
            pass
    except Exception:
        return None
    return None


def pub_year_from_published_at(published_at: Any) -> Optional[int]:
    if isinstance(published_at, str) and published_at.strip():
        return _extract_year_from_str(published_at)
    return None


def _normalize_doi(doi: str) -> str:
    d = (doi or "").strip()
    d = d.replace("https://doi.org/", "").replace("http://doi.org/", "")
    d = d.replace("doi:", "").replace("DOI:", "").strip()
    d = d.strip().strip(_TRAIL_PUNCT)
    return d


def extract_doi_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    m = _DOI_RE.search(text)
    if not m:
        return None
    d = m.group(1).strip()
    d = d.rstrip(_TRAIL_PUNCT)
    return _normalize_doi(d)


def extract_doi_from_html(html: str) -> Optional[str]:
    if not html:
        return None
    # Try meta first
    try:
        from bs4 import BeautifulSoup  # type: ignore
        soup = BeautifulSoup(html, "html.parser")
        for key in ("citation_doi", "DC.Identifier", "dc.identifier", "dc.Identifier", "doi"):
            tag = soup.find("meta", attrs={"name": key})
            if tag and tag.get("content"):
                d = _normalize_doi(tag["content"])
                if d.startswith("10."):
                    return d
        # Sometimes DOI appears as URL in meta
        for key in ("citation_doi", "dc.identifier"):
            tag = soup.find("meta", attrs={"property": key})
            if tag and tag.get("content"):
                d = _normalize_doi(tag["content"])
                if d.startswith("10."):
                    return d
    except Exception:
        pass
    # Fallback regex on raw html (fast)
    return extract_doi_from_text(html[:200000])


def openalex_lookup_by_doi(doi: str) -> Optional[Dict[str, Any]]:
    d = _normalize_doi(doi)
    if not d:
        return None
    url = f"https://api.openalex.org/works/https://doi.org/{quote(d)}"
    params = {}
    if OPENALEX_EMAIL:
        params["mailto"] = OPENALEX_EMAIL
    try:
        r = _SESSION.get(url, params=params, timeout=20)
        if r.status_code >= 400:
            return None
        return r.json()
    except Exception:
        return None


def best_url_from_openalex(work: Dict[str, Any]) -> Optional[str]:
    if not work:
        return None
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
    return None


def pub_year_from_openalex(work: Dict[str, Any]) -> Optional[int]:
    if not work:
        return None
    y = work.get("publication_year")
    if isinstance(y, int) and _year_ok(y):
        return y
    pd = work.get("publication_date")
    if isinstance(pd, str):
        return _extract_year_from_str(pd)
    return None


@dataclass
class FallbackStats:
    used: int = 0
    success: int = 0


def try_download_with_openalex_fallback(
    *,
    url: str,
    doi: Optional[str],
    hinted_pdf: bool,
    out_path: pathlib.Path,
    max_bytes: int,
    domain_403: Dict[str, int],
    domain_429: Dict[str, int],
    fb_stats: FallbackStats,
) -> Tuple[str, int, str, bytes, str, Optional[Dict[str, Any]]]:
    timeout_s = _request_timeout_for(url, hinted_pdf)
    try:
        final_url, n, sha, prefix, ctype = download_stream_to_file(url, out_path, max_bytes, timeout_s=timeout_s)
        return final_url, n, sha, prefix, ctype, None
    except requests.exceptions.HTTPError as e:
        status = getattr(e.response, "status_code", None)
        dom = _domain(url)
        if status == 403:
            domain_403[dom] = domain_403.get(dom, 0) + 1
        if status == 429:
            domain_429[dom] = domain_429.get(dom, 0) + 1

        if status not in (403, 429) or not doi:
            raise

        if status == 403 and domain_403.get(dom, 0) > MAX_403_PER_DOMAIN:
            raise
        if status == 429 and domain_429.get(dom, 0) > MAX_429_PER_DOMAIN:
            raise

        work = openalex_lookup_by_doi(doi)
        alt = best_url_from_openalex(work or {})
        alt = normalize_url(alt or "")
        if not alt or alt == normalize_url(url):
            raise

        fb_stats.used += 1
        timeout_s2 = _request_timeout_for(alt, hinted_pdf)
        final_url, n, sha, prefix, ctype = download_stream_to_file(alt, out_path, max_bytes, timeout_s=timeout_s2)
        fb_stats.success += 1
        return final_url, n, sha, prefix, ctype, work


def main() -> None:
    dom_cfg = load_domains("configs/domains.yaml")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PARSED_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Input candidates list for *this* RUN_ID
    candidates_path = pathlib.Path(env("CANDIDATES_PATH", state_path("candidates.jsonl")))
    if not candidates_path.exists():
        raise SystemExit(f"Missing candidates: {candidates_path} (run discover.py first)")

    def _load_jsonl(path: pathlib.Path) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
        return out

    items = _load_jsonl(candidates_path)

    # Load global seen URLs early so selection doesn't waste budget on already-seen URLs.
    seen_path = STATE_DIR / "seen_urls.jsonl"
    seen = load_seen(seen_path)

    # --- Diversity-aware portfolio selection (optional) ---
    # By default ingest tries all candidates (legacy behavior).
    # If INGEST_TARGET is set (>0), we select a diverse subset before downloading.
    INGEST_TARGET = int(env("INGEST_TARGET", "0"))
    INGEST_MAX_PER_DOMAIN = int(env("INGEST_MAX_PER_DOMAIN", "0"))
    _exp_raw = env("INGEST_EXPLORATION_PCT", "0").strip()
    if _exp_raw.endswith("%"):
        try:
            INGEST_EXPLORATION_PCT = float(_exp_raw[:-1].strip()) / 100.0
        except Exception:
            INGEST_EXPLORATION_PCT = 0.0
    else:
        INGEST_EXPLORATION_PCT = float(_exp_raw or 0.0)  # 0..1
    INGEST_HISTORY_DAYS = int(env("INGEST_HISTORY_DAYS", "7"))

    def _run_day_from_name(name: str) -> Optional[str]:
        # Supports both YYYY-MM-DD_ingested.json and RUN_ID-based names like 2026-02-27T071001Z_ingested.json
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", name)
        return m.group(1) if m else None

    def _load_history_domain_counts(days: int) -> Counter:
        cnt: Counter = Counter()
        if days <= 0:
            return cnt
        # consider only last N days based on filename day stamp
        # (cheap and robust; avoids parsing timestamps)
        today = day_stamp_utc()
        # Create a simple set of acceptable day stamps (UTC)
        try:
            import datetime as _dt

            t = _dt.date.fromisoformat(today)
            allowed = {str(t - _dt.timedelta(days=i)) for i in range(days)}
        except Exception:
            allowed = None

        for p in sorted(STATE_DIR.glob("*_ingested.json")):
            day = _run_day_from_name(p.name)
            if not day:
                continue
            if allowed is not None and day not in allowed:
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            docs = None
            if isinstance(data, dict):
                docs = data.get("ingested")
            if not isinstance(docs, list):
                continue
            for d in docs:
                if not isinstance(d, dict):
                    continue
                u = d.get("url") or d.get("source_url")
                dom = _domain_family(_domain(str(u or "")))
                if dom:
                    cnt[dom] += 1
        return cnt

    def _portfolio_select(cands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # Remove already-seen URLs *before* sampling, otherwise we waste selection budget.
        # We keep this filter here (instead of inside the ingest loop) to improve yield.
        before = len(cands)
        cands = [c for c in cands if normalize_url(c.get("url", "")) and normalize_url(c.get("url", "")) not in seen]
        filtered = before - len(cands)

        if INGEST_TARGET <= 0:
            if filtered:
                print(f"[ingest] prefilter_seen: removed={filtered} remaining={len(cands)}")
            return cands

        if filtered:
            print(f"[ingest] prefilter_seen: removed={filtered} remaining={len(cands)}")

        target = min(INGEST_TARGET, len(cands))
        max_per_dom = INGEST_MAX_PER_DOMAIN if INGEST_MAX_PER_DOMAIN > 0 else 10**9
        exploration_n = int(round(target * max(0.0, min(1.0, INGEST_EXPLORATION_PCT))))
        exploitation_n = target - exploration_n

        hist = _load_history_domain_counts(INGEST_HISTORY_DAYS)

        # Deterministic shuffle within ties (seed from RUN_ID)
        seed = int.from_bytes(run_id().encode("utf-8"), "little") % (2**32)
        rnd = random.Random(seed)

        def key_exploit(it: Dict[str, Any]):
            url = normalize_url(it.get("url", ""))
            fam = _domain_family(_domain(url))
            score = float(it.get("score") or 0.0)
            # Prefer higher score, but lightly prefer less frequent families
            return (-(score), hist.get(fam, 0), rnd.random())

        def key_explore(it: Dict[str, Any]):
            url = normalize_url(it.get("url", ""))
            fam = _domain_family(_domain(url))
            score = float(it.get("score") or 0.0)
            # Prefer families with low historical presence, then score
            return (hist.get(fam, 0), -(score), rnd.random())

        # Pre-shuffle to avoid bias from input ordering
        cands2 = list(cands)
        rnd.shuffle(cands2)

        selected: List[Dict[str, Any]] = []
        per_dom: Counter = Counter()
        used_urls: set[str] = set()

        def try_add(it: Dict[str, Any]) -> bool:
            url = normalize_url(it.get("url", ""))
            if not url:
                return False
            fam = _domain_family(_domain(url))
            if per_dom[fam] >= max_per_dom:
                return False
            if url in used_urls:
                return False
            used_urls.add(url)
            per_dom[fam] += 1
            selected.append(it)
            return True

        # Pass A: exploration (coverage)
        for it in sorted(cands2, key=key_explore):
            if len(selected) >= exploration_n:
                break
            try_add(it)

        # Pass B: exploitation (quality)
        for it in sorted(cands2, key=key_exploit):
            if len(selected) >= target:
                break
            try_add(it)

        # If we couldn't fill because of caps, relax caps for the remainder
        if len(selected) < target:
            for it in sorted(cands2, key=key_exploit):
                if len(selected) >= target:
                    break
                url = normalize_url(it.get("url", ""))
                if not url or url in used_urls:
                    continue
                used_urls.add(url)
                selected.append(it)

        # Telemetry
        fams = [_domain_family(_domain(normalize_url(i.get("url", "")))) for i in selected]
        fams = [f for f in fams if f]
        top = Counter(fams).most_common(8)
        print(
            f"[ingest] portfolio_select: candidates={len(cands)} -> selected={len(selected)} "
            f"target={target} exploration={exploration_n} exploitation={exploitation_n} "
            f"unique_domain_families={len(set(fams))} max_per_domain={max_per_dom} history_days={INGEST_HISTORY_DAYS}"
        )
        print("[ingest] top_domain_families_selected:")
        for k, v in top:
            print(f"  - {k}: {v}")

        return selected

    items = _portfolio_select(items)

    # Also track seen DOIs globally (helps discovery + dedup across URL variants)
    seen_doi_path = STATE_DIR / "seen_doi.jsonl"
    seen_doi: set[str] = set()
    if seen_doi_path.exists():
        try:
            for line in seen_doi_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    d = (obj.get("doi") or "").strip().lower()
                    if d:
                        seen_doi.add(d)
                except Exception:
                    continue
        except Exception:
            pass

    ingested: List[Dict[str, Any]] = []
    failures_by_reason: Dict[str, int] = {}
    failures_examples: Dict[str, List[Dict[str, Any]]] = {}
    new_seen_lines: List[Dict[str, Any]] = []

    skipped = {
        "already_seen": 0,
        "denied_domain": 0,
        "empty_url": 0,
        "domain_403_cooldown": 0,
        "domain_429_cooldown": 0,
    }

    domain_403: Dict[str, int] = {}
    domain_429: Dict[str, int] = {}
    fb_stats = FallbackStats()

    for it in tqdm(items, desc="Ingest"):
        url = normalize_url(it.get("url", ""))
        if not url:
            skipped["empty_url"] += 1
            continue

        dom = _domain(url)

        if domain_403.get(dom, 0) >= MAX_403_PER_DOMAIN:
            skipped["domain_403_cooldown"] += 1
            continue
        if domain_429.get(dom, 0) >= MAX_429_PER_DOMAIN:
            skipped["domain_429_cooldown"] += 1
            continue

        if url in seen:
            skipped["already_seen"] += 1
            continue
        if denied(url, dom_cfg.get("deny_domains", [])):
            skipped["denied_domain"] += 1
            continue

        cl = get_content_length(url)
        if cl is not None and cl > MAX_DOWNLOAD_BYTES:
            record_failure(
                failures_by_reason,
                failures_examples,
                "too_large_head",
                url,
                extra={"content_length": cl, "max_bytes": MAX_DOWNLOAD_BYTES},
            )
            continue

        hinted_pdf_by_url = url.lower().endswith(".pdf") or _looks_like_pdf_url(url)
        doi = (it.get("doi") or "").strip() or None
        if doi:
            doi = _normalize_doi(doi)
            # skip if DOI already seen globally (avoid reingesting same paper via new URL)
            if doi and doi.lower() in seen_doi:
                skipped.setdefault("already_seen_doi", 0)
                skipped["already_seen_doi"] += 1
                continue

        tmp_path = RAW_DIR / (safe_filename(url) + ".tmp")
        work_for_meta: Optional[Dict[str, Any]] = None

        try:
            final_url, content_len, chash, prefix, content_type, work_for_meta = try_download_with_openalex_fallback(
                url=url,
                doi=doi,
                hinted_pdf=hinted_pdf_by_url,
                out_path=tmp_path,
                max_bytes=MAX_DOWNLOAD_BYTES,
                domain_403=domain_403,
                domain_429=domain_429,
                fb_stats=fb_stats,
            )
        except requests.exceptions.Timeout:
            record_failure(failures_by_reason, failures_examples, "http_timeout", url)
            tmp_path.unlink(missing_ok=True)
            continue
        except requests.exceptions.TooManyRedirects:
            record_failure(failures_by_reason, failures_examples, "http_redirects", url)
            tmp_path.unlink(missing_ok=True)
            continue
        except requests.exceptions.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            reason = "http_error"
            if status == 403:
                reason = "http_403"
                domain_403[dom] = domain_403.get(dom, 0) + 1
            elif status == 429:
                reason = "http_429"
                domain_429[dom] = domain_429.get(dom, 0) + 1
            record_failure(failures_by_reason, failures_examples, reason, url, extra={"status": status})
            tmp_path.unlink(missing_ok=True)
            continue
        except RuntimeError as e:
            if str(e) == "download_exceeded_max_bytes":
                record_failure(failures_by_reason, failures_examples, "too_large_body", url, extra={"max_bytes": MAX_DOWNLOAD_BYTES})
                tmp_path.unlink(missing_ok=True)
                continue
            raise
        except Exception as e:
            record_failure(failures_by_reason, failures_examples, "http_exception", url, extra={"error": str(e)[:240]})
            tmp_path.unlink(missing_ok=True)
            continue

        if content_len <= 0:
            record_failure(failures_by_reason, failures_examples, "empty_body", final_url)
            tmp_path.unlink(missing_ok=True)
            continue

        hinted_pdf = ("application/pdf" in (content_type or "").lower()) or hinted_pdf_by_url or final_url.lower().endswith(".pdf")
        looks_pdf = hinted_pdf or _looks_like_pdf_bytes(prefix)
        if hinted_pdf and not _looks_like_pdf_bytes(prefix):
            looks_pdf = False

        fname = safe_filename(final_url)
        raw_path = RAW_DIR / (fname + (".pdf" if looks_pdf else ".html"))
        hash_path = RAW_DIR / (fname + ".sha256")
        parsed_path = PARSED_DIR / (fname + ".txt")
        meta_path = PARSED_DIR / (fname + ".meta.json")

        tmp_path.replace(raw_path)
        hash_path.write_text(chash, encoding="utf-8")

        meta: Dict[str, Any] = {
            "url": final_url,
            "focus": it.get("focus"),
            "source": it.get("source"),
            "title": it.get("title"),
            "published_at": it.get("published_at"),
            "doi": doi,
            "discovered_at": it.get("discovered_at"),
            "fetched_at": utc_now_iso(),
            "content_hash": chash,
            "raw_path": str(raw_path),
            "parsed_path": str(parsed_path),
            "is_pdf": looks_pdf,
            "content_length": int(content_len),
        }

        pub_year: Optional[int] = pub_year_from_published_at(meta.get("published_at"))

        text = ""
        if looks_pdf:
            try:
                with open(raw_path, "rb") as f:
                    head = f.read(5)
                if head != b"%PDF-":
                    record_failure(failures_by_reason, failures_examples, "not_a_pdf", final_url)
                    continue
            except Exception:
                record_failure(failures_by_reason, failures_examples, "pdf_head_read_error", final_url)
                continue

            text, parser = parse_pdf_with_fallback(raw_path, content_length=content_len)
            meta["pdf_parser"] = parser
            if not text:
                # Queue for OCR backlog (nightly job)
                try:
                    qb = pathlib.Path(env("OCR_QUEUE", "storage/backlog/ocr_queue.jsonl"))
                    qb.parent.mkdir(parents=True, exist_ok=True)
                    with open(qb, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"raw_path": str(raw_path), "parsed_path": str(parsed_path), "meta_path": str(meta_path), "reason": "pdf_parse_failed"}) + "\n")
                except Exception:
                    pass
                record_failure(failures_by_reason, failures_examples, "pdf_parse_failed", final_url, extra={"parser": parser})
                continue

            # DOI extraction from PDF text (early pages)
            if not meta.get("doi"):
                d2 = extract_doi_from_text(text[:120000])
                if d2:
                    meta["doi"] = d2

            if pub_year is None:
                pub_year = extract_pub_year_from_pdf_path(str(raw_path))

            if GROBID_ENABLE and looks_like_paper_url(final_url):
                try:
                    tei_header = grobid_process("processHeaderDocument", str(raw_path), timeout=180)
                    tei_header_path = PARSED_DIR / (fname + ".grobid.header.tei.xml")
                    tei_header_path.write_text(tei_header, encoding="utf-8", errors="ignore")
                    meta["grobid_header_tei"] = str(tei_header_path)

                    doi2 = extract_doi_from_tei(tei_header)
                    if doi2 and not meta.get("doi"):
                        meta["doi"] = _normalize_doi(doi2)
                    t = extract_title_from_tei(tei_header)
                    if t and (not meta.get("title") or meta["title"] in ("(no title)", None)):
                        meta["title"] = t
                except Exception:
                    pass

                if GROBID_FULLTEXT:
                    try:
                        tei_full = grobid_process("processFulltextDocument", str(raw_path), timeout=240)
                        tei_full_path = PARSED_DIR / (fname + ".grobid.fulltext.tei.xml")
                        tei_full_path.write_text(tei_full, encoding="utf-8", errors="ignore")
                        meta["grobid_fulltext_tei"] = str(tei_full_path)

                        if not meta.get("doi"):
                            doi3 = extract_doi_from_tei(tei_full)
                            if doi3:
                                meta["doi"] = _normalize_doi(doi3)
                    except Exception:
                        pass
        else:
            try:
                html = raw_path.read_text(encoding="utf-8", errors="ignore")

                # DOI extraction from HTML
                if not meta.get("doi"):
                    d2 = extract_doi_from_html(html)
                    if d2:
                        meta["doi"] = d2

                if pub_year is None:
                    pub_year = extract_pub_year_from_html(html)

                text = soup_text(html)
            except Exception as e:
                record_failure(failures_by_reason, failures_examples, "html_parse_error", final_url, extra={"error": str(e)[:240]})
                continue

        # OpenAlex enrichment for publication_year if DOI exists and still missing
        if meta.get("doi") and pub_year is None:
            work = work_for_meta or openalex_lookup_by_doi(str(meta["doi"]))
            y_oa = pub_year_from_openalex(work or {})
            if y_oa is not None:
                pub_year = y_oa
                meta["openalex_publication_year_source"] = "openalex"

        if pub_year is not None and _year_ok(pub_year):
            meta["publication_year"] = int(pub_year)

        cleaned, stats = clean_text_with_stats(text)
        meta["text_stats"] = {
            "raw_chars": stats.raw_chars,
            "clean_chars": stats.clean_chars,
            "removed_lines": stats.removed_lines,
            "total_lines": stats.total_lines,
            "boilerplate_share": round(stats.boilerplate_share, 4),
        }

        text = cleaned
        if not text or len(text.strip()) < 50:
            # Queue for OCR backlog if it was a PDF (common case: image-only scanned PDF)
            if looks_pdf:
                try:
                    qb = pathlib.Path(env("OCR_QUEUE", "storage/backlog/ocr_queue.jsonl"))
                    qb.parent.mkdir(parents=True, exist_ok=True)
                    with open(qb, "a", encoding="utf-8") as f:
                        f.write(
                            json.dumps(
                                {
                                    "raw_path": str(raw_path),
                                    "parsed_path": str(parsed_path),
                                    "meta_path": str(meta_path),
                                    "reason": "too_little_text",
                                }
                            )
                            + "\n"
                        )
                except Exception:
                    pass

            record_failure(
                failures_by_reason,
                failures_examples,
                "too_little_text",
                final_url,
                extra={"is_pdf": looks_pdf, "chars": len(text.strip()) if text else 0},
            )
            continue

        rel = compute_spirulina_relevance(url=final_url, title=str(meta.get("title") or ""), text=text[:120000])
        meta["spirulina_score"] = round(float(rel.score), 4)
        meta["spirulina_terms"] = rel.positive_terms[:20]
        meta["spirulina_reasons"] = rel.reasons[:10]

        if len(text) < SPIRULINA_MIN_TEXT_CHARS:
            meta["short_text"] = True

        parsed_path.write_text(text, encoding="utf-8", errors="ignore")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        ingested.append(meta)
        new_seen_lines.append({"url": final_url, "first_seen_at": utc_now_iso()})

        # Track seen DOI globally as well
        if meta.get("doi"):
            try:
                d = str(meta.get("doi") or "").strip().lower()
                if d and d not in seen_doi:
                    seen_doi.add(d)
            except Exception:
                pass

    if new_seen_lines:
        with open(seen_path, "a", encoding="utf-8") as f:
            for line in new_seen_lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")

    # Persist seen DOIs
    if seen_doi:
        try:
            existing = set()
            if seen_doi_path.exists():
                for line in seen_doi_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        d = (obj.get("doi") or "").strip().lower()
                        if d:
                            existing.add(d)
                    except Exception:
                        continue
            new_only = [d for d in sorted(seen_doi) if d and d not in existing]
            if new_only:
                with open(seen_doi_path, "a", encoding="utf-8") as f:
                    for d in new_only:
                        f.write(json.dumps({"doi": d, "first_seen_at": utc_now_iso()}, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _sources_kpi_from_ingested(docs: List[Dict[str, Any]]) -> Dict[str, Any]:
        urls = [d.get("url") or d.get("source_url") for d in docs if isinstance(d, dict)]
        fams = [_domain_family(_domain(str(u or ""))) for u in urls]
        fams = [f for f in fams if f]

        n = len(fams)
        by = Counter(fams)
        unique = len(by)

        def _share_top(k: int) -> float:
            if n <= 0:
                return 0.0
            return sum(v for _, v in by.most_common(k)) / float(n)

        shares = [v / float(n) for v in by.values()] if n else []
        hhi = float(sum(s * s for s in shares)) if shares else 0.0

        # Shannon entropy (nats)
        import math

        entropy = float(-sum(s * math.log(s) for s in shares if s > 0.0)) if shares else 0.0
        entropy_norm = float(entropy / math.log(unique) if unique > 1 else 0.0)

        return {
            "n_docs": len(docs),
            "n_with_domain": n,
            "unique_domain_families": unique,
            "top5_share": round(_share_top(5), 4),
            "top10_share": round(_share_top(10), 4),
            "hhi": round(hhi, 6),
            "entropy": round(entropy, 6),
            "entropy_norm": round(entropy_norm, 6),
            "top_domain_families": [{"domain": k, "count": v} for k, v in by.most_common(12)],
        }

    def _load_ingested_domains(path: pathlib.Path) -> set[str]:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return set()
        docs = data.get("ingested") if isinstance(data, dict) else None
        if not isinstance(docs, list):
            return set()
        out = set()
        for d in docs:
            if not isinstance(d, dict):
                continue
            u = d.get("url") or d.get("source_url")
            fam = _domain_family(_domain(str(u or "")))
            if fam:
                out.add(fam)
        return out

    def _find_previous_ingested_path(exclude_name: str) -> Optional[pathlib.Path]:
        # pick most recent *_ingested.json excluding current run
        cands = [p for p in STATE_DIR.glob("*_ingested.json") if p.name != exclude_name]
        if not cands:
            return None
        cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return cands[0]

    # Run-scoped summary output (do not overwrite across runs)
    out_path = pathlib.Path(env("INGESTED_PATH", state_path("ingested.json")))

    sources_kpi = _sources_kpi_from_ingested(ingested)

    # Novelty + overlap vs previous run
    hist_domains = set(_load_history_domain_counts(INGEST_HISTORY_DAYS).keys()) if INGEST_HISTORY_DAYS > 0 else set()
    today_domains = {x["domain"] for x in sources_kpi.get("top_domain_families", []) if isinstance(x, dict) and x.get("domain")}
    # NOTE: today_domains based on top list; compute full set instead
    today_domains = set([_domain_family(_domain(str((d.get("url") or d.get("source_url") or "")))) for d in ingested if isinstance(d, dict)])
    today_domains.discard("")

    novelty = 0.0
    if today_domains:
        novelty = len([d for d in today_domains if d not in hist_domains]) / float(len(today_domains)) if hist_domains else 1.0

    prev_path = _find_previous_ingested_path(out_path.name)
    jaccard = None
    if prev_path is not None:
        prev_domains = _load_ingested_domains(prev_path)
        if prev_domains or today_domains:
            jaccard = len(prev_domains & today_domains) / float(len(prev_domains | today_domains))

    sources_kpi["novelty_domains_share_history_days"] = round(float(novelty), 4)
    sources_kpi["history_days"] = INGEST_HISTORY_DAYS
    if jaccard is not None:
        sources_kpi["jaccard_vs_prev_run"] = round(float(jaccard), 4)
        sources_kpi["prev_run_ingested_path"] = str(prev_path)

    out_path.write_text(
        json.dumps(
            {
                "ingested": ingested,
                "failures_total": sum(failures_by_reason.values()),
                "failures_by_reason": failures_by_reason,
                "failures_examples": failures_examples,
                "skipped": skipped,
                "openalex_fallback": {"used": fb_stats.used, "success": fb_stats.success},
                "domain_403_top": sorted(domain_403.items(), key=lambda x: x[1], reverse=True)[:12],
                "domain_429_top": sorted(domain_429.items(), key=lambda x: x[1], reverse=True)[:12],
                "sources_kpi": sources_kpi,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(str(out_path))


if __name__ == "__main__":
    main()