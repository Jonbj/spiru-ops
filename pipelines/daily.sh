#!/usr/bin/env bash
# pipelines/daily.sh
# =============================================================================
# spiru-ops daily pipeline entrypoint (manual-friendly)
# =============================================================================
#
# This script runs the end-to-end pipeline steps in order:
#   1) (optional) seed_strains
#   2) discover   -> produces candidates list (URLs)
#   3) ingest     -> downloads + parses text into storage/parsed
#   4) index      -> chunk + embed + upsert into Qdrant
#   5) report     -> markdown run report
#   6) evaluate   -> quality checks (PASS/FAIL)
#
# IMPORTANT: RUN_ID
# -----------------
# The pipeline can run multiple times per day and can cross midnight.
# We therefore require a stable RUN_ID for the full run.
# - cron_run_daily.sh sets RUN_ID once and exports it.
# - if RUN_ID isn't set, we generate one here as a fallback.
#
# Every step uses RUN_ID-derived paths via environment variables:
#   CANDIDATES_PATH, INGESTED_PATH, INDEXED_PATH, REPORT_PATH, STRAIN_SEEDS_PATH
#
# This makes the pipeline:
# - deterministic
# - idempotent per RUN_ID
# - debuggable (artifacts won't overwrite)
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Load .env if present
# (Secrets and config belong here; do not hardcode API keys in code.)
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

# Apply runtime profile (manual + cron)
export PROFILE="${PROFILE:-balanced}"
# shellcheck disable=SC1091
source "$ROOT_DIR/pipelines/profiles.sh"

# Choose python interpreter (cron-safe)
# Cron often has a minimal PATH and won't find `python`.
PYBIN=""
if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYBIN="$ROOT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYBIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYBIN="$(command -v python)"
else
  echo "[daily] ERROR: no python interpreter found. Expected $ROOT_DIR/.venv/bin/python or python3 in PATH." >&2
  exit 127
fi

# Stable run id for this invocation (UTC)
export RUN_ID="${RUN_ID:-$(date -u +%Y-%m-%dT%H%M%SZ)}"

# Ensure directories exist
STATE_DIR="${STATE_DIR:-storage/state}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-storage/artifacts}"
mkdir -p "$STATE_DIR" "$ARTIFACTS_DIR"

# Canonical per-run paths (used by Python modules)
export STRAIN_SEEDS_PATH="${STRAIN_SEEDS_PATH:-$STATE_DIR/${RUN_ID}_strain_seeds.jsonl}"
export CANDIDATES_PATH="${CANDIDATES_PATH:-$STATE_DIR/${RUN_ID}_candidates.jsonl}"
export INGESTED_PATH="${INGESTED_PATH:-$STATE_DIR/${RUN_ID}_ingested.json}"
export INDEXED_PATH="${INDEXED_PATH:-$STATE_DIR/${RUN_ID}_indexed.json}"
export REPORT_PATH="${REPORT_PATH:-$ARTIFACTS_DIR/${RUN_ID}_report.md}"

echo "[daily] Using python: $PYBIN"
echo "[daily] RUN_ID=$RUN_ID PROFILE=$PROFILE"
echo "[daily] paths: candidates=$CANDIDATES_PATH ingested=$INGESTED_PATH indexed=$INDEXED_PATH report=$REPORT_PATH"

# Optional seed strains
SEED_STRAINS="${SEED_STRAINS:-0}"

if [[ "$SEED_STRAINS" == "1" ]]; then
  "$PYBIN" -m pipelines.seed_strains
else
  echo "[daily] SEED_STRAINS=0 -> skip pipelines.seed_strains (set SEED_STRAINS=1 to enable)"
fi

# Run the pipeline steps
"$PYBIN" -m pipelines.discover
"$PYBIN" -m pipelines.ingest

# Enrich DOI/OA metadata (best-effort; may require UNPAYWALL_EMAIL)
"$PYBIN" -m pipelines.enrich_doi_oa || echo "[daily] WARN: enrich_doi_oa failed (non-fatal)" >&2

"$PYBIN" -m pipelines.index
"$PYBIN" -m pipelines.report

# evaluate.py exits 2 on QC FAIL — we do NOT want that to abort the run.
# Capture the exit code, log it, and continue so downstream steps still run.
QC_EXIT=0
"$PYBIN" -m pipelines.evaluate || QC_EXIT=$?
if [[ $QC_EXIT -eq 0 ]]; then
  echo "[daily] QC: PASS"
elif [[ $QC_EXIT -eq 2 ]]; then
  echo "[daily] WARN: QC FAIL (exit 2) — run continues. Check report." >&2
  echo "{\"qc\":\"FAIL\",\"run_id\":\"${RUN_ID}\"}" > "${STATE_DIR}/${RUN_ID}_qc_fail.json"
else
  echo "[daily] ERROR: evaluate exited with unexpected code $QC_EXIT." >&2
  exit "$QC_EXIT"
fi

# Validator (can be heavy). Enabled by default as best-effort.
"$PYBIN" -m pipelines.kb_validate || echo "[daily] WARN: kb_validate failed (non-fatal)" >&2

# OCR backlog (nightly-ish). Keep it best-effort.
"$PYBIN" -m pipelines.ocr_backlog || echo "[daily] WARN: ocr_backlog failed (non-fatal)" >&2

# Daily aggregate (best-effort): does not fail the whole run if it breaks
"$PYBIN" -m pipelines.aggregate_daily || echo "[daily] WARN: aggregate_daily failed (non-fatal)" >&2

# Pruning old state files (best-effort)
bash "$ROOT_DIR/pipelines/prune_artifacts.sh" || echo "[daily] WARN: prune_artifacts failed (non-fatal)" >&2
