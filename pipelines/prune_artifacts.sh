#!/usr/bin/env bash
# pipelines/prune_artifacts.sh
# =============================================================================
# Remove per-run state files older than ARTIFACT_RETENTION_DAYS (default 30).
# Cap seen_urls.jsonl to the most recent SEEN_URLS_MAX_LINES lines.
# Print current storage/ disk usage.
#
# Safe to run multiple times (idempotent).
# Called best-effort from daily.sh at the end of each run.
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

RETENTION_DAYS="${ARTIFACT_RETENTION_DAYS:-30}"
SEEN_URLS_MAX="${SEEN_URLS_MAX_LINES:-15000}"
STATE_DIR="$ROOT_DIR/storage/state"

echo "[prune] Removing state files older than ${RETENTION_DAYS} days..."
find "$STATE_DIR" -maxdepth 1 -type f \
  \( -name "*_candidates.jsonl" \
     -o -name "*_ingested.json" \
     -o -name "*_indexed.json" \
     -o -name "*_strain_seeds.jsonl" \
     -o -name "*_qc_fail.json" \) \
  -mtime +"$RETENTION_DAYS" \
  -print -delete

# Cap seen_urls.jsonl to avoid unbounded growth
SEEN_URLS="$STATE_DIR/seen_urls.jsonl"
if [[ -f "$SEEN_URLS" ]]; then
  LINES=$(wc -l < "$SEEN_URLS")
  if (( LINES > SEEN_URLS_MAX )); then
    echo "[prune] seen_urls.jsonl: ${LINES} lines → capping to ${SEEN_URLS_MAX}"
    tail -n "$SEEN_URLS_MAX" "$SEEN_URLS" > "$SEEN_URLS.tmp" \
      && mv "$SEEN_URLS.tmp" "$SEEN_URLS"
  fi
fi

echo "[prune] storage/ disk usage: $(du -sh "$ROOT_DIR/storage" 2>/dev/null | cut -f1)"
echo "[prune] Done."
