# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**spiru-ops** is a daily knowledge-base (KB) build pipeline for Spirulina/Arthrospira cultivation research. It discovers, ingests, embeds, and indexes scientific documents into a Qdrant vector database, then exposes them through a RAG-based Streamlit copilot (SpiruCopilot).

## Services (Docker)

Four services for the full pipeline (SearXNG replaces Brave Search API at zero cost):

```bash
docker compose up -d   # Qdrant :6333, Unstructured :8000, Grobid :8070, SearXNG :8888
```

To start only search (lightweight dev mode):
```bash
docker compose up -d qdrant searxng
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in API keys
```

## Common commands

```bash
# Run full pipeline manually
bash pipelines/daily.sh

# Run with a different profile (kb_first = aggressive timeouts/sizes)
PROFILE=kb_first bash pipelines/daily.sh

# Run a single pipeline step
python -m pipelines.discover
python -m pipelines.ingest
python -m pipelines.index
python -m pipelines.report
python -m pipelines.evaluate

# Query the KB directly (no LLM)
python pipelines/query.py "PPFD e fotoperiodo per spirulina" --focus illumination_led_indoor --topk 10

# Run copilot UI
streamlit run ui/copilot.py   # http://localhost:8501

# Tests (unit — no external services needed)
pytest tests/
pytest tests/test_relevance.py   # single test file

# End-to-end smoke test (requires Docker services + API keys)
python tests/smoke_e2e_subset.py
```

## Pipeline architecture

The pipeline runs in this sequence: `discover → ingest → enrich_doi_oa → index → report → evaluate → kb_validate / ocr_backlog / aggregate_daily`

Steps marked "best-effort" (`enrich_doi_oa`, `kb_validate`, `ocr_backlog`, `aggregate_daily`) don't block the run if they fail. **QC FAIL (exit 2) from `evaluate.py` is also intentionally non-blocking** — `daily.sh` handles it gracefully.

Intermediate state files live in `storage/state/` and are all named `{RUN_ID}_*`. The `RUN_ID` is generated once at startup of `daily.sh` and exported as an env var so all child Python processes share the same stable value. This prevents midnight-split bugs when a run crosses midnight.

### Key modules

| File | Role |
|------|------|
| `pipelines/daily.sh` | Main orchestrator — entry point for manual runs |
| `pipelines/cron_run_daily.sh` | Production cron wrapper (Docker health checks, flock, retries) |
| `pipelines/profiles.sh` | Runtime presets (`balanced` / `kb_first`) |
| `pipelines/discover.py` | SearXNG (primary) / Brave (fallback) + OpenAlex → `{RUN_ID}_candidates.jsonl` |
| `pipelines/enrich_doi_oa.py` | Backfills missing DOI/abstract metadata via OpenAlex (best-effort) |
| `pipelines/ingest.py` | Download + parse (Unstructured → Grobid → pypdf/BS4 cascade) |
| `pipelines/index.py` | Chunk → embed (BAAI/bge-m3) → upsert Qdrant |
| `pipelines/rag_cloud.py` | Hybrid retrieval + multi-backend LLM for copilot |
| `pipelines/relevance.py` | Spirulina-centricity score [0,1] per document |
| `pipelines/common.py` | Shared utilities: URL dedup, chunking, env helpers |
| `pipelines/qdrant_rest.py` | Thin REST wrapper around Qdrant (no SDK dependency) |
| `pipelines/kb_validate.py` | Post-index KB quality checks (best-effort) |
| `pipelines/ocr_backlog.py` | Retry OCR on PDFs deferred from previous runs (best-effort) |
| `pipelines/aggregate_daily.py` | Cross-run daily aggregation and summary (best-effort) |
| `pipelines/seed_strains.py` | Seed strain catalog URLs into candidates (opt-in: `SEED_STRAINS=1`) |
| `pipelines/prune_artifacts.sh` | Delete old `storage/state/` files beyond retention window |
| `ui/copilot.py` | Streamlit RAG chat UI |

### Config files (all YAML in `configs/`)

- `focus.yaml` — 21 knowledge topics, prioritized P0/P1/P2
- `domains.yaml` — deny/prefer/pdf_bonus domain lists
- `scoring.yaml` — per-focus discovery scoring weights
- `competitors.yaml` — competitor intelligence registry
- `manual_seeds.yaml` — curated must-have source URLs
- `strain_seeds.yaml` — Spirulina strain catalog links

## Key design invariants

- **RUN_ID stability**: all steps share the same `RUN_ID`; never re-derive it independently.
- **Spirulina relevance is a hard filter**: `relevance.py` scores each document [0,1]; QC thresholds and retrieval both use this score.
- **Circuit-breaker**: domains accumulating ≥ N 403/429 errors are skipped for the rest of that run (tunable via env vars).
- **Portfolio selection in `ingest.py`**: balances exploration (new domains) vs. exploitation (proven sources) — don't break this balance when modifying ingest logic.
- **LLM backend is runtime-configurable**: `LLM_BACKEND` env var switches between `openai`, `anthropic`, and `ollama` without code changes.

## Storage layout

All runtime artifacts live under `storage/`:

| Directory | Contents |
|-----------|----------|
| `storage/state/` | Run artifacts: `{RUN_ID}_candidates.jsonl`, `_ingested.json`, `_indexed.json`, `_report.md` |
| `storage/raw/` | Downloaded HTML/PDFs |
| `storage/parsed/` | Cleaned text + metadata |
| `storage/artifacts/` | Final outputs: daily reports, living spec |
| `storage/qdrant/` | Vector DB data |
| `storage/backlog/` | PDFs queued for OCR retry |

`docs/` has architecture/operational documentation. `prompts/` holds LLM system prompts for the copilot.

## Key environment variables

Critical knobs for tuning and debugging (full list in `.env.example`):

| Variable | Default | Effect |
|----------|---------|--------|
| `BRAVE_MAX_QUERIES_PER_RUN` | `0` (unlimited) | Set to ~50 to cap cost (~$4.50/mo) |
| `MAX_TOTAL_CANDIDATES` | `1200` | Upper bound on discovery candidates |
| `DISCOVERY_SINCE_DAYS` | `120` | Look-back window for new sources |
| `INGEST_TARGET` | `300` | Max documents to ingest per run |
| `QC_MIN_INDEXED_POINTS` | `200` | FAIL threshold: too few KB points |
| `QC_MIN_SPIRULINA_SHARE` | `0.35` | FAIL threshold: off-topic content |
| `LLM_BACKEND` | `openai` | Switch to `anthropic` or `ollama` at runtime |
| `EMBED_MODEL` | `BAAI/bge-m3` | Change requires full reindex (`scripts/reindex_all.py`) |
| `EMBED_DEVICE` | _(empty = auto)_ | Force embedding device: `cpu` to avoid CUDA OOM when VRAM is shared with LLM |
| `QDRANT_COLLECTION` | `docs_chunks_v2` | Collection name used by index, query, rag, evaluate — must match across all steps |
| `SEED_STRAINS` | `0` | Set to `1` to run `seed_strains.py` before discover |
| `GROBID_ENABLE` | `0` | Enable Grobid for PDF parsing (fragile on corrupt PDFs; off by default in both profiles) |
| `SEARXNG_URL` | `http://localhost:8888` | Self-hosted web search (free). When set, Brave is not used. Empty = fall back to Brave |
| `SEMANTIC_SCHOLAR_KEY` | _(empty)_ | Optional API key for Semantic Scholar (increases rate limit) |

## Maintenance scripts

```bash
bash scripts/backup_spiru_ops.sh             # full backup to tar.gz
bash scripts/restore_spiru_ops.sh <file>     # restore from backup
python scripts/reindex_all.py                # re-embed entire KB (after model change)
python scripts/dedup_qdrant.py               # remove duplicate chunks
python scripts/inject_manual_seeds.py        # inject curated URLs into candidates

# Source analysis (post-run diagnostics)
python pipelines/analyze_sources_by_run.py   # novelty/coverage by run
python pipelines/analyze_sources_global.py   # global domain/OA saturation view

# Competitor intelligence (also run automatically at end of daily.sh)
python scripts/build_competitor_queries.py   # build search query preview from competitors.yaml
python scripts/write_competitor_inbox.py     # write competitor delta to Obsidian inbox

# Backfill / repair (one-off data migrations)
python scripts/backfill_doc_type.py
python scripts/backfill_published_at.py
python scripts/backfill_qdrant_doc_type.py
python scripts/backfill_qdrant_published_at.py
```

## Cron schedule

- `ops/cron/daily.cron`: daily run at **06:10 UTC** via `cron_run_daily.sh`
- `ops/cron/backup-weekly.cron`: weekly backup Sunday 03:00 UTC
