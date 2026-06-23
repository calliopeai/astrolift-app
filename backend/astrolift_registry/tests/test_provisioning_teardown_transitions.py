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
