"""pipelines/query.py — spiru-ops (documented version)

This file is part of the spiru-ops project, which builds a Spirulina/Arthrospira
knowledge base and a RAG Copilot.

The repository is intentionally documented with verbose comments so that:
- humans can quickly understand intent and invariants
- AI tools (agents, refactoring assistants) can reason about the code safely

This header is *documentation-only*; the runtime logic below is preserved.
"""

#!/usr/bin/env python3
import argparse
import pathlib
from typing import Optional

from pipelines.common import env
from pipelines.qdrant_rest import QdrantConfig, search, hybrid_query

_BGE_M3_NAMES = {"baai/bge-m3", "bge-m3"}


def smart_snippet(text: str, query: str, n: int = 900) -> str:
    """Return a snippet centered around first matching query term, fallback to start."""
    t = " ".join((text or "").split())
    if not t:
        return ""
    q_terms = [w.lower() for w in (query or "").split() if len(w) > 3][:8]
    tl = t.lower()
    for term in q_terms:
        pos = tl.find(term)
        if pos != -1:
            start = max(0, pos - 250)
            return t[start : start + n]
    return t[:n]


def pick_url(payload: dict) -> Optional[str]:
    # Prefer payload["url"] (your index puts "url"), else older keys.
    return payload.get("url") or payload.get("source_url") or payload.get("pdf_url")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", type=str)
    ap.add_argument("--focus", type=str, default=None)
    ap.add_argument("--topk", type=int, default=8)
    ap.add_argument("--export", type=str, default=None)
    args = ap.parse_args()

    qdrant_url = env("QDRANT_URL", required=True)
    collection = env("QDRANT_COLLECTION", "docs_chunks")
    model_name = env("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    is_bge_m3 = model_name.lower() in _BGE_M3_NAMES

    cfg = QdrantConfig(url=qdrant_url, collection=collection)
    qfilter = None
    if args.focus:
        qfilter = {"must": [{"key": "focus", "match": {"value": args.focus}}]}

    if is_bge_m3:
        from FlagEmbedding import BGEM3FlagModel  # type: ignore
        model = BGEM3FlagModel(model_name, use_fp16=True)
        enc = model.encode([args.question], return_dense=True, return_sparse=True, return_colbert_vecs=False)
        dense_vec = enc["dense_vecs"][0].tolist()
        svec = enc["lexical_weights"][0]
        sparse_indices = [int(k) for k in svec.keys()]
        sparse_values = [float(v) for v in svec.values()]
        res = hybrid_query(
            cfg,
            dense_vector=dense_vec,
            sparse_indices=sparse_indices,
            sparse_values=sparse_values,
            limit=args.topk * 3,
            qfilter=qfilter,
            prefetch_limit=args.topk * 6,
        )
    else:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(model_name)
        qvec = model.encode([args.question], normalize_embeddings=True)[0].tolist()
        res = search(cfg, vector=qvec, limit=args.topk * 3, qfilter=qfilter, timeout=30)

    hits = res.get("result") or []

    lines = []
    lines.append(f"# Query\n\n**Q:** {args.question}\n")
    if args.focus:
        lines.append(f"**Focus filter:** `{args.focus}`\n")
    lines.append(f"**TopK requested:** {args.topk}\n")
    lines.append(f"**Embed model:** `{model_name}`\n")

    seen_urls = set()
    out_n = 0

    for h in hits:
        p = h.get("payload") or {}

        url = pick_url(p)
        title = p.get("title") or "(no title)"
        focus = p.get("focus") or "unknown"
        score = h.get("score")

        # useful debug metadata
        published = p.get("published_at")
        source = p.get("source")
        doc_id = p.get("doc_id")
        chunk_i = p.get("chunk_i")

        if url:
            if url in seen_urls:
                continue
            seen_urls.add(url)

        text = (p.get("text") or "").strip()
        snippet = smart_snippet(text, args.question, 900)

        out_n += 1
        lines.append(f"\n## {out_n}) {title}")
        lines.append(f"- score: {score:.4f}" if score is not None else "- score: n/a")
        lines.append(f"- focus: {focus}")
        lines.append(f"- url: {url}")
        lines.append(f"- published_at: {published}")
        lines.append(f"- source: {source} | doc_id: {doc_id} | chunk: {chunk_i}")
        if snippet:
            lines.append(f"\n> {snippet}...\n")
        else:
            lines.append("\n> (no text in payload)\n")

        if out_n >= args.topk:
            break

    if out_n == 0:
        lines.append("\n_No hits found (or all were deduplicated)._")

    out = "\n".join(lines)
    print(out)

    if args.export:
        pathlib.Path(args.export).write_text(out, encoding="utf-8")
        print(f"\nExported to {args.export}")


if __name__ == "__main__":
    main()