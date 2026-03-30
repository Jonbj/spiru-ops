#!/usr/bin/env python3
"""Write per-run competitor deltas into Obsidian inbox.

Best-effort helper invoked at the end of daily.sh.
Reads the current RUN_ID artifacts and writes a markdown file to:
  obsidian-vault/progetto/competitors/inbox/{RUN_ID}.md

It never modifies competitor-map.md; it only reads it (and the competitor registry)
to distinguish:
- new competitors emerged
- new evidence on known competitors
- doubtful/unverified claims
"""

import json
import pathlib
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipelines.common import env, run_id

VAULT_DIR = pathlib.Path(env("OBSIDIAN_VAULT_DIR", str(ROOT / "obsidian-vault")))
STATE_DIR = pathlib.Path(env("STATE_DIR", str(ROOT / "storage/state")))
COMP_MAP_PATH = pathlib.Path(
    env(
        "COMPETITOR_MAP_PATH",
        str(VAULT_DIR / "progetto" / "competitors" / "competitor-map.md"),
    )
)
COMP_REGISTRY_PATH = pathlib.Path(
    env("COMPETITOR_REGISTRY_PATH", str(ROOT / "configs" / "competitors.yaml"))
)
INBOX_DIR = VAULT_DIR / "progetto" / "competitors" / "inbox"

FOCUS_NAME = "competitor_pricing_italy_eu"
MAX_EVIDENCE = 12
LOW_SCORE_THRESHOLD = 0.45
LOW_TEXT_THRESHOLD = 2000

SKIP_DOMAINS = {
    "consent.yahoo.com",
    "fortunebusinessinsights.com",
    "www.fortunebusinessinsights.com",
    "industrystatsreport.com",
}

KNOWN_NAME_HINTS = {
    "sant'egle": "Sant'Egle",
    "santegle": "Sant'Egle",
    "apulia kundi": "Apulia Kundi",
    "apuliakundi": "Apulia Kundi",
    "biospira": "Biospira Srl",
    "farmodena": "Farmodena",
    "spireat": "Spireat",
    "spiripau": "Spiripau",
    "salera": "Salera / Spirulina Bio Salera",
    "livegreen": "Livegreen",
    "ecospirulina": "Ecospirulina",
    "spirù": "Spirù / Ethos",
    "spiru": "Spirù / Ethos",
    "ethos": "Spirù / Ethos",
    "vehgro": "Vehgro",
    "ekowarehouse": "Ekowarehouse",
    "europages": "Europages suppliers",
    "becagli": "Severino Becagli / Spirulina Becagli",
    "spirulinabecagli": "Severino Becagli / Spirulina Becagli",
    "archimede ricerche": "Archimede Ricerche",
    "alghitaly": "AlghItaly",
}


def _load_json(path: pathlib.Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_known_competitors() -> dict[str, str]:
    known = {}
    if COMP_REGISTRY_PATH.exists():
        try:
            data = yaml.safe_load(COMP_REGISTRY_PATH.read_text(encoding="utf-8")) or {}
            for comp in data.get("competitors", []):
                canonical = (comp.get("canonical_name") or "").strip()
                if not canonical:
                    continue
                known[canonical.lower()] = canonical
                for alias in comp.get("aliases", []) or []:
                    alias = (alias or "").strip()
                    if alias:
                        known[alias.lower()] = canonical
                dom = (comp.get("domain") or "").strip().lower()
                if dom:
                    known[dom] = canonical
        except Exception:
            pass
    if not known and COMP_MAP_PATH.exists():
        text = COMP_MAP_PATH.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if not line.startswith("|"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                continue
            name = parts[1].strip()
            if not name or name.lower() == "nome" or set(name) == {"-"}:
                continue
            known[name.lower()] = name
    return known


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _infer_name(title: str, url: str, known: dict[str, str]) -> str | None:
    blob = f"{title} {url}".lower()
    for hint, canonical in KNOWN_NAME_HINTS.items():
        if hint in blob:
            return canonical
    for key, canonical in known.items():
        if key in blob:
            return canonical
    dom = _domain(url)
    if dom:
        host = dom.replace("www.", "").split(".")[0]
        if host and host not in {"shop", "store", "blog", "products", "product"}:
            return host.replace("-", " ").title()
    return None


def _read_snippet(parsed_path: str | None) -> str:
    if not parsed_path:
        return ""
    p = ROOT / parsed_path
    if not p.exists():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text[:280]


def _pillar_guess(title: str, url: str, snippet: str) -> list[str]:
    blob = f"{title} {url} {snippet}".lower()
    pillars = []
    checks = {
        "offering": ["capsule", "compresse", "polvere", "powder", "extract", "estratto"],
        "positioning": ["bio", "organic", "premium", "made in italy", "tracciabil", "quality"],
        "channels": ["wholesale", "private label", "distribut", "farmacia", "erborister", "shop"],
        "pricing": ["price", "prezzo", "eur", "€", "kg"],
        "operations": ["impianto", "serra", "facility", "production", "coltivazione"],
        "strategy": ["partnership", "launch", "funding", "collaboration", "press release"],
        "financials": ["bilancio", "ricavi", "revenue", "turnover", "utile", "employees"],
    }
    for pillar, terms in checks.items():
        if any(t in blob for t in terms):
            pillars.append(pillar)
    return pillars


def main() -> None:
    rid = run_id()
    ingested_path = STATE_DIR / f"{rid}_ingested.json"
    data = _load_json(ingested_path)
    known = _load_known_competitors()
    INBOX_DIR.mkdir(parents=True, exist_ok=True)

    run_date = datetime.now(timezone.utc).date().isoformat()
    out_path = INBOX_DIR / f"{rid}.md"

    items = []
    if data:
        items = [x for x in data.get("ingested", []) if x.get("focus") == FOCUS_NAME]

    new_competitors = []
    new_evidence = []
    doubtful = []
    non_useful = []

    for item in items:
        title = item.get("title") or ""
        url = item.get("url") or ""
        dom = _domain(url)
        score = float(item.get("spirulina_score") or 0)
        raw_chars = int(((item.get("text_stats") or {}).get("raw_chars") or 0))
        snippet = _read_snippet(item.get("parsed_path"))
        inferred = _infer_name(title, url, known)

        if dom in SKIP_DOMAINS:
            non_useful.append((title, url, "domain blocklist / market report or low value source"))
            continue

        if score < LOW_SCORE_THRESHOLD or raw_chars < LOW_TEXT_THRESHOLD:
            doubtful.append(
                {
                    "name": inferred or title,
                    "title": title,
                    "url": url,
                    "reason": f"low-confidence signal (score={score:.2f}, raw_chars={raw_chars})",
                    "snippet": snippet,
                    "pillars": _pillar_guess(title, url, snippet),
                }
            )
            continue

        is_known = False
        matched_known = None
        blob = f"{title} {url}".lower()
        for key, canonical in known.items():
            if key in blob:
                is_known = True
                matched_known = canonical
                break
        if not is_known and inferred and inferred.lower() in known:
            is_known = True
            matched_known = known[inferred.lower()]

        entry = {
            "name": inferred or title,
            "title": title,
            "url": url,
            "score": score,
            "snippet": snippet,
            "domain": dom,
            "pillars": _pillar_guess(title, url, snippet),
        }
        if is_known:
            entry["known_name"] = matched_known or inferred or title
            new_evidence.append(entry)
        else:
            new_competitors.append(entry)

    new_competitors = new_competitors[:MAX_EVIDENCE]
    new_evidence = new_evidence[:MAX_EVIDENCE]
    doubtful = doubtful[:MAX_EVIDENCE]
    non_useful = non_useful[:MAX_EVIDENCE]

    lines = [
        "---",
        f"run_date: {run_date}",
        f"run_id: {rid}",
        "source: spiru-ops",
        "status: unreviewed",
        f"focus_topic: {FOCUS_NAME}",
        f"items_found: {len(items)}",
        f"items_useful: {len(new_competitors) + len(new_evidence)}",
        "---",
        "",
        f"# Competitor delta — {rid}",
        "",
        "> Generato automaticamente da spiru-ops. Non aggiornare il master direttamente da qui.",
        "> Revisiona e consolida solo le evidenze utili in `competitor-map.md`.",
        "",
        "## Nuovi competitor emersi",
    ]

    if new_competitors:
        for e in new_competitors:
            lines += [
                f"- **{e['name']}**",
                f"  - fonte: {e['url']}",
                f"  - titolo: {e['title']}",
                f"  - confidenza: {'alta' if e['score'] >= 0.8 else 'media'}",
                f"  - spirulina_score: {e['score']:.3f}",
                f"  - pillars: {', '.join(e['pillars']) if e['pillars'] else 'identity'}",
                f"  - nota: {e['snippet'] or 'snippet non disponibile'}",
            ]
    else:
        lines += ["- Nessun nuovo competitor emerso in modo convincente."]

    lines += ["", "## Nuove evidenze su competitor noti"]
    if new_evidence:
        for e in new_evidence:
            lines += [
                f"- **{e['known_name']}**",
                f"  - nuova informazione da: {e['url']}",
                f"  - titolo: {e['title']}",
                f"  - spirulina_score: {e['score']:.3f}",
                f"  - pillars: {', '.join(e['pillars']) if e['pillars'] else 'identity'}",
                f"  - nota: {e['snippet'] or 'snippet non disponibile'}",
            ]
    else:
        lines += ["- Nessuna nuova evidenza solida su competitor già noti."]

    lines += ["", "## Informazioni dubbie / da verificare"]
    if doubtful:
        for e in doubtful:
            lines += [
                f"- **{e['name']}**",
                f"  - dubbio: {e['reason']}",
                f"  - fonte: {e['url']}",
                f"  - titolo: {e['title']}",
                f"  - pillars: {', '.join(e['pillars']) if e['pillars'] else 'identity'}",
                f"  - nota: {e['snippet'] or 'snippet non disponibile'}",
            ]
    else:
        lines += ["- Nessuna informazione dubbia rilevante da segnalare."]

    lines += ["", "## Fonti non utili questa run"]
    if non_useful:
        for title, url, reason in non_useful:
            lines += [f"- {title or url} — {reason}"]
    else:
        lines += ["- Nessuna fonte scartata degna di nota."]

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[obsidian] competitor inbox aggiornato: {out_path}")


if __name__ == "__main__":
    main()
