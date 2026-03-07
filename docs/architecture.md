# Architettura — spiru-ops

## Scopo del progetto

spiru-ops è un **knowledge-base building system** focalizzato su Spirulina/Arthrospira.
Il contesto operativo è la progettazione di un fotobioreattore (PBR) da 50L per la coltivazione di spirulina a uso alimentare/cosmetico. Il sistema costruisce e mantiene una knowledge base (KB) tecnico-scientifica interrogabile tramite RAG Copilot.

**Dominio specifico**: coltura di cianobatteri filamentosi, in particolare *Arthrospira platensis* e *Limnospira maxima*, nei seguenti ambiti:
- Fotobioreattori airlift, raceway pond
- Controllo pH, CO₂, temperatura
- Illuminazione LED (spettro, PPFD)
- Raccolta biomassa (microstrainer, filtrazione)
- Sicurezza alimentare/cosmetica (EFSA, FDA, GRAS, ISO 22000, GMP)
- Caratterizzazione ceppi (SAG, CCAP, DSMZ)
- Economia circolare (acque reflue, digestato)

---

## Componenti principali

```
┌─────────────────────────────────────────────────────────────────┐
│                        spiru-ops host                           │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Pipeline (Python + Bash)                                │  │
│  │                                                          │  │
│  │  discover.py → ingest.py → enrich_doi_oa.py →            │  │
│  │  index.py → report.py → evaluate.py                      │  │
│  │  → kb_validate.py → ocr_backlog.py → aggregate_daily.py  │  │
│  │  → prune_artifacts.sh                                    │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  Qdrant (Docker) │  │ Unstructured │  │  Grobid (Docker) │  │
│  │  :6333           │  │ (Docker):8000│  │  :8070           │  │
│  │  Vector DB       │  │ HTML/PDF     │  │  Header estraz.  │  │
│  │  storage/qdrant/ │  │ parsing API  │  │  paper accadem.  │  │
│  └──────────────────┘  └──────────────┘  └──────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  SpiruCopilot (Streamlit — ui/copilot.py)                │  │
│  │  RAG: query Qdrant → assemble context → OpenAI API       │  │
│  └──────────────────┘───────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
           ↑                        ↑
   API esterne (Discovery)   API esterne (Arricchimento)
   - Brave Search API         - Crossref API
   - OpenAlex API             - Unpaywall API
                              - OpenAI API (copilot)
```

---

## Flusso dati end-to-end

```
Brave Search API  ──┐
OpenAlex API      ──┤
                    ▼
              discover.py
              Genera lista URL candidati (JSONL)
                    │
                    ▼
              ingest.py
              Download HTML/PDF → parsing testo
              (Unstructured API + Grobid + pypdf fallback)
              Filtra: seen_urls, domini negati, circuit-breaker 403/429
              Produce: raw/, parsed/, ingested.json
                    │
                    ▼
          enrich_doi_oa.py
          Per ogni DOI trovato:
          → Crossref: metadati bibliografici
          → Unpaywall: URL PDF open-access
          Aggiorna ingested.json + .meta.json
                    │
                    ▼
              index.py
              Per ogni doc ingested:
              → chunk testo (2200 chars, overlap 240)
              → embed con all-MiniLM-L6-v2
              → upsert in Qdrant (batch 64)
              Produce: indexed.json
                    │
                    ▼
              report.py
              Genera report Markdown con:
              - statistiche run
              - distribuzione domini/focus
              - segnali Spirulina
              - failure categories
                    │
                    ▼
             evaluate.py
             QC: 9 check su soglie configurabili
             exit 0 = PASS, exit 2 = FAIL
             (FAIL non blocca il run)
                    │
                    ▼
   kb_validate.py (best-effort)
   ocr_backlog.py (best-effort)
   aggregate_daily.py (best-effort)
   prune_artifacts.sh (best-effort)
```

---

## Stack tecnologico

| Layer | Tecnologia | Note |
|-------|-----------|------|
| Orchestrazione | Bash (`cron_run_daily.sh`, `daily.sh`) | Set -euo pipefail, flock, trap EXIT |
| Scheduling | cron (4×/giorno) | 02:10, 08:10, 14:10, 20:10 UTC |
| Discovery | Python + requests | Brave Search API, OpenAlex REST |
| Parsing HTML | Python + BeautifulSoup | In `common.py` — soup_text() |
| Parsing PDF/HTML avanzato | Unstructured API (Docker) | Fallback: local pypdf |
| Parsing header paper | Grobid (Docker) | Solo se GROBID_ENABLE=1 |
| Arricchimento bibliografico | requests + Crossref/Unpaywall | In `enrich_doi_oa.py` |
| Embedding | sentence-transformers | Modello: all-MiniLM-L6-v2 (384 dim) |
| Vector DB | Qdrant (Docker) | Collection: docs_chunks |
| RAG Copilot | Streamlit + OpenAI Responses API | `ui/copilot.py` |
| Config | YAML (configs/) + .env | Tutto configurabile via env |
| Lingua principale | Python 3.10+ | typing, dataclasses, pathlib |

---

## Struttura directory

```
spiru-ops/
├── pipelines/               # Tutti gli step Python + script Bash
│   ├── cron_run_daily.sh    # Wrapper cron: Docker up/down, lock, retry
│   ├── daily.sh             # Pipeline inner: sequenza step Python
│   ├── profiles.sh          # Profili runtime (balanced, kb_first)
│   ├── prune_artifacts.sh   # Cleanup state files vecchi
│   ├── common.py            # Utilities condivise (env, RUN_ID, chunking, URL)
│   ├── relevance.py         # Scoring Spirulina [0,1]
│   ├── discover.py          # Step 1: discovery URL
│   ├── ingest.py            # Step 2: download + parsing
│   ├── enrich_doi_oa.py     # Step 3: arricchimento DOI/OA
│   ├── index.py             # Step 4: embedding + upsert Qdrant
│   ├── report.py            # Step 5: report Markdown
│   ├── evaluate.py          # Step 6: QC checks
│   ├── kb_validate.py       # Step 7: dedup KB (best-effort)
│   ├── ocr_backlog.py       # Step 8: OCR PDF backlog (best-effort)
│   ├── aggregate_daily.py   # Step 9: aggregato giornaliero (best-effort)
│   ├── seed_strains.py      # Opzionale: seed ceppi iniziali
│   ├── reprocess_grobid.py  # Utility: ri-processa PDF con Grobid
│   ├── analyze_sources_*.py # Utility: analisi sorgenti
│   ├── qdrant_rest.py       # Client REST Qdrant (thin wrapper)
│   ├── query.py             # CLI: query RAG senza LLM
│   └── rag_cloud.py         # RAG + OpenAI Responses API
│
├── configs/                 # Configurazione dominio
│   ├── focus.yaml           # 18 aree tematiche (query Brave + OpenAlex)
│   ├── scoring.yaml         # Pesi per Qdrant scoring
│   ├── domains.yaml         # Domini preferiti/penalizzati/negati
│   └── strain_seeds.yaml    # Seed ceppi iniziali
│
├── ui/
│   └── copilot.py           # SpiruCopilot (Streamlit)
│
├── prompts/                 # Prompt LLM
│   ├── copilot_system.md    # System prompt copilot
│   ├── copilot_user_template.md
│   └── deep_research_weekly.md
│
├── ops/cron/                # Cron entries
│   ├── daily.cron           # Entry produzione (4×/giorno)
│   └── weekly.cron          # Entry settimanale
│
├── tests/                   # Test unitari e smoke
│
├── storage/                 # Dati runtime (NON in git)
│   ├── raw/                 # Download originali
│   ├── parsed/              # Testo estratto + .meta.json
│   ├── state/               # Artefatti per-run (JSONL/JSON)
│   ├── artifacts/           # Report e aggregati
│   ├── qdrant/              # Dati Qdrant (volume Docker)
│   ├── logs/                # Log cron_daily_YYYY-MM-DD.log
│   └── backlog/             # Coda OCR
│
├── docker-compose.yml
├── requirements.txt
├── .env                     # Segreti e config (NON in git)
├── .env.example             # Template .env pubblico
└── docs/                    # Questa cartella
```

---

## Principi di design

### 1. RUN_ID stabile
Ogni invocazione del cron genera un `RUN_ID` (timestamp UTC: `YYYY-MM-DDTHHMMSSZ`) **una volta sola** in `cron_run_daily.sh`. Questo ID viene esportato a tutti i processi figli. Ogni artefatto porta il RUN_ID nel nome (`storage/state/2026-03-07T102121Z_candidates.jsonl`). Questo risolve:
- **Midnight split**: discover può partire alle 23:55, ingest alle 00:05 — stesso RUN_ID, nessuna rottura di path
- **Retry sicuro**: attempt 2 riusa gli stessi artefatti dell'attempt 1
- **Diagnosticabilità**: ogni run è isolato e ricostruibile

### 2. Idempotenza
- `seen_urls.jsonl` impedisce di ri-scaricare URL già visti
- `seen_doi.jsonl` penalizza (-25 pt) DOI già nel KB
- Gli upsert Qdrant usano ID stabili (hash di `url+content_hash`) — rieseguire indicizza lo stesso documento senza duplicati

### 3. Best-effort per step non critici
`kb_validate`, `ocr_backlog`, `aggregate_daily`, `prune_artifacts` sono invocati con `|| echo WARN` — un loro fallimento non blocca il run.

### 4. Profili runtime
I profili (`balanced`, `kb_first`) sono set di variabili d'ambiente definiti in `profiles.sh`. In produzione il cron usa `kb_first`. Un developer locale può usare `balanced` per run più veloci.

### 5. Config-driven (no hardcoding)
Tutte le soglie QC, limiti di download, modello embedding, nomi collection, URL servizi sono in `.env` o YAML. Non è necessario toccare il codice Python per cambiare i parametri operativi.
