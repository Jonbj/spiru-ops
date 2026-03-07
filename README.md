# spiru-ops — Spirulina knowledge engine + SpiruCopilot (RAG + Cloud LLM)

This repo:
- Discovers new sources daily (OpenAlex + Brave Search API)
- Downloads and parses HTML/PDF (Unstructured)
- Indexes chunks into Qdrant (local vector DB)
- Produces daily report + living spec
- Provides a **chat-like Design Copilot** (Streamlit) that uses RAG + OpenAI Responses API

## Prereqs
- Docker + docker compose
- Python 3.10+

## 1) Start services
```bash
docker compose up -d
```

## 2) Python environment
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3) Configure env
```bash
cp .env.example .env
# set BRAVE_API_KEY (optional but recommended)
# set OPENAI_API_KEY (required for copilot chat)
```

## 4) Run daily pipeline manually
```bash
bash pipelines/daily.sh
```

Outputs:
- storage/state/YYYY-MM-DD_candidates.jsonl
- storage/state/YYYY-MM-DD_ingested.json
- storage/state/YYYY-MM-DD_indexed.json
- storage/artifacts/YYYY-MM-DD_report.md
- storage/artifacts/living_spec.md

## 5) Query KB (no LLM)
```bash
python pipelines/query.py "PPFD e fotoperiodo per spirulina" --focus luce_e_led --topk 10
```

## 6) Run SpiruCopilot chat (RAG + Cloud LLM)
```bash
streamlit run ui/copilot.py
```

Tip:
- Use **Preview evidence** first to sanity-check sources.
- Then **Run Copilot** to generate a structured design answer with citations.

## Cron
Example (edit paths):
`ops/cron/daily.cron`

## Notes on OpenAI Responses API
Copilot uses the OpenAI Python SDK and the Responses API. See docs:
- Python SDK reference
- Responses API & tools / deep research
