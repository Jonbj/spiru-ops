# REVIEW_DAILY_PIPELINE.md — spiru-ops

> Review generata il 2026-03-06. Aggiornata con risposte alle domande aperte.
> Ultimo aggiornamento: 2026-03-06 — patch applicate e verificate con run manuale.

---

## Changelog patch

| Data | Ref | File/azione | Esito verifica |
|------|-----|-------------|----------------|
| 2026-03-06 | H3, H4 | `cron_run_daily.sh`: `flock -n` (lock), `trap EXIT` (docker down), distinzione exit 2 QC vs infra, logging `[START]/[STEP]/[OK]/[DONE] duration=` | ✅ Run manuale: `[DONE] duration=1443s`, Docker down eseguito, lock attivo |
| 2026-03-06 | H2 | `daily.sh`: `evaluate.py` non propaga exit 2 — scrive `{RUN_ID}_qc_fail.json` e continua | ✅ Run manuale: QC FAIL → run continuato fino a `aggregate_daily` e `prune` |
| 2026-03-06 | M1 | `evaluate.py`: `indexed_points` usa `points_upserted` da `{RUN_ID}_indexed.json` (per-run, non cumulativo Qdrant) | ✅ Run manuale: 489 punti del run corrente rilevati correttamente |
| 2026-03-06 | M1 | `.env`: `QC_MIN_INDEXED_POINTS=200` — ricalibrato su 37 run storici (min=355, tipico=466–800; 200 = floor per indexing rotto) | ✅ |
| 2026-03-06 | M2 | `pipelines/prune_artifacts.sh`: nuovo script — elimina state files >30gg, cappa `seen_urls.jsonl` a 15k righe, stampa uso disco | ✅ Run manuale: `storage/ 1.7G`, 0 file rimossi (tutti <30gg) |
| 2026-03-06 | M2 | `daily.sh`: hook `prune_artifacts.sh` (best-effort) aggiunto a fine run | ✅ |
| 2026-03-06 | M5 | `discover.py`: eccezioni Brave e OpenAlex loggano `WARN` invece di `pass` silenzioso | ✅ (sintassi ok; visibile su prossimo errore API reale) |
| 2026-03-06 | H3 | `.env`: `chmod 600` — API keys non più world-readable | ✅ `-rw-------` |
| 2026-03-06 | M4 | `.env`: aggiunte `UNPAYWALL_EMAIL=` e `CROSSREF_MAILTO=` (da compilare per attivare Unpaywall) | ⏳ Richiede email utente |
| 2026-03-06 | M7 | `ops/cron/daily.cron`: aggiornato con cron reale di produzione | ✅ |
| 2026-03-06 | L6 | `storage/state/daily.lock`: rimosso (residuo, non usato) | ✅ |
| 2026-03-07 | L2 | `cron_run_daily.sh`: `GROBID_HEALTH_URL` + `wait_http_200 grobid` condizionale su `GROBID_ENABLE=1` | ✅ |
| 2026-03-07 | M4 | `.env`: `UNPAYWALL_EMAIL=stefano.delgobbo@gmail.com`, `CROSSREF_MAILTO=stefano.delgobbo@gmail.com`, `USER_AGENT` aggiornato | ✅ |
| 2026-03-07 | M6 | `ocr_backlog.py`: timeout 1800s → 300s; `.env`: `OCR_LIMIT=5` | ✅ |
| 2026-03-07 | L4 | `docker-compose.yml`: immagine Unstructured pinnata al digest `sha256:3b9280eb` (era `latest`) | ✅ |

---

## 1. Executive Summary

**spiru-ops** è un sistema di knowledge-base building automatico per la coltivazione di spirulina/arthrospira. Ogni giorno lancia 4 run (02:10, 08:10, 14:10, 20:10 UTC) con profilo `kb_first`. La pipeline end-to-end:

1. Scopre URL candidati da Brave Search + OpenAlex
2. Scarica e parsifica HTML/PDF (via Unstructured API in Docker)
3. Arricchisce metadati bibliografici (Crossref + Unpaywall)
4. Embeds i chunk testuali e li indicizza in Qdrant
5. Genera report Markdown e controlla la qualità del run

Il design è **solido per un progetto di ricerca personale**: buon uso di RUN_ID stabile, profili runtime, fallback su OOM di Unstructured, artefatti per run diagnosticabili.

**Criticità risolte (2026-03-06):**
- ✅ **Locking `flock`** aggiunto a `cron_run_daily.sh` — una sola istanza alla volta
- ✅ **`evaluate.py` QC FAIL** non abortisce più il run — scrive `{RUN_ID}_qc_fail.json` e continua
- ✅ **`.env` chmod 600** — API keys non più world-readable
- ✅ **`trap EXIT`** → `docker compose down` sempre eseguita
- ✅ **`prune_artifacts.sh`** — cleanup state files >30gg + cap `seen_urls.jsonl`
- ✅ **`indexed_points` per-run** in `evaluate.py` + soglia ricalibrata a 200
- ✅ **WARN Brave/OpenAlex** invece di `pass` silenzioso
- ✅ **`ops/cron/daily.cron`** aggiornato con cron reale

**Criticità aperte:**
- ✅ **`UNPAYWALL_EMAIL`** configurata — Unpaywall attivo da prossimo run — 2026-03-07
- ⚠️ **Nessuna notifica** su run falliti — solo log locali (webhook opzionale già predisposto in `cron_run_daily.sh`)
- ✅ **Immagine Unstructured** pinnata al digest corrente — 2026-03-07
- ✅ **Grobid health check** aggiunto in `cron_run_daily.sh` (condizionale su `GROBID_ENABLE=1`) — 2026-03-07

---

## 2. Pipeline Map dettagliata

### Cron reale (produzione)

```
10 2,8,14,20 * * *  cd /home/stefano/Documents/Projects/spiru-ops && \
  PROFILE=kb_first /bin/bash pipelines/cron_run_daily.sh \
  >> storage/logs/cron_crontab.log 2>&1
```

> **Nota doppio logging**: il cron redirige su `cron_crontab.log`, ma `cron_run_daily.sh` cattura l'intero blocco interno su `cron_daily_YYYY-MM-DD.log`. Il primo file riceve solo il pre-blocco (poche righe); il secondo riceve tutto. Per debug usare sempre `cron_daily_*.log`.

### Diagramma testuale

```
cron (10 2,8,14,20 UTC — PROFILE=kb_first)
  └─► cron_run_daily.sh
        ├── load .env
        ├── source profiles.sh (PROFILE=kb_first)
        ├── export RUN_ID=YYYY-MM-DDTHHMMSSZ
        ├── docker compose up -d qdrant unstructured grobid
        ├── wait_http_200 qdrant (60s)  ──► timeout → EXIT 1 [no docker down!]
        ├── wait_http_200 unstructured (90s) ──► timeout → EXIT 1 [no docker down!]
        │
        ├── [attempt 1] bash daily.sh
        │     ├── discover.py ──► {RUN_ID}_candidates.jsonl
        │     ├── ingest.py   ──► storage/raw/ + storage/parsed/ + {RUN_ID}_ingested.json
        │     ├── enrich_doi_oa.py (best-effort) ──► aggiorna {RUN_ID}_ingested.json
        │     ├── index.py    ──► Qdrant upsert + {RUN_ID}_indexed.json
        │     ├── report.py   ──► {RUN_ID}_report.md
        │     ├── evaluate.py ──► exit 0 (PASS) | exit 2 (FAIL) [⚠ FAIL aborts daily.sh!]
        │     ├── kb_validate.py (best-effort) ──► {YYYY-MM-DD}_kb_dedup_report.md
        │     ├── ocr_backlog.py (best-effort) ──► storage/backlog/ocr_queue.jsonl
        │     └── aggregate_daily.py (best-effort) ──► {YYYY-MM-DD}_daily_aggregate.md
        │
        ├── [se attempt 1 exit != 0] restart unstructured → wait 90s → [attempt 2]
        │   [⚠ questo innesca anche per QC FAIL, non solo OOM]
        │
        ├── docker compose down
        └── log principale: storage/logs/cron_daily_YYYY-MM-DD.log
```

### Tabella step-by-step

| # | Step | Comando | Input | Output | Failure mode | Idempotente? |
|---|------|---------|-------|--------|--------------|--------------|
| 0 | Wrapper env | `cron_run_daily.sh:42-56` | `.env`, env | `RUN_ID`, `PROFILE` export | N/A | Sì |
| 1 | Docker up | `docker compose up -d qdrant unstructured grobid` | `docker-compose.yml` | Servizi avviati | Docker non risponde → hang | No (side-effect) |
| 2 | Health check | `wait_http_200 qdrant/unstructured` | HTTP endpoints | Pronto o exit 1 | Timeout → exit 1, **no `docker down`** | Sì |
| 3 | discover.py | `python -m pipelines.discover` | `configs/scoring.yaml`, `configs/domains.yaml`, Brave API, OpenAlex API, `seen_doi.jsonl` | `{RUN_ID}_candidates.jsonl` | Eccezioni API silenziate con `pass` | Sì (sovrascrive) |
| 4 | ingest.py | `python -m pipelines.ingest` | `{RUN_ID}_candidates.jsonl`, Unstructured API | `storage/raw/`, `storage/parsed/`, `{RUN_ID}_ingested.json`, `seen_doi.jsonl`, `seen_urls.jsonl` | 403/429/timeout → skip doc; OOM → exit non-zero | Parzialmente (skip se seen) |
| 5 | enrich_doi_oa.py | `... \|\| warn` | `{RUN_ID}_ingested.json`, Crossref, Unpaywall | Aggiorna in-place `{RUN_ID}_ingested.json` + `.meta.json` | **`UNPAYWALL_EMAIL` non configurata → Unpaywall saltato** | Sì |
| 6 | index.py | `python -m pipelines.index` | `{RUN_ID}_ingested.json`, `storage/parsed/*.txt`, Qdrant | `{RUN_ID}_indexed.json`, punti vettoriali in Qdrant | Qdrant down → exit non-zero | Sì (upsert idempotente) |
| 7 | report.py | `python -m pipelines.report` | `{RUN_ID}_candidates.jsonl`, ingested, indexed, `seen_urls.jsonl` | `{RUN_ID}_report.md` | File mancanti → report vuoto | Sì (sovrascrive) |
| 8 | evaluate.py | `python -m pipelines.evaluate` | candidates, ingested, Qdrant API | stdout QC, exit 0/2 | **exit 2 → daily.sh abortisce → retry OOM** | Sì |
| 9 | kb_validate.py | `... \|\| warn` | Tutti `*_ingested.json` in `storage/state/` | `{YYYY-MM-DD}_kb_dedup_report.md` | Scansione cresce col tempo | Sì |
| 10 | ocr_backlog.py | `... \|\| warn` | `storage/backlog/ocr_queue.jsonl` | PDF OCR, queue riscritta | `ocrmypdf` mancante → skip; timeout 1800s/file | Sì |
| 11 | aggregate_daily.py | `... \|\| warn` | `*_ingested.json`, `*_indexed.json` del giorno | `{YYYY-MM-DD}_daily_aggregate.md` | Fallisce silenziosamente | Sì (sovrascrive) |
| 12 | Docker down | `docker compose down` | Servizi attivi | Servizi fermati | **Non eseguita se step 2 fallisce** | N/A |

---

## 3. "Focus" del daily run

Il pipeline usa focus tematici definiti in `configs/scoring.yaml` + `configs/focus.yaml` (PROFILE=`kb_first` in produzione):

| Focus | Cosa cerca |
|-------|-----------|
| `pbr_airlift_geometry_and_scale_down` | Design fotobioreattori airlift, kLa, gas holdup |
| `process_control_setpoints_ph_co2_temp` | Controllo pH, CO₂, temperatura colture |
| `illumination_led_commercial_roi` | LED spectrum, PPFD, efficienza fotonica |
| `harvesting_fresh_biomass_filtration` | Raccolta biomassa, microstrainer, filtrazione |
| `biomass_analytics_food_cosmetic_safety` | Safety EFSA/FDA, metalli pesanti, microbiologico |
| `certifications_protocols_food_cosmetic` | ISO 22000, HACCP, ISO 22716, GMP |
| + altri in `configs/focus.yaml` | PBR raceway, circular economy, partner locali, … |

**Definizione di "successo"** (`evaluate.py` con soglie env-tunable):
- `candidates >= 200`
- `indexed_points >= 1500` ← ⚠️ cumulativo Qdrant, non per-run (vedi Issue M1)
- `penal_share <= 35%` (domini penalizzati)
- `prefer_share >= 10%` (.edu/.gov/FAO/WHO/CNR)
- `top5_domain_share <= 70%`
- `unique_domains >= min(60, ceil(n_ing × 0.55))`
- `spirulina_share_ingested >= 35%` (docs con score >= 0.50)
- `avg_spirulina_score >= 0.28`

---

## 4. Knowledge Lifecycle

```
Dati grezzi (permanenti, no cleanup)
  storage/raw/{domain}_{path}__{hash}.{ext}         ← download originale (HTML/PDF)
  storage/raw/{...}.sha256                           ← hash per dedup

Testo estratto (permanente, no cleanup)
  storage/parsed/{...}.txt                           ← testo pulito
  storage/parsed/{...}.meta.json                     ← metadati (url, focus, spirulina_score, doi, …)

Stato run per-RUN_ID (permanente, no cleanup)
  storage/state/{RUN_ID}_candidates.jsonl
  storage/state/{RUN_ID}_ingested.json
  storage/state/{RUN_ID}_indexed.json
  storage/state/{RUN_ID}_strain_seeds.jsonl          ← opzionale

Stato globale cross-run (append-only, no cleanup pianificata)
  storage/state/seen_doi.jsonl      ← 141 DOI visti; cresce senza pruning
  storage/state/seen_urls.jsonl     ← 5.415 URL visti; cresce senza pruning
  storage/state/daily.lock          ← origine sconosciuta; non usato da nessun codice

Vettori (Qdrant — persistente)
  storage/qdrant/collections/docs_chunks/
  Payload chunk: url, focus, spirulina_score, text, chunk_i, content_hash, …

Report/Artefatti (permanenti)
  storage/artifacts/{RUN_ID}_report.md
  storage/artifacts/{YYYY-MM-DD}_daily_aggregate.md
  storage/artifacts/{YYYY-MM-DD}_kb_dedup_report.md
  storage/artifacts/latest_*.md                      ← copia più recente (non symlink)
  storage/artifacts/living_spec.md                   ← origine sconosciuta, non aggiornata dalla pipeline

Log
  storage/logs/cron_daily_{YYYY-MM-DD}.log           ← log completo del run (usare questo per debug)
  storage/logs/cron_crontab.log                      ← solo pre-blocco (poche righe); ignorabile
```

**Flusso di riuso cross-run:**
1. `discover.py` legge `seen_doi.jsonl` → down-rank DOI già processati (−25 pt)
2. `ingest.py` legge `seen_urls.jsonl` → skip URL già visti
3. `index.py` usa `content_hash` + `url` per IDs stabili → upsert idempotente
4. `kb_validate.py` scansiona tutti gli `*_ingested.json` → dedup globale DOI/hash

**Problema crescita**: con 4 run/giorno, `storage/state/` accumula ~12 file/giorno. `kb_validate.py` li scansiona tutti ad ogni run → rallentamento progressivo. Nessuna pulizia pianificata, nessun limite disco noto.

---

## 5. Issue List (prioritizzata)

### 🔴 HIGH

#### ~~H1~~ — ~~Nessun locking cron (concorrenza)~~ ✅ RISOLTO 2026-03-06
- **File**: `cron_run_daily.sh`, `daily.sh`
- **Evidenza**: `daily.lock` esiste in `storage/state/` ma nessun codice del progetto lo usa. Se due istanze si sovrappongono (run lento + next cron tick), entrambe scrivono su `seen_doi.jsonl`/`seen_urls.jsonl` in append concorrente e fanno upsert paralleli su Qdrant.
- **Impatto**: corruzione stato globale, duplicati nel KB.
- **Fix**: `flock -n` in `cron_run_daily.sh` (Patch 1).

#### ~~H2~~ — ~~`evaluate.py` QC FAIL innesca il retry OOM~~ ✅ RISOLTO 2026-03-06
- **File**: `daily.sh:99-101`, `cron_run_daily.sh:90-102`
- **Contesto**: si vuole che un QC FAIL faccia **continuare** il run scrivendo FAIL nel report, non che abortisca e inneschi il retry.
- **Evidenza**: `evaluate.py` exit 2 + `set -euo pipefail` in `daily.sh` → exit 2 propagato → `cron_run_daily.sh` riavvia Unstructured e rilancia tutto.
- **Fix**: due cambiamenti (Patch 2 + Patch 3):
  1. In `daily.sh`: catturare exit code di evaluate e non propagarlo (`|| true`, scrivendo il risultato in un file)
  2. In `cron_run_daily.sh`: distinguere exit 2 (QC) da exit 1 (infra)

#### ~~H3~~ — ~~`.env` world-readable con API keys~~ ✅ RISOLTO 2026-03-06
- **File**: `.env` (permissions: `-rw-r--r--` 644)
- **Impatto**: qualunque utente locale può leggere `BRAVE_API_KEY`, `OPENAI_API_KEY`.
- **Fix**: `chmod 600 .env` — un comando, 30 secondi.

#### ~~H4~~ — ~~No `trap EXIT` → `docker compose down` non eseguita su errore precoce~~ ✅ RISOLTO 2026-03-06
- **File**: `cron_run_daily.sh:65-107`
- **Evidenza**: se `wait_http_200` va in timeout (exit 1), si esce senza `docker compose down`. I container rimangono up e consumano RAM/CPU fino al run successivo.
- **Fix**: aggiungere `trap` (Patch 1).

---

### 🟡 MEDIUM

#### ~~M1~~ — ~~`indexed_points` in evaluate.py è cumulativo, non per-run~~ ✅ RISOLTO 2026-03-06
- **File**: `evaluate.py:108-114`
- **Evidenza**: `_qdrant_points_count()` conta i punti totali della collection. Su KB maturo il check `>= 1500` passa sempre anche se il run ha indicizzato 0 documenti.
- **Fix**: usare `{RUN_ID}_indexed.json` → `points_upserted` per il check per-run (Patch 4).

#### ~~M2~~ — ~~`seen_urls.jsonl` e `storage/state/` crescono senza limite~~ ✅ RISOLTO 2026-03-06
- **Evidenza**: 5.415 entry in `seen_urls.jsonl`, 70+ file in `storage/state/`, nessuna pulizia pianificata, nessun limite disco noto.
- **Impatto**: `kb_validate.py` rallenta progressivamente; rischio disco pieno a lungo termine.
- **Fix**: script di pruning (Patch 5) + `seen_urls.jsonl` cap a N entry più recenti.

#### M3 — Nessuna notifica su run falliti
- **Evidenza**: confermato — solo log locali. Un run fallito è scopribile solo leggendo manualmente i log.
- **Fix**: aggiungere notifica (es. email o webhook) al termine di `cron_run_daily.sh` su exit non-zero (Patch 1, sezione notifica).

#### M4 — `UNPAYWALL_EMAIL` non configurata
- **File**: `pipelines/enrich_doi_oa.py:55`, `.env`
- **Cos'è Unpaywall**: servizio gratuito (unpaywall.org) che, dato un DOI, restituisce URL open-access del PDF. Fondamentale per recuperare PDF di paper accademici senza passare per paywalled. Richiede solo un indirizzo email valido.
- **Impatto attuale**: `enrich_doi_oa.py` chiama solo Crossref (metadati bibliografici) ma salta completamente Unpaywall. Per tutti i documenti con DOI, si perdono potenziali URL PDF open-access alternativi.
- **Fix**: aggiungere a `.env`: `UNPAYWALL_EMAIL=tua@email.com` (e `CROSSREF_MAILTO=tua@email.com`).

#### ~~M5~~ — ~~Errori Brave/OpenAlex silenziosamente ingoiati~~ ✅ RISOLTO 2026-03-06
- **File**: `discover.py:448-449`, `discover.py:514-515`
- **Evidenza**: `except Exception: pass` su entrambi i loop API.
- **Fix**: loggare l'eccezione prima del `pass`.

#### M6 — `ocr_backlog.py` timeout 1800s per file
- **File**: `ocr_backlog.py:108-113`
- **Impatto**: worst case 20 PDF × 30 min = 10 ore (best-effort, non blocca il run core ma occupa risorse).
- **Fix**: ridurre a 300s, abbassare `OCR_LIMIT` a 5.

#### ~~M7~~ — ~~`ops/cron/daily.cron` è un template obsoleto e fuorviante~~ ✅ RISOLTO 2026-03-06
- **File**: `ops/cron/daily.cron:1`
- **Evidenza**: punta a `daily.sh` con path placeholder. Il cron reale è già corretto e usa `cron_run_daily.sh`.
- **Fix**: aggiornare il file template con il cron reale, o eliminarlo e documentarlo nel README.

---

### 🟢 LOW

#### L1 — No log rotation su `cron_daily_*.log`
- Un run verboso può generare file di log grandi. Non c'è cap né rotation automatica.

#### ~~L2~~ — ~~Grobid readiness non verificata~~ ✅ RISOLTO 2026-03-07
- Aggiunto `GROBID_HEALTH_URL` e `wait_http_200 grobid` condizionale su `GROBID_ENABLE=1` in `cron_run_daily.sh`.

#### L3 — `$SERVICES` non quotato in `docker compose up`
- `cron_run_daily.sh:84`: word splitting intenzionale ma fragile.

#### L4 — Immagine Unstructured non pinnata
- `docker-compose.yml`: `unstructured-api:latest` — un aggiornamento automatico può rompere il parsing silenziosamente.
- **Fix**: pin a versione specifica (es. `unstructured-api:0.0.75`).

#### L5 — `living_spec.md` non aggiornata dalla pipeline
- Presente in `storage/artifacts/` ma nessun modulo attivo la scrive. Origine sconosciuta. Considerare rimozione o documentare chi la mantiene manualmente.

#### ~~L6~~ — ~~`storage/state/daily.lock` di origine sconosciuta~~ ✅ RISOLTO 2026-03-06
- Presente ma nessun codice lo usa. Probabilmente residuo di una versione precedente. Considerare rimozione.

---

## 6. Roadmap miglioramenti

### Quick wins (< 2 ore)

1. ~~`chmod 600 .env`~~ ✅ 2026-03-06
2. ~~**Patch 1**: `flock` + `trap EXIT` + distingui exit 2 in `cron_run_daily.sh`~~ ✅ 2026-03-06
3. ~~**Patch 2+3**: `evaluate.py` non abortisce il run — scrive FAIL nel report e continua~~ ✅ 2026-03-06
4. **Aggiungere `UNPAYWALL_EMAIL=tua@email.com` in `.env`** ⏳ aperto
5. ~~Loggare eccezioni Brave/OpenAlex invece di `pass` silenzioso~~ ✅ 2026-03-06
6. ~~Aggiornare/eliminare `ops/cron/daily.cron` e `storage/state/daily.lock`~~ ✅ 2026-03-06

### 1–2 giorni

1. ~~**Patch 4**: fix `indexed_points` check (per-run invece di cumulativo)~~ ✅ 2026-03-06
2. ~~**Patch 5**: script di pruning `storage/state/` + cap `seen_urls.jsonl`~~ ✅ 2026-03-06
3. Notifica su run fallito (webhook già predisposto in `cron_run_daily.sh` — serve `NOTIFY_WEBHOOK_URL`) ⏳
4. Pin immagine Unstructured in `docker-compose.yml` ⏳
5. Ridurre timeout OCR a 300s, `OCR_LIMIT=5` ⏳
6. ~~Monitorare spazio disco~~ — già incluso in `prune_artifacts.sh` (`du -sh storage/`) ✅ 2026-03-06

### 1–2 settimane

1. **Semantic chunking**: split su paragrafi/heading invece di caratteri fissi
2. **Dedup pre-upsert**: skip embedding se `content_hash` già presente in Qdrant
3. **Pipeline version**: tag git commit nei metadati chunk per tracciabilità codice ↔ output
4. **Test regression**: golden file per `relevance.py` (score deterministico dato input fisso)
5. **`seen_urls.jsonl` pruning** automatico a N entry più recenti (es. 10.000 con LRU)

---

## 7. Patch/Snippet concreti

### Patch 1 — `cron_run_daily.sh`: lock + trap EXIT + distinzione exit 2

Aggiungere dopo `LOG_FILE=...` e prima del blocco `{}`:

```bash
# Lock: una sola istanza alla volta
LOCK_FILE="$ROOT_DIR/storage/state/cron_daily.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "$(ts) WARN: altra istanza in esecuzione. Uscita." >&2
  exit 0
fi

# Cleanup Docker anche su errore precoce
trap '/usr/bin/docker compose down 2>/dev/null || true' EXIT
```

Sostituire la logica di retry (righe 89–102):

```bash
START_TS=$(date +%s)
{
  echo "$(ts) [START] RUN_ID=${RUN_ID} PROFILE=${PROFILE}"

  echo "$(ts) Starting services: ${SERVICES}"
  /usr/bin/docker compose up -d $SERVICES

  wait_http_200 "$QDRANT_URL" "qdrant" 60 1
  wait_http_200 "$UNSTRUCTURED_HEALTH_URL" "unstructured" 90 1

  DAILY_EXIT=0
  bash "$ROOT_DIR/pipelines/daily.sh" || DAILY_EXIT=$?

  if [[ $DAILY_EXIT -eq 0 ]]; then
    echo "$(ts) [OK] Pipeline completata."
  elif [[ $DAILY_EXIT -eq 2 ]]; then
    # QC FAIL da evaluate.py — non è un problema infrastrutturale, non ritentare
    echo "$(ts) [WARN] QC FAIL (exit 2). Controllare report." >&2
    # Notifica opzionale
    NOTIFY_URL="${NOTIFY_WEBHOOK_URL:-}"
    [[ -n "$NOTIFY_URL" ]] && curl -fsS -X POST "$NOTIFY_URL" \
      -H "Content-Type: application/json" \
      -d "{\"text\":\"spiru-ops QC FAIL run_id=${RUN_ID}\"}" >/dev/null 2>&1 || true
  else
    echo "$(ts) [RETRY] Fallimento infrastrutturale (exit $DAILY_EXIT). Restart unstructured..." >&2
    /usr/bin/docker compose restart unstructured || true
    wait_http_200 "$UNSTRUCTURED_HEALTH_URL" "unstructured" 90 1
    echo "$(ts) [STEP] attempt 2/2"
    bash "$ROOT_DIR/pipelines/daily.sh" || echo "$(ts) [FAIL] Anche il retry è fallito." >&2
  fi

  DURATION=$(( $(date +%s) - START_TS ))
  echo "$(ts) [DONE] durata=${DURATION}s"
} >>"$LOG_FILE" 2>&1
```

---

### Patch 2 — `daily.sh`: evaluate non abortisce il run

Sostituire la riga `"$PYBIN" -m pipelines.evaluate` (attuale riga ~100):

```bash
# QC evaluate: non deve abortire il run su FAIL.
# Scrive l'esito nel log e in un file di stato; il run continua.
QC_EXIT=0
"$PYBIN" -m pipelines.evaluate || QC_EXIT=$?
if [[ $QC_EXIT -eq 0 ]]; then
  echo "[daily] QC: PASS"
elif [[ $QC_EXIT -eq 2 ]]; then
  echo "[daily] WARN: QC FAIL (exit 2) — run continua, controllare report." >&2
  echo "{\"qc\":\"FAIL\",\"run_id\":\"${RUN_ID}\"}" \
    > "${STATE_DIR}/${RUN_ID}_qc_fail.json"
else
  # Exit inatteso da evaluate: trattarlo come errore critico
  echo "[daily] ERROR: evaluate uscito con exit $QC_EXIT (inatteso)." >&2
  exit $QC_EXIT
fi
```

---

### Patch 3 — Pruning `storage/state/` e cap `seen_urls.jsonl`

Creare `pipelines/prune_artifacts.sh`:

```bash
#!/usr/bin/env bash
# Elimina state files più vecchi di RETENTION_DAYS (default 30)
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RETENTION_DAYS="${ARTIFACT_RETENTION_DAYS:-30}"
STATE_DIR="$ROOT_DIR/storage/state"

echo "[prune] storage/state/ files più vecchi di ${RETENTION_DAYS}g..."
find "$STATE_DIR" -maxdepth 1 -type f \
  \( -name "*_candidates.jsonl" -o -name "*_ingested.json" \
     -o -name "*_indexed.json"  -o -name "*_strain_seeds.jsonl" \
     -o -name "*_qc_fail.json" \) \
  -mtime +"$RETENTION_DAYS" -print -delete

# Cap seen_urls.jsonl a ultime 15000 righe
SEEN_URLS="$STATE_DIR/seen_urls.jsonl"
if [[ -f "$SEEN_URLS" ]]; then
  LINES=$(wc -l < "$SEEN_URLS")
  if (( LINES > 15000 )); then
    echo "[prune] seen_urls.jsonl: $LINES righe → cap 15000"
    tail -n 15000 "$SEEN_URLS" > "$SEEN_URLS.tmp" && mv "$SEEN_URLS.tmp" "$SEEN_URLS"
  fi
fi

# Spazio disco residuo
echo "[prune] Spazio storage/: $(du -sh "$ROOT_DIR/storage" 2>/dev/null | cut -f1)"
```

Aggiungere in `daily.sh` (best-effort, alla fine):
```bash
bash "$ROOT_DIR/pipelines/prune_artifacts.sh" \
  || echo "[daily] WARN: prune_artifacts fallito (non fatale)" >&2
```

---

### Patch 4 — Fix `indexed_points` per-run in `evaluate.py`

Sostituire le righe che chiamano `_qdrant_points_count` con:

```python
# Check per-run: punti upsertati in questo run (da indexed.json)
idx_path = pathlib.Path(env("INDEXED_PATH", state_path("indexed.json")))
run_indexed_points = 0
if idx_path.exists():
    idx_data = _load_json(idx_path)
    run_indexed_points = int(idx_data.get("points_upserted") or 0)

# Check collection health (cumulativo Qdrant — informativo)
try:
    collection_points = _qdrant_points_count(QDRANT_URL, QDRANT_COLLECTION)
except Exception as e:
    collection_points = 0
    print(f"[qc] WARN: impossibile contattare Qdrant: {e}")

print(f"[qc] run_indexed_points={run_indexed_points}")
print(f"[qc] collection_points_total={collection_points}")

# Usare run_indexed_points per il QC check
# ...
checks.append((run_indexed_points >= MIN_INDEXED_POINTS,
               f"run_indexed_points>=min: {run_indexed_points} >= {MIN_INDEXED_POINTS}"))
```

---

## 8. Domande aperte — Risposte

| # | Domanda | Risposta | Azione derivata |
|---|---------|----------|-----------------|
| 1 | Cron schedule reale | `10 2,8,14,20 * * *` → 4 run/giorno, punta già a `cron_run_daily.sh` | H2 (vecchio) chiuso; `ops/cron/daily.cron` è solo template obsoleto da aggiornare |
| 2 | PROFILE in produzione | `kb_first` (impostato nel cron via env) | Documentato; profilo più aggressivo (download 120 MB, timeout più lunghi) |
| 3 | `living_spec.md` | Non si sa chi la aggiorna | Probabilmente residuo di vecchia pipeline; considerare rimozione o nota nel README |
| 4 | `storage/state/daily.lock` | Origine sconosciuta | Residuo da eliminare; il lock reale va implementato (Patch 1) |
| 5 | `seen_urls.jsonl` crescita | Nessuna pulizia pianificata | Aggiungere cap + pruning (Patch 3) |
| 6 | `evaluate.py` exit 2 | Si vuole che il run **continui** scrivendo FAIL nel report | Implementare Patch 2 (non propagare exit 2) |
| 7 | Immagine Unstructured `latest` | Non si sa la versione esatta | Consigliato pin a versione stabile; trovare versione con `docker inspect` |
| 8 | `UNPAYWALL_EMAIL` | Non configurata, non si sapeva cos'è | **Servizio gratuito per URL PDF open-access via DOI.** Aggiungere `UNPAYWALL_EMAIL=tua@email.com` in `.env` per attivarlo — migliora il recupero PDF accademici |
| 9 | Storage disk | Nessun limite preciso noto | Aggiungere `du -sh storage/` nel log di ogni run; impostare alert se > 80% |
| 10 | Notifiche | Nessuna | Aggiungere webhook/email minimo (Patch 1, sezione notifica) |

---

*Fine report.*
