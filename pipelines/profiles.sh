#!/usr/bin/env bash
# pipelines/profiles.sh
# =============================================================================
# Runtime profiles for spiru-ops
# =============================================================================
#
# Why profiles exist
# ------------------
# The same codebase runs in different operating modes:
# - "balanced": reasonable stability and throughput (good default)
# - "kb_first": maximize knowledge capture (accept slower runs and more I/O)
#
# Profiles are implemented as *environment variable presets*.
# This is intentionally simple:
# - It avoids complex config loaders in bash.
# - It plays well with cron (which is environment-variable driven).
#
# How it is used
# --------------
# - pipelines/daily.sh sets PROFILE=${PROFILE:-balanced} and sources this file.
# - pipelines/cron_run_daily.sh can set PROFILE=kb_first for scheduled runs.
#
# Design goals
# ------------
# - Make the pipeline behavior reproducible between manual runs and cron.
# - Allow "knobs" for stability/cost without editing code.
#
# =============================================================================

set -euo pipefail

PROFILE="${PROFILE:-balanced}"

apply_balanced() {
  # ----------------------------
  # Balanced: good default
  # ----------------------------
  # Download size limit (MB)
  # - big PDFs can be slow and can crash Unstructured
  export MAX_DOWNLOAD_MB="${MAX_DOWNLOAD_MB:-50}"

  # PDFs larger than this threshold bypass Unstructured (use local fallback)
  export UNSTRUCTURED_MAX_MB="${UNSTRUCTURED_MAX_MB:-25}"

  # Networking timeouts (seconds)
  export PDF_REQUEST_TIMEOUT_S="${PDF_REQUEST_TIMEOUT_S:-90}"
  export HTML_REQUEST_TIMEOUT_S="${HTML_REQUEST_TIMEOUT_S:-40}"
  export HEAD_TIMEOUT_S="${HEAD_TIMEOUT_S:-20}"

  # Domain-level cooldown limits
  # - Prevent hammering paywalled sites and wasting minutes on repeated 403/429.
  export MAX_403_PER_DOMAIN="${MAX_403_PER_DOMAIN:-5}"
  export MAX_429_PER_DOMAIN="${MAX_429_PER_DOMAIN:-3}"

  # Parsing / enrichment toggles
  export UNSTRUCTURED_ENABLE="${UNSTRUCTURED_ENABLE:-1}"
  export GROBID_ENABLE="${GROBID_ENABLE:-0}"
  export GROBID_FULLTEXT="${GROBID_FULLTEXT:-0}"

  # OpenAlex enrichment behavior
  # - "balanced" keeps this off by default (reduces API calls).
  export OPENALEX_ENRICH_ALWAYS="${OPENALEX_ENRICH_ALWAYS:-0}"
  export OPENALEX_CACHE_ENABLE="${OPENALEX_CACHE_ENABLE:-1}"
}

apply_kb_first() {
  # ----------------------------
  # KB-first: maximize knowledge
  # ----------------------------
  # Larger downloads allowed to capture more content.
  export MAX_DOWNLOAD_MB="${MAX_DOWNLOAD_MB:-120}"

  # BUT: keep Unstructured safer by feeding it only smaller PDFs.
  # Big PDFs still get downloaded, but parsed via local fallback.
  export UNSTRUCTURED_MAX_MB="${UNSTRUCTURED_MAX_MB:-15}"

  # More generous timeouts.
  export PDF_REQUEST_TIMEOUT_S="${PDF_REQUEST_TIMEOUT_S:-120}"
  export HTML_REQUEST_TIMEOUT_S="${HTML_REQUEST_TIMEOUT_S:-60}"
  export HEAD_TIMEOUT_S="${HEAD_TIMEOUT_S:-25}"

  # Allow more 403/429 before cooling down a domain.
  # Rationale: KB-first tries harder, but still eventually stops wasting time.
  export MAX_403_PER_DOMAIN="${MAX_403_PER_DOMAIN:-15}"
  export MAX_429_PER_DOMAIN="${MAX_429_PER_DOMAIN:-5}"

  export UNSTRUCTURED_ENABLE="${UNSTRUCTURED_ENABLE:-1}"

  # GROBID is powerful but can be fragile (corrupt PDFs). Keep off by default.
  export GROBID_ENABLE="${GROBID_ENABLE:-0}"
  export GROBID_FULLTEXT="${GROBID_FULLTEXT:-0}"

  # KB-first: do OpenAlex enrichment when DOI is known.
  # This helps:
  # - Recover alternative OA/PDF URLs when publishers 403 on PDF-direct links.
  # - Fill metadata like publication year.
  export OPENALEX_ENRICH_ALWAYS="${OPENALEX_ENRICH_ALWAYS:-1}"
  export OPENALEX_CACHE_ENABLE="${OPENALEX_CACHE_ENABLE:-1}"
}

case "$PROFILE" in
  kb_first) apply_kb_first ;;
  balanced|*) apply_balanced ;;
esac
