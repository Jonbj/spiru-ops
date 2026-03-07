"""Pytest configuration for spiru-ops.

Why this file exists
--------------------
The project is intentionally lightweight and does not require an installed
package (no `pip install -e .` step) for local development.

However, pytest collects tests from the `tests/` directory, and without adjusting
`sys.path`, importing `pipelines.*` can fail depending on how pytest is invoked.

This conftest ensures that the repository root is always on `sys.path` so that
`import pipelines...` works consistently in CI, locally, and in AI tool runners.

This is *test-only* behavior and does not affect production code.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
