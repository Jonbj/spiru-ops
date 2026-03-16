#!/usr/bin/env python3
"""scripts/update_obsidian_diario.py

Appende una riga di log al diario Obsidian dopo ogni run della pipeline.
Legge i dati dal daily_aggregate e dall'ingested del run corrente.
Invocato da daily.sh come passo finale best-effort.
"""

import json
import os
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipelines.common import env, run_id, day_stamp_utc

VAULT_DIR = pathlib.Path(env("OBSIDIAN_VAULT_DIR", str(ROOT / "obsidian-vault")))
DIARIO_PATH = VAULT_DIR / "coltura" / "diario.md"
STATE_DIR = pathlib.Path(env("STATE_DIR", str(ROOT / "storage/state")))
ARTIFACTS_DIR = pathlib.Path(env("ARTIFACTS_DIR", str(ROOT / "storage/artifacts")))


def _load_ingested_summary(rid: str) -> dict:
    p = STATE_DIR / f"{rid}_ingested.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        ingested = data.get("ingested", [])
        spiru_scores = [float(d.get("spirulina_score") or 0) for d in ingested]
        avg_spiru = round(sum(spiru_scores) / len(spiru_scores), 3) if spiru_scores else 0
        indexed_path = STATE_DIR / f"{rid}_indexed.json"
        points = 0
        if indexed_path.exists():
            idx = json.loads(indexed_path.read_text(encoding="utf-8"))
            points = idx.get("points_upserted", 0)
        return {
            "ingested": len(ingested),
            "failures": data.get("failures_total", 0),
            "avg_spiru": avg_spiru,
            "points": points,
        }
    except Exception:
        return {}


def _load_qc_status(rid: str) -> str:
    qc_fail = STATE_DIR / f"{rid}_qc_fail.json"
    return "❌ FAIL" if qc_fail.exists() else "✅ PASS"


def main():
    if not VAULT_DIR.exists():
        print(f"[obsidian] vault not found at {VAULT_DIR}, skipping.")
        return

    rid = run_id()
    today = day_stamp_utc()
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    summary = _load_ingested_summary(rid)
    qc = _load_qc_status(rid)

    ingested = summary.get("ingested", "?")
    failures = summary.get("failures", "?")
    avg_spiru = summary.get("avg_spiru", "?")
    points = summary.get("points", "?")

    log_line = (
        f"\n### {today} — run `{rid}` ({now})\n"
        f"- **Documenti ingestiti**: {ingested} "
        f"(failures: {failures})\n"
        f"- **Punti Qdrant aggiunti**: {points}\n"
        f"- **Avg spirulina score**: {avg_spiru}\n"
        f"- **QC**: {qc}\n"
    )

    # Ensure diario exists
    if not DIARIO_PATH.exists():
        DIARIO_PATH.parent.mkdir(parents=True, exist_ok=True)
        DIARIO_PATH.write_text(
            "# Diario Pipeline — spiru-ops\n\n"
            "> Log automatico aggiornato ad ogni run della pipeline.\n",
            encoding="utf-8",
        )

    with open(DIARIO_PATH, "a", encoding="utf-8") as f:
        f.write(log_line)

    print(f"[obsidian] diario aggiornato: {DIARIO_PATH}")


if __name__ == "__main__":
    main()
