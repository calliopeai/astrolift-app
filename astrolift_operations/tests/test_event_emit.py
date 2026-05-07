"""
Tests for ``Event.emit()`` (P0.5c #180).

Real Postgres — no mocks. We exercise the emit path end-to-end:
populate a tenant + actor on the contextvar, call emit, and read the
row back from the ``Event`` table to assert provenance was captured.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_operations.models import Event
from core.events import Event as EventEmitter
from core.events import register_event_writer
from core.request_context import (
    TraceContext,
    reset_request_id,
    reset_trace_context,
    set_request_id,
    set_trace_context,
)
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _make_tenant() -> tuple[Organization, Team, Project]:
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(name="Eng", slug="eng", organization=org)
    project = Project.objects.create(name="API", slug="api", team=team)
    return org, team, project


# ---- emit -> persistent row -------------------------------------


def test_emit_creates_persisted_event_with_tenant_chain():
    # The persistent writer is registered in
    # ``astrolift_operations.apps.ready``; in tests django.setup runs
    # apps.ready, so emit() should already point at the DB writer.
    org, team, project = _make_tenant()
    with tenant_context(
        TenantContext(
            organization_id=org.id,
            team_id=team.id,
            project_id=project.id,
            actor_user_id=None,
        )
    ):
        EventEmitter.emit(
            "app.created",
            {"slug": "demo"},
            resource_kind="RegisteredApp",
            resource_id="abc-123",
        )

    row = Event.objects.get()
    assert row.event_type == "app.created"
    assert row.payload == {"slug": "demo"}
    assert row.organization_id == org.id
    assert row.team_id == team.id
    assert row.project_id == project.id
    assert row.resource_kind == "RegisteredApp"
    assert row.resource_id == "abc-123"


def test_emit_pulls_request_and_trace_from_contextvars():
    org, _team, _project = _make_tenant()
    rid = "01EVENTREQUESTIDFROMCONTEXTVAR"
    trace = TraceContext(
        trace_id="cafebabecafebabecafebabecafebabe",
        span_id="cafebabecafebabe",
    )
    rid_token = set_request_id(rid)
    trace_token = set_trace_context(trace)
    try:
        with tenant_context(TenantContext(organization_id=org.id)):
            EventEmitter.emit("app.deployed", {"image": "x:y"})
    finally:
        reset_trace_context(trace_token)
        reset_request_id(rid_token)

    row = Event.objects.get()
    assert row.request_id == rid
    assert row.trace_id == trace.trace_id


def test_emit_explicit_request_id_overrides_contextvar():
    org, _t, _p = _make_tenant()
    rid_token = set_request_id("01CONTEXTVARRIDOVERRIDDEN0000")
    try:
        with tenant_context(TenantContext(organization_id=org.id)):
            EventEmitter.emit(
                "member.invited",
                {"email": "x@y"},
                request_id="01EXPLICITLYPASSEDREQUESTID00",
                trace_id="deadbeefdeadbeefdeadbeefdeadbeef",
            )
    finally:
        reset_request_id(rid_token)

    row = Event.objects.get()
    assert row.request_id == "01EXPLICITLYPASSEDREQUESTID00"
    assert row.trace_id == "deadbeefdeadbeefdeadbeefdeadbeef"


def test_emit_drops_tenantless_event_quietly():
    # No tenant on the contextvar, no organization_id passed → the
    # writer logs a warning and drops the row. We assert no row was
    # created and no exception was raised.
    EventEmitter.emit("system.startup", {"version": "0.0.40"})
    assert Event.objects.count() == 0


def test_emit_with_explicit_organization_id_succeeds_without_tenant():
    org, _t, _p = _make_tenant()
    EventEmitter.emit(
        "system.broadcast",
        {"channel": "ops"},
        organization_id=org.id,
    )
    row = Event.objects.get()
    assert row.organization_id == org.id


# ---- AppendOnly enforcement -------------------------------------


def test_event_row_cannot_be_updated():
    org, _t, _p = _make_tenant()
    EventEmitter.emit("app.created", {}, organization_id=org.id)
    row = Event.objects.get()

    row.payload = {"tampered": True}
    with pytest.raises(RuntimeError, match="append-only"):
        row.save()


def test_event_row_cannot_be_deleted():
    org, _t, _p = _make_tenant()
    EventEmitter.emit("app.created", {}, organization_id=org.id)
    row = Event.objects.get()

    with pytest.raises(RuntimeError, match="append-only"):
        row.delete()


# ---- Writer swap (covers test isolation) ------------------------


def test_writer_can_be_temporarily_replaced_under_test():
    captured: list = []

    def fake_writer(env):
        captured.append(env)

    from astrolift_operations.event_writer import write_event_envelope

    register_event_writer(fake_writer)
    try:
        EventEmitter.emit("custom.event", {"k": "v"}, organization_id=1)
        assert len(captured) == 1
        assert captured[0].event_type == "custom.event"
    finally:
        register_event_writer(write_event_envelope)
