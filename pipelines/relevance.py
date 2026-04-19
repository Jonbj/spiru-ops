"""Spirulina/Arthrospira-centric relevance scoring.

Design goals:
- cheap (no LLM / no extra services)
- explainable (matched terms)
- stable across HTML/PDF noise

Score is in [0, 1].

Optional LLM augmentation (RELEVANCE_LLM_ENABLE=1):
- llm_spirulina_score() calls a local OpenAI-compatible server (e.g. llama-server)
- Uses lazy imports so this file stays stdlib-only when LLM is disabled
- Intended for borderline documents where keyword scoring is uncertain
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class SpirulinaRelevance:
    score: float
    positive_terms: list[str]
    negative_terms: list[str]
    reasons: list[str]


# Core entity terms (high weight)
_POS_CORE = [
    ("spirulina", 3.0),
    ("arthrospira", 3.0),
    ("limnospira", 3.0),
    ("a. platensis", 2.4),
    ("arthrospira platensis", 2.4),
    ("arthrospira maxima", 2.2),
    ("limnospira maxima", 2.2),
]

# Spirulina-adjacent terms (medium weight)
_POS_CONTEXT = [
    ("zarrouk", 1.8),
    ("zarrouk medium", 2.2),
    ("sodium bicarbonate", 1.2),
    ("bicarbonate", 0.8),
    ("alkaline", 0.6),
    ("phycocyanin", 1.2),
    ("c-phycocyanin", 1.2),
    ("cyanobacteria", 0.6),
]

# Common confounders (penalize when core is missing)
_NEG_OTHER_ALGAE = [
    ("chlorella", 1.8),
    ("dunaliella", 1.6),
    ("haematococcus", 1.6),
    ("nannochloropsis", 1.6),
    ("scenedesmus", 1.6),
    ("spirulina" + " platensis", 0.0),  # placeholder to avoid accidental partial penalty
]


def _norm(s: str) -> str:
    s = (s or "")
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _count_term(blob: str, term: str) -> int:
    # word-boundary-ish for safety
    t = re.escape(_norm(term))
    pat = re.compile(r"\b" + t + r"\b", flags=re.IGNORECASE)
    return len(pat.findall(blob))


def compute_spirulina_relevance(*, url: str = "", title: str = "", text: str = "") -> SpirulinaRelevance:
    """Compute a Spirulina-centric relevance score.

    Heuristic:
    - treat URL+title as stronger signals than body text
    - apply penalty for other-algae terms when core entity terms are absent
    """
    url_n = _norm(url)
    title_n = _norm(title)
    text_n = _norm(text)

    blob_body = text_n
    blob_head = (title_n + " " + url_n).strip()
    blob_all = (blob_head + " " + blob_body).strip()

    pos_terms: list[str] = []
    neg_terms: list[str] = []
    reasons: list[str] = []

    pos_weight = 0.0
    core_hits = 0

    for term, w in _POS_CORE:
        c = _count_term(blob_all, term)
        if c:
            core_hits += c
            pos_terms.append(term)
            # URL/title hits count more
            c_head = _count_term(blob_head, term)
            c_body = max(0, c - c_head)
            pos_weight += (math.log1p(c_head) * (w + 0.8)) + (math.log1p(c_body) * w)

    for term, w in _POS_CONTEXT:
        c = _count_term(blob_all, term)
        if c:
            pos_terms.append(term)
            c_head = _count_term(blob_head, term)
            c_body = max(0, c - c_head)
            pos_weight += (math.log1p(c_head) * (w + 0.4)) + (math.log1p(c_body) * w)

    neg_weight = 0.0
    for term, w in _NEG_OTHER_ALGAE:
        if not term or w <= 0:
            continue
        c = _count_term(blob_all, term)
        if c:
            neg_terms.append(term)
            neg_weight += math.log1p(c) * w

    # Convert weights to [0,1]
    # Higher pos_weight quickly saturates.
    base = 1.0 - math.exp(-(pos_weight / 4.0))

    # Penalize only if we don't see the core entity.
    penalty = 0.0
    if core_hits == 0 and neg_weight > 0:
        penalty = min(0.65, 1.0 - math.exp(-(neg_weight / 6.0)))
        reasons.append("confounders_without_core")

    # If completely empty signals, keep it near 0.
    if pos_weight <= 0.2 and core_hits == 0:
        reasons.append("no_spirulina_signals")

    score = max(0.0, min(1.0, base * (1.0 - penalty)))
    return SpirulinaRelevance(
        score=score,
        positive_terms=sorted(set(pos_terms)),
        negative_terms=sorted(set(neg_terms)),
        reasons=reasons,
    )


def is_spirulina_centric(score: float, threshold: float = 0.30) -> bool:
    return float(score or 0.0) >= float(threshold)


def llm_spirulina_score(title: str, text_preview: str) -> Optional[float]:
    """Call a local OpenAI-compatible LLM to score Spirulina relevance.

    Activated only when RELEVANCE_LLM_ENABLE=1.
    Uses lazy imports to keep this module stdlib-only when disabled.

    Returns a float in [0, 1], or None on error/timeout/disabled.

    Designed for borderline documents where keyword scoring is uncertain
    (typically score in 0.05–0.50). The calling code decides whether to blend
    this with the keyword score.
    """
    import json as _json
    import os as _os
    import re as _re

    if _os.environ.get("RELEVANCE_LLM_ENABLE", "0").strip() not in ("1", "true"):
        return None

    try:
        import requests as _req
    except ImportError:
        return None

    ollama_api_key = _os.environ.get("OLLAMA_API_KEY", "").strip()
    url = (
        _os.environ.get("RELEVANCE_LLM_URL")
        or _os.environ.get("OLLAMA_URL", "https://ollama.com" if ollama_api_key else "http://127.0.0.1:8080")
    ).rstrip("/")
    model = (
        _os.environ.get("RELEVANCE_LLM_MODEL")
        or _os.environ.get("OLLAMA_MODEL", "local")
    )
    timeout = int(_os.environ.get("RELEVANCE_LLM_TIMEOUT", "45"))

    system_prompt = (
        "You are a strict relevance classifier for a knowledge base about "
        "Spirulina (Arthrospira platensis/maxima) cultivation and production.\n"
        "Score the document's relevance to Spirulina cultivation on a 0.0–1.0 scale:\n"
        "  1.0 = exclusively about Spirulina/Arthrospira cultivation, biology, or production\n"
        "  0.7 = Spirulina is the main subject alongside closely related topics\n"
        "  0.4 = Spirulina discussed among other microalgae\n"
        "  0.1 = Spirulina briefly mentioned or tangentially related\n"
        "  0.0 = not about Spirulina at all\n"
        'Respond with ONLY valid JSON on a single line, no other text: {"score": 0.0}'
    )
    user_prompt = (
        f"Title: {(title or '').strip()[:300]}\n"
        f"Text excerpt: {(text_preview or '').strip()[:700]}"
    )

    try:
        if ollama_api_key:
            r = _req.post(
                f"{url}/api/chat",
                headers={"Authorization": f"Bearer {ollama_api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                },
                timeout=timeout,
            )
            r.raise_for_status()
            j = r.json()
            try:
                from pipelines.common import llm_log_call
                llm_log_call("relevance", model, j.get("prompt_eval_count", 0), j.get("eval_count", 0))
            except Exception:
                pass
            content = (j["message"]["content"] or "").strip()
        else:
            r = _req.post(
                f"{url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": 350,
                    "temperature": 0.0,
                },
                timeout=timeout,
            )
            r.raise_for_status()
            j = r.json()
            try:
                from pipelines.common import llm_log_call
                usage = j.get("usage") or {}
                llm_log_call("relevance", model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
            except Exception:
                pass
            msg = j["choices"][0]["message"]
            content = (msg.get("content") or "").strip()
            if not content:
                content = (msg.get("reasoning_content") or "").strip()

        content = _re.sub(r"<think>.*?</think>", "", content, flags=_re.DOTALL).strip()

        # Extract score from JSON — be lenient with surrounding whitespace/text
        m = _re.search(r'\{[^{}]*"score"\s*:\s*([\d.]+)[^{}]*\}', content)
        if m:
            return float(min(1.0, max(0.0, float(m.group(1)))))
    except Exception:
        pass

    return None
