"""Tests for the ``runAstroliftAgent`` dispatch mutation (spec 33, PR-1).

``run_astrolift_agent`` is the user-facing seam that bridges a registered
agent ``Workload(kind=agent)`` to the existing Temporal dispatch pipeline.
These tests pin, against a real database:

  * dispatch creates an ``AgentTask`` with ``agent_definition`` set to the
    resolved Workload (the gap ``launch_task`` leaves open) and advances it
    to QUEUED, and enqueues ``DispatchAgentTaskWorkflow`` keyed to the task
    guid;
  * the enqueued workflow's activity (``dispatch_agent_task``) drives that
    same task to a terminal state via the reused spawn/poll pipeline;
  * dispatch is denied without ``agent.dispatch``;
  * a cross-org agent slug is not resolvable (NOT_FOUND, no leak);
  * a non-agent workload is rejected (VALIDATION);
  * an unknown environment-spec id is NOT_FOUND.

Resolvers are exercised by direct invocation (the agents-test convention)
with a controllable permission resolver + tenant context bound. The
Temporal client facade is monkeypatched to an in-memory recorder so the
enqueue is asserted without a running Temporal server (mirrors the
lifecycle ``temporal_recorder`` pattern).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_agents.models import AgentEnvironmentSpec, AgentTask
from astrolift_agents.schema.mutations import AgentsMutation, RunAstroliftAgentInput
from astrolift_agents.schema.types import AgentTaskType
from astrolift_dispatch.spawners import registry as spawner_registry
from astrolift_dispatch.spawners.base import SpawnResult, TaskStatus
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Team
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_workflows.activities import agent_stage
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def info():
    """Minimal ``info``-shaped object; an anonymous request user so the
    dispatch actor falls back to the tenant context's actor."""
    request = SimpleNamespace(user=SimpleNamespace(is_authenticated=False))
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


@pytest.fixture
def org():
    return Organization.objects.create(name="Dispatch Org", slug="dispatch-org")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Dispatch Other", slug="dispatch-other")


@pytest.fixture
def with_tenant_org():
    def _enter(org, *, actor_user_id=None):
        return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=actor_user_id))

    return _enter


def _agent_workload(org, *, app_slug="triage-app", workload_slug="triage", kind=Workload.Kind.AGENT):
    team = Team.objects.create(organization=org, name=f"T-{app_slug}", slug=f"t-{app_slug}")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        name=app_slug.replace("-", " ").title(),
        slug=app_slug,
        provisioning_status="ready",
    )
    return Workload.objects.create(
        registered_app=app,
        name=workload_slug.replace("-", " ").title(),
        slug=workload_slug,
        kind=kind,
    )


@pytest.fixture
def temporal_recorder(monkeypatch):
    """Record ``start_workflow`` calls without a running Temporal server.

    The resolver imports ``start_workflow`` lazily from
    ``astrolift_workflows.client``, so patch it at the source module.
    """
    starts: list[tuple[str, list, str]] = []

    def _start(name, args, *, workflow_id, task_queue=None):
        starts.append((name, list(args), workflow_id))
        from astrolift_workflows.client import WorkflowHandle

        return WorkflowHandle(workflow_id=workflow_id, run_id=f"run-{len(starts)}", enqueued=True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _start)
    return starts


# ---- fake spawner (drives the dispatch activity to terminal) ----------


class _FakeSpawner:
    """In-memory ContainerSpawner stand-in (mirrors the agent_stage test)."""

    def __init__(self, *, spawn_result: SpawnResult, status_sequence: list[TaskStatus]):
        self._spawn_result = spawn_result
        self._status_sequence = list(status_sequence)
        self.spawned_task = None

    def spawn(self, task) -> SpawnResult:
        self.spawned_task = task
        return self._spawn_result

    def status(self, external_id: str) -> TaskStatus:
        if len(self._status_sequence) > 1:
            return self._status_sequence.pop(0)
        return self._status_sequence[0]

    def stop(self, external_id: str) -> None:  # pragma: no cover - unused here
        pass


# ---------------------------------------------------------------------------
# Happy path: creates AgentTask(agent_definition=...) + enqueues workflow
# ---------------------------------------------------------------------------


def test_dispatch_creates_task_with_agent_definition_and_enqueues(
    permission_resolver, info, org, with_tenant_org, temporal_recorder
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    workload = _agent_workload(org)

    with with_tenant_org(org):
        result = AgentsMutation().run_astrolift_agent(
            info, input=RunAstroliftAgentInput(agent_slug=workload.slug)
        )

    assert result.ok is True
    assert isinstance(result.data, AgentTaskType)

    task = AgentTask.objects.get(guid=str(result.data.id))
    # The core gap launch_task leaves open: agent_definition is set.
    assert task.agent_definition_id == workload.id
    assert task.organization_id == org.id
    # Created DRAFT then advanced through the sanctioned transition.
    assert task.status == AgentTask.Status.QUEUED
    assert task.queued_at is not None

    # Exactly one DispatchAgentTaskWorkflow enqueued, keyed to the task guid,
    # carrying the task pk in its input.
    assert len(temporal_recorder) == 1
    name, args, workflow_id = temporal_recorder[0]
    assert name == "DispatchAgentTaskWorkflow"
    assert workflow_id == f"DispatchAgentTaskWorkflow-{task.guid}"
    assert args[0].agent_task_id == task.pk


def test_dispatch_binds_environment_spec_and_vnc(
    permission_resolver, info, org, with_tenant_org, temporal_recorder
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    workload = _agent_workload(org)
    spec = AgentEnvironmentSpec.objects.create(
        organization=org,
        name="Claude Dev",
        slug="claude-dev",
        image_tag="ecr.example/agent:latest",
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
        vnc_enabled=True,
    )

    with with_tenant_org(org):
        result = AgentsMutation().run_astrolift_agent(
            info,
            input=RunAstroliftAgentInput(
                agent_slug=workload.slug,
                environment_spec_id=GUID(str(spec.guid)),
                timeout_seconds=1800,
            ),
        )

    assert result.ok is True
    task = AgentTask.objects.get(guid=str(result.data.id))
    assert task.environment_spec_id == spec.id
    assert task.timeout_seconds == 1800
    # VNC eligibility frozen from the spec at dispatch.
    assert task.vnc_enabled is True


def test_dispatch_persists_adhoc_trigger_payload(
    permission_resolver, info, org, with_tenant_org, temporal_recorder
):
    """The ad-hoc ``trigger_payload`` is frozen on the task so the spawner can
    surface it to the pod as ASTROLIFT_TRIGGER_PAYLOAD (#930)."""
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    workload = _agent_workload(org)
    payload = {"prompt": "fix the bug", "pr": 42}

    with with_tenant_org(org):
        result = AgentsMutation().run_astrolift_agent(
            info,
            input=RunAstroliftAgentInput(agent_slug=workload.slug, trigger_payload=payload),
        )

    assert result.ok is True
    task = AgentTask.objects.get(guid=str(result.data.id))
    assert task.dispatch_input == payload


def test_dispatch_without_payload_leaves_dispatch_input_null(
    permission_resolver, info, org, with_tenant_org, temporal_recorder
):
    """No ad-hoc input -> no stored payload -> no spurious pod env var (#930)."""
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    workload = _agent_workload(org)

    with with_tenant_org(org):
        result = AgentsMutation().run_astrolift_agent(
            info, input=RunAstroliftAgentInput(agent_slug=workload.slug)
        )

    assert result.ok is True
    task = AgentTask.objects.get(guid=str(result.data.id))
    assert task.dispatch_input is None


# ---------------------------------------------------------------------------
# The enqueued workflow's activity drives the SAME task to terminal
# ---------------------------------------------------------------------------


def test_enqueued_task_reaches_terminal_via_dispatch_activity(
    permission_resolver, info, org, with_tenant_org, temporal_recorder, monkeypatch
):
    """End-to-end Once: the mutation creates + enqueues; the dispatch
    activity (what DispatchAgentTaskWorkflow invokes) walks the task
    PROVISIONING -> RUNNING -> COMPLETED via the reused spawn/poll path."""
    from astrolift_clusters.models import ProviderPlugin, TenantCluster

    # Seeded directly; the plugin row is scaffolding for this test.
    plugin = ProviderPlugin(
        name="K8s Dispatch",
        slug="k8s-dispatch",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    plugin = ProviderPlugin.objects.get(slug="k8s-dispatch")
    # A managed cluster is the dispatch target the spawn helper resolves.
    TenantCluster.objects.create(
        organization=org,
        name="dev-cluster",
        slug="dev-cluster",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://dev.cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    workload = _agent_workload(org)

    fake = _FakeSpawner(
        spawn_result=SpawnResult(external_id="agent-task-run"),
        status_sequence=[TaskStatus(running=True), TaskStatus(succeeded=True, exit_code=0)],
    )
    monkeypatch.setattr(spawner_registry, "get_spawner", lambda backend, **kw: fake)

    with with_tenant_org(org):
        result = AgentsMutation().run_astrolift_agent(
            info, input=RunAstroliftAgentInput(agent_slug=workload.slug)
        )
    task = AgentTask.objects.get(guid=str(result.data.id))

    # Drive the dispatch activity's spawn + poll on the existing task pk —
    # this is exactly what DispatchAgentTaskWorkflow does via the activity.
    spawn = agent_stage._spawn_agent_task_sync(task.pk)
    assert spawn["ok"] is True
    # The spawner was handed the task carrying our agent_definition.
    assert fake.spawned_task.agent_definition_id == workload.id

    first = agent_stage._poll_agent_task_sync(task.pk)
    assert first["terminal"] is False
    assert AgentTask.objects.get(pk=task.pk).status == AgentTask.Status.RUNNING

    second = agent_stage._poll_agent_task_sync(task.pk)
    assert second["terminal"] is True
    task.refresh_from_db()
    assert task.status == AgentTask.Status.COMPLETED
    assert task.result == {"exit_code": 0}
    assert task.ended_at is not None


# ---------------------------------------------------------------------------
# Denials + scoping
# ---------------------------------------------------------------------------


def test_dispatch_denied_without_permission(info, org, with_tenant_org, temporal_recorder):
    """No agent.dispatch grant -> PERMISSION_DENIED, no task, no enqueue.

    No ``permission_resolver`` fixture here, so the process default
    deny-all resolver is in force.
    """
    workload = _agent_workload(org)

    with with_tenant_org(org):
        result = AgentsMutation().run_astrolift_agent(
            info, input=RunAstroliftAgentInput(agent_slug=workload.slug)
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    assert AgentTask.objects.count() == 0
    assert temporal_recorder == []


def test_dispatch_cross_org_slug_not_found(
    permission_resolver, info, org, other_org, with_tenant_org, temporal_recorder
):
    """An agent slug that exists only in another org is not resolvable for
    the caller's tenant — NOT_FOUND (no existence leak), no task."""
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    foreign = _agent_workload(other_org, app_slug="foreign-app", workload_slug="foreign-agent")

    with with_tenant_org(org):
        result = AgentsMutation().run_astrolift_agent(
            info, input=RunAstroliftAgentInput(agent_slug=foreign.slug)
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value
    assert AgentTask.objects.count() == 0
    assert temporal_recorder == []


def test_dispatch_non_agent_workload_rejected(
    permission_resolver, info, org, with_tenant_org, temporal_recorder
):
    """A workload that exists in the caller's org but is not kind=agent is
    rejected with VALIDATION (not dispatched)."""
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    deployment = _agent_workload(org, app_slug="web-app", workload_slug="web", kind=Workload.Kind.DEPLOYMENT)

    with with_tenant_org(org):
        result = AgentsMutation().run_astrolift_agent(
            info, input=RunAstroliftAgentInput(agent_slug=deployment.slug)
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.VALIDATION.value
    assert AgentTask.objects.count() == 0
    assert temporal_recorder == []


def test_dispatch_unknown_environment_spec_not_found(
    permission_resolver, info, org, with_tenant_org, temporal_recorder
):
    permission_resolver.grant(Permission.AGENT_DISPATCH)
    workload = _agent_workload(org)

    with with_tenant_org(org):
        result = AgentsMutation().run_astrolift_agent(
            info,
            input=RunAstroliftAgentInput(
                agent_slug=workload.slug,
                environment_spec_id=GUID("00000000-0000-0000-0000-000000000000"),
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value
    assert AgentTask.objects.count() == 0
    assert temporal_recorder == []
