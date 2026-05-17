"""GraphQL coverage for the workflow viewer schema (#437).

These tests run against the in-process schema with Temporal disabled
so the client helpers return empty / None — the assertions focus on
the resolver wiring (permission gate, field shape, mutation
envelope). The Temporal happy path is exercised by the live-runtime
workflow integration tests that spin up the time-skipping env.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.test import RequestFactory

from astrolift_workflows.schema.mutations import TemporalWorkflowsMutation
from astrolift_workflows.schema.queries import TemporalWorkflowsQuery
from core.permissions import Permission

pytestmark = pytest.mark.django_db


def _info():
    rf = RequestFactory()
    return SimpleNamespace(
        context=SimpleNamespace(
            user=None,
            request=rf.get("/app/gql/config/"),
        )
    )


# ---- queries -----------------------------------------------------------


def test_list_workflow_instances_denied_without_read(permission_resolver, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    q = TemporalWorkflowsQuery()
    from core.permissions import PermissionDenied

    with pytest.raises(PermissionDenied):
        q.astrolift_workflow_instances(_info())


def test_list_workflow_instances_returns_empty_page_when_disabled(permission_resolver, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    permission_resolver.grant(Permission.AUDIT_LOG_READ)

    q = TemporalWorkflowsQuery()
    page = q.astrolift_workflow_instances(_info())
    assert page.items == []
    assert page.next_cursor is None


def test_list_workflow_instances_passes_filters(permission_resolver, settings, monkeypatch):
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    captured: dict = {}

    def _fake_list(*, workflow_type, status, limit):
        captured["workflow_type"] = workflow_type
        captured["status"] = status
        captured["limit"] = limit
        return [
            {
                "workflow_id": "DeployAppWorkflow-x",
                "workflow_type": "DeployAppWorkflow",
                "status": "RUNNING",
                "started_at": "2026-05-17T00:00:00+00:00",
                "closed_at": "",
                "run_id": "r1",
                "duration_seconds": None,
                "task_queue": "astrolift-main",
            }
        ]

    monkeypatch.setattr("astrolift_workflows.schema.queries.list_workflow_instances", _fake_list)
    q = TemporalWorkflowsQuery()
    page = q.astrolift_workflow_instances(_info(), workflow_type="DeployAppWorkflow", status="RUNNING", limit=10)
    assert captured == {"workflow_type": "DeployAppWorkflow", "status": "RUNNING", "limit": 10}
    assert len(page.items) == 1
    assert page.items[0].workflow_id == "DeployAppWorkflow-x"
    assert page.items[0].duration_seconds is None


def test_instance_detail_returns_none_when_missing(permission_resolver, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    q = TemporalWorkflowsQuery()
    assert q.astrolift_workflow_instance_detail(_info(), "missing-id") is None


def test_instance_detail_returns_none_for_empty_workflow_id(permission_resolver, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    q = TemporalWorkflowsQuery()
    assert q.astrolift_workflow_instance_detail(_info(), "") is None


def test_instance_detail_shapes_history(permission_resolver, settings, monkeypatch):
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    monkeypatch.setattr(
        "astrolift_workflows.schema.queries.describe_workflow_instance",
        lambda wid: {
            "workflow_id": wid,
            "workflow_type": "DeployAppWorkflow",
            "status": "COMPLETED",
            "started_at": "2026-05-17T00:00:00+00:00",
            "closed_at": "2026-05-17T00:01:00+00:00",
            "run_id": "r1",
            "duration_seconds": 60.0,
            "task_queue": "astrolift-main",
        },
    )
    monkeypatch.setattr(
        "astrolift_workflows.schema.queries.workflow_history",
        lambda wid: [
            {
                "event_type": "EVENT_TYPE_ACTIVITY_TASK_COMPLETED",
                "timestamp": "2026-05-17T00:00:30+00:00",
                "payload": {"activity_type": "PushImage"},
                "retry_count": 1,
                "decision": "completed",
            }
        ],
    )

    q = TemporalWorkflowsQuery()
    detail = q.astrolift_workflow_instance_detail(_info(), "DeployAppWorkflow-x")
    assert detail is not None
    assert detail.instance.status == "COMPLETED"
    assert detail.instance.duration_seconds == 60.0
    assert len(detail.history) == 1
    assert detail.history[0].decision == "completed"
    assert detail.history[0].retry_count == 1
    assert detail.history[0].payload == {"activity_type": "PushImage"}


# ---- mutations ---------------------------------------------------------


def test_cancel_denied_without_admin(permission_resolver):
    permission_resolver.grant(Permission.AUDIT_LOG_READ)  # read only — admin missing
    m = TemporalWorkflowsMutation()
    from core.permissions import PermissionDenied

    with pytest.raises(PermissionDenied):
        m.cancel_workflow_instance(_info(), "any-id")


def test_cancel_rejects_empty_id(permission_resolver):
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
    m = TemporalWorkflowsMutation()
    result = m.cancel_workflow_instance(_info(), "")
    assert result.ok is False
    assert result.errors[0].field == "workflow_id"


def test_cancel_returns_error_when_undeliverable(permission_resolver, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
    m = TemporalWorkflowsMutation()
    result = m.cancel_workflow_instance(_info(), "wf-1")
    assert result.ok is False
    assert "cancel" in result.errors[0].messages[0]


def test_cancel_succeeds(permission_resolver, monkeypatch):
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
    monkeypatch.setattr("astrolift_workflows.schema.mutations.cancel_workflow", lambda wid: True)
    m = TemporalWorkflowsMutation()
    result = m.cancel_workflow_instance(_info(), "wf-1")
    assert result.ok is True
    assert result.errors == []


def test_terminate_requires_reason(permission_resolver):
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
    m = TemporalWorkflowsMutation()
    result = m.terminate_workflow_instance(_info(), "wf-1", "")
    assert result.ok is False
    assert result.errors[0].field == "reason"


def test_terminate_succeeds(permission_resolver, monkeypatch):
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
    captured: dict = {}

    def _fake_term(wid, reason):
        captured["wid"] = wid
        captured["reason"] = reason
        return True

    monkeypatch.setattr("astrolift_workflows.schema.mutations.terminate_workflow", _fake_term)
    m = TemporalWorkflowsMutation()
    result = m.terminate_workflow_instance(_info(), "wf-1", "stuck-on-step-3")
    assert result.ok is True
    assert captured == {"wid": "wf-1", "reason": "stuck-on-step-3"}


def test_signal_requires_name(permission_resolver):
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
    m = TemporalWorkflowsMutation()
    result = m.signal_workflow_instance(_info(), "wf-1", "")
    assert result.ok is False
    assert result.errors[0].field == "signal_name"


def test_signal_passes_payload_when_present(permission_resolver, monkeypatch):
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
    captured: dict = {}

    def _fake_sig(wid, name, *args):
        captured["wid"] = wid
        captured["name"] = name
        captured["args"] = args
        return True

    monkeypatch.setattr("astrolift_workflows.schema.mutations.signal_workflow", _fake_sig)
    m = TemporalWorkflowsMutation()
    result = m.signal_workflow_instance(_info(), "wf-1", "abort", payload={"x": 1})
    assert result.ok is True
    assert captured["args"] == ({"x": 1},)


def test_signal_omits_payload_when_none(permission_resolver, monkeypatch):
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
    captured: dict = {}

    def _fake_sig(wid, name, *args):
        captured["args"] = args
        return True

    monkeypatch.setattr("astrolift_workflows.schema.mutations.signal_workflow", _fake_sig)
    m = TemporalWorkflowsMutation()
    result = m.signal_workflow_instance(_info(), "wf-1", "abort", payload=None)
    assert result.ok is True
    assert captured["args"] == ()
