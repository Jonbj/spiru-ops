# Operazioni — cron, log, debug, manutenzione

## Setup iniziale

### 1. Installare il cron

```bash
crontab -e
```

Incollare (il file di riferimento è `ops/cron/daily.cron`):
```
10 2,8,14,20 * * * cd /home/stefano/Documents/Projects/spiru-ops && PROFILE=kb_first /bin/bash /home/stefano/Documents/Projects/spiru-ops/pipelines/cron_run_daily.sh >> /home/stefano/Documents/Projects/spiru-ops/storage/logs/cron_crontab.log 2>&1
```

Il cron gira 4 volte al giorno: 02:10, 08:10, 14:10, 20:10 UTC.

### 2. Verificare che il cron sia installato

```bash
crontab -l
```

### 3. Verificare i permessi

```bash
chmod 600 .env                          # API keys non world-readable
chmod +x pipelines/cron_run_daily.sh
chmod +x pipelines/daily.sh
chmod +x pipelines/prune_artifacts.sh
```

---

## Run manuale

Per eseguire un run completo (equivalente al cron):
```bash
cd /home/stefano/Documents/Projects/spiru-ops
PROFILE=kb_first /bin/bash pipelines/cron_run_daily.sh
```

Per eseguire solo la pipeline interna (Docker già up):
```bash
cd /home/stefano/Documents/Projects/spiru-ops
source .env
bash pipelines/daily.sh
```

Per eseguire un singolo step Python:
```bash
source .env && source pipelines/profiles.sh
export RUN_ID=2026-03-07T102121Z          # riusa run esistente
export CANDIDATES_PATH=storage/state/${RUN_ID}_candidates.jsonl
export INGESTED_PATH=storage/state/${RUN_ID}_ingested.json
export INDEXED_PATH=storage/state/${RUN_ID}_indexed.json

.venv/bin/python -m pipelines.evaluate    # solo QC
.venv/bin/python -m pipelines.report      # solo report
```

---

## Log e monitoring

### Log principale

```bash
# Log del run corrente
tail -f storage/logs/cron_daily_$(date +%F).log

# Log degli ultimi 3 giorni
ls -lth storage/logs/cron_daily_*.log | head -5

# Cercare errori nell'ultimo log
grep -E "ERROR|FAIL|WARN" storage/logs/cron_daily_$(date +%F).log
```

### Struttura log

Il log `cron_daily_YYYY-MM-DD.log` contiene:
```
[2026-03-07 11:21:21] [START] RUN_ID=2026-03-07T102121Z PROFILE=kb_first
[2026-03-07 11:21:22] qdrant ready: http://localhost:6333
[2026-03-07 11:21:26] unstructured ready: http://localhost:8000/healthcheck
[2026-03-07 11:21:36] grobid ready: http://localhost:8070/api/isalive
[2026-03-07 11:21:36] [STEP] Daily attempt 1/2 starting...
[daily] Using python: .venv/bin/python
[daily] RUN_ID=2026-03-07T102121Z PROFILE=kb_first
...output di ogni step...
[qc] ✅ QUALITY PASS
[2026-03-07 11:38:18] [OK] Daily finished successfully.
[2026-03-07 11:38:32] [DONE] duration=999s
```

### Stato corrente KB

```bash
# Numero punti in Qdrant
curl -s http://localhost:6333/collections/docs_chunks | python3 -m json.tool | grep points_count

# Uso disco
du -sh storage/
du -sh storage/raw storage/parsed storage/qdrant storage/state storage/artifacts

# Numero URL visti
wc -l storage/state/seen_urls.jsonl

# Numero DOI visti
wc -l storage/state/seen_doi.jsonl

# Ultimo report
ls -lth storage/artifacts/*.md | head -5
cat $(ls -t storage/artifacts/*_report.md | head -1)
```

---

## Debug di un run fallito

### 1. Trovare il run fallito

```bash
# Log del giorno
cat storage/logs/cron_daily_YYYY-MM-DD.log | grep -A5 "FAIL\|ERROR\|exit"

# File QC fail (creato solo se QC FAIL)
ls storage/state/*_qc_fail.json
cat storage/state/2026-03-07T102121Z_qc_fail.json
```

### 2. Capire quale step ha fallito

I marker nel log:
- `[STEP] Daily attempt 1/2 starting...` → inizio pipeline
- `[OK] Daily finished successfully` → tutto ok
- `[WARN] QC FAIL (exit 2)` → pipeline completata ma QC non passa
- `[RETRY] Daily attempt 1 failed` → crash infrastrutturale → retry
- `[FAIL] Retry also failed` → entrambi i tentativi falliti

### 3. Analizzare candidati/ingest/index

```bash
# Quanti candidati ha trovato l'ultimo run?
ls -t storage/state/*_candidates.jsonl | head -1 | xargs wc -l

# Quanti documenti ingestionati?
ls -t storage/state/*_ingested.json | head -1 | \
  xargs python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print('ingested:', len(d.get('ingested',[])), 'failures:', d.get('failures_total'))"

# Failure breakdown
ls -t storage/state/*_ingested.json | head -1 | \
  xargs python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps(d.get('failures_by_reason',{}), indent=2))"
```

### 4. Testare singoli servizi Docker

```bash
# Qdrant
curl -s http://localhost:6333/collections | python3 -m json.tool

# Unstructured
curl -s http://localhost:8000/healthcheck

# Grobid
curl -s http://localhost:8070/api/isalive
```

---

## Modificare la configurazione

### Aggiungere una nuova area tematica (focus)

1. Aprire `configs/focus.yaml`
2. Aggiungere un nuovo blocco:
   ```yaml
   - name: mio_nuovo_focus
     keywords:
       - termine1
       - termine2
     openalex_query: '"termine1" AND ("termine2" OR "termine3")'
     brave_queries:
       - termine1 termine2 pdf
       - '"termine1" site:.edu filetype:pdf'
   ```
3. Aggiungere in `configs/scoring.yaml`:
   ```yaml
   - name: mio_nuovo_focus
     base_score: 20
     queries:
       - "termine1 termine2 applicazione"
   ```
4. Il focus verrà usato automaticamente dal prossimo run.

### Aggiungere un dominio da negare

In `configs/domains.yaml`:
```yaml
deny_domains:
  - example.com    # aggiungere qui
```

### Modificare le soglie QC

In `.env`:
```bash
QC_MIN_CANDIDATES=150        # se discovery produce meno di 200
QC_MIN_SPIRULINA_SHARE=0.25  # se si vogliono anche doc meno focalizzati
```

### Cambiare il modello embedding

**Attenzione**: cambiare il modello invalida tutti i vettori esistenti in Qdrant!
Se si cambia `EMBED_MODEL`:
1. Eliminare la collection Qdrant: `curl -X DELETE http://localhost:6333/collections/docs_chunks`
2. Aggiornare `.env`: `EMBED_MODEL=nuovo/modello`
3. Ri-indicizzare: il prossimo run ricrea la collection con la nuova dimensione

---

## Manutenzione periodica

### Mensile

```bash
# Verifica spazio disco
du -sh storage/

# Verifica integrità Qdrant
curl -s http://localhost:6333/collections/docs_chunks | python3 -m json.tool

# Leggi l'ultimo kb_dedup_report per trovare duplicati
cat $(ls -t storage/artifacts/*_kb_dedup_report.md | head -1)
```

### Quando il disco è quasi pieno

```bash
# Quanto occupa ogni cartella
du -sh storage/raw storage/parsed storage/qdrant storage/state storage/logs

# Rimuovere log vecchi (oltre 90 giorni)
find storage/logs -name "*.log" -mtime +90 -delete

# Rimuovere raw/parsed per doc non più nel KB
# (non automatico — fare con cautela)
```

### Aggiornare Unstructured

L'immagine è pinnata a un digest specifico in `docker-compose.yml`. Per aggiornarla:
1. Pullare la nuova versione: `docker pull downloads.unstructured.io/unstructured-io/unstructured-api:latest`
2. Ottenere il nuovo digest: `docker inspect downloads.unstructured.io/unstructured-io/unstructured-api:latest --format '{{index .RepoDigests 0}}'`
3. Aggiornare `docker-compose.yml` con il nuovo digest
4. Fare un run di test manuale per verificare che il parsing funzioni

---

## Comandi utili

### Qdrant

```bash
# Info collection
curl -s http://localhost:6333/collections/docs_chunks | python3 -m json.tool

# Cerca un documento per URL
curl -s -X POST http://localhost:6333/collections/docs_chunks/points/scroll \
  -H "Content-Type: application/json" \
  -d '{"filter":{"must":[{"key":"url","match":{"value":"https://..."}}]},"limit":5,"with_payload":true}' \
  | python3 -m json.tool

# Numero totale punti
curl -s http://localhost:6333/collections/docs_chunks | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['result']['points_count'])"
```

### Docker

```bash
# Stato services
docker compose ps

# Log Unstructured (per debug parsing)
docker compose logs unstructured --tail=50

# Log Grobid
docker compose logs grobid --tail=50

# Restart un singolo service
docker compose restart unstructured

# Spazio occupato dai container
docker system df
```

### Pipeline

```bash
# Query veloce al KB
docker compose up -d qdrant && .venv/bin/python -m pipelines.query "spirulina pH bicarbonate" --topk 5

# Avvio copilot
docker compose up -d qdrant && streamlit run ui/copilot.py

# Run test unitari
.venv/bin/python -m pytest tests/ -v
```

---

## Notifiche

Non ci sono notifiche attive al momento. Il sistema è configurato per supportare un webhook opzionale:

```bash
# In .env, aggiungere:
NOTIFY_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
```

Se configurato, `cron_run_daily.sh` invierà un POST JSON:
- Su QC FAIL: `{"text": "spiru-ops QC FAIL run_id=YYYY-MM-DDTHHMMSSZ"}`
- Su retry failed: `{"text": "spiru-ops FAILED after retry run_id=... exit=1"}`

Compatibile con Slack incoming webhooks, Discord webhooks, o qualsiasi endpoint che accetti POST JSON.

---

## Backup consigliato

I dati critici da includere in backup:
1. `storage/qdrant/` — tutto il vector DB (può essere ri-generato ma richiede giorni)
2. `storage/parsed/` — testi estratti + metadati (ri-scaricare è lento)
3. `storage/state/seen_urls.jsonl` + `seen_doi.jsonl` — dedup globale
4. `.env` — configurazione e API keys (backup separato, sicuro)
5. `storage/artifacts/living_spec.md` — note di progettazione accumulate

Non necessario:
- `storage/raw/` — sono i download originali, recuperabili
- `storage/state/*.jsonl/*.json` (tranne seen_*) — artefatti per-run, prunati ogni 30gg
- `storage/logs/` — log, non critici
