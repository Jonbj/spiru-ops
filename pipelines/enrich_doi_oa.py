"""pipelines/enrich_doi_oa.py — DOI / OA enrichment (Crossref + Unpaywall)
================================================================================

Purpose
-------
Enrich ingested documents with better bibliographic metadata and open-access (OA)
links.

This is intentionally a *post-ingest* step:
- ingest extracts DOI best-effort from HTML/PDF
- enrichment uses DOI to fetch metadata from:
  - Crossref (bibliographic metadata)
  - Unpaywall (OA locations)

It updates:
- each document's `storage/parsed/<...>.meta.json`
- the run summary JSON (`INGESTED_PATH`) so downstream steps (index/report)
  can see the enriched fields.

Inputs
------
- INGESTED_PATH (env) or storage/state/<RUN_ID>_ingested.json

Output
------
- Overwrites INGESTED_PATH with additional fields per doc:
  - oa_url, oa_pdf_url, crossref_* fields

Env
---
- UNPAYWALL_EMAIL (recommended; required by Unpaywall)
- CROSSREF_MAILTO (optional; polite)
- ENRICH_LIMIT (int, default 0 = no limit)
- ENRICH_SLEEP_S (float, default 0.2)

Usage
-----
  python -m pipelines.enrich_doi_oa

"""

from __future__ import annotations

import json
import pathlib
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

import requests

from pipelines.common import env, state_path


UNPAYWALL_EMAIL = (env("UNPAYWALL_EMAIL", "") or "").strip()
CROSSREF_MAILTO = (env("CROSSREF_MAILTO", "") or "").strip()

ENRICH_LIMIT = int(env("ENRICH_LIMIT", "0") or 0)
ENRICH_SLEEP_S = float(env("ENRICH_SLEEP_S", "0.2") or 0.2)

_SESSION = requests.Session()


def _load_json(path: pathlib.Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: pathlib.Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _norm_doi(doi: str) -> str:
    d = (doi or "").strip()
    d = d.replace("https://doi.org/", "").replace("http://doi.org/", "")
    d = d.replace("doi:", "").strip()
    return d


def crossref_lookup(doi: str) -> Optional[Dict[str, Any]]:
    d = _norm_doi(doi)
    if not d:
        return None
    url = f"https://api.crossref.org/works/{quote(d)}"
    params = {}
    if CROSSREF_MAILTO:
        params["mailto"] = CROSSREF_MAILTO
    try:
        r = _SESSION.get(url, params=params, timeout=20)
        if r.status_code >= 400:
            return None
        j = r.json()
        return (j.get("message") or {}) if isinstance(j, dict) else None
    except Exception:
        return None


def unpaywall_lookup(doi: str) -> Optional[Dict[str, Any]]:
    d = _norm_doi(doi)
    if not d or not UNPAYWALL_EMAIL:
        return None
    url = f"https://api.unpaywall.org/v2/{quote(d)}"
    try:
        r = _SESSION.get(url, params={"email": UNPAYWALL_EMAIL}, timeout=20)
        if r.status_code >= 400:
            return None
        j = r.json()
        return j if isinstance(j, dict) else None
    except Exception:
        return None


def _pick_oa_urls(upw: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Return (oa_url, oa_pdf_url) best-effort."""
    if not upw:
        return None, None

    best = upw.get("best_oa_location") or {}
    oa_url = (best.get("url") or "").strip() or None
    oa_pdf = (best.get("url_for_pdf") or "").strip() or None

    # fallback to first oa_location
    if not oa_url:
        locs = upw.get("oa_locations") or []
        if isinstance(locs, list) and locs:
            l0 = locs[0] or {}
            oa_url = (l0.get("url") or "").strip() or oa_url
            oa_pdf = (l0.get("url_for_pdf") or "").strip() or oa_pdf

    return oa_url, oa_pdf


def main() -> None:
    ing_path = pathlib.Path(env("INGESTED_PATH", state_path("ingested.json")))
    if not ing_path.exists():
        raise SystemExit(f"Missing ingested state: {ing_path}")

    data = _load_json(ing_path)
    ing = data.get("ingested") or []
    if not isinstance(ing, list):
        raise SystemExit(f"Invalid ingested schema in {ing_path} (expected list)")

    n = 0
    for meta in ing:
        if not isinstance(meta, dict):
            continue
        if ENRICH_LIMIT and n >= ENRICH_LIMIT:
            break

        doi = (meta.get("doi") or "").strip()
        if not doi:
            continue

        doi = _norm_doi(doi)
        if not doi:
            continue

        # --- Unpaywall (OA URLs)
        upw = unpaywall_lookup(doi)
        oa_url, oa_pdf = _pick_oa_urls(upw or {})
        if oa_url:
            meta["oa_url"] = oa_url
        if oa_pdf:
            meta["oa_pdf_url"] = oa_pdf
        if upw and isinstance(upw, dict):
            meta["unpaywall"] = {
                "is_oa": upw.get("is_oa"),
                "oa_status": upw.get("oa_status"),
                "host_type": (upw.get("best_oa_location") or {}).get("host_type"),
            }

        # --- Crossref (bibliographic)
        cr = crossref_lookup(doi)
        if cr and isinstance(cr, dict):
            # Keep a compact subset
            title = None
            try:
                t = cr.get("title")
                if isinstance(t, list) and t:
                    title = (t[0] or "").strip() or None
            except Exception:
                title = None

            meta["crossref"] = {
                "type": cr.get("type"),
                "publisher": cr.get("publisher"),
                "container_title": (cr.get("container-title") or [None])[0] if isinstance(cr.get("container-title"), list) else cr.get("container-title"),
                "issued": (cr.get("issued") or {}),
                "is_referenced_by_count": cr.get("is-referenced-by-count"),
                "references_count": cr.get("references-count"),
            }
            if title and (not meta.get("title") or meta.get("title") == meta.get("url")):
                meta["title"] = title

        # Also update parsed meta.json if present
        meta_path = meta.get("meta_path")
        if meta_path and pathlib.Path(str(meta_path)).exists():
            try:
                doc_meta = _load_json(pathlib.Path(str(meta_path)))
                # mirror fields
                for k in ("oa_url", "oa_pdf_url", "unpaywall", "crossref"):
                    if k in meta:
                        doc_meta[k] = meta[k]
                if meta.get("title"):
                    doc_meta["title"] = meta.get("title")
                _write_json(pathlib.Path(str(meta_path)), doc_meta)
            except Exception:
                pass

        n += 1
        if ENRICH_SLEEP_S:
            time.sleep(ENRICH_SLEEP_S)

    # overwrite run summary
    data["ingested"] = ing
    data["doi_oa_enriched"] = True
    _write_json(ing_path, data)
    print(str(ing_path))


if __name__ == "__main__":
    main()
