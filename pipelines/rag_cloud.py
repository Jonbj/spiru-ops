"""pipelines/rag_cloud.py — spiru-ops (documented version)

This file is part of the spiru-ops project, which builds a Spirulina/Arthrospira
knowledge base and a RAG Copilot.

The repository is intentionally documented with verbose comments so that:
- humans can quickly understand intent and invariants
- AI tools (agents, refactoring assistants) can reason about the code safely

This header is *documentation-only*; the runtime logic below is preserved.
"""

import json
import pathlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from sentence_transformers import SentenceTransformer

from pipelines.common import env, normalize_url
from pipelines.qdrant_rest import QdrantConfig, search

QDRANT_URL = env("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = env("QDRANT_COLLECTION", "docs_chunks")

# Retrieval embedding model (local)
SENTENCE_MODEL = env("SENTENCE_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# Generation (OpenAI) – raw HTTP (no SDK proxy surprises)
OPENAI_API_KEY = env("OPENAI_API_KEY", required=True)
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-4o-mini")


# Diversity controls (important!)
DIVERSIFY = env("COPILOT_DIVERSIFY", "1").strip() in ("1", "true", "TRUE", "yes", "YES")
MAX_PER_DOMAIN = int(env("COPILOT_MAX_PER_DOMAIN", 2))
OVERFETCH_MULT = int(env("COPILOT_OVERFETCH_MULT", 6))  # fetch topk*mult then prune
MIN_UNIQUE_DOMAINS = int(env("COPILOT_MIN_UNIQUE_DOMAINS", 4))

# Hard preference for Spirulina-centric content
MIN_SPIRULINA_SCORE = float(env("COPILOT_MIN_SPIRULINA_SCORE", "0.22"))


@dataclass
class Evidence:
    n: int
    title: str
    url: str
    domain: str
    focus: str
    source: str
    published_at: Optional[str]
    score: float
    spirulina_score: float
    spirulina_terms: List[str]
    doc_id: str
    boilerplate_share: float
    text: str


_embedder: Optional[SentenceTransformer] = None


def get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(SENTENCE_MODEL)
    return _embedder


def _domain(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def qdrant_search(vector: List[float], limit: int, focus: Optional[str] = None) -> Dict[str, Any]:
    cfg = QdrantConfig(url=QDRANT_URL, collection=QDRANT_COLLECTION)
    must = []
    if focus:
        must.append({"key": "focus", "match": {"value": focus}})
    # Prefer Spirulina-centric chunks by default; user can lower via env.
    if MIN_SPIRULINA_SCORE > 0:
        must.append({"key": "spirulina_score", "range": {"gte": MIN_SPIRULINA_SCORE}})
    qfilter = {"must": must} if must else None
    return search(cfg, vector=vector, limit=limit, qfilter=qfilter, timeout=30)


def _pick_url(payload: Dict[str, Any]) -> str:
    u = payload.get("url") or payload.get("source_url") or ""
    u2 = normalize_url(u) or u
    return u2


def _lexical_overlap_score(text: str, query: str) -> float:
    """Cheap reranking signal to downweight boilerplate and off-topic chunks."""
    t = (text or "").lower()
    q_terms = [w.lower() for w in (query or "").split() if len(w) > 3]
    q_terms = q_terms[:12]
    if not t or not q_terms:
        return 0.0
    hits = 0
    for w in q_terms:
        if w in t:
            hits += 1
    return hits / max(1, len(q_terms))


def _dedup_and_diversify(evs: List[Evidence], topk: int) -> List[Evidence]:
    if not DIVERSIFY:
        return evs[:topk]

    out: List[Evidence] = []
    by_domain: Dict[str, int] = {}
    seen_doc: set[str] = set()

    for e in evs:
        if not e.url:
            continue
        if e.doc_id and e.doc_id in seen_doc:
            continue
        if e.doc_id:
            seen_doc.add(e.doc_id)

        d = e.domain or _domain(e.url)
        if d:
            by_domain.setdefault(d, 0)
            if by_domain[d] >= MAX_PER_DOMAIN:
                continue

        out.append(e)
        if d:
            by_domain[d] += 1
        if len(out) >= topk:
            break

    # If diversity is too low, relax domain cap slightly to fill topk
    if len(out) < topk:
        for e in evs:
            if len(out) >= topk:
                break
            if not e.url or e.url in {x.url for x in out}:
                continue
            out.append(e)

    # renumber
    for i, e in enumerate(out, start=1):
        e.n = i

    return out


def retrieve(query: str, focus: Optional[str] = None, topk: int = 8) -> List[Evidence]:
    emb = get_embedder().encode([query], normalize_embeddings=True)[0].tolist()

    fetch_n = max(topk * OVERFETCH_MULT, topk)
    res = qdrant_search(emb, limit=fetch_n, focus=focus)

    raw: List[Evidence] = []
    for hit in (res.get("result") or []):
        payload = hit.get("payload") or {}
        url = _pick_url(payload)
        d = payload.get("domain") or _domain(url)
        spiru_score = float(payload.get("spirulina_score") or 0.0)
        bp_share = float(payload.get("text_boilerplate_share") or 0.0)
        raw.append(
            Evidence(
                n=0,
                title=(payload.get("title") or "(no title)"),
                url=url,
                domain=d,
                focus=(payload.get("focus") or ""),
                source=(payload.get("source") or ""),
                published_at=payload.get("published_at"),
                score=float(hit.get("score") or 0.0),
                spirulina_score=spiru_score,
                spirulina_terms=(payload.get("spirulina_terms") or [])[:12],
                doc_id=str(payload.get("doc_id") or ""),
                boilerplate_share=bp_share,
                text=(payload.get("text") or ""),
            )
        )

    # Rerank: combine vector score + lexical overlap + spirulina score; penalize boilerplate
    def _final_score(e: Evidence) -> float:
        lex = _lexical_overlap_score(e.text, query)
        # Qdrant score is already cosine similarity; keep it dominant.
        s = (0.78 * e.score) + (0.14 * lex) + (0.08 * e.spirulina_score)
        s *= max(0.55, 1.0 - min(0.6, e.boilerplate_share))
        return s

    raw_sorted = sorted(raw, key=_final_score, reverse=True)
    out = _dedup_and_diversify(raw_sorted, topk=topk)

    # If still low unique domains and we have room, try to force at least MIN_UNIQUE_DOMAINS
    if DIVERSIFY and len(out) >= 3:
        uniq = {e.domain for e in out if e.domain}
        if len(uniq) < MIN_UNIQUE_DOMAINS:
            # greedily add new domains from tail if any slot remains (already done by relax, but keep)
            pass

    return out


def load_living_spec_excerpt(max_chars: int = 8000) -> str:
    spec_path = pathlib.Path(env("ARTIFACTS_DIR", "storage/artifacts")) / "living_spec.md"
    if not spec_path.exists():
        return ""
    txt = spec_path.read_text(encoding="utf-8", errors="ignore")
    return txt[-max_chars:] if len(txt) > max_chars else txt


def _smart_snippet(text: str, query: str, n: int = 1200) -> str:
    t = " ".join((text or "").split())
    if not t:
        return ""
    q_terms = [w.lower() for w in (query or "").split() if len(w) > 3][:10]
    tl = t.lower()
    for term in q_terms:
        pos = tl.find(term)
        if pos != -1:
            start = max(0, pos - 260)
            return t[start : start + n]
    return t[:n]


def build_evidence_block(evidence: List[Evidence], query: str, max_chars: int = 18000, per_item_chars: int = 2200) -> str:
    blocks: List[str] = []
    used = 0
    for e in evidence:
        excerpt = _smart_snippet(e.text or "", query, n=per_item_chars)
        if len(excerpt) > per_item_chars:
            excerpt = excerpt[:per_item_chars].rstrip() + "…"

        b = (
            f"[{e.n}] {e.title}\n"
            f"URL: {e.url}\n"
            f"Domain: {e.domain}\n"
            f"Focus: {e.focus}\n"
            f"Source: {e.source}\n"
            f"Published_at: {e.published_at}\n"
            f"Score: {e.score:.3f}\n"
            f"Spirulina_score: {e.spirulina_score:.3f}\n"
            f"Spirulina_terms: {', '.join(e.spirulina_terms or [])}\n"
            f"Excerpt:\n{excerpt}\n"
        )
        if used + len(b) > max_chars:
            break
        blocks.append(b)
        used += len(b)
    return "\n\n".join(blocks)


def openai_chat(system: str, user: str) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    payload = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    return (data["choices"][0]["message"]["content"] or "").strip()


def ask_copilot(question: str, focus: Optional[str] = None) -> Dict[str, Any]:
    topk = int(env("COPILOT_TOPK", 10))
    max_ctx = int(env("COPILOT_MAX_CONTEXT_CHARS", 18000))

    evidence = retrieve(question, focus=focus, topk=topk)
    evidence_block = build_evidence_block(evidence, query=question, max_chars=max_ctx)
    spec_excerpt = load_living_spec_excerpt(max_chars=8000)

    system = pathlib.Path("prompts/copilot_system.md").read_text(encoding="utf-8")
    user_tmpl = pathlib.Path("prompts/copilot_user_template.md").read_text(encoding="utf-8")
    user = user_tmpl.format(
        living_spec_excerpt=spec_excerpt,
        question=question,
        evidence=evidence_block,
    )

    answer = openai_chat(system=system, user=user)

    return {
        "answer": answer,
        "evidence_used": [
            {
                "n": e.n,
                "title": e.title,
                "url": e.url,
                "domain": e.domain,
                "focus": e.focus,
                "source": e.source,
                "published_at": e.published_at,
                "score": e.score,
                "spirulina_score": e.spirulina_score,
                "doc_id": e.doc_id,
            }
            for e in evidence
        ],
    }


def append_to_living_spec(decision_block_md: str, question: Optional[str] = None) -> None:
    artifacts = pathlib.Path(env("ARTIFACTS_DIR", "storage/artifacts"))
    artifacts.mkdir(parents=True, exist_ok=True)
    spec_path = artifacts / "living_spec.md"
    spec = spec_path.read_text(encoding="utf-8", errors="ignore") if spec_path.exists() else ""

    header = "\n\n---\n## Copilot session\n"
    if question:
        header += f"\n**Question:** {question.strip()}\n"

    spec += header + "\n" + decision_block_md.strip() + "\n"
    spec_path.write_text(spec, encoding="utf-8")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("question", type=str)
    ap.add_argument("--focus", type=str, default=None)
    ap.add_argument("--save", action="store_true", help="append answer to living_spec")
    args = ap.parse_args()

    out = ask_copilot(args.question, focus=args.focus)
    print(out["answer"])
    if args.save:
        append_to_living_spec(out["answer"], question=args.question)