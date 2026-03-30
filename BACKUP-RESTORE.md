# spiru-ops — Backup & Restore

## What this covers
A complete practical backup strategy to restart `spiru-ops` on another machine if the current one fails.

## Critical assets
1. GitHub repo
2. `.env` (kept outside git)
3. `storage/qdrant/`
4. `storage/parsed/`
5. `storage/state/seen_urls.jsonl`
6. `storage/state/seen_doi.jsonl`
7. `storage/artifacts/`
8. optional `postgres` dump

## Backup script
Use:
```bash
bash scripts/backup_spiru_ops.sh
```

### Output
Default local target:
```bash
~/Backups/spiru-ops/
```
Can be overridden with:
```bash
export SPIRU_OPS_BACKUP_DIR=/path/to/backups
```

## Weekly scheduled backup
Recommended weekly full backup every Sunday.
Example cron entry:
```cron
0 3 * * 0 cd /home/stefano/Documents/Projects/spiru-ops && /bin/bash scripts/backup_spiru_ops.sh >> storage/logs/backup_weekly.log 2>&1
```

## Restore
### Executable restore script
```bash
bash scripts/restore_spiru_ops.sh /path/to/spiru-ops-backup_*.tar.gz [/target/project/dir]
```

### Additional notes
- `scripts/restore_spiru_ops.md`
- `.env.backup` should sit next to the archive, or be restored manually
- if a postgres dump exists, restore it manually after starting postgres
