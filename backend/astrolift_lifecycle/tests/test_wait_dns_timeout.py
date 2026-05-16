"""
wait_dns default timeout (#359).

Route53 propagation + new managed-domain setup commonly exceeds
the previous 120s window on the first deploy of a brand-new env.
Bumped to 300s by default. Workflows that need a different ceiling
still pass an explicit ``timeout_seconds`` arg — only the default
moves here.
"""

from __future__ import annotations

import inspect

from astrolift_workflows.activities.app_lifecycle import wait_dns


def test_wait_dns_default_timeout_is_300_seconds():
    """Inspect the activity function signature rather than calling
    it, because the activity opens DB connections and is wrapped by
    a Temporal decorator."""
    sig = inspect.signature(wait_dns)
    assert sig.parameters["timeout_seconds"].default == 300
