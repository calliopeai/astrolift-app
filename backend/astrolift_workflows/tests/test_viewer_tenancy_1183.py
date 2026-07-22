"""Cross-tenant isolation for the Temporal workflow *viewer* reads (#1183).

``AUDIT_LOG_READ`` is a per-org permission, so a non-elevated holder must
only ever see Temporal runs their own org owns. The read gate mirrors the
write gate in ``mutations._gate_instance_op``: the elevated platform-operator
pair (``AUDIT_LOG_READ`` + ``ADMIN_ELEVATE``) sees every run fleet-wide;
everyone else is scoped to runs their org owns (resolved through the tier-3
``WorkflowInstance`` mirror, then the ops ``WorkflowRun`` mirror).

The high-value leak is ``astroliftWorkflowInstanceDetail`` — it returns the
full Temporal history payload. These tests prove a foreign-org caller can't
read it (and that ``describe`` / ``history`` are never even fetched on the
foreign path), plus the list + summary resolvers.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization
from astrolift_workflows.schema.queries import TemporalWorkflowsQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None)))


@pytest.fixture
def org():
    return Organization.objects.create(name="Viewer Org A", slug="vt-org-a")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Viewer Org B", slug="vt-org-b")


def _org_tenant(org):
    return tenant_context(TenantContext(organization_id=org.pk))


def _org_run(org, wid):
    from workflows.models import WorkflowInstance

    return WorkflowInstance.objects.create(
        organization=org, temporal_workflow_id=wid, current_state="running"
    )


_INSTANCE_ROW = {
    "workflow_id": "",  # filled per-call
    "workflow_type": "DeployAppWorkflow",
    "status": "RUNNING",
    "started_at": "2026-05-17T00:00:00+00:00",
    "closed_at": "",
    "run_id": "r1",
    "duration_seconds": None,
    "task_queue": "astrolift-main",
}


def _row_for(wid):
    row = dict(_INSTANCE_ROW)
    row["workflow_id"] = wid
    return row


# ---------------------------------------------------------------------------
# astroliftWorkflowInstanceDetail — full Temporal history payload
# ---------------------------------------------------------------------------


def test_instance_detail_foreign_org_returns_none_and_never_fetches_history(
    permission_resolver, monkeypatch, org, other_org
):
    """A non-elevated AUDIT_LOG_READ holder asking for another org's run
    gets ``None`` (same as a nonexistent id) AND the Temporal describe /
    history calls are never made — no payload is fetched to leak."""
    _org_run(other_org, "wf-foreign-detail")
    permission_resolver.grant(Permission.AUDIT_LOG_READ)  # non-elevated: no ADMIN_ELEVATE

    def _no_describe(wid):
        raise AssertionError("describe_workflow_instance must not run for a foreign-org run")

    def _no_history(wid):
        raise AssertionError("workflow_history must not run for a foreign-org run")

    monkeypatch.setattr("astrolift_workflows.schema.queries.describe_workflow_instance", _no_describe)
    monkeypatch.setattr("astrolift_workflows.schema.queries.workflow_history", _no_history)

    q = TemporalWorkflowsQuery()
    with _org_tenant(org):
        assert q.astrolift_workflow_instance_detail(_info(), "wf-foreign-detail") is None


def test_instance_detail_same_org_returns_history(permission_resolver, monkeypatch, org):
    """The owning org reads its own run's full history — the scoping
    doesn't break the feature for the legitimate caller."""
    _org_run(org, "wf-own-detail")
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    monkeypatch.setattr(
        "astrolift_workflows.schema.queries.describe_workflow_instance",
        lambda wid: _row_for(wid),
    )
    monkeypatch.setattr(
        "astrolift_workflows.schema.queries.workflow_history",
        lambda wid: [
            {
                "event_type": "EVENT_TYPE_ACTIVITY_TASK_COMPLETED",
                "timestamp": "2026-05-17T00:00:30+00:00",
                "payload": {"activity_type": "PushImage"},
                "retry_count": 0,
                "decision": "completed",
            }
        ],
    )
    q = TemporalWorkflowsQuery()
    with _org_tenant(org):
        detail = q.astrolift_workflow_instance_detail(_info(), "wf-own-detail")
    assert detail is not None
    assert detail.instance.workflow_id == "wf-own-detail"
    assert len(detail.history) == 1


def test_instance_detail_elevated_pair_reads_foreign_history(
    permission_resolver, monkeypatch, org, other_org
):
    """The elevated platform-operator pair still reaches a foreign org's
    run — the fleet-wide viewer must keep working."""
    _org_run(other_org, "wf-foreign-elevated")
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
    monkeypatch.setattr(
        "astrolift_workflows.schema.queries.describe_workflow_instance",
        lambda wid: _row_for(wid),
    )
    monkeypatch.setattr("astrolift_workflows.schema.queries.workflow_history", lambda wid: [])
    q = TemporalWorkflowsQuery()
    with _org_tenant(org):
        detail = q.astrolift_workflow_instance_detail(_info(), "wf-foreign-elevated")
    assert detail is not None
    assert detail.instance.workflow_id == "wf-foreign-elevated"


# ---------------------------------------------------------------------------
# astroliftWorkflowInstances — the list is filtered to owned runs
# ---------------------------------------------------------------------------


def test_instances_list_filters_out_foreign_runs(permission_resolver, monkeypatch, org, other_org):
    """The namespace-wide Temporal list is filtered down to the runs the
    caller's org owns; a foreign run in the raw list is dropped."""
    _org_run(org, "wf-own-list")
    _org_run(other_org, "wf-foreign-list")
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    monkeypatch.setattr(
        "astrolift_workflows.schema.queries.list_workflow_instances",
        lambda **kw: [_row_for("wf-own-list"), _row_for("wf-foreign-list")],
    )
    q = TemporalWorkflowsQuery()
    with _org_tenant(org):
        page = q.astrolift_workflow_instances(_info())
    ids = [item.workflow_id for item in page.items]
    assert ids == ["wf-own-list"]


def test_instances_list_elevated_pair_sees_all(permission_resolver, monkeypatch, org, other_org):
    """The elevated pair sees the whole namespace — both rows survive."""
    _org_run(org, "wf-own-list-2")
    _org_run(other_org, "wf-foreign-list-2")
    permission_resolver.grant(Permission.AUDIT_LOG_READ)
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
    monkeypatch.setattr(
        "astrolift_workflows.schema.queries.list_workflow_instances",
        lambda **kw: [_row_for("wf-own-list-2"), _row_for("wf-foreign-list-2")],
    )
    q = TemporalWorkflowsQuery()
    with _org_tenant(org):
        page = q.astrolift_workflow_instances(_info())
    ids = sorted(item.workflow_id for item in page.items)
    assert ids == ["wf-foreign-list-2", "wf-own-list-2"]


# ---------------------------------------------------------------------------
# astroliftWorkflowInstance — the cheap summary poll
# ---------------------------------------------------------------------------


def test_instance_summary_foreign_org_returns_none(permission_resolver, monkeypatch, org, other_org):
    """The polling summary is gated identically — a foreign run answers
    ``None`` and describe is never called."""
    _org_run(other_org, "wf-foreign-summary")
    permission_resolver.grant(Permission.AUDIT_LOG_READ)

    def _no_describe(wid):
        raise AssertionError("describe_workflow_instance must not run for a foreign-org run")

    monkeypatch.setattr("astrolift_workflows.schema.queries.describe_workflow_instance", _no_describe)
    q = TemporalWorkflowsQuery()
    with _org_tenant(org):
        assert q.astrolift_workflow_instance(_info(), "wf-foreign-summary") is None
