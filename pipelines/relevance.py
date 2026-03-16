"""Spirulina/Arthrospira-centric relevance scoring.

Design goals:
- cheap (no LLM / no extra services)
- explainable (matched terms)
- stable across HTML/PDF noise

Score is in [0, 1].
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable


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
