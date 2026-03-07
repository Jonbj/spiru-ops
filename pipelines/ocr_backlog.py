"""pipelines/ocr_backlog.py — OCR backlog processor (best-effort)
================================================================================

Purpose
-------
Process PDFs that were queued for OCR because they contained too little text.

Queue file:
- storage/backlog/ocr_queue.jsonl

Each line should contain at least:
- raw_path (path to the pdf)
- parsed_path (target text file)
- meta_path (target meta json)

We try OCR using `ocrmypdf` if installed.
If not available, we exit with code 0 and print a warning (silent-safe).

Env
---
- OCR_QUEUE (default storage/backlog/ocr_queue.jsonl)
- OCR_OUT_DIR (default storage/ocr)
- OCR_LIMIT (default 20)

Usage
-----
  python -m pipelines.ocr_backlog

"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from typing import Any, Dict, List

from pipelines.common import env


QUEUE = pathlib.Path(env("OCR_QUEUE", "storage/backlog/ocr_queue.jsonl"))
OUT_DIR = pathlib.Path(env("OCR_OUT_DIR", "storage/ocr"))
OCR_LIMIT = int(env("OCR_LIMIT", "20") or 20)


def _load_json(path: pathlib.Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: pathlib.Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_text_pypdf(pdf_path: str) -> str:
    from pypdf import PdfReader  # type: ignore

    r = PdfReader(pdf_path)
    parts: List[str] = []
    for page in r.pages[:250]:
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        t = (t or "").strip()
        if t:
            parts.append(t)
    return "\n\n".join(parts)


def main() -> None:
    if not QUEUE.exists():
        print(str(QUEUE))
        return

    if shutil.which("ocrmypdf") is None:
        # best-effort: do not fail pipeline
        print("[ocr_backlog] ocrmypdf not installed; skipping")
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    lines = [ln for ln in QUEUE.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
    keep: List[str] = []
    done = 0

    for ln in lines:
        if done >= OCR_LIMIT:
            keep.append(ln)
            continue

        try:
            obj = json.loads(ln)
        except Exception:
            keep.append(ln)
            continue

        raw_path = str(obj.get("raw_path") or "").strip()
        parsed_path = str(obj.get("parsed_path") or "").strip()
        meta_path = str(obj.get("meta_path") or "").strip()
        if not raw_path or not pathlib.Path(raw_path).exists():
            continue

        out_pdf = OUT_DIR / (pathlib.Path(raw_path).stem + ".ocr.pdf")

        # Run OCR
        try:
            subprocess.run(
                ["ocrmypdf", "--skip-text", "--deskew", "--optimize", "1", raw_path, str(out_pdf)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=300,
            )
        except Exception:
            # keep in queue
            keep.append(ln)
            continue

        # Extract text from OCR'd PDF
        try:
            txt = _extract_text_pypdf(str(out_pdf))
        except Exception:
            keep.append(ln)
            continue

        if txt and parsed_path:
            pathlib.Path(parsed_path).write_text(txt, encoding="utf-8", errors="ignore")

        if meta_path and pathlib.Path(meta_path).exists():
            try:
                m = _load_json(pathlib.Path(meta_path))
                m["ocr"] = {"status": "done", "ocr_pdf": str(out_pdf)}
                _write_json(pathlib.Path(meta_path), m)
            except Exception:
                pass

        done += 1

    # rewrite queue with remaining
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    QUEUE.write_text("\n".join(keep).strip() + ("\n" if keep else ""), encoding="utf-8")
    print(str(QUEUE))


if __name__ == "__main__":
    main()
