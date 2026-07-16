"""Shared test setup for the providers suite.

``tests/aws/test_binding_envelope.py`` pins the cross-tree contract
(#1003) between driver ``binding()`` keys and the canonical envelopes
in ``astrolift_manifest.env_injection``. That module is dependency-free
(no Django), but it lives in the parent backend tree which is not
installed in the providers venv — put the backend root on ``sys.path``
so the contract test can import it.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
