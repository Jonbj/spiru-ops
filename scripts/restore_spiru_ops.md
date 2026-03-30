# Restore spiru-ops on a new machine

## Goal
Recover the project using:
- GitHub repo
- local backup archive
- optional `.env.backup`
- optional `postgres_dump.sql.gz`

## 1. Clone repo
```bash
git clone git@github.com:Jonbj/spiru-ops.git
cd spiru-ops
```

## 2. Restore `.env`
If available:
```bash
cp /path/to/.env.backup .env
chmod 600 .env
```

## 3. Extract backup archive
```bash
mkdir -p /tmp/spiru-ops-restore
tar -xzf /path/to/spiru-ops-backup_*.tar.gz -C /tmp/spiru-ops-restore
rsync -a /tmp/spiru-ops-restore/repo/ ./
rsync -a /tmp/spiru-ops-restore/runtime/storage/ ./storage/
```

## 4. Python environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 5. Start services
```bash
docker compose up -d qdrant unstructured grobid
```
If using postgres too:
```bash
docker compose up -d postgres
```

## 6. Restore Postgres (if dump exists)
```bash
gunzip -c /path/to/postgres_dump.sql.gz | docker compose exec -T postgres psql -U ${POSTGRES_USER:-spiru} ${POSTGRES_DB:-spiru_ops}
```

## 7. Sanity checks
```bash
docker compose ps
python -m pipelines.query "spirulina" --topk 3 || true
```

## Notes
- `storage/raw/` is not strictly required to recover the system.
- `storage/qdrant/` + `storage/parsed/` are the most valuable runtime assets.
- `.env` should remain outside git and be backed up securely.
