#!/usr/bin/env bash
# pipelines/cron_run_daily.sh
# =============================================================================
# spiru-ops cron wrapper
# =============================================================================
#
# Functional role
# ---------------
# Cron should run this wrapper (not daily.sh directly) because it:
# - starts required docker services (qdrant, unstructured, grobid)
# - waits for readiness (HTTP 200 checks)
# - executes the daily pipeline
# - retries once if Unstructured crashes (common failure mode)
# - shuts containers down to save resources
#
# Technical role
# --------------
# - Generates a stable RUN_ID once and exports it so attempt 1 and attempt 2
#   operate on the *same* artifacts.
# - Writes logs into storage/logs/cron_daily_YYYY-MM-DD.log
# - Holds a flock lock so only one instance runs at a time.
# - Traps EXIT to ensure docker compose down always runs.
#
# Observability
# -------------
# Tail the log during a run:
#   tail -f storage/logs/cron_daily_$(date +%F).log
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

LOG_DIR="$ROOT_DIR/storage/logs"
mkdir -p "$LOG_DIR"

DAY="$(date +%F)"
LOG_FILE="$LOG_DIR/cron_daily_${DAY}.log"

ts() { date "+[%Y-%m-%d %H:%M:%S]"; }

# Load .env if present
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

# Apply runtime profile
export PROFILE="${PROFILE:-balanced}"
# shellcheck disable=SC1091
source "$ROOT_DIR/pipelines/profiles.sh"

# Stable RUN_ID for this wrapper invocation (UTC)
# Attempt 1 and attempt 2 MUST reuse this same RUN_ID.
export RUN_ID="${RUN_ID:-$(date -u +%Y-%m-%dT%H%M%SZ)}"

# Services to run. Postgres is intentionally omitted by default (not used yet).
SERVICES="${SERVICES:-qdrant unstructured grobid}"

# Readiness endpoints
QDRANT_URL="${QDRANT_URL:-http://localhost:6333/}"
UNSTRUCTURED_HEALTH_URL="${UNSTRUCTURED_HEALTH_URL:-http://localhost:8000/healthcheck}"
GROBID_HEALTH_URL="${GROBID_HEALTH_URL:-http://localhost:8070/api/isalive}"

# --- Lock: only one instance at a time ---
# Uses flock on a dedicated lock file. If another instance is running, exit 0
# (not an error: the cron simply fires again while the previous run is still up).
mkdir -p "$ROOT_DIR/storage/state"
LOCK_FILE="$ROOT_DIR/storage/state/cron_daily.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "$(ts) WARN: another instance is running (lock: $LOCK_FILE). Exiting." >&2
  exit 0
fi

# --- Cleanup: always stop Docker services on exit ---
_cleanup() {
  echo "$(ts) trap EXIT: stopping services..."
  /usr/bin/docker compose down 2>/dev/null || true
}
trap '_cleanup' EXIT

wait_http_200() {
  local url="$1"
  local name="$2"
  local max_tries="${3:-60}"
  local sleep_s="${4:-1}"

  for ((i=1; i<=max_tries; i++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "$(ts) $name ready: $url"
      return 0
    fi
    sleep "$sleep_s"
  done
  echo "$(ts) ERROR: $name not ready after $max_tries tries: $url" >&2
  return 1
}

START_TS=$(date +%s)

{
  echo "$(ts) [START] RUN_ID=${RUN_ID} PROFILE=${PROFILE}"
  echo "$(ts) Starting services: ${SERVICES}"
  /usr/bin/docker compose up -d $SERVICES

  wait_http_200 "$QDRANT_URL" "qdrant" 60 1
  wait_http_200 "$UNSTRUCTURED_HEALTH_URL" "unstructured" 90 1
  if [[ "${GROBID_ENABLE:-0}" == "1" ]]; then
    wait_http_200 "$GROBID_HEALTH_URL" "grobid" 60 1
  fi

  echo "$(ts) [STEP] Daily attempt 1/2 starting..."
  DAILY_EXIT=0
  bash "$ROOT_DIR/pipelines/daily.sh" || DAILY_EXIT=$?

  if [[ $DAILY_EXIT -eq 0 ]]; then
    echo "$(ts) [OK] Daily finished successfully."
  elif [[ $DAILY_EXIT -eq 2 ]]; then
    # exit 2 = QC FAIL from evaluate.py — not an infrastructure problem, do not retry.
    echo "$(ts) [WARN] QC FAIL (exit 2). Run completed but quality checks failed. Check report." >&2
    # Optional webhook notification (set NOTIFY_WEBHOOK_URL in .env)
    NOTIFY_URL="${NOTIFY_WEBHOOK_URL:-}"
    if [[ -n "$NOTIFY_URL" ]]; then
      curl -fsS -X POST "$NOTIFY_URL" \
        -H "Content-Type: application/json" \
        -d "{\"text\":\"spiru-ops QC FAIL run_id=${RUN_ID}\"}" >/dev/null 2>&1 || true
    fi
  else
    # Most common mid-run fatal issue is Unstructured being OOM-killed.
    # We restart it and retry once.
    echo "$(ts) [RETRY] Daily attempt 1 failed (exit $DAILY_EXIT). Restarting unstructured..." >&2
    /usr/bin/docker compose restart unstructured || true
    wait_http_200 "$UNSTRUCTURED_HEALTH_URL" "unstructured" 90 1

    echo "$(ts) [STEP] Daily attempt 2/2 starting..."
    RETRY_EXIT=0
    bash "$ROOT_DIR/pipelines/daily.sh" || RETRY_EXIT=$?
    if [[ $RETRY_EXIT -eq 0 ]]; then
      echo "$(ts) [OK] Daily finished successfully (after retry)."
    else
      echo "$(ts) [FAIL] Retry also failed (exit $RETRY_EXIT)." >&2
      NOTIFY_URL="${NOTIFY_WEBHOOK_URL:-}"
      if [[ -n "$NOTIFY_URL" ]]; then
        curl -fsS -X POST "$NOTIFY_URL" \
          -H "Content-Type: application/json" \
          -d "{\"text\":\"spiru-ops FAILED after retry run_id=${RUN_ID} exit=${RETRY_EXIT}\"}" \
          >/dev/null 2>&1 || true
      fi
    fi
  fi

  DURATION=$(( $(date +%s) - START_TS ))
  echo "$(ts) [DONE] duration=${DURATION}s"
} >>"$LOG_FILE" 2>&1
