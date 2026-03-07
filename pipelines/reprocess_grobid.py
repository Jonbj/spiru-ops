"""pipelines/reprocess_grobid.py — spiru-ops (documented version)

This file is part of the spiru-ops project, which builds a Spirulina/Arthrospira
knowledge base and a RAG Copilot.

The repository is intentionally documented with verbose comments so that:
- humans can quickly understand intent and invariants
- AI tools (agents, refactoring assistants) can reason about the code safely

This header is *documentation-only*; the runtime logic below is preserved.
"""

import os
import json
import pathlib
import re
import requests
from tqdm import tqdm

from pipelines.common import env

RAW_DIR = pathlib.Path(env("RAW_DIR", "storage/raw"))
PARSED_DIR = pathlib.Path(env("PARSED_DIR", "storage/parsed"))

GROBID_URL = env("GROBID_URL", default="http://localhost:8070", required=True)
GROBID_FULLTEXT = env("GROBID_FULLTEXT", default="0").strip() in ("1", "true", "TRUE", "yes", "YES")

def grobid_process(endpoint: str, pdf_path: str, timeout: int) -> str:
    with open(pdf_path, "rb") as f:
        files = {"input": (os.path.basename(pdf_path), f, "application/pdf")}
        r = requests.post(f"{GROBID_URL}/api/{endpoint}", files=files, timeout=timeout)
    r.raise_for_status()
    return r.text  # TEI XML

def extract_doi_from_tei(tei_xml: str) -> str | None:
    m = re.search(r'<idno[^>]*type="DOI"[^>]*>\s*([^<\s]+)\s*</idno>', tei_xml, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m2 = re.search(r'\b10\.\d{4,9}/[^\s"<>\]]+\b', tei_xml)
    return m2.group(0) if m2 else None

def extract_title_from_tei(tei_xml: str) -> str | None:
    m = re.search(r"<title[^>]*>\s*([^<]{8,300})\s*</title>", tei_xml, flags=re.IGNORECASE)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    return None

def find_meta_for_pdf(pdf_path: pathlib.Path) -> pathlib.Path | None:
    # match by prefix: <fname>.pdf -> <fname>.meta.json in parsed
    prefix = pdf_path.stem  # file without .pdf
    meta_path = PARSED_DIR / f"{prefix}.meta.json"
    return meta_path if meta_path.exists() else None

def main():
    PARSED_DIR.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(RAW_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {RAW_DIR}")
        return

    processed = 0
    skipped = 0
    errors = 0

    for pdf in tqdm(pdfs, desc="GROBID reprocess"):
        prefix = pdf.stem
        header_out = PARSED_DIR / f"{prefix}.grobid.header.tei.xml"
        full_out = PARSED_DIR / f"{prefix}.grobid.fulltext.tei.xml"

        # If already exists, skip (idempotent)
        if header_out.exists() and (not GROBID_FULLTEXT or full_out.exists()):
            skipped += 1
            continue

        try:
            tei_header = grobid_process("processHeaderDocument", str(pdf), timeout=180)
            header_out.write_text(tei_header, encoding="utf-8", errors="ignore")

            doi = extract_doi_from_tei(tei_header)
            title = extract_title_from_tei(tei_header)

            if GROBID_FULLTEXT and not full_out.exists():
                tei_full = grobid_process("processFulltextDocument", str(pdf), timeout=240)
                full_out.write_text(tei_full, encoding="utf-8", errors="ignore")
                if not doi:
                    doi = extract_doi_from_tei(tei_full)

            # Update meta if available
            meta_path = find_meta_for_pdf(pdf)
            if meta_path:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                meta["grobid_header_tei"] = str(header_out)
                if GROBID_FULLTEXT and full_out.exists():
                    meta["grobid_fulltext_tei"] = str(full_out)
                if doi:
                    meta["doi"] = doi
                if title and (not meta.get("title") or meta.get("title") in ("(no title)", "")):
                    meta["title"] = title
                meta["grobid_reprocessed_at"] = env("UTC_NOW", None) or ""  # optional
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

            processed += 1
        except Exception:
            errors += 1
            continue

    print(f"processed: {processed}, skipped: {skipped}, errors: {errors}")

if __name__ == "__main__":
    main()