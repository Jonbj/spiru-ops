#!/usr/bin/env python3
"""scripts/customer_discovery.py — Prospect discovery per AlgaVitae.

Ricerca automatica di potenziali clienti B2B e B2C per AlgaVitae:
spirulina fresca e secca, canale alimentare e cosmetico.

Verticals coperti
-----------------
1.  Erboristerie & negozi bio     — Marche, centro Italia, online
2.  Farmacie & parafarmacia       — canale farmaceutico/integratori
3.  HoReCa premium                — ristoranti bio, juice bar, chef superfood
4.  Cosmetica naturale            — brand + laboratori conto terzi italiani
5.  Produttori integratori B2B    — nutraceutica, private label, conto terzi
6.  Distribuzione bio             — grossisti, importatori, centro Italia
7.  Alimenti funzionali           — pastifici, snack, prodotti arricchiti spirulina
8.  Fitness & sport nutrition     — sport nutrition, palestre, wellness
9.  SPA & centri benessere        — trattamenti estetici con alga/spirulina
10. GAS & mercati biologici       — filiera corta, gruppi acquisto, mercati

Output
------
Scrive un report markdown in:
  obsidian-vault/progetto/customers/inbox/{RUN_ID}.md

Il file è progettato per revisione manuale in Obsidian.
I prospect da conservare si consolidano in:
  obsidian-vault/progetto/customer-discovery.md

Come si usa
-----------
Invocato automaticamente da daily.sh (best-effort, non blocca il run).
Esecuzione manuale:
  python scripts/customer_discovery.py

Richiede SearXNG (SEARXNG_URL) o Brave Search (BRAVE_API_KEY) in .env.

Env vars opzionali
------------------
CUSTOMER_DISCOVERY_SLEEP_S       Sleep tra query (default: 1.5)
CUSTOMER_DISCOVERY_MAX_PER_QUERY Max risultati per query (default: 10)
CUSTOMER_DISCOVERY_MAX_PER_VERT  Max prospect per verticale (default: 15)
"""

import pathlib
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipelines.common import env, run_id

# ─── Config ───────────────────────────────────────────────────────────────────
SEARXNG_URL = env("SEARXNG_URL", "").strip().rstrip("/")
BRAVE_API_KEY = env("BRAVE_API_KEY", required=False)
USER_AGENT = env("USER_AGENT", "spiru-ops-bot/0.4")
VAULT_DIR = pathlib.Path(env("OBSIDIAN_VAULT_DIR", str(ROOT / "obsidian-vault")))

SLEEP_S = float(env("CUSTOMER_DISCOVERY_SLEEP_S", "1.5"))
MAX_PER_QUERY = int(env("CUSTOMER_DISCOVERY_MAX_PER_QUERY", "10"))
MAX_PER_VERTICAL = int(env("CUSTOMER_DISCOVERY_MAX_PER_VERT", "15"))

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = USER_AGENT

# TLD europei accettati. Domini con TLD fuori da questo set vengono scartati
# a meno che il contenuto non contenga segnali italiani espliciti.
_EU_TLDS = {
    "it", "eu", "sm",                                          # Italia + San Marino + EU
    "fr", "de", "es", "pt", "nl", "be", "at", "ch",           # Europa occidentale
    "pl", "cz", "sk", "hu", "ro", "bg", "hr", "si",           # Europa centrale/orientale
    "dk", "se", "no", "fi", "gr", "lu", "mt", "cy", "ie",     # Nord/Sud Europa
}

# Regex per segnali italiani nel contenuto — usata come fallback per domini .com/.net/.org
# Se il titolo/snippet contiene queste parole, il risultato è probabilmente italiano.
_ITALIAN_RE = re.compile(
    r"\b(s\.?r\.?l|s\.?p\.?a|s\.?n\.?c|s\.?r\.?l\.?s"
    r"|italia[ni]?|italiano|italiana"
    r"|marche|ancona|roma|milano|napoli|torino|firenze|bologna"
    r"|venezia|genova|palermo|bari|catania|fermo|civitanova"
    r"|macerata|pesaro|urbino|abruzzo|umbria|lazio|toscana"
    r"|piemonte|lombardia|sicilia|sardegna|puglia|campania"
    r"|emilia.romagna|veneto|liguria|calabria|basilicata|molise)\b",
    re.IGNORECASE,
)


def _is_european(url: str, title: str, desc: str) -> bool:
    """True se il risultato è verosimilmente italiano/europeo.

    Logica:
    - TLD europeo (.it, .fr, .de, .eu, .sm, …) → passa sempre
    - TLD generico (.com, .net, .org, …) → passa solo se titolo/snippet
      contiene segnali italiani espliciti (città, forme societarie, regioni)
    - Tutto il resto → scarta
    """
    dom = _domain(url)
    tld = dom.rsplit(".", 1)[-1] if "." in dom else ""
    if tld in _EU_TLDS:
        return True
    blob = f"{title} {desc} {dom}"
    return bool(_ITALIAN_RE.search(blob))


# Domini editoriali/marketplace — non sono prospect anche se citano spirulina
_SKIP_DOMAINS = {
    "wikipedia.org", "wikihow.com", "healthline.com", "medicalnewstoday.com",
    "corriere.it", "repubblica.it", "ilsole24ore.com", "quotidiano.net",
    "ilfattoquotidiano.it", "today.it", "fanpage.it", "ilgiornale.it",
    "amazon.it", "amazon.com", "ebay.it", "ebay.com", "iherb.com",
    "aliexpress.com", "idealo.it", "trovaprezzi.it", "kelkoo.it",
    "youtube.com", "facebook.com", "instagram.com", "tiktok.com",
    "sciencedirect.com", "pubmed.ncbi.nlm.nih.gov", "mdpi.com",
    "frontiersin.org", "researchgate.net", "ncbi.nlm.nih.gov",
}


# =============================================================================
# Search verticals — query per categoria prospect
# =============================================================================
# Ogni vertical ha:
#   id          → chiave univoca
#   label       → titolo sezione Obsidian
#   scope       → breve descrizione dell'ambito
#   queries     → lista di query da lanciare su SearXNG/Brave
#   fit_terms   → parole chiave che aumentano lo score prospect
#   avoid_terms → parole chiave che abbassano lo score (articoli, blog, ecc.)
#
# Razionale delle query:
# - Italiano per massimizzare risultati locali su SearXNG
# - Mix di query geografiche (Marche, centro Italia) e nazionali (per online/B2B)
# - Escludono esplicitamente marketplace e blog dove possibile
# =============================================================================

SEARCH_VERTICALS = [
    {
        "id": "retail_bio",
        "label": "Erboristerie & negozi bio",
        "scope": "Retail alimentare — Marche, centro Italia, online",
        "queries": [
            "erboristeria integratori spirulina Marche Ancona Fermo contatti",
            "negozio biologico superfood spirulina Marche Umbria Abruzzo",
            "erboristeria biologica spirulina polvere capsule acquisto centro Italia",
            "NaturaSì Marche spirulina erboristeria integratori contatti",
            "erboristeria bio spirulina vendita online Italia spedizione",
            "bottega biologica spirulina alga integratori centro Italia",
        ],
        "fit_terms": [
            "erboristeria", "biologico", "bio", "naturale", "integratore",
            "spirulina", "health", "benessere", "superfood",
        ],
        "avoid_terms": ["amazon", "iherb", "ebay", "aliexpress", "ricetta", "forum"],
    },
    {
        "id": "farmacia_parafarmacia",
        "label": "Farmacie & parafarmacia",
        "scope": "Canale farmaceutico — integratori spirulina Italia",
        "queries": [
            "parafarmacia integratori spirulina acquisto Italia",
            "farmacia online integratori spirulina biologica prezzi",
            "farmacia spirulina alga integratore capsule polvere Italia ordine",
        ],
        "fit_terms": [
            "farmacia", "parafarmacia", "integratore", "spirulina", "alga", "capsule",
        ],
        "avoid_terms": ["amazon", "iherb", "forum", "blog"],
    },
    {
        "id": "horeca_premium",
        "label": "HoReCa — ristoranti bio, juice bar, chef superfood",
        "scope": "Ristorazione premium, raw food, smoothie bar, wellness cafè",
        "queries": [
            "ristorante biologico vegano spirulina ingrediente Marche chef",
            "juice bar smoothie superfood spirulina Italia apertura",
            "raw food ristorante spirulina alga ingrediente Italia",
            "chef cucina creativa spirulina alga Marche Abruzzo ingrediente locale",
            "catering biologico superfood spirulina Italia evento fornitura",
            "ristorante stellato ingrediente spirulina microalga locale centro Italia",
            "wellness cafè superfood bar spirulina alga Italia fornitura",
        ],
        "fit_terms": [
            "ristorante", "chef", "cucina", "juice", "smoothie", "catering",
            "vegano", "biologico", "raw", "bar", "superfood",
        ],
        "avoid_terms": ["ricetta", "recipe", "blog", "articolo", "forum", "tutorial"],
    },
    {
        "id": "cosmetica_naturale",
        "label": "Cosmetica naturale — brand & laboratori conto terzi",
        "scope": "Brand cosmetici bio italiani + laboratori formulazione con spirulina/microalga",
        "queries": [
            "brand cosmetici naturali spirulina INCI ingrediente Italia",
            "cosmetica biologica alga spirulina Italia brand produttore",
            "laboratorio cosmetico conto terzi alga spirulina ingrediente Italia",
            "skincare biologico spirulina microalga brand italiano certificato",
            "azienda cosmetica italiana alga spirulina ingredient sourcing B2B",
            "cosmetici alga spirulina crema siero Italia brand vendita B2B",
            "phycocianina cosmetica colore naturale brand italiano ingrediente",
        ],
        "fit_terms": [
            "cosmetica", "skincare", "crema", "siero", "INCI", "formulazione",
            "alga", "microalga", "spirulina", "brand", "laboratorio", "phycocianina",
        ],
        "avoid_terms": ["amazon", "fai da te", "DIY", "ricetta", "forum"],
    },
    {
        "id": "integratori_nutraceutica",
        "label": "Produttori integratori & nutraceutica B2B",
        "scope": "Produttori italiani di integratori, private label, conto terzi",
        "queries": [
            "produttore integratori alimentari spirulina Italia conto terzi B2B",
            "azienda nutraceutica italiana spirulina ingrediente private label",
            "contract manufacturer integratori spirulina capsule polvere Italia",
            "produttore capsule spirulina polvere Italia B2B sourcing",
            "nutraceutica italiana spirulina ingrediente fornitore partner accordo",
            "Erbozeta San Marino integratori spirulina conto terzi ingredienti",
            "industria integratori alimentari spirulina alga fornitore Italia",
        ],
        "fit_terms": [
            "nutraceutica", "integratori", "private label", "conto terzi",
            "laboratorio", "spirulina", "produttore", "B2B", "contratto",
        ],
        "avoid_terms": ["amazon", "ebay", "retail", "acquista ora", "forum"],
    },
    {
        "id": "distribuzione_bio",
        "label": "Distribuzione bio — grossisti & importatori",
        "scope": "Grossisti e distributori biologici, centro Italia",
        "queries": [
            "distributore biologico grossista spirulina Italia B2B",
            "grossista integratori biologici spirulina Italia distribuzione",
            "distributore biologico Marche Umbria Abruzzo integratori spirulina contatti",
            "all ingrosso spirulina biologica Italia distributore B2B",
            "Ecor NaturaSì distributore ingrediente spirulina B2B Italia",
            "importatore spirulina biologica Italia grossista ingrediente",
        ],
        "fit_terms": [
            "distributore", "grossista", "biologico", "ingrosso", "B2B",
            "spirulina", "distribuzione", "importatore",
        ],
        "avoid_terms": ["amazon", "retail", "forum", "blog"],
    },
    {
        "id": "alimenti_funzionali",
        "label": "Alimenti funzionali — pasta, snack, prodotti arricchiti",
        "scope": "Pastifici artigianali e produttori alimenti con spirulina come ingrediente",
        "queries": [
            "pastificio artigianale spirulina pasta biologica Italia ingrediente",
            "pasta spirulina bio Italia produttore artigianale acquisto grosso",
            "produttore alimenti funzionali spirulina ingrediente Italia superfood",
            "pastificio biologico Marche Umbria spirulina ingrediente partnership",
            "La Terra e il Cielo Marche pasta biologica spirulina ingrediente",
            "snack biologico spirulina superfood Italia produttore B2B",
            "prodotto alimentare spirulina alga colorante naturale ingrediente Italia",
            "Rustichella Abruzzo spirulina pasta artigianale ingrediente biologico",
        ],
        "fit_terms": [
            "pastificio", "pasta", "alimenti", "funzionale", "spirulina",
            "biologico", "ingrediente", "snack", "barretta", "produttore",
        ],
        "avoid_terms": ["ricetta", "recipe", "blog", "forum", "tutorial"],
    },
    {
        "id": "fitness_sport",
        "label": "Fitness & sport nutrition",
        "scope": "Palestre, centri fitness, sport nutrition italiani con spirulina",
        "queries": [
            "integratori sportivi spirulina proteina vegetale Italia palestra",
            "sport nutrition spirulina proteina verde Italia shop B2B",
            "supplementi vegani spirulina atleti Italia negozio sport",
            "palestra centro fitness spirulina superfood integratori Italia",
        ],
        "fit_terms": [
            "fitness", "sport", "palestra", "atleta", "proteina", "spirulina",
            "integratore", "performance", "vegan protein",
        ],
        "avoid_terms": ["amazon", "ebay", "forum"],
    },
    {
        "id": "spa_wellness",
        "label": "SPA & centri benessere",
        "scope": "SPA e centri estetici con trattamenti alga/spirulina",
        "queries": [
            "SPA trattamenti spirulina alga cosmetica Italia centri benessere",
            "centro estetico trattamento alga spirulina fango corpo viso Italia",
            "wellness spa microalga spirulina trattamento estetico Italia fornitura",
            "thalasso alga spirulina centro benessere Italia fornitore ingrediente",
        ],
        "fit_terms": [
            "spa", "benessere", "estetica", "trattamento", "thalasso",
            "alga", "spirulina", "fango", "corpo",
        ],
        "avoid_terms": ["forum", "blog", "ricetta"],
    },
    {
        "id": "gas_mercati_biologici",
        "label": "GAS & mercati biologici locali",
        "scope": "Gruppi acquisto solidale, mercati produttori, filiera corta Marche",
        "queries": [
            "GAS gruppo acquisto solidale spirulina Marche contatti adesione",
            "mercato biologico produttore locale spirulina Marche Ancona",
            "filiera corta spirulina biologica Marche acquisto diretto produttore",
            "DES distretto economia solidale spirulina biologico Marche Umbria",
            "mercatino del contadino spirulina microalga biologico Marche",
        ],
        "fit_terms": [
            "GAS", "DES", "mercato", "biologico", "filiera", "solidale",
            "spirulina", "produttore", "contadino",
        ],
        "avoid_terms": [],
    },
]


# =============================================================================
# Search helpers
# =============================================================================

def _searxng_search(query: str, count: int) -> list[dict]:
    """Ricerca via SearXNG con lingua italiana per massimizzare risultati locali."""
    if not SEARXNG_URL:
        return []
    url = f"{SEARXNG_URL}/search"
    params = {
        "q": query,
        "format": "json",
        "engines": "google,bing,duckduckgo",
        "language": "it-IT",
        "pageno": "1",
    }
    try:
        r = _SESSION.get(url, params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get("results") or []
    except Exception as exc:
        print(f"[customer_discovery] WARN: searxng failed for '{query}': {exc}", flush=True)
        return []
    return [
        {
            "url": hit.get("url", ""),
            "title": hit.get("title", ""),
            "description": hit.get("content", ""),
        }
        for hit in results[:count]
        if hit.get("url")
    ]


def _brave_search(query: str, count: int) -> list[dict]:
    if not BRAVE_API_KEY:
        return []
    try:
        r = _SESSION.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY},
            params={"q": query, "count": str(count)},
            timeout=30,
        )
        r.raise_for_status()
        results = (r.json().get("web") or {}).get("results") or []
        return [
            {"url": x.get("url", ""), "title": x.get("title", ""), "description": x.get("description", "")}
            for x in results
        ]
    except Exception as exc:
        print(f"[customer_discovery] WARN: brave failed for '{query}': {exc}", flush=True)
        return []


def _web_search(query: str) -> list[dict]:
    """SearXNG prima (free, italiano), poi Brave come fallback."""
    if SEARXNG_URL:
        return _searxng_search(query, count=MAX_PER_QUERY)
    if BRAVE_API_KEY:
        return _brave_search(query, count=MAX_PER_QUERY)
    return []


# =============================================================================
# Prospect scoring
# =============================================================================

def _domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc.lstrip("www.")
    except Exception:
        return ""


def _normalize_url(url: str) -> str:
    """Normalizzazione minima per dedup (lowercase, strip trailing slash, rm www)."""
    url = (url or "").lower().strip().rstrip("/")
    url = re.sub(r"^https?://www\.", "https://", url)
    return url


def _score_prospect(result: dict, vertical: dict) -> int:
    """Punteggio prospect [−1, 5].

    −1  → da scartare (dominio editoriale o marketplace)
     0  → segnale debole
     1+ → segnale crescente di fit
    """
    title = (result.get("title") or "").lower()
    desc = (result.get("description") or "").lower()
    url = (result.get("url") or "").lower()
    blob = f"{title} {desc} {url}"

    # Salta domini editoriali/marketplace sempre
    dom = _domain(url)
    for skip in _SKIP_DOMAINS:
        if dom == skip or dom.endswith("." + skip):
            return -1

    # Salta risultati non europei
    if not _is_european(url, title, desc):
        return -1

    score = 0

    # Fit terms: ogni 2 hit = +1, con cap a +3
    fit_hits = sum(1 for t in vertical.get("fit_terms", []) if t.lower() in blob)
    score += min(fit_hits // 2, 3)

    # Bonus dominio italiano
    if ".it/" in url or url.endswith(".it"):
        score += 1

    # Bonus URL che assomiglia a sito aziendale (non articolo/blog)
    path = urlparse(result.get("url", "")).path.lower()
    if not any(x in path for x in ["/blog/", "/news/", "/articol", "/post/", "/wiki/"]):
        score += 1

    # Penalità avoid_terms
    for t in vertical.get("avoid_terms", []):
        if t.lower() in blob:
            score -= 1

    return score


def _star_label(score: int) -> str:
    if score >= 4:
        return "★★★"
    if score >= 2:
        return "★★"
    return "★"


# =============================================================================
# Output
# =============================================================================

def _write_inbox(results_by_vertical: dict, rid: str, run_date: str) -> pathlib.Path:
    inbox_dir = VAULT_DIR / "progetto" / "customers" / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    out_path = inbox_dir / f"{rid}.md"

    total = sum(len(v) for v in results_by_vertical.values())
    high_q = sum(
        sum(1 for p in v if p["score"] >= 4)
        for v in results_by_vertical.values()
    )

    lines = [
        "---",
        f"run_date: {run_date}",
        f"run_id: {rid}",
        "source: customer_discovery",
        "status: unreviewed",
        f"verticals: {len(SEARCH_VERTICALS)}",
        f"prospects_total: {total}",
        f"high_quality: {high_q}",
        "---",
        "",
        f"# Customer discovery — {rid}",
        "",
        "> Generato automaticamente da spiru-ops.",
        "> Revisiona e consolida i contatti utili in `progetto/customer-discovery.md`.",
        "",
        f"**{total} prospect trovati** — {high_q} ad alta rilevanza (★★★)",
        "",
    ]

    for vertical in SEARCH_VERTICALS:
        vid = vertical["id"]
        prospects = results_by_vertical.get(vid, [])
        lines += [
            f"## {vertical['label']}",
            f"*{vertical['scope']}*",
            "",
        ]

        if not prospects:
            lines += ["*Nessun prospect trovato per questo verticale.*", ""]
            continue

        for p in prospects:
            star = _star_label(p["score"])
            title_or_domain = (p["title"] or p["domain"]).strip()
            lines.append(f"- **[{title_or_domain}]({p['url']})** {star}")
            if p["description"]:
                # Tronca a 180 char per leggibilità in Obsidian
                snippet = p["description"][:180].replace("\n", " ").strip()
                lines.append(f"  > {snippet}")
            lines.append(f"  - dominio: `{p['domain']}` | score: {p['score']}")
        lines.append("")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    rid = run_id()
    run_date = datetime.now(timezone.utc).date().isoformat()

    if not SEARXNG_URL and not BRAVE_API_KEY:
        print(
            "[customer_discovery] WARN: nessun motore configurato "
            "(SEARXNG_URL o BRAVE_API_KEY). Skip.",
            flush=True,
        )
        return

    print(f"[customer_discovery] RUN_ID={rid} — avvio discovery su {len(SEARCH_VERTICALS)} verticali", flush=True)

    all_seen: set[str] = set()
    results_by_vertical: dict[str, list[dict]] = {}

    for vertical in SEARCH_VERTICALS:
        vid = vertical["id"]
        prospects: list[dict] = []
        seen_this_vertical: set[str] = set()

        for query in vertical["queries"]:
            time.sleep(SLEEP_S)
            hits = _web_search(query)

            for hit in hits:
                url = hit.get("url", "")
                if not url:
                    continue
                norm = _normalize_url(url)
                # Dedup globale (stesso URL non appare in due verticali)
                if norm in all_seen or norm in seen_this_vertical:
                    continue

                score = _score_prospect(hit, vertical)
                if score < 0:
                    continue

                seen_this_vertical.add(norm)
                all_seen.add(norm)
                prospects.append(
                    {
                        "url": url,
                        "title": (hit.get("title") or "").strip(),
                        "description": (hit.get("description") or "").strip(),
                        "domain": _domain(url),
                        "score": score,
                    }
                )

        # Ordina per score, poi tronca
        prospects.sort(key=lambda x: x["score"], reverse=True)
        results_by_vertical[vid] = prospects[:MAX_PER_VERTICAL]
        print(
            f"[customer_discovery]   {vid}: {len(results_by_vertical[vid])} prospect",
            flush=True,
        )

    out_path = _write_inbox(results_by_vertical, rid, run_date)
    total = sum(len(v) for v in results_by_vertical.values())
    high_q = sum(sum(1 for p in v if p["score"] >= 4) for v in results_by_vertical.values())

    print(f"[customer_discovery] report scritto: {out_path}", flush=True)
    print(
        f"[customer_discovery] totale: {total} prospect, {high_q} alta rilevanza",
        flush=True,
    )


if __name__ == "__main__":
    main()
