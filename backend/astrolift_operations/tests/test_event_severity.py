"""Tests for the ``Event.severity`` column (#540).

Covers:

* The shared heuristic in ``core.events.infer_event_severity`` over
  the full precedence ladder (explicit payload > status mapping >
  event-type shape > default).
* The persistent writer stamps severity on the row.
* ``astrolift_events`` and ``astrolift_events_page`` accept the
  ``severity`` arg and filter the queryset accordingly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization
from astrolift_operations.models import Event
from astrolift_operations.schema.queries import OperationsQuery
from core.events import Event as EventEmitter
from core.events import infer_event_severity
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    """Stand-in for ``strawberry.types.Info`` — the resolvers read
    ``.context.user`` indirectly through the permission decorator;
    callers grant the permission via ``permission_resolver`` instead."""
    return SimpleNamespace(context=SimpleNamespace(user=None))


def _make_org() -> Organization:
    return Organization.objects.create(name="Acme", slug="acme")


# ---- heuristic ---------------------------------------------------


def test_infer_payload_severity_wins_over_event_type():
    assert (
        infer_event_severity(
            "app.deploy.failed",
            {"severity": "info"},
        )
        == "info"
    )


def test_infer_payload_level_fallback():
    assert infer_event_severity("custom.thing", {"level": "warn"}) == "warn"


def test_infer_status_failed_maps_to_error():
    assert infer_event_severity("custom.thing", {"status": "failed"}) == "error"


def test_infer_status_degraded_maps_to_warn():
    assert infer_event_severity("custom.thing", {"status": "degraded"}) == "warn"


def test_infer_event_type_failed_suffix():
    assert infer_event_severity("deploy.failed", {}) == "error"


def test_infer_event_type_error_substring():
    assert infer_event_severity("workload.error_burst", {}) == "error"


def test_infer_event_type_warned_suffix():
    assert infer_event_severity("policy.warned", {}) == "warn"


def test_infer_event_type_warn_substring():
    assert infer_event_severity("config.drift_warning", {}) == "warn"


def test_infer_default_info():
    assert infer_event_severity("app.created", {"slug": "demo"}) == "info"


def test_infer_ignores_non_canonical_payload_severity():
    # ``severity`` outside the canonical set is ignored; we fall
    # through to the next layer instead of trusting arbitrary strings.
    assert infer_event_severity("app.created", {"severity": "fatal"}) == "info"


# ---- writer stamps severity ----------------------------------------


def test_emit_stamps_severity_from_payload():
    org = _make_org()
    with tenant_context(TenantContext(organization_id=org.id)):
        EventEmitter.emit("custom.fact", {"severity": "warn"})
    row = Event.objects.get()
    assert row.severity == "warn"


def test_emit_stamps_severity_from_event_type():
    org = _make_org()
    with tenant_context(TenantContext(organization_id=org.id)):
        EventEmitter.emit("deploy.failed", {"image": "x:y"})
    row = Event.objects.get()
    assert row.severity == "error"


def test_emit_default_severity_is_info():
    org = _make_org()
    with tenant_context(TenantContext(organization_id=org.id)):
        EventEmitter.emit("app.created", {"slug": "demo"})
    row = Event.objects.get()
    assert row.severity == "info"


# ---- query filter --------------------------------------------------


def test_astrolift_events_filters_by_severity(permission_resolver):
    org = _make_org()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        EventEmitter.emit("deploy.failed", {})
        EventEmitter.emit("app.created", {})
        EventEmitter.emit("policy.warned", {})

        rows = OperationsQuery().astrolift_events(_info(), severity="error")
    assert len(rows) == 1
    assert rows[0].event_type == "deploy.failed"
    assert rows[0].severity == "error"


def test_astrolift_events_page_filters_by_severity(permission_resolver):
    org = _make_org()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        EventEmitter.emit("deploy.failed", {})
        EventEmitter.emit("app.created", {})
        EventEmitter.emit("policy.warned", {})

        page = OperationsQuery().astrolift_events_page(_info(), severity="warn")
    assert len(page.items) == 1
    assert page.items[0].event_type == "policy.warned"
    assert page.items[0].severity == "warn"


def test_astrolift_events_unfiltered_returns_all_severities(permission_resolver):
    org = _make_org()
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    with tenant_context(TenantContext(organization_id=org.id)):
        EventEmitter.emit("deploy.failed", {})
        EventEmitter.emit("app.created", {})

        rows = OperationsQuery().astrolift_events(_info())
    assert {r.severity for r in rows} == {"info", "error"}
