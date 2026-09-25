"""Tests for the ``setFeatureFlag`` runtime feature-flipper mutation.

Covers the framework the admin "feature flipper" screen
(/administration/features) wires to:

* platform-operator gating (#1978): anyone else is denied before any
  Constance write happens;
* the public allow-list is the ONLY settable surface — unknown /
  non-public keys (including build-time feature keys) are rejected;
* a successful toggle writes the backing Constance value and is
  reflected on the next ``astroliftServerInfo`` handshake.

Constance's default backend is a Django model + cache; following the
repo idiom (see ``astrolift_lifecycle/tests/test_deploy_pipeline_
constance.py`` and ``core/schema/tests/test_server_info.py``) we swap a
fake ``constance`` module into ``sys.modules`` rather than round-trip a
DB write, so both the mutation's write and the handshake's read resolve
the same in-memory config object.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from config.schema import schema
from core.permissions import Permission
from core.schema.context import StrawberryContext
from core.schema.mutations.feature_flags import FeatureFlagMutations

_SERVER_INFO_QUERY = """
{
  astroliftServerInfo {
    featureFlags { key enabled description }
    buildTimeFeatures { key enabled envVar description }
  }
}
"""


def _info(user=None):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=None))


def _operator():
    # Setting a flag is the platform operator's alone (#1978): an active
    # superuser. Unsaved, because the gate reads only the user's flags.
    return get_user_model()(username="flag-operator", is_superuser=True, is_active=True)


def _anonymous_context() -> StrawberryContext:
    request = RequestFactory().get("/app/gql/config/")
    request.user = AnonymousUser()
    request.session = {}
    return StrawberryContext(request)


def _server_info() -> dict:
    result = schema.execute_sync(_SERVER_INFO_QUERY, context_value=_anonymous_context())
    assert result.errors is None, f"unexpected errors: {result.errors}"
    return result.data["astroliftServerInfo"]


@pytest.fixture
def fake_constance(monkeypatch):
    """Install an in-memory ``constance.config`` shared by the mutation
    (write) and the ``astroliftServerInfo`` resolver (read)."""
    config = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "constance", SimpleNamespace(config=config))
    return config


# ---- gating ---------------------------------------------------------


def test_set_feature_flag_denied_for_non_admin(permission_resolver, fake_constance):
    """No ``admin.elevate`` grant → PERMISSION_DENIED envelope, and the
    Constance write never happens (the gate fires before the body)."""
    result = FeatureFlagMutations().set_feature_flag(
        _info(), key="zentinelle.enabled", enabled=True
    )
    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"
    # The gate fired before any write.
    assert not hasattr(fake_constance, "ZENTINELLE_ENABLED")


# ---- allow-list enforcement -----------------------------------------


def test_set_feature_flag_rejects_unknown_key(permission_resolver, fake_constance):
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
    result = FeatureFlagMutations().set_feature_flag(
        _info(_operator()), key="totally.bogus", enabled=True
    )
    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "key"
    assert vars(fake_constance) == {}


def test_set_feature_flag_rejects_build_time_feature_key(
    permission_resolver, fake_constance
):
    """Build-time features (``config.features.Feature``) gate app / schema
    LOADING at boot and are read-only — their keys are not in the runtime
    allow-list, so ``setFeatureFlag`` rejects them."""
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
    result = FeatureFlagMutations().set_feature_flag(_info(_operator()), key="agents", enabled=False)
    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert vars(fake_constance) == {}


# ---- toggle + reflection --------------------------------------------


def test_set_feature_flag_toggles_and_reflects_in_server_info(
    permission_resolver, fake_constance
):
    permission_resolver.grant(Permission.ADMIN_ELEVATE)

    # Baseline: admin.cost_enabled absent on the fake config → disabled.
    flags0 = {f["key"]: f["enabled"] for f in _server_info()["featureFlags"]}
    assert flags0["admin.cost_enabled"] is False

    # Flip it on.
    result = FeatureFlagMutations().set_feature_flag(
        _info(_operator()), key="admin.cost_enabled", enabled=True
    )
    assert result.ok, result.errors
    assert result.data.key == "admin.cost_enabled"
    assert result.data.enabled is True
    assert result.data.description  # the allow-list description is echoed
    # The backing Constance key was written.
    assert fake_constance.ADMIN_COST_ENABLED is True

    # Next handshake reflects it.
    flags1 = {f["key"]: f["enabled"] for f in _server_info()["featureFlags"]}
    assert flags1["admin.cost_enabled"] is True

    # Flip back off — handshake follows.
    off = FeatureFlagMutations().set_feature_flag(
        _info(_operator()), key="admin.cost_enabled", enabled=False
    )
    assert off.ok and off.data.enabled is False
    assert fake_constance.ADMIN_COST_ENABLED is False
    flags2 = {f["key"]: f["enabled"] for f in _server_info()["featureFlags"]}
    assert flags2["admin.cost_enabled"] is False


# ---- the three new admin-screen flags -------------------------------


def test_new_admin_flags_present_and_default_off(fake_constance):
    flags = {f["key"]: f["enabled"] for f in _server_info()["featureFlags"]}
    for key in ("admin.cost_enabled", "admin.quotas_enabled", "admin.permissions_enabled"):
        assert key in flags, f"{key} missing from featureFlags"
        assert flags[key] is False, f"{key} must default OFF"


# ---- build-time (install-time) read-only inventory ------------------


def test_build_time_features_surface_read_only(fake_constance):
    info = _server_info()
    by_key = {f["key"]: f for f in info["buildTimeFeatures"]}

    # The config.features.Feature enum is reflected with its env var.
    assert by_key["agents"]["envVar"] == "FEATURE_AGENTS"
    assert "workflows" in by_key
    assert by_key["workflows"]["description"]

    # Build-time keys are disjoint from the runtime allow-list keys, so
    # the FE renders them as separate (read-only) rows and setFeatureFlag
    # never targets them.
    runtime = {f["key"] for f in info["featureFlags"]}
    assert set(by_key) & runtime == set()
