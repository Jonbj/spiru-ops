# Knowledge Lifecycle — come nascono, vivono e muoiono i dati

## Schema completo storage/

```
storage/
├── raw/                           # Download originali — permanenti
│   ├── {domain}_{path}__{hash8}.html
│   ├── {domain}_{path}__{hash8}.pdf
│   └── {domain}_{path}__{hash8}.{ext}.sha256    # SHA256 per dedup
│
├── parsed/                        # Testo estratto — permanenti
│   ├── {domain}_{path}__{hash8}.txt             # Testo pulito (boilerplate rimosso)
│   └── {domain}_{path}__{hash8}.meta.json       # Metadati documento
│
├── state/                         # Artefatti per-run — retention 30 giorni
│   ├── {RUN_ID}_candidates.jsonl     # URL candidati (da discover.py)
│   ├── {RUN_ID}_ingested.json        # Sommario ingest + metadati docs
│   ├── {RUN_ID}_indexed.json         # Sommario indexing (points_upserted, ecc.)
│   ├── {RUN_ID}_strain_seeds.jsonl   # Seed ceppi (opzionale)
│   ├── {RUN_ID}_qc_fail.json         # Creato solo se QC FAIL
│   ├── seen_urls.jsonl               # Stato globale: URL già visti (max 15k)
│   └── seen_doi.jsonl                # Stato globale: DOI già processati
│
├── artifacts/                     # Report e aggregati — permanenti
│   ├── {RUN_ID}_report.md            # Report per run
│   ├── {YYYY-MM-DD}_daily_aggregate.md  # Aggregato giornaliero
│   ├── {YYYY-MM-DD}_kb_dedup_report.md  # Report dedup cross-run
│   └── living_spec.md                # Note di progettazione (append dal copilot)
│
├── qdrant/                        # Volume Docker Qdrant — permanente
│   └── collections/docs_chunks/   # Vettori + payload
│
├── logs/                          # Log cron — permanenti (no rotation)
│   ├── cron_daily_{YYYY-MM-DD}.log   # Log completo run (usare questo per debug)
│   └── cron_crontab.log              # Solo output pre-blocco (ignorabile)
│
├── backlog/                       # Coda OCR — gestita da ocr_backlog.py
│   └── ocr_queue.jsonl
│
└── pgdata/                        # PostgreSQL — non usato attivamente
```

---

## Ciclo di vita di un documento

### 1. Scoperta (discover.py)
Un URL entra in `{RUN_ID}_candidates.jsonl` con metadati preliminari:
- `url`, `title`, `focus`, `source` (brave/openalex), `score`, `doi` (se noto), `published_at`

### 2. Pre-filtro (ingest.py)
L'URL viene confrontato con `seen_urls.jsonl`:
- **Se già visto** → skip (`already_seen`). Non viene scaricato né conteggiato come fallimento.
- **Se dominio negato** (`deny_domains`) → skip (`denied_domain`).

### 3. Download
Il file viene scritto in `storage/raw/`. Il nome file è deterministico:
```
{domain}_{path sanitized}__{sha256[:8]}.{ext}
```
Esempio: `link.springer.com_article_10.1186-s40643-025__3b9280eb.pdf`

### 4. Parsing
Testo pulito → `storage/parsed/{...}.txt`
Metadati → `storage/parsed/{...}.meta.json`

Il `.meta.json` include:
```json
{
  "url": "https://...",
  "raw_path": "storage/raw/...",
  "parsed_path": "storage/parsed/....txt",
  "focus": "pbr_airlift_geometry_and_scale_down",
  "spirulina_score": 0.72,
  "spirulina_terms": ["spirulina", "arthrospira"],
  "doi": "10.1186/...",
  "publication_year": 2024,
  "content_hash": "sha256:abcdef...",
  "fetched_at": "2026-03-07T10:21:21Z",
  "text_stats": {
    "raw_chars": 45230,
    "clean_chars": 32100,
    "boilerplate_share": 0.12
  }
}
```

### 5. Arricchimento (enrich_doi_oa.py)
Se il documento ha un DOI, il `.meta.json` viene aggiornato con:
```json
{
  "oa_url": "https://bioresourcesbioprocessing.springeropen.com/...",
  "oa_pdf_url": "https://bioresourcesbioprocessing.springeropen.com/counter/pdf/...",
  "unpaywall": {"is_oa": true, "oa_status": "gold", "host_type": "publisher"},
  "crossref": {
    "type": "journal-article",
    "publisher": "Springer Science and Business Media LLC",
    "container_title": "Bioresources and Bioprocessing",
    "issued": {"date-parts": [[2025, 3, 5]]},
    "is_referenced_by_count": 0
  }
}
```

### 6. Indicizzazione (index.py)
Il testo viene chunkizzato e ogni chunk diventa un punto in Qdrant:
- **ID punto**: `int(sha1(url + content_hash), 16) % 10^12 + chunk_index`
  → Stabile: stesso documento + chunk = stesso ID → upsert idempotente
- **Vettore**: embedding 384-dim (all-MiniLM-L6-v2)
- **Payload**: url, domain, focus, spirulina_score, title, testo del chunk

### 7. Stato "seen"
Dopo l'ingest, l'URL viene aggiunto a `seen_urls.jsonl`.
Il DOI (se estratto) viene aggiunto a `seen_doi.jsonl`.

Questi file persistono cross-run e garantiscono che lo stesso contenuto non venga ri-scaricato.

---

## Deduplication

Tre livelli:

| Livello | Dove | Quando | Meccanismo |
|---------|------|--------|-----------|
| URL | `seen_urls.jsonl` | Pre-ingest | Normalizzazione URL + hash |
| DOI | `seen_doi.jsonl` | Pre-discovery (penalità) | DOI canonico (senza `doi:` prefix) |
| Content | `content_hash` in Qdrant | Post-index | sha256 del testo estratto |

---

## Crescita e pruning

### Crescita attesa

Con 4 run/giorno e ~27 doc/run (media osservata):
- **`storage/raw/`** e **`storage/parsed/`**: ~108 doc/giorno × 30 giorni = ~3240 file/mese. Nessun pruning sui file grezzi.
- **`storage/state/`**: ~12 file/giorno (3 artefatti × 4 run). Pruning automatico a 30 giorni → max ~360 file.
- **`seen_urls.jsonl`**: ~27 URL/run × 4 run/giorno = ~108/giorno. Capped a 15000 righe (~4.5 mesi).
- **Qdrant**: ~636 punti/run × 4 run/giorno = ~2544 punti/giorno. A 67000+ punti già (a marzo 2026).

### Pruning automatico

`prune_artifacts.sh` (eseguito alla fine di ogni run):
- Elimina `state/` files > 30 giorni: `_candidates.jsonl`, `_ingested.json`, `_indexed.json`, `_strain_seeds.jsonl`, `_qc_fail.json`
- Cappa `seen_urls.jsonl` a 15000 righe (tail delle più recenti)
- Stampa uso disco `storage/`

### Cosa NON viene prunato

- `storage/raw/` — download originali (permanenti)
- `storage/parsed/` — testi estratti (permanenti)
- `storage/artifacts/` — report (permanenti)
- Qdrant — vettori (permanenti, ma upsert idempotente)
- `seen_doi.jsonl` — cresce senza limite (tipicamente piccolo: ~141 DOI)

---

## RUN_ID — identità stabile del run

`RUN_ID` è generato una volta sola in `cron_run_daily.sh`:
```bash
export RUN_ID="${RUN_ID:-$(date -u +%Y-%m-%dT%H%M%SZ)}"
```
Formato: `YYYY-MM-DDTHHMMSSZ` (es. `2026-03-07T102121Z`)

**Perché è critico**:
- Previene il **midnight split**: se discover parte alle 23:55 e ingest alle 00:05, senza RUN_ID stabile i due step avrebbero path diversi e il run fallirebbe
- Rende il **retry sicuro**: l'attempt 2 riusa gli artefatti dell'attempt 1 invece di ripartire da zero
- Ogni run è **isolato e diagnosticabile**: tutti gli artefatti portano lo stesso prefisso

**Dove viene letto**: tutti i moduli Python leggono da `pipelines/common.py → run_id()`, che legge `os.getenv("RUN_ID")` o genera un fallback UTC se non presente.

---

## `living_spec.md`

File speciale in `storage/artifacts/living_spec.md`.
Non viene generato dalla pipeline automaticamente. Viene aggiornato (append) ogni volta che si usa il copilot Streamlit con la checkbox "Append answer to living_spec.md" attiva. Serve come notebook di progettazione: accumula le risposte del copilot nel tempo.
