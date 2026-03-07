"""pipelines/kb_validate.py — Global dedup + validator
================================================================================

Purpose
-------
Validate KB integrity and produce a dedup report across *all* runs.

This does NOT delete data. It:
- builds a global index across storage/state/*_ingested.json
- detects duplicates by DOI and by content_hash
- writes a Markdown report
- optionally annotates per-document meta.json with duplicate markers

Definitions
-----------
Canonical key priority:
- DOI (preferred)
- else content_hash

Env
---
- STATE_DIR (default storage/state)
- ARTIFACTS_DIR / ART_DIR (default storage/artifacts)
- VALIDATOR_FIX=0|1 (default 0)  # annotate meta.json with duplicate_of
- DAY=YYYY-MM-DD (optional) for naming the report

Usage
-----
  python -m pipelines.kb_validate

"""

from __future__ import annotations

import json
import pathlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from pipelines.common import env


STATE_DIR = pathlib.Path(env("STATE_DIR", "storage/state"))
ART_DIR = pathlib.Path(env("ARTIFACTS_DIR", env("ART_DIR", "storage/artifacts")))
VALIDATOR_FIX = (env("VALIDATOR_FIX", "0") or "0").strip() in ("1", "true", "TRUE", "yes", "YES")


def _today_utc_day() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_json(path: pathlib.Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: pathlib.Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _norm_doi(doi: str) -> str:
    d = (doi or "").strip().lower()
    d = d.replace("https://doi.org/", "").replace("http://doi.org/", "")
    d = d.replace("doi:", "").strip()
    return d


def _key(meta: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    doi = _norm_doi(str(meta.get("doi") or ""))
    if doi:
        return (f"doi:{doi}", None)
    ch = str(meta.get("content_hash") or "").strip()
    if ch:
        return (None, f"hash:{ch}")
    return (None, None)


@dataclass
class DocRef:
    run_id: str
    url: str
    title: str
    meta_path: str


def main() -> None:
    day = (env("DAY", "") or "").strip() or _today_utc_day()
    ART_DIR.mkdir(parents=True, exist_ok=True)

    by_doi: Dict[str, List[DocRef]] = defaultdict(list)
    by_hash: Dict[str, List[DocRef]] = defaultdict(list)

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
            doi_key, hash_key = _key(m)
            url = str(m.get("url") or "").strip()
            title = str(m.get("title") or "").strip()
            meta_path = str(m.get("meta_path") or "").strip()
            ref = DocRef(run_id=rid, url=url, title=title, meta_path=meta_path)
            if doi_key:
                by_doi[doi_key].append(ref)
            if hash_key:
                by_hash[hash_key].append(ref)

    dup_doi = {k: v for k, v in by_doi.items() if len(v) >= 2}
    dup_hash = {k: v for k, v in by_hash.items() if len(v) >= 2}

    out: List[str] = []
    out.append(f"# spiru-ops — KB validator / dedup report ({day})\n")
    out.append(f"Scanned ingested runs: **{len(ing_files)}**")
    out.append(f"Duplicate DOI groups: **{len(dup_doi)}**")
    out.append(f"Duplicate content_hash groups: **{len(dup_hash)}**\n")

    def _emit_group(title: str, groups: Dict[str, List[DocRef]], limit: int = 30) -> None:
        out.append(f"## {title}\n")
        n = 0
        for k, refs in sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True):
            n += 1
            if n > limit:
                break
            out.append(f"### {k} — {len(refs)} occurrences")
            for r in refs[:10]:
                line = f"- {r.run_id} | {r.url}"
                if r.title:
                    line += f" | {r.title}"
                out.append(line)
            if len(refs) > 10:
                out.append(f"- … ({len(refs)-10} more)")
            out.append("")

    _emit_group("Duplicate DOI (top)", dup_doi)
    _emit_group("Duplicate content_hash (top)", dup_hash)

    report_path = ART_DIR / f"{day}_kb_dedup_report.md"
    report_path.write_text("\n".join(out).strip() + "\n", encoding="utf-8")

    # Optional fix: annotate meta.json with duplicate markers
    if VALIDATOR_FIX:
        # DOI duplicates: pick the first occurrence as canonical
        for k, refs in dup_doi.items():
            canonical = refs[0]
            for r in refs[1:]:
                if not r.meta_path:
                    continue
                mp = pathlib.Path(r.meta_path)
                if not mp.exists():
                    continue
                try:
                    d = _load_json(mp) or {}
                    d["duplicate_of"] = {
                        "kind": "doi",
                        "key": k,
                        "canonical_run_id": canonical.run_id,
                        "canonical_url": canonical.url,
                    }
                    _write_json(mp, d)
                except Exception:
                    continue

        for k, refs in dup_hash.items():
            canonical = refs[0]
            for r in refs[1:]:
                if not r.meta_path:
                    continue
                mp = pathlib.Path(r.meta_path)
                if not mp.exists():
                    continue
                try:
                    d = _load_json(mp) or {}
                    if "duplicate_of" not in d:
                        d["duplicate_of"] = {
                            "kind": "content_hash",
                            "key": k,
                            "canonical_run_id": canonical.run_id,
                            "canonical_url": canonical.url,
                        }
                        _write_json(mp, d)
                except Exception:
                    continue

    # convenience
    (ART_DIR / "latest_kb_dedup_report.md").write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")

    print(str(report_path))


if __name__ == "__main__":
    main()
