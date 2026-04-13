# spiru-ops — Knowledge Base Spirulina/Arthrospira

## Contesto del progetto
Sistema autonomo che costruisce e mantiene una knowledge base tecnico-scientifica
su Spirulina/Arthrospira per supportare la progettazione di un fotobioreattore
(PBR) airlift da 50L a uso alimentare/cosmetico. Il proprietario è Stefano,
che gestisce il progetto su Linux localmente.

**Obiettivo finale**: avviare una piccola produzione commerciale di spirulina
fresca in Italia (Marche), con valutazione di un percorso food vs cosmetic.

---

## Architettura (pipeline giornaliera)

```
discover → ingest → enrich_doi_oa → index → report → evaluate
→ kb_validate / ocr_backlog / aggregate_daily (best-effort)
```

- **Cron**: 1×/giorno alle 06:10 UTC via `pipelines/cron_run_daily.sh`
- **Docker services**: Qdrant :6333, Unstructured :8000, Grobid :8070, SearXNG :8888
- **Vector DB**: Qdrant, collection `docs_chunks_v2`, ~67.000+ punti (aprile 2026)
- **Embedding**: `BAAI/bge-m3` (dense+sparse hybrid) in produzione
- **LLM Copilot**: multi-backend via `LLM_BACKEND` env (openai/anthropic/ollama, default openai gpt-4o)
- **Discovery**: SearXNG self-hosted (primary, gratuito) + OpenAlex + Brave Search (fallback)

---

## Comandi principali

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d qdrant searxng

# Run pipeline completo
bash pipelines/daily.sh

# Singoli step
python -m pipelines.discover
python -m pipelines.ingest
python -m pipelines.index

# Copilot UI
streamlit run ui/copilot.py   # http://localhost:8501

# Query KB senza LLM
python pipelines/query.py "kLa airlift spirulina" --topk 10

# Test
pytest tests/
```

---

## File chiave

| File | Ruolo |
|------|-------|
| `pipelines/daily.sh` | Orchestratore principale |
| `pipelines/discover.py` | Discovery URL (SearXNG/Brave + OpenAlex) |
| `pipelines/ingest.py` | Download + parsing (Unstructured/Grobid/pypdf) |
| `pipelines/index.py` | Chunk + embed + upsert Qdrant |
| `pipelines/rag_cloud.py` | RAG + LLM multi-backend per copilot |
| `pipelines/relevance.py` | Score Spirulina-centricity [0,1] per doc |
| `pipelines/qdrant_rest.py` | Client REST Qdrant (thin wrapper, no SDK) |
| `ui/copilot.py` | Streamlit RAG chat UI |
| `configs/focus.yaml` | 19 aree tematiche per discovery |
| `configs/competitors.yaml` | 16 competitor IT/EU tracciati |
| `configs/domains.yaml` | Domini deny/prefer/pdf_bonus |
| `.env.example` | Template config completo |

---

## Aree tematiche (focus.yaml) — priorità

**P0 — Decisionali bloccanti**
- production_system_selection, seasonal_productivity_italy,
  capex_opex_economics, customer_discovery_italy,
  competitor_pricing_italy_eu, regulatory_pathway_italy,
  food_vs_cosmetic_strategy

**P1 — Operativi pilota**
- harvesting_and_drying, contamination_management,
  process_control_and_cleaning, quality_qc_shelf_life,
  temperature_and_cold_management, water_site_infrastructure, strains_inoculum

**P2 — Supporto**
- illumination_led_indoor, packaging_labeling, fresh_spirulina_market,
  sales_channels_italy, grants_funding

---

## Competitor monitorati (competitors.yaml)

Produttori italiani attivi: Sant'Egle (Toscana), Apulia Kundi (Puglia),
Biospira, Farmodena (Emilia-Romagna), Spireat, Spiripau, Salera,
Livegreen (Sardegna), Spirulina Becagli (Toscana).
EU: Ecospirulina (Spagna). Wholesaler: Vehgro, Ekowarehouse.

---

## Invarianti di design critici

1. **RUN_ID stabile**: generato una volta in `cron_run_daily.sh`, condiviso
   da tutti i processi figli. Tutti gli artefatti usano `{RUN_ID}_*`.
2. **Spirulina score**: ogni doc ha score [0,1] da `relevance.py`.
   Documenti sotto 0.25 non vengono indicizzati.
3. **Circuit breaker**: domini con ≥ N errori 403/429 bloccati per il run
   (balanced: 5/3, kb_first: 15/5).
4. **QC non bloccante**: `evaluate.py` exit 2 (FAIL) non blocca il run —
   `daily.sh` lo gestisce e continua.
5. **Upsert idempotente**: ID Qdrant = hash(url+content_hash+chunk_i) →
   ri-eseguire l'index non duplica.

---

## Variabili d'ambiente critiche (.env)

```bash
QDRANT_COLLECTION=docs_chunks_v2
EMBED_MODEL=BAAI/bge-m3
LLM_BACKEND=openai          # openai | anthropic | ollama
OPENAI_MODEL=gpt-4o
ANTHROPIC_MODEL=claude-sonnet-4-6
SEARXNG_URL=http://localhost:8888   # vuoto = usa Brave
INGEST_TARGET=300
MAX_TOTAL_CANDIDATES=1200
QC_MIN_SPIRULINA_SHARE=0.35
```

---

## Storage layout

```
storage/
├── raw/         # Download originali (permanenti)
├── parsed/      # Testo + .meta.json (permanenti)
├── state/       # Artefatti per-run (retention 30gg)
│   ├── {RUN_ID}_candidates.jsonl
│   ├── {RUN_ID}_ingested.json
│   ├── {RUN_ID}_indexed.json
│   ├── seen_urls.jsonl   # dedup globale (max 15k righe)
│   └── seen_doi.jsonl
├── artifacts/   # Report + living_spec.md (permanenti)
├── qdrant/      # Volume Docker Qdrant
└── logs/        # cron_daily_YYYY-MM-DD.log
```

---

## Profili runtime

- `balanced`: download max 50MB, timeout PDF 90s, circuit breaker 5×403
- `kb_first` (cron): download max 120MB, timeout PDF 120s,
  circuit breaker 15×403, OpenAlex enrich sempre attivo

---

## Stato attuale (aprile 2026)

- KB: ~67.000+ chunk in Qdrant (docs_chunks_v2)
- Ceppi valutati per acquisto inoculo: ACUF 677 (prima scelta),
  BEA 0873B (fallback) — email pendente a info@acuf.net
- SearXNG attivo come discovery primaria (sostituisce Brave, zero costi)
- Semantic Scholar integrato come fonte aggiuntiva
