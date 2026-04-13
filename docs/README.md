# spiru-ops — Documentazione tecnica

Questa cartella contiene la documentazione completa del progetto `spiru-ops`.
Scritta per essere letta dopo mesi di inattività, da zero.

---

## Indice

| File | Contenuto |
|------|-----------|
| [architecture.md](architecture.md) | Visione d'insieme, componenti, diagramma dati |
| [pipeline.md](pipeline.md) | Ogni step della pipeline in dettaglio (discover → ingest → index → report → QC) |
| [configuration.md](configuration.md) | Tutte le variabili d'ambiente, profili, file YAML di config |
| [knowledge_lifecycle.md](knowledge_lifecycle.md) | Come i dati nascono, si trasformano e vengono conservati |
| [services.md](services.md) | Docker: Qdrant, Unstructured, Grobid — cosa fanno e come funzionano |
| [copilot.md](copilot.md) | SpiruCopilot: RAG + OpenAI, UI Streamlit, query CLI |
| [operations.md](operations.md) | Cron, log, debug, manutenzione, comandi utili |

---

## Una-riga description

**spiru-ops** è un sistema autonomo che ogni giorno (06:10 UTC via cron):
1. Cerca documenti tecnico-scientifici su Spirulina/Arthrospira via SearXNG (self-hosted) / Brave Search (fallback) e OpenAlex
2. Scarica e parsifica HTML e PDF (Unstructured + Grobid)
3. Arricchisce i metadati bibliografici (Crossref + Unpaywall)
4. Embedded i chunk in un vettore e li indicizza in Qdrant (`docs_chunks_v2`)
5. Genera un report Markdown e controlla la qualità del run
6. Espone una chat RAG (SpiruCopilot) per fare domande progettuali con citazioni (multi-backend LLM: openai/anthropic/ollama)

Il tutto gira localmente su un singolo host, senza cloud.
