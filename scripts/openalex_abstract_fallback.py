"""scripts/openalex_abstract_fallback.py

For parsed docs with very little text (<800 chars) that have a DOI,
queries the OpenAlex API for the abstract and saves it as the parsed text.
Then re-computes spirulina_score and marks for re-indexing.

Only processes docs where the abstract would be a meaningful improvement.
"""

import json
import pathlib
import sys
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pipelines.common import env
from pipelines.relevance import compute_spirulina_relevance

PARSED_DIR = pathlib.Path(env("PARSED_DIR", "storage/parsed"))
OPENALEX_EMAIL = env("OPENALEX_EMAIL", env("CROSSREF_MAILTO", "stefano.delgobbo@gmail.com"))
MIN_ABSTRACT_CHARS = 200
MAX_EXISTING_CHARS = 800

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": env("USER_AGENT", "spiru-ops-bot/0.3")})


def fetch_abstract(doi: str) -> str | None:
    """Query OpenAlex works API by DOI, return abstract or None."""
    doi_clean = doi.strip().lstrip("https://doi.org/").lstrip("http://dx.doi.org/")
    url = f"https://api.openalex.org/works/doi:{doi_clean}"
    params = {"mailto": OPENALEX_EMAIL, "select": "title,abstract_inverted_index,publication_date"}
    try:
        r = SESSION.get(url, params=params, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()

        # OpenAlex stores abstracts as inverted index {word: [positions]}
        inv = data.get("abstract_inverted_index") or {}
        if not inv:
            return None

        # Reconstruct abstract from inverted index
        max_pos = max(pos for positions in inv.values() for pos in positions)
        words = [""] * (max_pos + 1)
        for word, positions in inv.items():
            for pos in positions:
                words[pos] = word
        abstract = " ".join(w for w in words if w).strip()
        return abstract if len(abstract) >= MIN_ABSTRACT_CHARS else None
    except Exception:
        return None


def main():
    meta_files = sorted(PARSED_DIR.glob("*.meta.json"))

    candidates = []
    for f in meta_files:
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
            doi = str(m.get("doi") or "").strip()
            if not doi:
                continue
            txt_path = PARSED_DIR / f.name.replace(".meta.json", ".txt")
            chars = len(txt_path.read_text(errors="ignore")) if txt_path.exists() else 0
            if chars < MAX_EXISTING_CHARS:
                candidates.append((f, m, txt_path, chars))
        except Exception:
            continue

    print(f"Docs with DOI and text < {MAX_EXISTING_CHARS} chars: {len(candidates)}")
    enriched = 0
    not_found = 0

    for meta_path, meta, txt_path, old_chars in candidates:
        doi = str(meta.get("doi") or "").strip()
        abstract = fetch_abstract(doi)
        time.sleep(0.15)  # OpenAlex polite: ~7 req/s

        if not abstract:
            not_found += 1
            continue

        # Build enriched text: title + abstract
        title = str(meta.get("title") or "").strip()
        text = f"{title}\n\n{abstract}" if title else abstract

        # Re-score spirulina relevance
        rel = compute_spirulina_relevance(
            url=str(meta.get("url") or ""),
            title=title,
            text=text,
        )

        # Update parsed text
        try:
            txt_path.write_text(text, encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"  [write error] {txt_path.name}: {e}")
            continue

        # Update meta
        meta["spirulina_score"] = round(float(rel.score), 4)
        meta["spirulina_terms"] = rel.positive_terms[:20]
        meta["spirulina_reasons"] = rel.reasons[:10]
        meta["abstract_fallback"] = True
        meta.pop("short_text", None)

        try:
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"  [meta write error] {meta_path.name}: {e}")
            continue

        enriched += 1
        print(f"  [{enriched}] {old_chars}→{len(text)} chars  score={rel.score:.3f}  doi={doi[:50]}")

    print(f"\nDone.")
    print(f"  Enriched with abstract: {enriched}")
    print(f"  Abstract not found    : {not_found}")
    if enriched > 0:
        print(f"\n  ⚠ Run 'python -m pipelines.index' to re-index enriched documents.")


if __name__ == "__main__":
    main()
