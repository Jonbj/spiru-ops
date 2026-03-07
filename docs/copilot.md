# SpiruCopilot — RAG + OpenAI

## Cos'è

SpiruCopilot è l'interfaccia di interrogazione del knowledge base. Permette di fare domande di progettazione (es. "Proponi un design del sistema di areazione per un PBR airlift da 50L") e ottenere risposte strutturate con citazioni, basate sui documenti indicizzati.

Architettura: **RAG (Retrieval Augmented Generation)**
1. La domanda viene embeddedta con lo stesso modello usato dall'indexing
2. I K chunk più simili vengono recuperati da Qdrant
3. I chunk vengono assemblati come contesto
4. Il contesto + la domanda vengono inviati a OpenAI (Responses API)
5. Il modello genera una risposta citando le fonti

---

## Avvio

Requisiti:
- Qdrant in esecuzione: `docker compose up -d qdrant`
- `.env` configurato con `OPENAI_API_KEY` e `QDRANT_URL`

```bash
streamlit run ui/copilot.py
```

Apre su `http://localhost:8501`.

---

## Interfaccia (ui/copilot.py)

### Sidebar
- **Focus** (text input): filtra i chunk recuperati per area tematica. Es. `pbr_airlift_geometry_and_scale_down`. Se vuoto, cerca in tutto il KB.
- **TopK evidence** (slider 3–20): numero di chunk da recuperare. Default: `COPILOT_TOPK` da `.env` (10).
- **Append answer to living_spec.md**: se attivo, la risposta del copilot viene aggiunta in append a `storage/artifacts/living_spec.md`.

### Pulsanti
- **Preview evidence**: mostra i chunk che verrebbero usati come contesto, senza invocare il LLM. Utile per verificare la qualità delle sorgenti prima di fare una domanda costosa.
- **Run Copilot (LLM)**: invoca la pipeline RAG completa e mostra la risposta.

---

## Pipeline RAG (rag_cloud.py)

### Fase 1: Retrieve

```python
retrieve(question, focus=None, topk=10)
```

1. Embed la domanda con `all-MiniLM-L6-v2`
2. Query Qdrant (filtro opzionale per `focus`)
3. Deduplication per URL (stesso documento, chunk diverso → prende solo il chunk con score più alto)
4. Ritorna lista di `Evidence(n, url, title, focus, score, text)`

### Fase 2: Assemble context

I chunk recuperati vengono formattati come:
```
[1] Titolo (focus: pbr_airlift_geometry_and_scale_down)
URL: https://...
score: 0.847
---
...testo del chunk...

[2] ...
```

Il contesto è troncato a `COPILOT_MAX_CONTEXT_CHARS=18000` caratteri.

### Fase 3: LLM call (OpenAI Responses API)

Il system prompt è caricato da `prompts/copilot_system.md`.
Il user template è `prompts/copilot_user_template.md`.

La chiamata usa l'OpenAI Responses API:
```python
client.responses.create(
    model=OPENAI_MODEL,
    instructions=system_prompt,
    input=user_message_with_context
)
```

### Output

```python
{
    "answer": "..testo risposta con citazioni...",
    "sources": [...lista URLs citati...]
}
```

---

## Query CLI (pipelines/query.py)

Per interrogare il KB senza LLM, direttamente da terminale:

```bash
# Avvia Qdrant prima
docker compose up -d qdrant

# Query base
python -m pipelines.query "kLa in airlift photobioreactor" --topk 8

# Con filtro focus
python -m pipelines.query "microstrainer 20 micron spirulina" \
  --focus harvesting_fresh_biomass_filtration --topk 5

# Esporta risultati in Markdown
python -m pipelines.query "pH setpoint spirulina bicarbonate" \
  --topk 10 --export /tmp/query_result.md
```

Output: Markdown con titolo, score, URL, snippet di testo per ogni hit.

---

## Modello di embedding

`sentence-transformers/all-MiniLM-L6-v2`:
- 384 dimensioni
- Ottimizzato per semantic similarity
- Veloce (inference su CPU ~10ms/chunk)
- Il modello viene scaricato la prima volta e cachato in `~/.cache/torch/sentence_transformers/`

---

## Prompt di sistema

`prompts/copilot_system.md` definisce il comportamento del copilot:
- Risponde in italiano
- Struttura la risposta (analisi, proposta, BOM, setpoint, test plan)
- Cita le fonti con numero [1], [2], ecc.
- Usa `TBD` quando l'informazione non è nelle fonti (non hallucina)

---

## Modello OpenAI

Configurato via `.env`:
```
OPENAI_MODEL=gpt-5.2
OPENAI_DEEP_RESEARCH_MODEL=o3-deep-research
```

`gpt-5.2` è il modello principale per il copilot quotidiano.
`o3-deep-research` è usato per sessioni di ricerca profonda (settimanale, `prompts/deep_research_weekly.md`).

---

## living_spec.md

Ogni risposta del copilot (se la checkbox è attiva) viene appended a `storage/artifacts/living_spec.md`. Questo file funziona come notebook di progettazione accumlativo: contiene la storia di tutte le sessioni di design copilot. Non è generato automaticamente dalla pipeline.

Per leggerlo:
```bash
cat storage/artifacts/living_spec.md
```

---

## Limiti e considerazioni

### Qualità RAG dipende dalla KB
Se la KB non ha documenti su un argomento specifico, il copilot risponderà con molti `TBD`. Il modo corretto per migliorare è:
1. Aggiungere query specifiche in `configs/focus.yaml`
2. Aspettare il prossimo run del cron
3. Verificare nel report che i nuovi documenti siano stati ingestionati

### Context window
Con `COPILOT_MAX_CONTEXT_CHARS=18000` e chunk da ~2200 chars, il contesto include al massimo ~8 chunk "interi". Con `topk=10` e dedup per URL, si ottengono ~8-10 sorgenti distinte nel contesto.

### Costo API
Ogni chiamata al copilot con `gpt-5.2` e ~18k chars di contesto corrisponde a ~5000-6000 token. Usare "Preview evidence" prima di "Run Copilot" per verificare che le sorgenti siano pertinenti.
