# Pipeline — dettaglio step per step

## Entrypoint: `pipelines/cron_run_daily.sh`

Il cron **non invoca mai `daily.sh` direttamente**. Invoca sempre `cron_run_daily.sh`, che è il wrapper responsabile di:

1. Caricare `.env`
2. Applicare il profilo runtime (`profiles.sh`)
3. Generare `RUN_ID` stabile (una volta sola, esportato a tutti i figli)
4. Acquisire un flock lock su `storage/state/cron_daily.lock` — impedisce concorrenza
5. Registrare `trap EXIT → docker compose down` — i container si fermano sempre, anche su errore precoce
6. Avviare i Docker services (`docker compose up -d qdrant unstructured grobid`)
7. Fare health check HTTP su qdrant (60s), unstructured (90s), grobid se `GROBID_ENABLE=1` (60s)
8. Invocare `daily.sh` (attempt 1)
9. Distinguere exit code:
   - `0` → PASS, fine
   - `2` → QC FAIL (da evaluate.py) → non retry, logga WARN, notifica webhook opzionale
   - altro → infra failure → restart unstructured → attempt 2
10. Scrivere `[DONE] duration=Xs` nel log

**Log principale**: `storage/logs/cron_daily_YYYY-MM-DD.log`
Il blocco `{} >> $LOG_FILE 2>&1` cattura tutto l'output interno. Il cron di sistema redirige solo il pre-blocco su `cron_crontab.log` (poche righe, ignorabile). Per debug usare sempre `cron_daily_*.log`.

---

## Inner pipeline: `pipelines/daily.sh`

Eseguita da `cron_run_daily.sh` (e direttamente per run manuali).

Sequenza:
1. Carica `.env`, applica profilo
2. Sceglie l'interprete Python (`.venv/bin/python` → `python3` → `python`)
3. Crea directory `storage/state/` e `storage/artifacts/`
4. Esporta i path per-run canonici (tutti derivati da `RUN_ID`):
   - `STRAIN_SEEDS_PATH`, `CANDIDATES_PATH`, `INGESTED_PATH`, `INDEXED_PATH`, `REPORT_PATH`
5. Invoca in sequenza i moduli Python
6. Gestisce `evaluate.py` senza propagare exit 2 (QC FAIL → run continua)
7. Invoca step best-effort (kb_validate, ocr_backlog, aggregate_daily, prune_artifacts)

---

## Step 0 (opzionale): `pipelines/seed_strains.py`

Abilitato solo se `SEED_STRAINS=1`. Carica da `configs/strain_seeds.yaml` una lista di ceppi noti (es. *A. platensis* SAG 21.99) e genera URL di catalogo per fornire un set di seed alla discovery.

Output: `{RUN_ID}_strain_seeds.jsonl`

---

## Step 1: `pipelines/discover.py`

**Cosa fa**: produce la lista di URL candidati da ingestionare.

### Fonti di discovery

**Brave Search API** (principale):
- Per ogni focus in `configs/focus.yaml` esegue N query Brave
- Ogni query ha varianti in italiano/francese/spagnolo e versioni per PDF, tesi, review, siti istituzionali (`.edu`, `.gov`, `.int`, FAO, EFSA, WHO)
- Risultati paginati, con diversificazione temporale (`since_days` rotante)

**OpenAlex API** (secondaria, per paper accademici):
- Usa la query OpenAlex definita per ogni focus in `configs/focus.yaml`
- Filtro per anno recente, open-access preferito
- Arricchisce con DOI, abstract, publication year, journal

### Scoring candidati

Ogni URL candidato riceve un punteggio basato su:
- `focus.base_score` (da `configs/scoring.yaml`, es. 30 per `pbr_airlift`)
- Bonus se il dominio è in `prefer_domains` (`.edu`, `.gov`, FAO, EFSA, CNR, ecc.)
- Penalità se DOI già visto in `seen_doi.jsonl` (−25 pt)
- Bonus se URL è un PDF diretto

### Deduplication

- Round-robin per-domain cap: max `DISCOVER_MAX_CAND_PER_DOMAIN=6` candidati per dominio per run
- Cap globale `MAX_TOTAL_CANDIDATES=800`
- `DENY_RESEARCHGATE=1`: skippa automaticamente ResearchGate (paywall duro)

### Output

`{RUN_ID}_candidates.jsonl` — una riga JSON per URL:
```json
{
  "url": "https://...",
  "title": "...",
  "focus": "pbr_airlift_geometry_and_scale_down",
  "source": "brave",
  "score": 42,
  "doi": "10.1016/...",
  "published_at": "2024-01-15"
}
```

---

## Step 2: `pipelines/ingest.py`

**Cosa fa**: scarica e parsifica ogni URL candidato, produce il testo pulito e i metadati.

### Pre-filtro

Prima del download, ogni URL viene filtrato:
- **seen_urls**: se l'URL (normalizzato) è già in `storage/state/seen_urls.jsonl` → skip (`already_seen`)
- **domain deny list**: se il dominio è in `configs/domains.yaml → deny_domains` → skip (`denied_domain`)

### Portfolio selection (diversità)

Con `INGEST_TARGET > 0` (default 200), invece di ingestionare tutti i 147+ candidati rimasti, l'ingest applica un algoritmo di **portfolio selection** che massimizza la diversità:
- Separa candidati in **exploitation** (domini già visti di recente nei run passati — `INGEST_HISTORY_DAYS=14`) e **exploration** (nuovi)
- Mix: `INGEST_EXPLORATION_PCT=70%` exploration + 30% exploitation
- Cap per-domain family: `INGEST_MAX_PER_DOMAIN=10`

### Download

Per ogni URL candidato selezionato:
1. HEAD request (timeout `HEAD_TIMEOUT_S`) per rilevare content-type e dimensione
2. Se PDF e size > `MAX_DOWNLOAD_MB` → skip
3. Download completo con timeout appropriato (`PDF_REQUEST_TIMEOUT_S` o `HTML_REQUEST_TIMEOUT_S`)
4. Salva in `storage/raw/{domain}_{path}__{sha256[:8]}.{ext}` + `.sha256`

### Circuit breaker

Per evitare di perdere minuti su domini problematici:
- Se un dominio restituisce ≥ `MAX_403_PER_DOMAIN` errori 403 → quel dominio è "bloccato" per questo run
- Stesso per `MAX_429_PER_DOMAIN` (rate limit)

### Parsing testo

**Unstructured API** (prioritaria):
- Chiamata HTTP a `http://localhost:8000` (Docker)
- Gestisce PDF complessi, HTML, DOCX
- Se il file è > `UNSTRUCTURED_MAX_MB` → bypass, usa fallback locale

**Grobid** (se `GROBID_ENABLE=1`):
- Chiamata a `http://localhost:8070`
- Usato per paper accademici: estrae header strutturato (autori, abstract, DOI, affiliazioni)
- Gated da `looks_like_paper_url(url)` che detecta URL DOI/arXiv/PubMed

**Fallback locale**:
- PDF: pypdf per estrazione testo grezzo
- HTML: `common.soup_text()` — BeautifulSoup + boilerplate removal

### Scoring Spirulina

Per ogni documento parsificato, `relevance.py` calcola un punteggio [0,1]:
- Termini core (peso 3.0): `spirulina`, `arthrospira`, `limnospira`, `a. platensis`
- Termini contesto (peso 0.6–2.2): `zarrouk`, `phycocyanin`, `cyanobacteria`, `bicarbonate`
- Penalità per altri algae (`chlorella`, `dunaliella`, ecc.) solo se i termini core sono assenti
- Formula: `score = 1 - exp(-pos_weight/5.0) * (1 - confounder_penalty)`
- Titolo e URL pesano di più del body (moltiplicatore +0.8 e +0.4 rispettivamente)

### Output

- `storage/raw/{...}` — file originali
- `storage/parsed/{...}.txt` — testo pulito
- `storage/parsed/{...}.meta.json` — metadati documento:
  ```json
  {
    "url": "...",
    "title": "...",
    "focus": "...",
    "spirulina_score": 0.72,
    "spirulina_terms": ["spirulina", "arthrospira"],
    "doi": "10.1016/...",
    "publication_year": 2023,
    "content_hash": "sha256...",
    "text_stats": {"boilerplate_share": 0.12, ...}
  }
  ```
- `{RUN_ID}_ingested.json` — sommario run:
  ```json
  {
    "ingested": [...lista meta per doc...],
    "failures_by_reason": {"timeout": 5, "403": 3},
    "failures_total": 8,
    "skipped": {"already_seen": 318, "denied_domain": 2}
  }
  ```
- `storage/state/seen_urls.jsonl` — URL visti (append-only, capped a 15k da prune)
- `storage/state/seen_doi.jsonl` — DOI visti (append-only)

---

## Step 3: `pipelines/enrich_doi_oa.py`

**Cosa fa**: per ogni documento con DOI, arricchisce i metadati via:

### Unpaywall
- Servizio gratuito (`unpaywall.org/products/api`)
- Data un DOI, restituisce tutte le location open-access del paper
- Richiede solo `UNPAYWALL_EMAIL` in `.env` (nessuna API key)
- Se trova un URL PDF open-access → aggiunge `oa_pdf_url` al documento
- Questo URL può essere usato in run futuri per scaricare il PDF completo (oggi non automatico)

### Crossref
- API REST pubblica, rate-limit generoso con `CROSSREF_MAILTO`
- Restituisce: tipo documento, publisher, journal, data pubblicazione, citation count
- Aggiorna `title` se quello trovato è più accurato dell'estratto dall'HTML

### Output
- Aggiorna in-place `{RUN_ID}_ingested.json` aggiungendo per ogni doc con DOI:
  - `oa_url`, `oa_pdf_url` (da Unpaywall)
  - `unpaywall: {is_oa, oa_status, host_type}`
  - `crossref: {type, publisher, container_title, issued, citation_count}`
- Aggiorna il `.meta.json` corrispondente in `storage/parsed/`

---

## Step 4: `pipelines/index.py`

**Cosa fa**: trasforma i testi parsificati in vettori e li inserisce in Qdrant.

### Filtro pre-embedding
Solo i documenti con `spirulina_score >= INDEX_MIN_SPIRULINA_SCORE` (default 0.25) vengono indicizzati. I documenti sotto soglia vengono skippati (`docs_skipped_low_relevance`). Questo mantiene la KB focalizzata su Spirulina.

### Chunking
`common.chunk_text(text, max_chars=2200, overlap=240)`:
- Divide il testo in segmenti da max 2200 caratteri con overlap di 240
- L'overlap preserva il contesto ai confini dei chunk
- Chunking character-based (no tokenizer) → stabile, senza dipendenze extra

### Embedding
- Modello: `sentence-transformers/all-MiniLM-L6-v2`
- Dimensione vettore: 384
- `normalize_embeddings=True` — vettori unit-norm, cosine similarity ≡ dot product

### Upsert Qdrant

ID punto: `int(sha1(url + content_hash), 16) % 10^12 + chunk_index`
- Stabile: lo stesso documento+chunk produce sempre lo stesso ID
- Re-eseguire l'index aggiorna il payload senza duplicare

Payload per chunk:
```json
{
  "url": "...",
  "source_url": "...",
  "domain": "springer.com",
  "doc_id": "a3b4c5d6e7f8",
  "focus": "pbr_airlift_geometry_and_scale_down",
  "title": "...",
  "published_at": "2024-01",
  "spirulina_score": 0.72,
  "spirulina_terms": ["spirulina", "arthrospira"],
  "is_spirulina": true,
  "chunk_i": 0,
  "text": "...primo chunk di testo..."
}
```

Batch size: `QDRANT_UPSERT_BATCH=64`

### Output
`{RUN_ID}_indexed.json`:
```json
{
  "collection": "docs_chunks",
  "embed_model": "sentence-transformers/all-MiniLM-L6-v2",
  "docs_indexed": 27,
  "docs_skipped_low_relevance": 0,
  "points_upserted": 636
}
```

---

## Step 5: `pipelines/report.py`

Genera un report Markdown in `storage/artifacts/{RUN_ID}_report.md` con:
- Pipeline summary (candidati trovati, ingestionati, indicizzati, failure totali)
- Candidate health (distribuzione per source, focus, dominio; % PDF; novelty vs seen_urls)
- Failure categories con esempi (max 2 per categoria)
- Ingest distribution (per focus, per dominio)
- Source diversity KPIs (HHI, entropia, Jaccard vs run precedente)
- Segnali Spirulina (top termini, avg score)
- Note interpretative (soglie consigliate, segnali di allarme)

---

## Step 6: `pipelines/evaluate.py`

**Cosa fa**: 9 controlli di qualità sul run appena completato.

Se tutti i check passano → exit 0 (PASS).
Se uno o più falliscono → exit 2 (FAIL).

`daily.sh` cattura exit 2 senza propagarlo: scrive `{RUN_ID}_qc_fail.json` e il run continua.

### Check e soglie (tunable via env)

| Check | Variabile env | Default | Cosa misura |
|-------|--------------|---------|-------------|
| `candidates>=min` | `QC_MIN_CANDIDATES` | 200 | Discovery funziona? |
| `indexed_points>=min` | `QC_MIN_INDEXED_POINTS` | 200 | Index funziona? (per-run, non cumulativo) |
| `penal_share<=max` | `QC_MAX_PENAL_SHARE` | 35% | % documenti da domini penalizzati |
| `missing_pub<=max` | `QC_MAX_MISSING_PUB_SHARE` | 60% | % documenti senza anno pubblicazione |
| `prefer_share>=min` | `QC_MIN_PREFER_SHARE` | 10% | % documenti da fonti preferite (.edu/.gov/FAO/EFSA) |
| `top5_domain_share<=max` | `QC_MAX_TOP5_DOMAIN_SHARE` | 70% | Concentrazione domini (top 5) |
| `unique_domains>=min` | `QC_MIN_UNIQUE_DOMAINS` | 60 (dynamic) | Diversità sorgenti |
| `spirulina_share>=min` | `QC_MIN_SPIRULINA_SHARE` | 35% | % doc con spirulina_score ≥ 0.50 |
| `avg_spirulina_score>=min` | `QC_MIN_AVG_SPIRULINA_SCORE` | 0.28 | Score medio Spirulina |

**Nota sul check `unique_domains`**: il minimo è dinamico:
`min(MIN_UNIQUE_DOMAINS, ceil(n_ingested × MIN_UNIQUE_DOMAINS_SHARE))`.
Questo evita che un run con pochi documenti fallisca per un threshold impossibile.

**Nota su `QC_MIN_INDEXED_POINTS`**: calibrato su 37 run storici (min osservato = 355, tipico 466–800). Il valore 200 è il floor per "indexing rotto" — non un target di qualità.

---

## Step 7: `pipelines/kb_validate.py` (best-effort)

Scansiona tutti i file `*_ingested.json` in `storage/state/` per trovare:
- Duplicati DOI cross-run
- Duplicati content_hash cross-run
- Produce `storage/artifacts/{YYYY-MM-DD}_kb_dedup_report.md`

**Attenzione**: cresce lentamente col tempo perché scansiona tutto `storage/state/`. Con 4 run/giorno e retention di 30 giorni, ci sono ~480 file. Non è un problema attuale.

---

## Step 8: `pipelines/ocr_backlog.py` (best-effort)

Processa PDF in coda OCR (`storage/backlog/ocr_queue.jsonl`). I PDF vengono messi in coda da `ingest.py` quando hanno testo troppo scarso (probabilmente scan non OCR-izzati).

Usa `ocrmypdf` (deve essere installato sul sistema). Timeout per file: 300s (default). Limite documenti per run: `OCR_LIMIT=5`.

---

## Step 9: `pipelines/aggregate_daily.py` (best-effort)

Aggrega statistiche di tutti i run del giorno (ci possono essere fino a 4 run/giorno). Produce `storage/artifacts/{YYYY-MM-DD}_daily_aggregate.md`.

---

## Step 10: `pipelines/prune_artifacts.sh` (best-effort)

Chiamato alla fine di ogni run:
1. Elimina da `storage/state/` tutti i file `*_candidates.jsonl`, `*_ingested.json`, `*_indexed.json`, `*_strain_seeds.jsonl`, `*_qc_fail.json` più vecchi di `ARTIFACT_RETENTION_DAYS` (default 30 giorni)
2. Cappa `seen_urls.jsonl` alle ultime `SEEN_URLS_MAX_LINES` (default 15000) righe
3. Stampa l'uso disco di `storage/`
