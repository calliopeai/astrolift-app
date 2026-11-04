"""Where a provisioning attempt starts, given where the last failed one stopped (#2606).

Pure, with no Temporal import, so the rule is unit-testable on its own. The
workflow runs the steps ``provision`` (driver call), ``wait_ready`` (poll the
backend until available) and ``finalize`` (persist the handle, go ACTIVE).
"""

from __future__ import annotations

PROVISION = "provision"
WAIT_READY = "wait_ready"
FINALIZE = "finalize"

# Steps that need the backend handle, which only finalize persists today.
# A resume at one of them is taken only when a handle is already on the row.
_NEEDS_HANDLE = frozenset({WAIT_READY, FINALIZE})
_STEPS = frozenset({PROVISION, WAIT_READY, FINALIZE})


def resume_step(failed_step: str, backend_ref: str) -> str:
    """The step the next attempt starts from.

    An unknown or empty ``failed_step`` starts at the beginning. A resume past
    the driver call needs the recorded handle; without one the driver call runs
    again, which is safe because drivers probe for an existing resource first.
    """
    if failed_step not in _STEPS:
        return PROVISION
    if failed_step in _NEEDS_HANDLE and not backend_ref:
        return PROVISION
    return failed_step
