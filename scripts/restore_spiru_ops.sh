#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 /path/to/spiru-ops-backup_*.tar.gz [project_dir]"
  exit 1
fi

ARCHIVE_PATH="$1"
TARGET_DIR="${2:-$HOME/spiru-ops}"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

mkdir -p "$TARGET_DIR"

echo "[restore] extracting archive to temporary dir..."
tar -xzf "$ARCHIVE_PATH" -C "$TMP_DIR"

echo "[restore] syncing repo snapshot..."
rsync -a "$TMP_DIR/repo/" "$TARGET_DIR/"

if [[ -d "$TMP_DIR/runtime/storage" ]]; then
  echo "[restore] syncing runtime storage..."
  mkdir -p "$TARGET_DIR/storage"
  rsync -a "$TMP_DIR/runtime/storage/" "$TARGET_DIR/storage/"
fi

if [[ -f "$(dirname "$ARCHIVE_PATH")/.env.backup" ]]; then
  echo "[restore] restoring .env from sibling .env.backup"
  cp "$(dirname "$ARCHIVE_PATH")/.env.backup" "$TARGET_DIR/.env"
  chmod 600 "$TARGET_DIR/.env"
else
  echo "[restore] .env.backup not found next to archive; restore manually if needed"
fi

echo "[restore] done. Next recommended steps:"
echo "  cd $TARGET_DIR"
echo "  python3 -m venv .venv"
echo "  source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  docker compose up -d qdrant unstructured grobid"
echo "  # if using postgres too: docker compose up -d postgres"
echo "  # if you have a postgres dump: gunzip -c postgres_dump.sql.gz | docker compose exec -T postgres psql -U \${POSTGRES_USER:-spiru} \${POSTGRES_DB:-spiru_ops}"
