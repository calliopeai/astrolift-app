"""``_managed_binding_for`` threads ``ManagedService.config`` into the
driver's ``binding`` call (#1038).

Config-driven binding fields (e.g. the SES driver's from_name / reply_to
/ env_senders) only render when the operator config reaches ``binding``.
The resolver must pass it to drivers whose signature accepts ``config``
and must NOT pass it to drivers that take ``(self, handle)`` only — those
are the majority (the 7 working kinds) and an unconditional kwarg would
TypeError them.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from astrolift_workflows.activities import managed_service_lifecycle as msl


class _ConfigAwareDriver:
    last_config: dict[str, Any] | None = None

    def __init__(self, *, config: Any) -> None:
        self._config = config

    def binding(self, handle: Any, config: dict[str, Any] | None = None) -> Any:
        type(self).last_config = config
        return SimpleNamespace(env_vars={}, handle=handle.handle)


class _ConfigBlindDriver:
    called = False

    def __init__(self, *, config: Any) -> None:
        self._config = config

    def binding(self, handle: Any) -> Any:  # no config param
        type(self).called = True
        return SimpleNamespace(env_vars={}, handle=handle.handle)


def _svc(config: dict[str, Any] | None = None) -> SimpleNamespace:
    cluster = SimpleNamespace(
        slug="aws-prod",
        provider_plugin=SimpleNamespace(slug="aws"),
    )
    return SimpleNamespace(
        backend_ref="email/acme.example",
        app_environment=SimpleNamespace(tenant_cluster=cluster),
        kind="email",
        variant="",
        config=config or {},
        # A saved row always has one; drivers stamp it as the ownership id.
        guid=uuid.UUID("00000000-0000-4000-8000-0000000000ab"),
    )


@pytest.fixture
def _patched(monkeypatch: pytest.MonkeyPatch):
    """Patch the driver registry + config builder the resolver imports."""
    from astrolift_drivers import registry
    from core import cluster_observability

    monkeypatch.setattr(
        cluster_observability,
        "managed_config_for",
        lambda *a, **k: object(),
    )
    _ConfigAwareDriver.last_config = None
    _ConfigBlindDriver.called = False
    return registry


def test_config_aware_driver_receives_service_config(_patched, monkeypatch) -> None:
    """Falsifiable: drop the config passthrough and last_config is None."""
    monkeypatch.setattr(_patched.plugins, "get", lambda *a, **k: _ConfigAwareDriver)
    cfg = {"from_name": "Acme", "base_domain": "tenant.example"}
    binding = msl._managed_binding_for(_svc(cfg))
    assert binding is not None
    assert _ConfigAwareDriver.last_config == cfg


def test_config_blind_driver_is_called_without_config(_patched, monkeypatch) -> None:
    """Defensive non-regression: a driver whose binding takes only
    (self, handle) must not be handed a config kwarg — that would
    TypeError. Falsifiable: pass config unconditionally and this raises."""
    monkeypatch.setattr(_patched.plugins, "get", lambda *a, **k: _ConfigBlindDriver)
    binding = msl._managed_binding_for(_svc({"from_name": "Acme"}))
    assert binding is not None
    assert _ConfigBlindDriver.called is True


def test_the_stamped_identity_is_the_guid_and_never_the_primary_key():
    """What a driver writes onto the cloud resource has to be what the next
    operation reads back (#1365).

    ``guid`` is a non-null UUID with a default on the base model, so a saved row
    always has one. A ``pk`` fallback would stamp an integer where every other
    path stamps a UUID, and the resource would fail its own ownership check on
    every later update and teardown -- which fail closed, so it could never be
    removed.
    """
    svc = _svc()

    assert msl._service_identity(svc) == "00000000-0000-4000-8000-0000000000ab"


def test_a_row_without_a_guid_is_refused_rather_than_stamped_with_its_pk():
    svc = SimpleNamespace(pk=42, backend_ref="email/acme.example")

    with pytest.raises(RuntimeError, match="no guid"):
        msl._service_identity(svc)
