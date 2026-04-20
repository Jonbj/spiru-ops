#!/usr/bin/env python3
"""
scripts/ingest_local_pdfs.py — Index locally-downloaded PDF files directly into Qdrant.

Usage:
    python scripts/ingest_local_pdfs.py [pdf_file ...]

If no arguments are given, processes all *.pdf in the project root.
Each PDF is parsed, chunked, embedded (BGE-M3 or configured model), and
upserted into the same Qdrant collection used by the main pipeline.

Regulatory documents typically score low on spirulina relevance, so
spirulina_score filtering is bypassed here (is_spirulina is forced True).
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

# Bootstrap: load .env and add project root to sys.path
ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
_env_file = ROOT_DIR / ".env"
if _env_file.exists():
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
sys.path.insert(0, str(ROOT_DIR))

from pipelines.common import env, chunk_text, safe_id
from pipelines.qdrant_rest import (
    QdrantConfig,
    ensure_collection,
    upsert_points,
    ensure_collection_hybrid,
    upsert_points_hybrid,
)
from pipelines.relevance import compute_spirulina_relevance

# ── Config ────────────────────────────────────────────────────────────────────
QDRANT_URL   = env("QDRANT_URL", required=True)
COLLECTION   = env("QDRANT_COLLECTION", "docs_chunks_v2")
EMBED_MODEL  = env("EMBED_MODEL", "BAAI/bge-m3")
CHUNK_MAX    = int(env("CHUNK_MAX_CHARS", 2200))
CHUNK_OVERLAP = int(env("CHUNK_OVERLAP", 240))
EMBED_DEVICE = env("EMBED_DEVICE", "").strip().lower() or None
UPSERT_BATCH = int(env("QDRANT_UPSERT_BATCH", 64))
MAX_CHUNKS   = int(env("MAX_CHUNKS_PER_DOC", "50"))

_BGE_M3_NAMES = {"baai/bge-m3", "bge-m3"}
IS_BGE_M3 = EMBED_MODEL.lower() in _BGE_M3_NAMES

# ── Metadata for known regulatory files ──────────────────────────────────────
_KNOWN: dict[str, dict] = {
    "CELEX_32018R0848_IT_TXT": {
        "url":      "https://eur-lex.europa.eu/legal-content/IT/TXT/?uri=CELEX:32018R0848",
        "title":    "Regolamento (UE) 2018/848 — Produzione biologica e etichettatura prodotti biologici",
        "focus":    "organic_bio_certification",
        "doc_type": "regulatory_doc",
        "source":   "EUR-Lex (manual)",
    },
    "eu_reg_2021_1165_annex_I": {
        "url":      "https://eur-lex.europa.eu/legal-content/IT/TXT/?uri=CELEX:32021R1165",
        "title":    "Reg. delegato (UE) 2021/1165 Allegato I — Prodotti e sostanze autorizzati per produzione biologica",
        "focus":    "organic_bio_certification",
        "doc_type": "regulatory_doc",
        "source":   "EUR-Lex (manual)",
    },
    "JRC145765_01": {
        "url":      "https://publications.jrc.ec.europa.eu/repository/handle/JRC145765",
        "title":    "JRC 2023 — Algae for food and feed in the EU: state of play and future perspectives",
        "focus":    "organic_bio_certification",
        "doc_type": "report",
        "source":   "JRC Publications (manual)",
    },
}


def _extract_text_pypdf(path: pathlib.Path) -> str:
    try:
        import pypdf
        reader = pypdf.PdfReader(str(path))
        parts = []
        for page in reader.pages:
            t = page.extract_text() or ""
            parts.append(t)
        return "\n\n".join(parts)
    except Exception as e:
        print(f"  [pypdf] failed: {e}", flush=True)
        return ""


def _extract_text_unstructured(path: pathlib.Path) -> str:
    """Try Unstructured local API (port 8000) for better PDF parsing."""
    try:
        import requests
        unstructured_url = env("UNSTRUCTURED_URL", "http://localhost:8000")
        with open(path, "rb") as fh:
            r = requests.post(
                f"{unstructured_url}/general/v0/general",
                files={"files": (path.name, fh, "application/pdf")},
                data={"strategy": "hi_res"},
                timeout=120,
            )
        if r.ok:
            elements = r.json()
            return "\n\n".join(
                e.get("text", "") for e in elements if e.get("text", "").strip()
            )
    except Exception as e:
        print(f"  [unstructured] failed: {e}", flush=True)
    return ""


def extract_text(path: pathlib.Path) -> str:
    print(f"  Parsing {path.name} ...", flush=True)
    # Try unstructured first (better layout handling), fall back to pypdf
    text = _extract_text_unstructured(path)
    if len(text) > 200:
        print(f"  → unstructured: {len(text)} chars", flush=True)
        return text
    text = _extract_text_pypdf(path)
    print(f"  → pypdf: {len(text)} chars", flush=True)
    return text


def get_meta(path: pathlib.Path) -> dict:
    stem = path.stem
    known = _KNOWN.get(stem)
    if known:
        return dict(known)
    # Fallback: derive from filename
    return {
        "url":      f"file://{path.resolve()}",
        "title":    stem.replace("_", " ").replace("-", " "),
        "focus":    "organic_bio_certification",
        "doc_type": "regulatory_doc",
        "source":   "manual",
    }


def process_pdf(path: pathlib.Path, model) -> int:
    meta = get_meta(path)
    print(f"\n[{path.name}]", flush=True)
    print(f"  url:   {meta['url']}", flush=True)
    print(f"  title: {meta['title']}", flush=True)

    text = extract_text(path)
    if not text.strip():
        print("  SKIP: no text extracted", flush=True)
        return 0

    # Compute spirulina relevance (informational only — not used as filter here)
    rel = compute_spirulina_relevance(title=meta["title"], text=text[:3000])
    print(f"  spirulina_score: {rel.score:.3f} (terms: {rel.positive_terms})", flush=True)

    chunks = chunk_text(text, CHUNK_MAX, CHUNK_OVERLAP)
    if MAX_CHUNKS > 0 and len(chunks) > MAX_CHUNKS:
        chunks = chunks[:MAX_CHUNKS]
    print(f"  chunks: {len(chunks)}", flush=True)

    url = meta["url"]
    content_hash = hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()[:16]
    doc_id = safe_id(f"doc|{url}|{content_hash}")[:16]
    base = int(safe_id(url + content_hash), 16) % (10**12)
    now_iso = datetime.now(timezone.utc).isoformat()

    qcfg = QdrantConfig(url=QDRANT_URL, collection=COLLECTION)

    def _payload(ch: str, i: int) -> dict:
        return {
            "url":                  url,
            "source_url":           url,
            "domain":               (urlparse(url).netloc or "").lower(),
            "doc_id":               doc_id,
            "focus":                meta.get("focus"),
            "source":               meta.get("source"),
            "title":                meta.get("title"),
            "published_at":         None,
            "discovered_at":        now_iso,
            "fetched_at":           now_iso,
            "content_hash":         content_hash,
            "spirulina_score":      rel.score,
            "spirulina_terms":      rel.positive_terms[:12],
            "is_spirulina":         True,   # force-include regulatory docs
            "doc_type":             meta.get("doc_type", "regulatory_doc"),
            "text_boilerplate_share": 0.0,
            "chunk_i":              i,
            "chunk_idx":            i,
            "text":                 ch[:6000],
        }

    points_upserted = 0
    batch: list[dict] = []

    if IS_BGE_M3:
        enc = model.encode(
            chunks,
            batch_size=32,
            max_length=512,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )
        for i, (ch, dvec, svec) in enumerate(zip(chunks, enc["dense_vecs"], enc["lexical_weights"])):
            batch.append({
                "id": base + i,
                "vector": {
                    "dense":  dvec.tolist(),
                    "sparse": {"indices": [int(k) for k in svec], "values": [float(v) for v in svec.values()]},
                },
                "payload": _payload(ch, i),
            })
            if len(batch) >= UPSERT_BATCH:
                upsert_points_hybrid(qcfg, batch, wait=True, timeout=60)
                points_upserted += len(batch)
                batch = []
    else:
        vectors = model.encode(chunks, normalize_embeddings=True)
        for i, (ch, vec) in enumerate(zip(chunks, vectors)):
            batch.append({"id": base + i, "vector": vec.tolist(), "payload": _payload(ch, i)})
            if len(batch) >= UPSERT_BATCH:
                upsert_points(qcfg, batch, wait=True, timeout=60)
                points_upserted += len(batch)
                batch = []

    if batch:
        if IS_BGE_M3:
            upsert_points_hybrid(qcfg, batch, wait=True, timeout=60)
        else:
            upsert_points(qcfg, batch, wait=True, timeout=60)
        points_upserted += len(batch)

    print(f"  upserted: {points_upserted} chunks", flush=True)
    return points_upserted


def main() -> None:
    if len(sys.argv) > 1:
        pdf_paths = [pathlib.Path(p) for p in sys.argv[1:]]
    else:
        pdf_paths = sorted(ROOT_DIR.glob("*.pdf"))

    if not pdf_paths:
        print("No PDF files found.", flush=True)
        sys.exit(1)

    print(f"[ingest_local_pdfs] {len(pdf_paths)} file(s) — model={EMBED_MODEL} collection={COLLECTION}", flush=True)

    qcfg = QdrantConfig(url=QDRANT_URL, collection=COLLECTION)
    if IS_BGE_M3:
        from FlagEmbedding import BGEM3FlagModel  # type: ignore
        model = BGEM3FlagModel(
            EMBED_MODEL,
            use_fp16=(EMBED_DEVICE != "cpu"),
            devices=[EMBED_DEVICE] if EMBED_DEVICE else None,
        )
        ensure_collection_hybrid(qcfg, dense_dim=1024)
    else:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(EMBED_MODEL, device=EMBED_DEVICE)
        dim = model.get_sentence_embedding_dimension()
        ensure_collection(qcfg, dim=dim)

    total = 0
    for p in pdf_paths:
        if not p.exists():
            print(f"SKIP: {p} not found", flush=True)
            continue
        total += process_pdf(p, model)

    print(f"\n[ingest_local_pdfs] Done — {total} chunks upserted total.", flush=True)


if __name__ == "__main__":
    main()
