"""GraphQL coverage for the workflow viewer schema (#437).

These tests run against the in-process schema with Temporal disabled
so the client helpers return empty / None — the assertions focus on
the resolver wiring (permission gate, field shape, mutation
envelope). The Temporal happy path is exercised by the live-runtime
workflow integration tests that spin up the time-skipping env.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.test import RequestFactory

from astrolift_workflows.schema.mutations import TemporalWorkflowsMutation
from astrolift_workflows.schema.queries import TemporalWorkflowsQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    rf = RequestFactory()
    return SimpleNamespace(
        context=SimpleNamespace(
            user=None,
            request=rf.get("/app/gql/config/"),
        )
    )


@contextmanager
def _tenant():
    """Bind a minimal tenant snapshot for the duration of a resolver call.

    ``@tenant_scoped()`` short-circuits without one — these tests don't
    exercise tenancy filtering, they just need the contextvar set."""
    with tenant_context(TenantContext(organization_id=1)):
        yield


# ---- queries -----------------------------------------------------------


def test_list_workflow_instances_denied_without_read(permission_resolver, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    q = TemporalWorkflowsQuery()
    from core.permissions import PermissionDenied

    with _tenant():
        with pytest.raises(PermissionDenied):
            q.astrolift_workflow_instances(_info())


def test_list_workflow_instances_returns_empty_page_when_disabled(permission_resolver, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    permission_resolver.grant(Permission.AUDIT_LOG_READ)

    q = TemporalWorkflowsQuery()
    with _tenant():
        page = q.astrolift_workflow_instances(_info())
    assert page.items == []
    assert page.next_cursor is None


def test_list_workflow_instances_passes_filters(permission_resolver, settings, monkeypatch):
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    # The viewer list is org-scoped (#1183); this filter-passthrough test
    # uses the fleet-wide elevated pair so the monkeypatched (unowned) row
    # isn't filtered out — tenancy scoping is covered in test_viewer_tenancy_1183.
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
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
    with _tenant():
        page = q.astrolift_workflow_instances(
            _info(), workflow_type="DeployAppWorkflow", status="RUNNING", limit=10
        )
    assert captured == {"workflow_type": "DeployAppWorkflow", "status": "RUNNING", "limit": 10}
    assert len(page.items) == 1
    assert page.items[0].workflow_id == "DeployAppWorkflow-x"
    assert page.items[0].duration_seconds is None


def test_instance_detail_returns_none_when_missing(permission_resolver, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    q = TemporalWorkflowsQuery()
    with _tenant():
        assert q.astrolift_workflow_instance_detail(_info(), "missing-id") is None


def test_instance_detail_returns_none_for_empty_workflow_id(permission_resolver, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    q = TemporalWorkflowsQuery()
    with _tenant():
        assert q.astrolift_workflow_instance_detail(_info(), "") is None


def test_instance_detail_shapes_history(permission_resolver, settings, monkeypatch):
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    # History-shaping test: use the fleet-wide elevated pair so the
    # org-scoping gate (#1183) doesn't hide the monkeypatched (unowned) run.
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
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
    with _tenant():
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


# ---- instance-op re-gating: org-owned vs legacy org-less runs -----------


@pytest.fixture
def org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Viewer Org A", slug="vw-org-a")


@pytest.fixture
def other_org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Viewer Org B", slug="vw-org-b")


def _org_tenant(org):
    return tenant_context(TenantContext(organization_id=org.pk))


def _org_run(org, wid):
    from workflows.models import WorkflowInstance

    return WorkflowInstance.objects.create(
        organization=org, temporal_workflow_id=wid, current_state="running"
    )


def test_cancel_org_owned_run_with_workflow_trigger(permission_resolver, monkeypatch, org):
    """Own-org run: WORKFLOW_TRIGGER alone is enough — no admin perms."""
    _org_run(org, "wf-own-1")
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    monkeypatch.setattr("astrolift_workflows.schema.mutations.cancel_workflow", lambda wid: True)
    m = TemporalWorkflowsMutation()
    with _org_tenant(org):
        result = m.cancel_workflow_instance(_info(), "wf-own-1")
    assert result.ok is True
    assert result.errors == []


def test_cancel_org_owned_run_with_elevated_pair(permission_resolver, monkeypatch, org):
    """Own-org run: the legacy AUDIT_LOG_READ + ADMIN_ELEVATE platform
    operator still works — both paths are valid."""
    _org_run(org, "wf-own-2")
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
    monkeypatch.setattr("astrolift_workflows.schema.mutations.cancel_workflow", lambda wid: True)
    m = TemporalWorkflowsMutation()
    with _org_tenant(org):
        result = m.cancel_workflow_instance(_info(), "wf-own-2")
    assert result.ok is True
    assert result.errors == []


def test_cancel_org_owned_run_requires_tenant_context(permission_resolver, org):
    _org_run(org, "wf-own-3")
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    from core.decorators import TenantRequired

    m = TemporalWorkflowsMutation()
    with pytest.raises(TenantRequired):
        m.cancel_workflow_instance(_info(), "wf-own-3")


def test_cancel_foreign_org_run_reads_as_not_found(permission_resolver, monkeypatch, org, other_org):
    """Oracle closure: to an un-elevated caller a foreign org's run is
    INDISTINGUISHABLE from a nonexistent id — same envelope, same code —
    never a forbidden that confirms the id exists. Nothing is delivered."""
    _org_run(other_org, "wf-foreign-1")
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)

    def _boom(wid):
        raise AssertionError("cancel must not be delivered for a foreign-org run")

    monkeypatch.setattr("astrolift_workflows.schema.mutations.cancel_workflow", _boom)
    m = TemporalWorkflowsMutation()
    with _org_tenant(org):
        foreign = m.cancel_workflow_instance(_info(), "wf-foreign-1")
        nonexistent = m.cancel_workflow_instance(_info(), "wf-does-not-exist")
    assert foreign.ok is False
    assert foreign.errors[0].field == "workflow_id"
    assert "not found" in foreign.errors[0].messages[0]
    # The two envelopes must be byte-identical.
    assert nonexistent.ok == foreign.ok
    assert [(e.field, e.messages) for e in nonexistent.errors] == [
        (e.field, e.messages) for e in foreign.errors
    ]


def test_cancel_foreign_org_run_allowed_for_elevated_pair(permission_resolver, monkeypatch, org, other_org):
    """The elevated platform operator reaches foreign-org runs — the
    fleet-wide Running tab admin actions must keep working."""
    _org_run(other_org, "wf-foreign-elevated")
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
    monkeypatch.setattr("astrolift_workflows.schema.mutations.cancel_workflow", lambda wid: True)
    m = TemporalWorkflowsMutation()
    with _org_tenant(org):
        result = m.cancel_workflow_instance(_info(), "wf-foreign-elevated")
    assert result.ok is True
    assert result.errors == []


def test_signal_foreign_org_run_reads_as_not_found(permission_resolver, monkeypatch, org, other_org):
    _org_run(other_org, "wf-foreign-2")
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)

    def _boom(wid, name, *args):
        raise AssertionError("signal must not be delivered for a foreign-org run")

    monkeypatch.setattr("astrolift_workflows.schema.mutations.signal_workflow", _boom)
    m = TemporalWorkflowsMutation()
    with _org_tenant(org):
        result = m.signal_workflow_instance(_info(), "wf-foreign-2", "abort")
    assert result.ok is False
    assert result.errors[0].field == "workflow_id"
    assert "not found" in result.errors[0].messages[0]


def test_terminate_org_owned_via_workflow_run_mirror(permission_resolver, monkeypatch, org):
    """Ownership also resolves through the astrolift_operations WorkflowRun
    mirror when no WorkflowInstance row carries the org."""
    from astrolift_operations.models import WorkflowRun

    WorkflowRun.objects.create(
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id="wf-mirror-1",
        run_id="r1",
        organization=org,
    )
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    monkeypatch.setattr("astrolift_workflows.schema.mutations.terminate_workflow", lambda wid, reason: True)
    m = TemporalWorkflowsMutation()
    with _org_tenant(org):
        result = m.terminate_workflow_instance(_info(), "wf-mirror-1", "wedged")
    assert result.ok is True


def test_cancel_org_less_run_not_found_for_workflow_trigger_holder(permission_resolver, monkeypatch, org):
    """Legacy org-less runs stay on the elevated path — to a WORKFLOW_TRIGGER
    holder they answer the same not-found as a nonexistent id (oracle
    closure), and nothing is delivered."""
    from workflows.models import WorkflowInstance

    WorkflowInstance.objects.create(
        organization=None, temporal_workflow_id="wf-legacy-1", current_state="running"
    )
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)

    def _boom(wid):
        raise AssertionError("cancel must not be delivered for an org-less run on the tenant path")

    monkeypatch.setattr("astrolift_workflows.schema.mutations.cancel_workflow", _boom)
    m = TemporalWorkflowsMutation()
    with _org_tenant(org):
        org_less = m.cancel_workflow_instance(_info(), "wf-legacy-1")
        nonexistent = m.cancel_workflow_instance(_info(), "wf-does-not-exist")
    assert org_less.ok is False
    assert "not found" in org_less.errors[0].messages[0]
    assert [(e.field, e.messages) for e in nonexistent.errors] == [
        (e.field, e.messages) for e in org_less.errors
    ]


def test_cancel_org_less_run_allowed_for_elevated_pair(permission_resolver, monkeypatch):
    """Legacy org-less runs remain reachable via AUDIT_LOG_READ +
    ADMIN_ELEVATE, as before."""
    from workflows.models import WorkflowInstance

    WorkflowInstance.objects.create(
        organization=None, temporal_workflow_id="wf-legacy-2", current_state="running"
    )
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
    monkeypatch.setattr("astrolift_workflows.schema.mutations.cancel_workflow", lambda wid: True)
    m = TemporalWorkflowsMutation()
    result = m.cancel_workflow_instance(_info(), "wf-legacy-2")
    assert result.ok is True
    assert result.errors == []
