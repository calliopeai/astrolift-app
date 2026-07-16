"""Configured-Workflow GraphQL surface + run mapping (spec 40 §3/§6, #967/#968).

Exercises the tier-2 ``Workflow`` CRUD, the run mapping into
``WorkflowDefinitionRunWorkflow``, the per-Workflow Temporal Schedule sync,
and the org-scoping / read-only-global guards — all against real Postgres,
with the Temporal client patched at its canonical entry points (the run
service + schedule sync import them locally, so patching the module attr is
authoritative).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_workflows.inputs import WorkflowDefinitionRunInput
from astrolift_workflows.schema.mutations import WorkflowsMutation
from astrolift_workflows.schema.queries import WorkflowsQuery
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx
from workflows.models import Workflow, WorkflowDefinition, WorkflowInstance, WorkflowStage

User = get_user_model()
pytestmark = pytest.mark.django_db


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=None))


@pytest.fixture
def org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Org A", slug="cw-org-a")


@pytest.fixture
def other_org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Org B", slug="cw-org-b")


@pytest.fixture
def member():
    return User.objects.create_user(username="cw_member", email="cw_m@t.com", password="pw")


@pytest.fixture
def agent_workload(org):
    """A concrete kind=agent Workload in ``org`` — stage_bindings must
    resolve to one (#1094)."""
    from astrolift_identity.models import Project, Team
    from astrolift_registry.models import RegisteredApp, Workload

    team = Team.objects.create(organization=org, name="Eng", slug="cw-eng-a")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="cw-demo-a")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug="cw-app-a",
        provisioning_status="ready",
    )
    return Workload.objects.create(
        registered_app=app,
        name="Coder",
        slug="cw-coder-a",
        kind=Workload.Kind.AGENT.value,
        run_family=Workload.RunFamily.TASK.value,
    )


def _make_def(slug, *, organization=None, agent=None):
    d = WorkflowDefinition.objects.create(
        name=f"Def {slug}",
        slug=slug,
        organization=organization,
        model_label="",
        pattern_kind=WorkflowDefinition.PatternKind.CHAINED,
        states=[],
        transitions=[],
        is_enabled=True,
    )
    WorkflowStage.objects.create(
        definition=d,
        slug=f"{slug}-stage-0",
        order=0,
        kind=WorkflowStage.StageKind.AGENT_DISPATCH,
        role="implementer",
        agent_definition=agent,
    )
    return d


@pytest.fixture
def patched_start(monkeypatch):
    """Patch the Temporal start entry point the run service imports locally."""
    calls = []

    def _fake(name, *, args=None, workflow_id=None, **kw):
        calls.append({"name": name, "args": args, "workflow_id": workflow_id})
        return SimpleNamespace(enqueued=True, run_id="test-run-id")

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _fake)
    return calls


class _FakeScheduleHandle:
    def __init__(self, calls):
        self._calls = calls

    async def delete(self):
        self._calls.append(("delete",))


class _FakeTemporalClient:
    def __init__(self, calls):
        self._calls = calls

    def get_schedule_handle(self, schedule_id):
        self._calls.append(("get_handle", schedule_id))
        return _FakeScheduleHandle(self._calls)

    async def create_schedule(self, schedule_id, schedule):
        self._calls.append(("create_schedule", schedule_id))


@pytest.fixture
def patched_schedule(monkeypatch):
    """Patch the Temporal client boundary the schedule sync uses locally."""
    calls = []

    async def _fake_client():
        return _FakeTemporalClient(calls)

    monkeypatch.setattr("astrolift_workflows.client._temporal_enabled", lambda: True)
    monkeypatch.setattr("astrolift_workflows.client._get_client_async", _fake_client)
    return calls


# ---------------------------------------------------------------------------
# createWorkflow — org required + unbound-stage rejection (§2.2)
# ---------------------------------------------------------------------------


def test_create_workflow_requires_org_context(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    _make_def("cw-def-noorg", organization=org)
    m = WorkflowsMutation()
    from core.decorators import TenantRequired

    # No tenant context → @tenant_scoped raises (the org is mandatory).
    with pytest.raises(TenantRequired):
        m.create_workflow(_info(member), name="WF", definition_slug="cw-def-noorg")


def test_create_workflow_rejects_unbound_agent_stage(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    _make_def("cw-unbound", organization=org)  # global-less, stage agent=None
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.create_workflow(_info(member), name="WF", definition_slug="cw-unbound")
    assert not res.ok
    assert any("stage 0" in msg for e in res.errors for msg in e.messages)


def test_create_workflow_bound_via_stage_bindings(member, org, agent_workload, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    _make_def("cw-bound", organization=org)
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.create_workflow(
            _info(member),
            name="Bound WF",
            slug="bound-wf",
            definition_slug="cw-bound",
            stage_bindings={"0": {"agent_workload_id": str(agent_workload.guid)}},
        )
    assert res.ok, res.errors
    wf = Workflow.objects.get(slug="bound-wf")
    assert wf.organization_id == org.id
    assert res.workflow.guid == str(wf.guid)


def test_create_workflow_rejects_garbage_binding(member, org, permission_resolver):
    """#1094: an agent_workload_id that resolves to no live kind=agent
    Workload in the caller's org is rejected at save time with a field
    error naming the stage order."""
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    _make_def("cw-garbage", organization=org)
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.create_workflow(
            _info(member),
            name="Garbage WF",
            slug="garbage-wf",
            definition_slug="cw-garbage",
            stage_bindings={"0": {"agent_workload_id": str(uuid.uuid4())}},
        )
    assert not res.ok
    assert any(e.field == "stage_bindings" for e in res.errors)
    assert any("stage 0" in msg for e in res.errors for msg in e.messages)
    assert not Workflow.objects.filter(slug="garbage-wf").exists()


def test_update_workflow_rejects_foreign_org_binding(
    member, org, other_org, agent_workload, permission_resolver
):
    """#1094: rebinding a Workflow to another org's agent workload is
    rejected — the resolution is scoped via registered_app.organization."""
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    from astrolift_identity.models import Project, Team
    from astrolift_registry.models import RegisteredApp, Workload

    team = Team.objects.create(organization=other_org, name="Eng", slug="cw-eng-x")
    project = Project.objects.create(organization=other_org, team=team, name="Demo", slug="cw-demo-x")
    app = RegisteredApp.objects.create(
        organization=other_org,
        team=team,
        project=project,
        name="App",
        slug="cw-app-x",
        provisioning_status="ready",
    )
    theirs = Workload.objects.create(
        registered_app=app,
        name="Coder",
        slug="cw-coder-x",
        kind=Workload.Kind.AGENT.value,
        run_family=Workload.RunFamily.TASK.value,
    )

    _make_def("cw-rebind", organization=org)
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.create_workflow(
            _info(member),
            name="Rebind WF",
            slug="rebind-wf",
            definition_slug="cw-rebind",
            stage_bindings={"0": {"agent_workload_id": str(agent_workload.guid)}},
        )
        assert res.ok, res.errors
        res = m.update_workflow(
            _info(member),
            slug="rebind-wf",
            stage_bindings={"0": {"agent_workload_id": str(theirs.guid)}},
        )
    assert not res.ok
    assert any("stage 0" in msg for e in res.errors for msg in e.messages)
    wf = Workflow.objects.get(slug="rebind-wf")
    assert wf.stage_bindings == {"0": {"agent_workload_id": str(agent_workload.guid)}}


# ---------------------------------------------------------------------------
# runWorkflow — validates bindings, starts the engine, returns a run id (§3)
# ---------------------------------------------------------------------------


def test_run_workflow_starts_engine_and_creates_instance(
    member, org, agent_workload, permission_resolver, patched_start
):
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    d = _make_def("cw-run", organization=org)
    agent_guid = str(agent_workload.guid)
    wf = Workflow.objects.create(
        organization=org,
        definition=d,
        name="Run WF",
        slug="run-wf",
        inputs={"a": 1},
        stage_bindings={"0": {"agent_workload_id": agent_guid}},
    )
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.run_workflow(_info(member), workflow_id=str(wf.guid), inputs={"b": 2})

    assert res.ok, res.errors
    assert res.run_id  # a run id is returned
    assert res.workflow_run_id

    # The engine was started with a WorkflowDefinitionRunInput of the right shape.
    assert len(patched_start) == 1
    call = patched_start[0]
    assert call["name"] == "WorkflowDefinitionRunWorkflow"
    run_input = call["args"][0]
    assert isinstance(run_input, WorkflowDefinitionRunInput)
    assert run_input.workflow_definition_slug == d.slug
    # Merged inputs: workflow.inputs ⊕ call inputs (call wins).
    assert run_input.trigger_payload == {"a": 1, "b": 2}
    # Bindings ride along as the durable run mapping.
    assert run_input.stage_bindings == {"0": {"agent_workload_id": agent_guid}}

    # Tier-3 WorkflowInstance created, linked to the Workflow, org denormalized.
    inst = WorkflowInstance.objects.get(configured_workflow=wf)
    assert inst.organization_id == org.id
    assert inst.temporal_workflow_id == res.run_id
    assert res.instance_id == str(inst.pk)


def test_run_workflow_rejects_unbound_bindings(
    member, org, agent_workload, permission_resolver, patched_start
):
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    d = _make_def("cw-run-unbound", organization=org)
    # Save with a binding so the row persists, then strip it to simulate drift.
    wf = Workflow.objects.create(
        organization=org,
        definition=d,
        name="Unbound run",
        slug="unbound-run",
        stage_bindings={"0": {"agent_workload_id": str(agent_workload.guid)}},
    )
    Workflow.objects.filter(pk=wf.pk).update(stage_bindings={})
    wf.refresh_from_db()
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.run_workflow(_info(member), workflow_id=str(wf.guid))
    assert not res.ok
    assert any("stage 0" in msg for e in res.errors for msg in e.messages)
    assert patched_start == []  # engine NOT started


# ---------------------------------------------------------------------------
# Schedule trigger — create / update / delete one Temporal Schedule (§3)
# ---------------------------------------------------------------------------


def test_schedule_created_on_scheduled_workflow(
    member, org, agent_workload, permission_resolver, patched_schedule
):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    _make_def("cw-sched", organization=org)
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.create_workflow(
            _info(member),
            name="Sched WF",
            slug="sched-wf",
            definition_slug="cw-sched",
            stage_bindings={"0": {"agent_workload_id": str(agent_workload.guid)}},
            trigger_kind="schedule",
            schedule_cron="*/5 * * * *",
        )
    assert res.ok, res.errors
    wf = Workflow.objects.get(slug="sched-wf")
    sched_id = f"workflow-{wf.guid}"
    assert ("create_schedule", sched_id) in patched_schedule


def test_schedule_deleted_when_disabled(member, org, agent_workload, permission_resolver, patched_schedule):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    permission_resolver.grant(Permission.WORKFLOW_DELETE)
    _make_def("cw-sched2", organization=org)
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        m.create_workflow(
            _info(member),
            name="Sched2",
            slug="sched2",
            definition_slug="cw-sched2",
            stage_bindings={"0": {"agent_workload_id": str(agent_workload.guid)}},
            trigger_kind="schedule",
            schedule_cron="*/5 * * * *",
        )
        wf = Workflow.objects.get(slug="sched2")
        sched_id = f"workflow-{wf.guid}"

        patched_schedule.clear()
        # Disabling tears the schedule down (get the handle, then delete it).
        m.update_workflow(_info(member), slug="sched2", is_enabled=False)
        assert ("get_handle", sched_id) in patched_schedule
        assert ("delete",) in patched_schedule

        patched_schedule.clear()
        # Deleting the workflow also deletes the schedule.
        m.delete_workflow(_info(member), slug="sched2")
        assert ("get_handle", sched_id) in patched_schedule
        assert ("delete",) in patched_schedule


# ---------------------------------------------------------------------------
# Cross-org denial + read scoping (§6)
# ---------------------------------------------------------------------------


def test_cross_org_workflow_query_denied(member, org, other_org, agent_workload, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_READ)
    d = _make_def("cw-xorg", organization=org)
    Workflow.objects.create(
        organization=org,
        definition=d,
        name="A WF",
        slug="a-wf",
        stage_bindings={"0": {"agent_workload_id": str(agent_workload.guid)}},
    )
    q = WorkflowsQuery()
    # Caller in other_org cannot see org A's workflow.
    with _tenant_ctx(TenantContext(organization_id=other_org.id, actor_user_id=member.id)):
        assert q.workflow(_info(member), slug="a-wf") is None
        assert q.workflows(_info(member)) == []
    # Owner can.
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        assert q.workflow(_info(member), slug="a-wf") is not None


def test_cross_org_run_denied(member, org, other_org, agent_workload, permission_resolver, patched_start):
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    d = _make_def("cw-xrun", organization=org)
    wf = Workflow.objects.create(
        organization=org,
        definition=d,
        name="X run",
        slug="x-run",
        stage_bindings={"0": {"agent_workload_id": str(agent_workload.guid)}},
    )
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=other_org.id, actor_user_id=member.id)):
        res = m.run_workflow(_info(member), workflow_id=str(wf.guid))
    assert not res.ok
    assert patched_start == []


def test_workflow_runs_query_org_scoped(member, org, agent_workload, permission_resolver, patched_start):
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    permission_resolver.grant(Permission.WORKFLOW_READ)
    d = _make_def("cw-runs", organization=org)
    wf = Workflow.objects.create(
        organization=org,
        definition=d,
        name="Runs WF",
        slug="runs-wf",
        stage_bindings={"0": {"agent_workload_id": str(agent_workload.guid)}},
    )
    m = WorkflowsMutation()
    q = WorkflowsQuery()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        m.run_workflow(_info(member), workflow_id=str(wf.guid))
        runs = q.workflow_runs(_info(member), workflow_id=str(wf.guid))
    assert len(runs) == 1
    assert runs[0].is_completed is False


# ---------------------------------------------------------------------------
# Global definition writes stay rejected (§2.1)
# ---------------------------------------------------------------------------


def test_global_definition_stage_write_denied(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    g = _make_def("cw-global", organization=None)  # platform-global template
    stage = g.stages.first()
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.update_workflow_stage(_info(member), stage_guid=str(stage.guid), role="hacked")
    assert not res.ok
    assert any("platform template" in msg for e in res.errors for msg in e.messages)


def test_update_stage_rejects_foreign_org_workload(member, org, other_org, permission_resolver):
    """Org A must not rebind its stage to org B's agent workload — the
    workload lookup is scoped via registered_app.organization."""
    from astrolift_identity.models import Project, Team
    from astrolift_registry.models import RegisteredApp, Workload

    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    d = _make_def("cw-xbind", organization=org)
    stage = d.stages.first()
    team = Team.objects.create(organization=other_org, name="Eng", slug="cw-eng-b")
    project = Project.objects.create(organization=other_org, team=team, name="Demo", slug="cw-demo-b")
    app = RegisteredApp.objects.create(
        organization=other_org,
        team=team,
        project=project,
        name="App",
        slug="cw-app-b",
        provisioning_status="ready",
    )
    theirs = Workload.objects.create(
        registered_app=app,
        name="Coder",
        slug="cw-coder-b",
        kind=Workload.Kind.AGENT.value,
        run_family=Workload.RunFamily.TASK.value,
    )
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.update_workflow_stage(
            _info(member), stage_guid=str(stage.guid), agent_definition_guid=str(theirs.guid)
        )
    assert not res.ok
    assert any("Workload not found" in msg for e in res.errors for msg in e.messages)
    stage.refresh_from_db()
    assert stage.agent_definition_id is None


def test_update_stage_prompt_and_approvers(member, org, permission_resolver):
    """#1095: a gate's prompt/approvers are editable after create — the
    update surface previously dropped them, so a saved human_gate could
    never change its question or approver set."""
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    d = _make_def("cw-gate-edit", organization=org)
    stage = d.stages.first()
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.update_workflow_stage(
            _info(member),
            stage_guid=str(stage.guid),
            kind="human_gate",
            prompt="Ship it?",
            approvers=["team-leads"],
        )
    assert res.ok, res.errors
    stage.refresh_from_db()
    assert stage.kind == "human_gate"
    assert stage.prompt == "Ship it?"
    assert stage.approvers == ["team-leads"]


def test_reorder_stages_on_org_definition(member, org, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    d = _make_def("cw-reorder", organization=org)
    WorkflowStage.objects.create(
        definition=d,
        slug="cw-reorder-stage-1",
        order=1,
        kind=WorkflowStage.StageKind.CHECKPOINT,
        role="cp",
    )
    s0, s1 = list(d.stages.order_by("order"))
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.reorder_workflow_stages(
            _info(member),
            definition_slug="cw-reorder",
            stage_guids=[str(s1.guid), str(s0.guid)],
        )
    assert res.ok, res.errors
    s0.refresh_from_db()
    s1.refresh_from_db()
    assert s1.order == 0
    assert s0.order == 1
