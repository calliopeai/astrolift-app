"""`python -m astrolift_ci_convert` — the same CLI as `alci` (#1601).

Present so the converter is runnable from a checkout with no install step.
`alci` itself comes from the console script in this package's `pyproject.toml`.
"""

from __future__ import annotations

import sys

from astrolift_ci_convert.cli import main

if __name__ == "__main__":
    sys.exit(main())
