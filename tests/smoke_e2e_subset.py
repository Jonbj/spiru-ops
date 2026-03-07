"""End-to-end smoke test (network + docker services).

Usage (from repo root):

  python -m pipelines.discover
  python tests/smoke_e2e_subset.py --n 20

It will:
- take top-N URLs from today's candidates file
- run ingest + index in an isolated STATE_DIR

This is intentionally NOT part of unit-test discovery.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess

from pipelines.common import day_stamp_utc, env


def read_jsonl(path: pathlib.Path) -> list[dict]:
    rows = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        rows.append(json.loads(ln))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--candidates", type=str, default=None, help="override candidates jsonl")
    args = ap.parse_args()

    day = day_stamp_utc()
    default_cand = pathlib.Path(env("STATE_DIR", "storage/state")) / f"{day}_candidates.jsonl"
    cand_path = pathlib.Path(args.candidates) if args.candidates else default_cand
    if not cand_path.exists():
        raise SystemExit(f"Missing candidates file: {cand_path} (run discover first)")

    rows = read_jsonl(cand_path)
    rows = sorted(rows, key=lambda r: float(r.get("score") or 0.0), reverse=True)
    subset = rows[: max(1, int(args.n))]

    smoke_state = pathlib.Path("storage/state/_smoke")
    smoke_state.mkdir(parents=True, exist_ok=True)
    smoke_cand = smoke_state / f"{day}_candidates.jsonl"
    smoke_cand.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in subset) + "\n", encoding="utf-8")

    env2 = os.environ.copy()
    env2["STATE_DIR"] = str(smoke_state)
    env2["CANDIDATES_PATH"] = str(smoke_cand)
    # Make smoke fast(er)
    env2.setdefault("MAX_DOWNLOAD_MB", "12")
    env2.setdefault("REQUEST_TIMEOUT_S", "45")
    env2.setdefault("UNSTRUCTURED_MAX_MB", "12")

    print(f"[smoke] candidates_subset={len(subset)} -> {smoke_cand}")

    subprocess.check_call(["python", "-m", "pipelines.ingest"], env=env2)
    subprocess.check_call(["python", "-m", "pipelines.index"], env=env2)

    print("[smoke] ✅ ingest+index completed")


if __name__ == "__main__":
    main()
