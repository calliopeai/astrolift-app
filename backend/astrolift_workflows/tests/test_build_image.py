"""Tests for BuildImageActivity scaffold (#865, #867).

Covers:
- ``_build_image_sync`` returns the stub result when no BuildDriver is
  wired (the common case in test environments and pre-driver deploys).
- ``_build_image_sync`` short-circuits cleanly when build_strategy is "off".
- ``_fetch_app_build_strategy_sync`` returns the correct value from the DB.
- ``BuildImageInput`` is a plain frozen dataclass that serialises correctly
  (Temporal requires primitive-typed fields).
"""

from __future__ import annotations

import pytest

from astrolift_workflows.activities.build_image import (
    BuildImageInput,
    _build_image_sync,
    _fetch_app_build_strategy_sync,
)


# ---------------------------------------------------------------------------
# BuildImageInput contract
# ---------------------------------------------------------------------------


def test_build_image_input_is_frozen():
    inp = BuildImageInput(app_guid="abc-123", image_tag="reg/repo:sha", commit_sha="deadbeef")
    with pytest.raises((AttributeError, TypeError)):
        inp.app_guid = "changed"  # type: ignore[misc]


def test_build_image_input_fields():
    inp = BuildImageInput(app_guid="g", image_tag="t", commit_sha="s")
    assert inp.app_guid == "g"
    assert inp.image_tag == "t"
    assert inp.commit_sha == "s"


# ---------------------------------------------------------------------------
# _build_image_sync with no real DB (no-db marker — pure logic tests)
# ---------------------------------------------------------------------------


def test_build_image_sync_returns_stub_when_no_driver(monkeypatch):
    """When no BuildDriver is wired the activity must return the stub result
    with the original image_tag so the workflow can proceed unblocked."""
    import sys
    # Use sys.modules to get the actual module, not the re-exported function
    # from astrolift_workflows.activities.__init__ (which has the same name).
    import importlib
    build_image_mod = importlib.import_module("astrolift_workflows.activities.build_image")

    # Simulate app found with build_strategy='dockerfile' but no driver.
    class FakeApp:
        build_strategy = "dockerfile"
        source_repo = ""
        organization = None
        default_tenant_cluster = None
        slug = "fake-app"

    monkeypatch.setattr(build_image_mod, "_resolve_build_driver", lambda _cluster: None)

    class FakeManager:
        def select_related(self, *a, **kw):
            return self

        def get(self, **kw):
            return FakeApp()

    import astrolift_registry.models.registered_app as ra_mod

    monkeypatch.setattr(ra_mod.RegisteredApp, "objects", FakeManager())

    inp = BuildImageInput(app_guid="fake-guid", image_tag="reg/repo:abc", commit_sha="abc123")
    result = _build_image_sync(inp)

    assert result["ok"] is True
    assert result["image_ref"] == "reg/repo:abc"
    assert result["stub"] is True


def test_build_image_sync_skips_when_build_strategy_off(monkeypatch):
    """When build_strategy is 'off' the activity returns the stub immediately
    without attempting driver resolution."""
    import importlib
    build_image_mod = importlib.import_module("astrolift_workflows.activities.build_image")

    class FakeApp:
        build_strategy = "off"
        source_repo = ""
        organization = None
        default_tenant_cluster = None
        slug = "off-app"

    driver_called = []

    def _never_called(cluster):
        driver_called.append(True)
        return None

    monkeypatch.setattr(build_image_mod, "_resolve_build_driver", _never_called)

    class FakeManager:
        def select_related(self, *a, **kw):
            return self

        def get(self, **kw):
            return FakeApp()

    import astrolift_registry.models.registered_app as ra_mod

    monkeypatch.setattr(ra_mod.RegisteredApp, "objects", FakeManager())

    inp = BuildImageInput(app_guid="off-guid", image_tag="reg/repo:sha", commit_sha="")
    result = _build_image_sync(inp)

    assert result["ok"] is True
    assert result["stub"] is True
    # Driver should never have been consulted.
    assert driver_called == []


# ---------------------------------------------------------------------------
# DB-backed tests
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.django_db


def test_fetch_app_build_strategy_returns_correct_value():
    from astrolift_identity.models import Organization, Team
    from astrolift_registry.models import RegisteredApp

    org = Organization.objects.create(name="BuildOrg", slug="buildorg-bm-act")
    team = Team.objects.create(organization=org, name="T", slug="t-bm-act")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        name="App",
        slug="app-bm-act",
        provisioning_status="ready",
        subdomain="app-act",
        build_strategy="nixpacks",
    )

    result = _fetch_app_build_strategy_sync(app.pk)
    assert result == "nixpacks"


def test_fetch_app_build_strategy_returns_off_for_missing_app():
    result = _fetch_app_build_strategy_sync(999_999_999)
    assert result == "off"


def test_fetch_app_build_strategy_returns_off_by_default():
    from astrolift_identity.models import Organization, Team
    from astrolift_registry.models import RegisteredApp

    org = Organization.objects.create(name="DefOrg", slug="deford-bm-act")
    team = Team.objects.create(organization=org, name="T", slug="t-def-bm-act")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        name="Def",
        slug="def-bm-act",
        provisioning_status="ready",
        subdomain="def-act",
    )

    result = _fetch_app_build_strategy_sync(app.pk)
    assert result == "off"
