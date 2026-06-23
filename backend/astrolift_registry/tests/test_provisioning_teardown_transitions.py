"""Regression tests for the teardown provisioning states (#993).

The deregister workflow flips an app to ``tearing_down`` then ``deregistered``
via ``transition_provisioning(ProvisioningStatus(<str>))``. Those values were
missing from the ``ProvisioningStatus`` enum, so the coercion raised
``ValueError: 'tearing_down' is not a valid RegisteredApp.ProvisioningStatus``
and teardown crashed mid-flight — leaving the app + its cloud resources
orphaned (verify-clean never converged, so the cycle couldn't repeat).
"""

from __future__ import annotations

from astrolift_registry.models import RegisteredApp

PS = RegisteredApp.ProvisioningStatus


def test_teardown_states_are_enum_members():
    # The exact strings the teardown activities pass to ProvisioningStatus(...).
    assert PS("tearing_down") is PS.TEARING_DOWN
    assert PS("deregistered") is PS.DEREGISTERED


def test_teardown_transitions_are_allowed():
    t = RegisteredApp._PROVISIONING_TRANSITIONS
    # Teardown reachable from every live state.
    for src in (PS.PENDING, PS.PROVISIONING, PS.READY, PS.FAILED):
        assert PS.TEARING_DOWN in t[src], f"{src} must allow → TEARING_DOWN"
    # And tearing-down completes to deregistered.
    assert PS.DEREGISTERED in t[PS.TEARING_DOWN]


def test_transition_to_current_state_is_idempotent_noop():
    """Re-firing deregister on an app already TEARING_DOWN must NOT raise
    (mark_tearing_down → tearing_down). Otherwise a teardown that failed
    after the first step leaves the app permanently unrecoverable (#1006)."""
    app = RegisteredApp(provisioning_status=PS.TEARING_DOWN.value)
    # Same-state transition returns early (before the save), so it neither
    # raises nor needs a persisted row — the recovery re-fire can proceed.
    app.transition_provisioning(PS.TEARING_DOWN)
    assert app.provisioning_status == PS.TEARING_DOWN.value


def test_invalid_transition_still_rejected():
    """The idempotent no-op must not weaken genuine guards."""
    import pytest

    app = RegisteredApp(provisioning_status=PS.READY.value)
    with pytest.raises(ValueError, match="cannot transition"):
        app.transition_provisioning(PS.DEREGISTERED)  # READY can't jump straight to deregistered
