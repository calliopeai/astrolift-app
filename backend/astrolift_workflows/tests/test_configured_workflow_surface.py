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
# updateWorkflow(definitionSlug): repoint fallback (#1822)
# ---------------------------------------------------------------------------


def test_update_workflow_repoints_to_new_definition(member, org, agent_workload, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    _make_def("cw-repoint-a", organization=org, agent=agent_workload)
    _make_def("cw-repoint-b", organization=org, agent=agent_workload)
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        created = m.create_workflow(
            _info(member), name="WF", slug="repoint-wf", definition_slug="cw-repoint-a"
        )
        assert created.ok, created.errors
        res = m.update_workflow(_info(member), slug="repoint-wf", definition_slug="cw-repoint-b")
    assert res.ok, res.errors
    assert res.workflow.definition_slug == "cw-repoint-b"
    wf = Workflow.objects.get(slug="repoint-wf")
    assert wf.definition.slug == "cw-repoint-b"


def test_update_workflow_repoint_refuses_when_bindings_invalid(
    member, org, agent_workload, permission_resolver
):
    """A repoint is refused (not partially applied) when the target
    definition's stages do not validate against the Workflow's existing
    bindings: the same guard a versioned importWorkflowManifest(replace:
    true) relies on (#1822)."""
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    _make_def("cw-repoint-bound", organization=org, agent=agent_workload)
    _make_def("cw-repoint-unbound", organization=org)  # stage's agent is unset
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        created = m.create_workflow(
            _info(member), name="WF", slug="repoint-wf-2", definition_slug="cw-repoint-bound"
        )
        assert created.ok, created.errors
        res = m.update_workflow(_info(member), slug="repoint-wf-2", definition_slug="cw-repoint-unbound")
    assert not res.ok
    assert any("stage 0" in msg for e in res.errors for msg in e.messages)
    wf = Workflow.objects.get(slug="repoint-wf-2")
    assert wf.definition.slug == "cw-repoint-bound"  # never repointed


def test_update_workflow_repoint_requires_create_on_target_definition(
    member, org, agent_workload, permission_resolver
):
    """WORKFLOW_UPDATE on the Workflow's own scope is not enough to bind it
    to a different definition: that also requires the WORKFLOW_CREATE a
    caller would need to configure a Workflow against that definition in
    the first place."""
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    _make_def("cw-repoint-c", organization=org, agent=agent_workload)
    _make_def("cw-repoint-d", organization=org, agent=agent_workload)
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        created = m.create_workflow(
            _info(member), name="WF", slug="repoint-wf-3", definition_slug="cw-repoint-c"
        )
        assert created.ok, created.errors

        # Now withdraw WORKFLOW_CREATE: WORKFLOW_UPDATE alone must not be
        # enough to point this Workflow at a different definition.
        permission_resolver.deny(Permission.WORKFLOW_CREATE)
        from core.permissions import PermissionDenied

        with pytest.raises(PermissionDenied):
            m.update_workflow(_info(member), slug="repoint-wf-3", definition_slug="cw-repoint-d")
    wf = Workflow.objects.get(slug="repoint-wf-3")
    assert wf.definition.slug == "cw-repoint-c"


def test_update_workflow_repoint_unknown_definition_slug(member, org, agent_workload, permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    permission_resolver.grant(Permission.WORKFLOW_UPDATE)
    _make_def("cw-repoint-e", organization=org, agent=agent_workload)
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        created = m.create_workflow(
            _info(member), name="WF", slug="repoint-wf-4", definition_slug="cw-repoint-e"
        )
        assert created.ok, created.errors
        res = m.update_workflow(_info(member), slug="repoint-wf-4", definition_slug="does-not-exist")
    assert not res.ok
    assert any(e.field == "definition_slug" for e in res.errors)


# ---------------------------------------------------------------------------
# runWorkflow: validates bindings, starts the engine, returns a run id (§3)
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


def test_run_workflow_persists_temporal_run_id(
    member, org, agent_workload, permission_resolver, patched_start
):
    """#1180: the Temporal run_id is captured on the tier-3 WorkflowInstance at
    start (mirroring the sibling WorkflowRun the stage executor keys on), so a
    historical run's DAG overlays without a live Temporal describe."""
    from astrolift_operations.models import WorkflowRun

    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    d = _make_def("cw-runid", organization=org)
    wf = Workflow.objects.create(
        organization=org,
        definition=d,
        name="RunId WF",
        slug="runid-wf",
        stage_bindings={"0": {"agent_workload_id": str(agent_workload.guid)}},
    )
    m = WorkflowsMutation()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.run_workflow(_info(member), workflow_id=str(wf.guid))
    assert res.ok, res.errors

    inst = WorkflowInstance.objects.get(configured_workflow=wf)
    # patched_start's fake handle returns run_id="test-run-id".
    assert inst.temporal_run_id == "test-run-id"
    # …and it equals the run_id on the sibling WorkflowRun mirror (the row
    # workflowStageExecutions resolves against by workflow_id + run_id).
    run = WorkflowRun.objects.get(workflow_id=inst.temporal_workflow_id)
    assert run.run_id == inst.temporal_run_id
    assert run.workflow_definition_id == d.pk


def test_workflow_runs_query_exposes_temporal_run_id(
    member, org, agent_workload, permission_resolver, patched_start
):
    """#1180: the tiered WorkflowRun type surfaces the persisted run_id so the
    live-flow DAG can key workflowStageExecutions off the DB mirror instead of
    a live describe that returns null once Temporal GCs the run."""
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    permission_resolver.grant(Permission.WORKFLOW_READ)
    d = _make_def("cw-runid-q", organization=org)
    wf = Workflow.objects.create(
        organization=org,
        definition=d,
        name="RunId Q WF",
        slug="runid-q-wf",
        stage_bindings={"0": {"agent_workload_id": str(agent_workload.guid)}},
    )
    m = WorkflowsMutation()
    q = WorkflowsQuery()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        res = m.run_workflow(_info(member), workflow_id=str(wf.guid))
        runs = q.workflow_runs(_info(member), workflow_id=str(wf.guid))
    assert len(runs) == 1
    assert runs[0].temporal_run_id == "test-run-id"
    assert runs[0].temporal_workflow_id == res.run_id


def test_workflow_runs_back_compat_null_run_id(member, org, permission_resolver):
    """#1180 back-compat: a run started before this field (temporal_run_id never
    captured) still resolves through the tiered query — run_id is None and the
    frontend falls back to the live Temporal describe, exactly as today."""
    permission_resolver.grant(Permission.WORKFLOW_READ)
    d = _make_def("cw-legacy", organization=org)
    wf = Workflow(organization=org, definition=d, name="Legacy WF", slug="legacy-wf")
    wf.save(skip_binding_validation=True)
    # A pre-#1180 run: temporal_workflow_id set, run_id left null.
    WorkflowInstance.start(configured_workflow=wf, user=member, temporal_workflow_id="wf-legacy")
    q = WorkflowsQuery()
    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        runs = q.workflow_runs(_info(member), workflow_id=str(wf.guid))
    assert len(runs) == 1
    assert runs[0].temporal_workflow_id == "wf-legacy"
    assert runs[0].temporal_run_id is None


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


def test_project_workflow_query_returns_bound_topology_and_model(
    member,
    org,
    agent_workload,
    permission_resolver,
):
    from astrolift_agents.models import AgentEnvironmentSpec

    permission_resolver.grant(Permission.WORKFLOW_READ)
    project = agent_workload.registered_app.project
    definition = _make_def("cw-project-topology", organization=org, agent=agent_workload)
    definition.project = project
    definition.source_repo = "steadymd/smd-agents"
    definition.source_path = "workflows/cw-project-topology.toml"
    definition.save()
    stage = definition.stages.get(order=0)
    stage.agent_ref = agent_workload.slug
    stage.environment_spec_slug = "cw-project-agent"
    stage.output_key = "evidence"
    stage.skill_refs = ["jira-read"]
    stage.prompt = "Inspect the issue"
    stage.fan_out_count = 3
    stage.save()
    AgentEnvironmentSpec.objects.create(
        organization=org,
        name="Project agent",
        slug="cw-project-agent",
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
        env_vars={
            "ANTHROPIC_MODEL": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "NOT_EXPOSED": "internal value",
        },
    )
    _make_def("cw-unassigned-template", organization=org, agent=agent_workload)

    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        rows = WorkflowsQuery().workflow_definitions(
            _info(member),
            project_id=str(project.guid),
        )

    assert [row.slug for row in rows] == ["cw-project-topology"]
    row = rows[0]
    assert row.project_guid == str(project.guid)
    assert row.project_slug == project.slug
    assert row.project_team_slug == project.team.slug
    assert row.stage_count == 1
    topology = row.stages[0]
    assert topology.agent_guid == str(agent_workload.guid)
    assert topology.agent_slug == agent_workload.slug
    assert topology.environment_spec_slug == "cw-project-agent"
    assert topology.resolved_model == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert topology.output_key == "evidence"
    assert topology.skill_refs == ["jira-read"]
    assert topology.has_prompt is True
    assert topology.fan_out_count == 3
    assert topology.fan_out_dynamic is False
    assert not hasattr(topology, "env_vars")


def test_project_workflow_query_denies_foreign_and_malformed_project_ids(
    member,
    org,
    other_org,
    agent_workload,
    permission_resolver,
):
    from astrolift_identity.models import Project, Team

    permission_resolver.grant(Permission.WORKFLOW_READ)
    definition = _make_def("cw-project-owned", organization=org, agent=agent_workload)
    definition.project = agent_workload.registered_app.project
    definition.save()
    other_team = Team.objects.create(organization=other_org, name="Other", slug="cw-other-team")
    other_project = Project.objects.create(
        organization=other_org,
        team=other_team,
        name="Other",
        slug="cw-demo-a",
    )

    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        query = WorkflowsQuery()
        assert query.workflow_definitions(_info(member), project_id=str(other_project.guid)) == []
        assert query.workflow_definitions(_info(member), project_id="not-a-guid") == []


def test_workflow_definition_runs_are_workflow_read_and_project_scoped(
    member,
    org,
    other_org,
    agent_workload,
    permission_resolver,
):
    from astrolift_operations.models import WorkflowRun

    permission_resolver.grant(Permission.WORKFLOW_READ)
    definition = _make_def("cw-project-runs", organization=org, agent=agent_workload)
    definition.project = agent_workload.registered_app.project
    definition.save()
    owned = WorkflowRun.objects.create(
        organization=org,
        workflow_definition=definition,
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id="WorkflowDefinitionRunWorkflow-owned",
        run_id="temporal-run-owned",
        status=WorkflowRun.Status.RUNNING,
    )
    other_definition = _make_def("cw-other-runs", organization=other_org)
    WorkflowRun.objects.create(
        organization=other_org,
        workflow_definition=other_definition,
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id="WorkflowDefinitionRunWorkflow-other",
        run_id="temporal-run-other",
        status=WorkflowRun.Status.FAILED,
    )

    with _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=member.id)):
        query = WorkflowsQuery()
        rows = query.workflow_definition_runs(_info(member))
        project_rows = query.workflow_definition_runs(_info(member), project_id=str(definition.project.guid))
        assert query.workflow_definition_runs(_info(member), project_id="not-a-guid") == []
        assert query.workflow_definition_runs(_info(member), status="unknown") == []

    assert [row.guid for row in rows] == [str(owned.guid)]
    assert [row.guid for row in project_rows] == [str(owned.guid)]
    assert rows[0].definition_slug == definition.slug
    assert rows[0].project_slug == definition.project.slug
    assert rows[0].status == "running"


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
