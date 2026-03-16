"""pipelines/common.py — Shared utilities for spiru-ops
================================================================================

This repository implements a *daily / multi-run-per-day* pipeline that builds a
Spirulina/Arthrospira-centric knowledge base (KB) and a RAG Copilot.

Why this file exists
--------------------
Nearly every pipeline step needs the same handful of primitives:

1) **Environment / config access**
   The pipeline is orchestrated by cron and by local developer shells. It must
   work in both contexts, so we centralize env var handling.

2) **Run identity and deterministic artifact naming**
   The pipeline writes intermediate artifacts to `storage/state/` and final
   artifacts to `storage/artifacts/`.

   A real production pitfall is the "midnight split":
   - Step A (discover) starts before midnight and writes `YYYY-MM-DD_candidates.jsonl`
   - Step B (evaluate) runs after midnight and tries to read `YYYY-MM-DD_candidates.jsonl`
     for the new day (and fails).

   The fix is a stable `RUN_ID` that is generated *once* by the entrypoint
   (`pipelines/cron_run_daily.sh`) and exported to all subprocesses.

   All steps should name artifacts with `<RUN_ID>_<suffix>`.

3) **Search-quality utilities**
   - URL canonicalization for dedup
   - Boilerplate removal for HTML-derived text
   - Simple chunking helper

Design constraints
------------------
- Keep dependencies minimal (std-lib + requests + bs4 + pyyaml, already used).
- Prefer deterministic behavior over clever heuristics.
- Be explicit and verbose in comments: this project is meant to be understood by
  humans *and* by AI coding tools.

"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
import yaml
from bs4 import BeautifulSoup

# python-dotenv is nice for local dev, but should not be a hard dependency.
# If it's not installed, we silently skip it.
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except Exception:
    pass


# =============================================================================
# Environment helpers
# =============================================================================

def env(name: str, default=None, required: bool = False):
    """Read an environment variable with optional default/required semantics.

    Parameters
    ----------
    name:
        Environment variable name.
    default:
        Default value if variable missing.
    required:
        If True, raise if missing/empty.

    Notes
    -----
    We keep this intentionally tiny (no extra dependency) because cron contexts
    can be finicky and we want predictable behavior.
    """

    v = os.getenv(name, default)
    if required and (v is None or str(v).strip() == ""):
        raise RuntimeError(f"Missing env var: {name}")
    return v


def utc_now_iso() -> str:
    """Return current UTC timestamp as ISO8601 string."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# =============================================================================
# RUN_ID helpers (pipeline-level determinism)
# =============================================================================

def run_id() -> str:
    """Return the stable identifier for the current pipeline run.

    - Preferred: RUN_ID is set by the cron wrapper once per run.
    - Fallback: generate a UTC timestamp identifier.

    Why this matters:
    - Prevents *midnight split* bugs.
    - Enables multiple runs per day without overwriting `storage/state/YYYY-MM-DD_*`.
    - Makes retry logic safe: attempt 2 should reuse the same artifacts.
    """

    rid = (os.getenv("RUN_ID") or "").strip()
    if rid:
        return rid
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def run_day_from_id(rid: str) -> str:
    """Extract YYYY-MM-DD from RUN_ID like '2026-02-25T233859Z'."""

    rid = (rid or "").strip()
    if len(rid) >= 10 and rid[4] == "-" and rid[7] == "-":
        return rid[:10]
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def day_stamp_utc() -> str:
    """Backward-compatible day stamp, but stable across a run.

    Historically the pipeline used `YYYY-MM-DD` filenames. We keep that idea
    for human readability, but we ensure the day is derived from RUN_ID when
    available.

    This is a *critical correctness invariant*:
    - discover, ingest, report, evaluate must agree on the same "day".

    Implementation:
    - If RUN_ID exists: derive the day from it.
    - Else: use current UTC day.
    """

    rid = (os.getenv("RUN_ID") or "").strip()
    if rid:
        return run_day_from_id(rid)
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def state_path(suffix: str) -> str:
    """Return a `storage/state/<RUN_ID>_<suffix>` path."""

    state_dir = env("STATE_DIR", "storage/state")
    return str(pathlib.Path(state_dir) / f"{run_id()}_{suffix}")


def artifact_path(suffix: str) -> str:
    """Return a `storage/artifacts/<RUN_ID>_<suffix>` path."""

    art_dir = env("ARTIFACTS_DIR", "storage/artifacts")
    return str(pathlib.Path(art_dir) / f"{run_id()}_{suffix}")


# =============================================================================
# Small hashing / URL utilities
# =============================================================================

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def safe_id(s: str) -> str:
    """Short stable id used for filenames and Qdrant point IDs."""

    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def normalize_url(url: str) -> str:
    """Best-effort URL canonicalization for dedup.

    Why we do this:
    - Search engines return multiple syntactic variants of the same page.
    - Without canonicalization, you'll ingest duplicates and poison retrieval.

    What we normalize:
    - remove fragments (#...)
    - scheme/netloc casing
    - collapse multiple slashes
    - remove tracking query params (utm_*, gclid, fbclid, etc.)
    - strip trailing slash (except root)

    This is heuristic and intentionally conservative.
    """

    u = (url or "").strip()
    if not u:
        return ""

    u = re.sub(r"\s+", "", u)
    u = re.sub(r"#.*$", "", u)

    try:
        p = urlparse(u)
        scheme = (p.scheme or "http").lower()
        netloc = (p.netloc or "").lower()

        # Handle inputs without scheme (e.g. "example.com/page")
        if not netloc and p.path and "." in p.path.split("/")[0]:
            p2 = urlparse("http://" + u)
            scheme = (p2.scheme or "http").lower()
            netloc = (p2.netloc or "").lower()
            p = p2

        # Strip default ports (:443 for https, :80 for http) so that
        # "https://host:443/path" and "https://host/path" normalize to the same string.
        _default_ports = {"https": "443", "http": "80"}
        if ":" in netloc:
            host_part, _, port_part = netloc.rpartition(":")
            if port_part == _default_ports.get(scheme):
                netloc = host_part

        path = re.sub(r"/{2,}", "/", p.path or "")
        if path.endswith("/") and path != "/":
            path = path[:-1]

        drop_prefix = ("utm_",)
        drop_keys = {
            "gclid",
            "fbclid",
            "igshid",
            "mc_cid",
            "mc_eid",
            "ref",
            "source",
            "spm",
        }
        q = []
        for k, v in parse_qsl(p.query, keep_blank_values=False):
            kl = k.lower()
            if kl in drop_keys or any(kl.startswith(px) for px in drop_prefix):
                continue
            q.append((k, v))
        query = urlencode(q, doseq=True)

        return urlunparse((scheme, netloc, path, "", query, ""))
    except Exception:
        return u


def domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def is_pdf_response(resp: requests.Response) -> bool:
    ct = (resp.headers.get("content-type") or "").lower()
    return "application/pdf" in ct or resp.url.lower().endswith(".pdf")


# =============================================================================
# Config loaders
# =============================================================================

def load_yaml(path: str) -> dict:
    return yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))


def load_focus(path: str = "configs/focus.yaml") -> list[dict]:
    d = load_yaml(path) or {}
    return d.get("focus", [])


def load_domains(path: str = "configs/domains.yaml") -> dict:
    return load_yaml(path) or {}


def load_scoring(path: str = "configs/scoring.yaml") -> dict:
    return load_yaml(path) or {}


# =============================================================================
# Domain allow/deny helpers
# =============================================================================

def denied(url: str, deny_domains: list[str]) -> bool:
    d = domain(url)
    return any(d.endswith(x) for x in deny_domains)


def prefer_score(url: str, prefer_domains: list[str]) -> int:
    """Return 1 if URL domain matches any prefer_domains, else 0."""

    d = domain(url)
    return 1 if any(d.endswith(x) or x in d for x in prefer_domains) else 0


# =============================================================================
# Text extraction + cleaning
# =============================================================================

def soup_text(html: str) -> str:
    """Extract readable text from HTML (BeautifulSoup fallback)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()
    text = soup.get_text("\n")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    cleaned, _stats = clean_text_with_stats(text)
    return cleaned


def extract_html_text(html: str) -> str:
    """Extract readable text from HTML.

    Tries trafilatura first (specialized content extractor — better at removing
    boilerplate from publisher/journal pages). Falls back to soup_text if
    trafilatura returns empty or very short text.
    """
    try:
        import trafilatura  # type: ignore
        result = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            favor_recall=True,
        )
        if result and len(result.strip()) > 200:
            return result.strip()
    except Exception:
        pass
    return soup_text(html)


def classify_doc_type(url: str = "", title: str = "", doi: str = "", source: str = "") -> str:
    """Classify document type as paper/thesis/guideline/regulation/webpage.

    Uses heuristic signals from URL, DOI presence, source label, and title keywords.
    Returns one of: 'paper', 'thesis', 'guideline', 'regulation', 'webpage'.
    """
    u = (url or "").lower()
    t = (title or "").lower()
    s = (source or "").lower()

    # Regulations / official standards
    reg_keywords = ("regulation", "directive", "ordinance", "decree", "statute", "official journal", "codex alimentarius")
    if any(k in t for k in reg_keywords) or any(k in u for k in ("regulation", "directive", "codex")):
        return "regulation"

    # Guidelines / recommendations / technical reports
    guide_keywords = ("guideline", "guidance", "recommendation", "position paper", "technical report", "opinion", "efsa", "who ", "fao ", "assessment")
    if any(k in t for k in guide_keywords) or any(k in u for k in ("guideline", "guidance", "efsa", "who.int", "fao.org")):
        return "guideline"

    # Theses / dissertations
    if "thesis" in t or "dissertation" in t or "thes" in u or "dissertat" in u:
        return "thesis"

    # Academic papers: DOI present, or known academic sources/domains
    academic_domains = ("doi.org", "pubmed", "arxiv", "biorxiv", "medrxiv", "springer", "elsevier",
                        "wiley", "tandfonline", "mdpi", "frontiersin", "nature.com", "science.org",
                        "core.ac.uk", "semanticscholar", "researchgate", "hal.", "zenodo")
    if doi or any(d in u for d in academic_domains) or s in ("openalex", "core", "pubmed"):
        return "paper"

    return "webpage"


@dataclass(frozen=True)
class TextCleanStats:
    raw_chars: int
    clean_chars: int
    removed_lines: int
    total_lines: int
    boilerplate_share: float


_BOILERPLATE_PATTERNS = [
    r"^\s*advertisement\s*$",
    r"^\s*cookie(s)?\b",
    r"privacy policy",
    r"terms of (use|service)",
    r"all rights reserved",
    r"subscribe",
    r"sign\s*(in|up)",
    r"log\s*in",
    r"skip to (main|content)",
    r"(accept|reject)\s+cookies",
    r"newsletter",
    r"back to top",
]


def clean_text_with_stats(text: str) -> tuple[str, TextCleanStats]:
    """Remove common boilerplate/noise from extracted text.

    This is a heuristic filter. The output is used for embedding, so we prefer:
    - slightly *less* text but higher information density
    - stable behavior (avoid fancy ML extraction)

    Returns:
    - cleaned text
    - stats object (useful for QC / report)
    """

    raw = (text or "")
    raw = re.sub(r"\r\n?", "\n", raw)
    raw_chars = len(raw)
    lines = [ln.strip() for ln in raw.split("\n")]
    lines = [ln for ln in lines if ln]

    if not lines:
        return "", TextCleanStats(raw_chars=raw_chars, clean_chars=0, removed_lines=0, total_lines=0, boilerplate_share=0.0)

    bp_re = re.compile("|".join(_BOILERPLATE_PATTERNS), flags=re.IGNORECASE)

    from collections import Counter

    cnt = Counter(lines)
    kept: list[str] = []
    removed = 0

    for ln in lines:
        if len(ln) < 3:
            removed += 1
            continue
        # Drop highly repetitive short lines (nav/menu)
        if cnt[ln] >= 8 and len(ln) < 120:
            removed += 1
            continue
        if bp_re.search(ln):
            removed += 1
            continue
        alpha = sum(ch.isalpha() for ch in ln)
        if alpha / max(1, len(ln)) < 0.18 and len(ln) < 200:
            removed += 1
            continue
        kept.append(ln)

    # De-duplicate consecutive identical lines
    deduped: list[str] = []
    prev: Optional[str] = None
    for ln in kept:
        if ln == prev:
            removed += 1
            continue
        deduped.append(ln)
        prev = ln

    out = "\n".join(deduped)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()

    clean_chars = len(out)
    total_lines = len(lines)
    boilerplate_share = (removed / total_lines) if total_lines else 0.0
    return out, TextCleanStats(raw_chars=raw_chars, clean_chars=clean_chars, removed_lines=removed, total_lines=total_lines, boilerplate_share=boilerplate_share)


# =============================================================================
# Chunking
# =============================================================================

def chunk_text(text: str, max_chars: int, overlap: int) -> list[str]:
    """Chunk text by character count.

    Why character-based chunking:
    - avoids tokenization dependencies
    - consistent across languages
    - good enough for MiniLM embeddings

    Parameters
    ----------
    max_chars:
        Target max chunk length.
    overlap:
        Overlap between chunks to preserve context continuity.

    Notes
    -----
    This chunking is intentionally simple. If you later adopt semantic splitting
    (headings, paragraphs, sentence boundaries), you can still keep this as a
    fallback.
    """

    text = re.sub(r"\n{3,}", "\n\n", (text or "")).strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + max_chars)
        chunks.append(text[start:end])
        if end >= n:
            break
        start = max(0, end - overlap)
    return chunks
