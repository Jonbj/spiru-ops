#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

STAMP="$(date +%F_%H%M%S)"
HOSTNAME_SAFE="$(hostname -s 2>/dev/null || hostname || echo unknown-host)"
BACKUP_BASE="${SPIRU_OPS_BACKUP_DIR:-$HOME/Backups/spiru-ops}"
BACKUP_DIR="$BACKUP_BASE/${STAMP}_${HOSTNAME_SAFE}"
mkdir -p "$BACKUP_DIR"

MANIFEST="$BACKUP_DIR/manifest.txt"
ARCHIVE_NAME="spiru-ops-backup_${STAMP}_${HOSTNAME_SAFE}.tar.gz"
ARCHIVE_PATH="$BACKUP_DIR/$ARCHIVE_NAME"
TMP_STAGE="$BACKUP_DIR/stage"
mkdir -p "$TMP_STAGE"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$MANIFEST"; }

log "ROOT_DIR=$ROOT_DIR"
log "BACKUP_DIR=$BACKUP_DIR"

# 1. Capture git metadata
{
  echo "repo=$(basename "$ROOT_DIR")"
  echo "branch=$(git branch --show-current 2>/dev/null || true)"
  echo "head=$(git rev-parse HEAD 2>/dev/null || true)"
  echo "status_start"
  git status --short 2>/dev/null || true
  echo "status_end"
} > "$BACKUP_DIR/git-state.txt"

# 2. Safe config + vault snapshot (excluding volatile local obsidian UI files)
mkdir -p "$TMP_STAGE/repo"
rsync -a \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude 'storage/raw' \
  --exclude 'storage/logs' \
  --exclude 'storage/pgdata' \
  --exclude 'obsidian-vault/.obsidian/workspace.json' \
  --exclude 'obsidian-vault/.obsidian/plugins' \
  --exclude '*.bak' \
  "$ROOT_DIR/" "$TMP_STAGE/repo/"

# 3. Critical runtime data
mkdir -p "$TMP_STAGE/runtime"
for p in \
  "storage/qdrant" \
  "storage/parsed" \
  "storage/state/seen_urls.jsonl" \
  "storage/state/seen_doi.jsonl" \
  "storage/artifacts"; do
  if [[ -e "$ROOT_DIR/$p" ]]; then
    mkdir -p "$TMP_STAGE/runtime/$(dirname "$p")"
    rsync -a "$ROOT_DIR/$p" "$TMP_STAGE/runtime/$(dirname "$p")/"
    log "included $p"
  fi
done

# 4. Secrets backup stored separately inside backup dir (not for git)
if [[ -f "$ROOT_DIR/.env" ]]; then
  cp "$ROOT_DIR/.env" "$BACKUP_DIR/.env.backup"
  chmod 600 "$BACKUP_DIR/.env.backup"
  log "included .env.backup"
fi

# 5. Postgres dump if service is available
if docker compose ps postgres >/dev/null 2>&1; then
  if docker compose ps --status running postgres | grep -q postgres; then
    log "postgres running: attempting pg_dump"
    if docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-spiru}" "${POSTGRES_DB:-spiru_ops}" > "$BACKUP_DIR/postgres_dump.sql" 2>>"$MANIFEST"; then
      gzip -f "$BACKUP_DIR/postgres_dump.sql"
      log "postgres dump created"
    else
      log "WARN postgres dump failed"
      rm -f "$BACKUP_DIR/postgres_dump.sql"
    fi
  else
    log "postgres service not running"
  fi
fi

# 6. Archive stage
log "creating archive $ARCHIVE_PATH"
tar -czf "$ARCHIVE_PATH" -C "$TMP_STAGE" .
rm -rf "$TMP_STAGE"

log "backup completed"
echo "$ARCHIVE_PATH"
