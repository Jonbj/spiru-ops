# Servizi Docker — Qdrant, Unstructured, Grobid

## Panoramica

Tre container Docker supportano la pipeline. Sono definiti in `docker-compose.yml` e gestiti da `cron_run_daily.sh` (avvio + shutdown automatico).

```bash
docker compose up -d qdrant unstructured grobid    # avvio
docker compose down                                 # shutdown (automatico via trap EXIT)
docker compose ps                                   # stato
```

---

## Qdrant — Vector Database

| Parametro | Valore |
|-----------|--------|
| Immagine | `qdrant/qdrant:v1.11.3` |
| Porta | `6333` (HTTP REST) |
| Volume | `./storage/qdrant:/qdrant/storage` |
| Health check | `wget -qO- http://localhost:6333/` |

### Cosa fa

Qdrant è il database vettoriale dove vivono tutti i chunk embeddati del KB. Ogni documento viene suddiviso in chunk di testo, ognuno embededdato con `all-MiniLM-L6-v2` (384 dimensioni) e inserito in Qdrant come "punto" con payload JSON.

### Collection: `docs_chunks`

Configurazione:
- Dimensione vettore: 384
- Metrica distanza: cosine (equivalente a dot product con vettori normalizzati)

Ogni punto ha payload:
```json
{
  "url": "https://...",
  "domain": "link.springer.com",
  "doc_id": "a3b4c5d6",
  "focus": "pbr_airlift_geometry_and_scale_down",
  "title": "...",
  "spirulina_score": 0.72,
  "is_spirulina": true,
  "chunk_i": 0,
  "text": "...testo del chunk..."
}
```

### Interazione

- **`pipelines/qdrant_rest.py`**: thin wrapper REST (no qdrant-client SDK) — usato da `index.py`
- **`qdrant-client` SDK**: usato da `rag_cloud.py` per query RAG
- **URL diretto**: `http://localhost:6333/collections/docs_chunks` → info sulla collection

### Stato attuale (marzo 2026)
- Punti totali: ~67.000+
- Crescita: ~2500 punti/giorno (4 run × ~636 punti/run)

### Backup

I dati Qdrant sono in `storage/qdrant/` (volume Docker). Per backup:
```bash
cp -r storage/qdrant storage/qdrant_backup_$(date +%F)
```

---

## Unstructured — Parsing API

| Parametro | Valore |
|-----------|--------|
| Immagine | `downloads.unstructured.io/unstructured-io/unstructured-api@sha256:3b9280eb...` |
| Porta | `8000` |
| Health check | `wget -qO- http://localhost:8000/healthcheck` |
| Memoria limite | `4g` |
| SHM | `1gb` |
| tmpfs `/tmp` | `2g` |

> L'immagine è pinnata a un digest specifico (non `latest`) per evitare aggiornamenti automatici che potrebbero rompere silenziosamente il parsing.

### Cosa fa

Unstructured è un'API che riceve un file (PDF, HTML, DOCX, ecc.) e restituisce il testo strutturato estratto. È più potente del semplice pypdf perché:
- Gestisce layout multi-colonna
- Riconosce tabelle, intestazioni, elenchi
- Migliore OCR per PDF scansionati (con dependency opzionali)

### Come viene usato

In `ingest.py`, se `UNSTRUCTURED_ENABLE=1` (default), i file sono inviati all'API HTTP locale:
```python
r = requests.post(
    "http://localhost:8000/general/v0/general",
    files={"files": (filename, file_bytes, content_type)},
    data={"strategy": "fast"},
    timeout=120
)
```

Se il file è > `UNSTRUCTURED_MAX_MB` (15 MB in `kb_first`, 25 MB in `balanced`), viene bypassed e si usa pypdf direttamente. Questo perché Unstructured è più lento e può crashare su PDF molto grandi (OOM).

### Failure mode tipico: OOM

Unstructured può essere killed dal kernel (OOM) su PDF molto grandi o complessi. Questo causa un exit non-zero di `daily.sh`, che `cron_run_daily.sh` interpreta come infra failure e:
1. Esegue `docker compose restart unstructured`
2. Aspetta che torni healthy (90s)
3. Lancia attempt 2

### Gestione health check

Il health check in `cron_run_daily.sh` aspetta fino a 90 secondi (90 tentativi × 1s):
```bash
wait_http_200 "$UNSTRUCTURED_HEALTH_URL" "unstructured" 90 1
```
Se Unstructured non risponde entro 90s, il run fallisce.

---

## Grobid — Academic Paper Header Extraction

| Parametro | Valore |
|-----------|--------|
| Immagine | `lfoppiano/grobid:latest-crf` |
| Porta | `8070` |
| Health check | `http://localhost:8070/api/isalive` |
| Memoria limite | `3g` |
| JVM | `-Xms512m -Xmx2g` |
| Volume | `./storage/grobid:/opt/grobid/grobid-home/tmp` |

> Nota: Grobid usa ancora `latest-crf` (non pinnato come Unstructured). Questo è low-risk perché Grobid è un progetto più stabile.

### Cosa fa

Grobid (GeneRation Of BIbliographic Data) è un tool ML per l'estrazione di metadati strutturati da paper accademici. A differenza di Unstructured (orientato al testo grezzo), Grobid estrae:
- **Header**: titolo, autori, affiliazioni, abstract, DOI, journal, anno
- **References** (se `GROBID_FULLTEXT=1`): lista bibliografica strutturata

### Come viene usato

In `ingest.py`, Grobid viene chiamato **solo se**:
```python
if GROBID_ENABLE and looks_like_paper_url(final_url):
    ...
```

La funzione `looks_like_paper_url()` detecta URL tipici di paper accademici:
- Contengono `doi.org`, `arxiv.org`, `pubmed`, `ncbi.nlm`, `springer.com/article`, `tandfonline.com/doi`, ecc.

Il risultato di Grobid (header strutturato in XML TEI) viene parsificato per estrarre DOI, autori, abstract, anno — integrati nel `.meta.json`.

### Configurazione in produzione

In `.env`:
```
GROBID_ENABLE=1
GROBID_FULLTEXT=0
```
In produzione Grobid è **sempre attivo** (sovrascrive il default del profilo `kb_first` che lo disabilita). `GROBID_FULLTEXT=0` significa solo header — abbastanza per DOI e metadati.

### Health check

Il health check è condizionale in `cron_run_daily.sh`:
```bash
if [[ "${GROBID_ENABLE:-0}" == "1" ]]; then
    wait_http_200 "$GROBID_HEALTH_URL" "grobid" 60 1
fi
```
Timeout: 60s. Grobid ci mette tipicamente 10-15s ad avviarsi.

### Quando usarlo

Grobid aggiunge valore principalmente per paper accademici con DOI dove Unstructured non riesce a estrarre il DOI dall'HTML. In pratica:
- Paper da Springer, Wiley, MDPI, Frontiers → Grobid estrae DOI + anno → enrich_doi_oa usa il DOI per Unpaywall
- HTML di landing page → Grobid non applicabile, si usa solo Unstructured

---

## PostgreSQL (non attivo)

Il `docker-compose.yml` definisce anche un servizio `postgres:16` ma:
- Non è incluso in `SERVICES="${SERVICES:-qdrant unstructured grobid}"`
- Non viene avviato dal cron
- Non è usato da alcun modulo Python attualmente
- Presente per future estensioni (es. metadata store relazionale)
