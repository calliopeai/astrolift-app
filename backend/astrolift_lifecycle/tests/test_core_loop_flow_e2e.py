"""End-to-end core-loop FLOW coverage (#976).

The sibling ``test_core_loop_e2e`` pins that the assembled schema *exposes*
the core-loop fields (a static SDL smoke). This module is the other half the
issue asks for: that the core loop is actually *callable end-to-end* and that
the mutations/queries the CLI drives behave correctly when chained.

What makes these e2e rather than per-resolver unit tests: each test drives the
real resolvers in sequence, feeding one mutation's output into the next
(``registerApp`` -> the app it creates -> ``startDeployment`` on the
environment ``registerApp`` bootstrapped -> agent dispatch on a workload of
that app -> drive the dispatched task to a terminal state). That chain catches
integration regressions the unit tests miss — e.g. ``registerApp`` ceasing to
bootstrap the ``production`` environment would leave ``startDeployment`` with
nothing to deploy to, which no single-resolver test would notice.

They assert three cross-cutting contracts the issue calls out:

* **MutationResult envelope** — every core-loop mutation returns
  ``{ok, errors, data}``; never raises. ``_assert_ok`` / ``_assert_err``
  pin both shapes.
* **Tenant scoping (deny cross-org)** — the agent surfaces enforce org
  isolation in the query itself (``run_astrolift_agent`` / ``agent_task`` /
  ``agent_tasks`` / ``cancel_task`` filter by ``organization_id``); a foreign
  tenant gets NOT_FOUND / empty, never another org's rows.
* **Terminal states** — a deploy reaches a terminal state through the real
  state machine (abort -> FAILED), and a dispatched agent task walks
  PROVISIONING -> RUNNING -> COMPLETED via the reused dispatch activity.

Posture: real Postgres + real model state (no DB mocks). The Temporal client
facade is replaced by an in-memory recorder — the repo-wide convention for
resolver/dispatch tests (mutation tests don't spin up a worker; workflow
*behavior* is covered by the time-skipping env in astrolift_workflows/tests).
``run_workflow_definition`` (the workflow-dispatch leg) has its own dedicated
coverage in workflows/tests/test_run_workflow_definition_mutation.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction

from astrolift_agents.models import AgentTask
from astrolift_agents.schema.mutations import AgentsMutation, RunAstroliftAgentInput
from astrolift_agents.schema.queries import AgentsQuery
from astrolift_dispatch.spawners import registry as spawner_registry
from astrolift_dispatch.spawners.base import SpawnResult, TaskStatus
from astrolift_identity.models import Organization, Team
from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.mutations import (
    AbortDeploymentInput,
    DeploymentByIdInput,
    LifecycleMutation,
    StartDeploymentInput,
)
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_registry.schema.mutations import RegisterAppInput, RegistryMutation
from astrolift_registry.schema.queries import RegistryQuery
from astrolift_services.models import ManagedService
from astrolift_services.schema.queries import ServicesQuery
from astrolift_workflows.activities import agent_stage
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx

pytestmark = pytest.mark.django_db

_CORE_LOOP_PERMS = [
    Permission.APP_CREATE,
    Permission.APP_DEPLOY,
    Permission.APP_APPROVE_DEPLOY,
    Permission.APP_ROLLBACK,
    Permission.APP_READ,
    Permission.AGENT_DISPATCH,
    Permission.AGENT_READ,
]


# ---------------------------------------------------------------------------
# Envelope assertions — the MutationResult{ok, errors, data} contract.
# ---------------------------------------------------------------------------


def _assert_ok(result, *, expect_data: bool = True):
    """A success envelope: ok True, no errors. ``data`` is present for
    payload-bearing mutations and ``None`` for ``MutationResult[None]``
    ones (e.g. cancelTask); set ``expect_data=False`` for the latter."""
    assert result.ok is True, getattr(result, "errors", None)
    assert list(result.errors) == []
    if expect_data:
        assert result.data is not None
    return result.data


def _assert_err(result, code: str, *, field: str | None = ...):
    """A failure envelope: ok False, no data, a coded+messaged error."""
    assert result.ok is False
    assert result.data is None
    assert len(result.errors) >= 1
    err = result.errors[0]
    assert err.code == code, f"expected {code}, got {err.code}: {err.message}"
    assert isinstance(err.message, str) and err.message
    if field is not ...:
        assert err.field == field
    return result.errors


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _info(user):
    """An ``info``-shaped object that satisfies every core-loop resolver:
    both ``context.user`` and ``context.request.user`` are populated."""
    request = SimpleNamespace(user=user)
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


@pytest.fixture
def grant_core_loop(permission_resolver):
    for perm in _CORE_LOOP_PERMS:
        permission_resolver.grant(perm)
    return permission_resolver


@pytest.fixture
def info(actor):
    return _info(actor)


def _ctx(org, actor=None):
    return _tenant_ctx(TenantContext(organization_id=org.id, actor_user_id=actor.id if actor else None))


class _FakeSpawner:
    """In-memory ContainerSpawner stand-in (mirrors the agent_stage tests):
    spawn returns a fixed external id; status walks the given sequence."""

    def __init__(self, *, status_sequence):
        self._status_sequence = list(status_sequence)
        self.spawned_task = None

    def spawn(self, task):
        self.spawned_task = task
        return SpawnResult(external_id="loop-agent-run")

    def status(self, external_id):
        if len(self._status_sequence) > 1:
            return self._status_sequence.pop(0)
        return self._status_sequence[0]

    def stop(self, external_id):  # pragma: no cover - unused
        pass


@pytest.fixture
def loop_recorder(monkeypatch, settings):
    """Unified Temporal recorder for the whole loop.

    Different core-loop resolvers reach the workflow client by different
    import paths, so all of them are patched to one recorder:

    * ``astrolift_workflows.client.start_workflow`` — register_app's onboard
      bootstrap + agent dispatch import it lazily from the canonical module;
    * ``astrolift_lifecycle.schema.mutations.{start,signal,terminate}_workflow``
      — the deploy resolvers bind these at module import.

    Deploy enqueues are registered via ``transaction.on_commit`` (#1025);
    under the test's outer transaction those callbacks never fire, so
    ``on_commit`` is patched to run immediately — modelling the production
    commit so the recorder observes exactly what a real commit would enqueue.
    """
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    from astrolift_workflows.client import WorkflowHandle

    starts: list[tuple[str, str]] = []
    signals: list[tuple[str, str]] = []

    def _start(name, args=None, *, workflow_id, task_queue=None):
        starts.append((name, workflow_id))
        return WorkflowHandle(workflow_id=workflow_id, run_id=f"run-{len(starts)}", enqueued=True)

    def _signal(workflow_id, signal_name, *a):
        signals.append((workflow_id, signal_name))
        return True

    def _terminate(workflow_id, reason):
        signals.append((workflow_id, f"terminate:{reason}"))
        return True

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _start)
    # Control-plane mutations were split into a mixin package; bind the fakes
    # into every submodule that imports the Temporal client fns by name.
    import importlib
    import pkgutil

    import astrolift_lifecycle.schema.mutations as _mutpkg

    _wf = {"start_workflow": _start, "signal_workflow": _signal, "terminate_workflow": _terminate}
    for _sub in pkgutil.iter_modules(_mutpkg.__path__):
        _m = importlib.import_module(f"astrolift_lifecycle.schema.mutations.{_sub.name}")
        for _name, _fn in _wf.items():
            if hasattr(_m, _name):
                monkeypatch.setattr(_m, _name, _fn)
    monkeypatch.setattr(transaction, "on_commit", lambda fn, using=None: fn())

    return SimpleNamespace(
        starts=starts,
        signals=signals,
        names=lambda: [n for n, _ in starts],
    )


def _register_app(info, project, *, slug="loop-app", name="Loop App"):
    return RegistryMutation().register_app(
        info,
        input=RegisterAppInput(
            project_id=str(project.guid),
            name=name,
            slug=slug,
            source_repo=f"acme/{slug}",
        ),
    )


# ---------------------------------------------------------------------------
# The full happy-path loop: register -> deploy -> dispatch -> terminal.
# ---------------------------------------------------------------------------


def test_core_loop_register_deploy_dispatch_to_terminal(
    org, project, cluster, actor, info, grant_core_loop, loop_recorder, monkeypatch
):
    # 1) registerApp — creates the app AND bootstraps a "production" env.
    with _ctx(org, actor):
        reg = _register_app(info, project)
    _assert_ok(reg)
    app = RegisteredApp.objects.get(slug="loop-app")
    assert app.organization_id == org.id
    env = app.environments.get(name="production", deleted_at__isnull=True)
    # registerApp kicked off OnboardAppWorkflow via the same recorder.
    assert ("OnboardAppWorkflow", f"OnboardAppWorkflow-{app.guid}") in loop_recorder.starts

    # 2) startDeployment on the env registerApp just created. This is the
    #    integration seam: the deploy targets an env produced by a *different*
    #    resolver. The workflow id is the canonical single-flight id the CLI
    #    /api/cli/v1 deploy endpoint shares.
    with _ctx(org, actor):
        dep = LifecycleMutation().start_deployment(
            info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="v1.0.0",
                trigger_kind="manual",
            ),
        )
    deploy_data = _assert_ok(dep)
    assert deploy_data.status == Deployment.Status.PENDING.value
    deployment = Deployment.objects.get(guid=str(deploy_data.id))
    assert deployment.image_tag == "v1.0.0"
    assert deployment.workflow_run_id is not None
    wf_id = f"DeployAppWorkflow-{app.guid}-{env.guid}"
    assert ("DeployAppWorkflow", wf_id) in loop_recorder.starts

    # 3) Agent dispatch on an agent workload of the same app.
    workload = Workload.objects.create(
        registered_app=app, name="Triage", slug="triage", kind=Workload.Kind.AGENT
    )
    fake = _FakeSpawner(status_sequence=[TaskStatus(running=True), TaskStatus(succeeded=True, exit_code=0)])
    monkeypatch.setattr(spawner_registry, "get_spawner", lambda backend, **kw: fake)

    with _ctx(org, actor):
        run = AgentsMutation().run_astrolift_agent(
            info, input=RunAstroliftAgentInput(agent_slug=workload.slug)
        )
    task_type = _assert_ok(run)
    task = AgentTask.objects.get(guid=str(task_type.id))
    assert task.agent_definition_id == workload.id
    assert task.organization_id == org.id
    assert task.status == AgentTask.Status.QUEUED
    assert ("DispatchAgentTaskWorkflow", f"DispatchAgentTaskWorkflow-{task.guid}") in loop_recorder.starts

    # 4) Terminal state: the dispatch activity (what DispatchAgentTaskWorkflow
    #    invokes) walks the SAME task to COMPLETED via spawn + poll.
    assert agent_stage._spawn_agent_task_sync(task.pk)["ok"] is True
    assert fake.spawned_task.agent_definition_id == workload.id
    assert agent_stage._poll_agent_task_sync(task.pk)["terminal"] is False
    assert AgentTask.objects.get(pk=task.pk).status == AgentTask.Status.RUNNING
    assert agent_stage._poll_agent_task_sync(task.pk)["terminal"] is True
    task.refresh_from_db()
    assert task.status == AgentTask.Status.COMPLETED
    assert task.result == {"exit_code": 0}
    assert task.ended_at is not None


def test_deploy_reaches_terminal_via_abort(
    org, project, cluster, actor, info, grant_core_loop, loop_recorder
):
    """A deploy reaches a terminal state (FAILED) through the real state
    machine, and the in-flight workflow is signalled to abort."""
    with _ctx(org, actor):
        _assert_ok(_register_app(info, project, slug="abort-app", name="Abort App"))
        app = RegisteredApp.objects.get(slug="abort-app")
        env = app.environments.get(name="production")
        dep = _assert_ok(
            LifecycleMutation().start_deployment(
                info,
                input=StartDeploymentInput(
                    app_slug=app.slug, environment_name=env.name, image_tag="v1", trigger_kind="manual"
                ),
            )
        )
        abort = LifecycleMutation().abort_deployment(
            info, input=AbortDeploymentInput(id=dep.id, reason="bad image, halting")
        )

    aborted = _assert_ok(abort)
    assert aborted.status == Deployment.Status.FAILED.value
    assert aborted.aborted_reason == "bad image, halting"
    # The in-flight DeployAppWorkflow was signalled to abort.
    assert any(sig == "abort" for _wf, sig in loop_recorder.signals)


def test_redeploy_app_clones_running_and_enqueues(
    org, project, cluster, actor, info, grant_core_loop, loop_recorder
):
    """redeployApp (a CLI-driven core-loop mutation) clones a running deploy's
    image into a fresh PENDING deploy and enqueues a new DeployAppWorkflow."""
    with _ctx(org, actor):
        _assert_ok(_register_app(info, project, slug="redeploy-app", name="Redeploy App"))
        app = RegisteredApp.objects.get(slug="redeploy-app")
        env = app.environments.get(name="production")
        source = Deployment.objects.create(
            registered_app=app,
            app_environment=env,
            triggered_by_user=actor,
            trigger_kind="manual",
            status=Deployment.Status.RUNNING.value,
            image_tag="v2.3.4",
            image_digest="sha256:cafe",
        )
        before = len([n for n in loop_recorder.names() if n == "DeployAppWorkflow"])
        result = LifecycleMutation().redeploy_app(info, input=DeploymentByIdInput(id=source.guid))

    data = _assert_ok(result)
    assert data.image_tag == "v2.3.4"
    assert data.image_digest == "sha256:cafe"
    assert data.status == Deployment.Status.PENDING.value
    after = len([n for n in loop_recorder.names() if n == "DeployAppWorkflow"])
    assert after == before + 1


# ---------------------------------------------------------------------------
# MutationResult envelope — failure shape.
# ---------------------------------------------------------------------------


def test_register_app_failure_returns_envelope_not_raise(org, info, grant_core_loop):
    """A bad input returns a coded failure envelope (never raises) — the
    deny-by-default contract the whole CLI relies on to render errors."""
    import uuid

    with _ctx(org):
        result = RegistryMutation().register_app(
            info,
            input=RegisterAppInput(
                project_id=str(uuid.uuid4()),  # no such project
                name="Ghost",
                slug="ghost-app",
                source_repo="acme/ghost",
            ),
        )
    _assert_err(result, ErrorCode.NOT_FOUND.value, field="projectId")
    assert not RegisteredApp.objects.filter(slug="ghost-app").exists()


# ---------------------------------------------------------------------------
# Tenant scoping — the agent surfaces deny cross-org (real query scoping).
# ---------------------------------------------------------------------------


@pytest.fixture
def other_org():
    org = Organization.objects.create(name="Globex", slug="globex-loop")
    Team.objects.create(organization=org, name="Eng", slug="globex-eng")
    return org


def _agent_workload_in(org, *, app_slug, workload_slug):
    team = Team.objects.create(organization=org, name=f"T-{app_slug}", slug=f"t-{app_slug}")
    app = RegisteredApp.objects.create(
        organization=org, team=team, name=app_slug, slug=app_slug, provisioning_status="ready"
    )
    return Workload.objects.create(
        registered_app=app, name=workload_slug, slug=workload_slug, kind=Workload.Kind.AGENT
    )


def test_agent_dispatch_denies_cross_org_slug(org, other_org, actor, info, grant_core_loop, loop_recorder):
    """An agent slug that exists only in another org is not resolvable for
    the caller's tenant — NOT_FOUND, no task, no enqueue (no existence leak)."""
    foreign = _agent_workload_in(other_org, app_slug="foreign-app", workload_slug="foreign-agent")

    with _ctx(org, actor):
        result = AgentsMutation().run_astrolift_agent(
            info, input=RunAstroliftAgentInput(agent_slug=foreign.slug)
        )

    _assert_err(result, ErrorCode.NOT_FOUND.value)
    assert AgentTask.objects.filter(agent_definition=foreign).count() == 0
    assert loop_recorder.starts == []


def test_agent_task_and_cancel_scoped_to_org(
    org, other_org, actor, info, grant_core_loop, loop_recorder, monkeypatch
):
    """A task dispatched in org A is invisible to — and uncancellable by — a
    caller in org B. agent_task -> None, agent_tasks -> excluded, cancel_task
    -> NOT_FOUND. cancel within the owning org -> CANCELLED (terminal)."""
    workload = _agent_workload_in(org, app_slug="scoped-app", workload_slug="scoped-agent")
    with _ctx(org, actor):
        run = AgentsMutation().run_astrolift_agent(
            info, input=RunAstroliftAgentInput(agent_slug=workload.slug)
        )
    task = AgentTask.objects.get(guid=str(_assert_ok(run).id))

    other_user = get_user_model().objects.create(username="globex@test", email="globex@test")
    other_info = _info(other_user)

    # Cross-org reads are empty / None; cross-org cancel is NOT_FOUND.
    with _ctx(other_org, other_user):
        assert AgentsQuery().agent_task(other_info, id=str(task.guid)) is None
        listed = AgentsQuery().agent_tasks(other_info, org_id=str(other_org.guid))
        assert all(str(t.id) != str(task.guid) for t in listed)
        denied = AgentsMutation().cancel_task(other_info, id=str(task.guid))
    _assert_err(denied, ErrorCode.NOT_FOUND.value)
    assert AgentTask.objects.get(pk=task.pk).status == AgentTask.Status.QUEUED

    # The owning org sees it and can cancel it -> terminal CANCELLED.
    with _ctx(org, actor):
        assert AgentsQuery().agent_task(info, id=str(task.guid)) is not None
        owned = AgentsQuery().agent_tasks(info, org_id=str(org.guid))
        assert any(str(t.id) == str(task.guid) for t in owned)
        cancelled = AgentsMutation().cancel_task(info, id=str(task.guid))
    _assert_ok(cancelled, expect_data=False)  # cancelTask is MutationResult[None]
    assert AgentTask.objects.get(pk=task.pk).status == AgentTask.Status.CANCELLED


def test_agent_cancel_rejects_app_deploy_without_agent_dispatch(permission_resolver, org, actor, info):
    permission_resolver.grant(Permission.APP_DEPLOY)

    with _ctx(org, actor):
        result = AgentsMutation().cancel_task(info, id="00000000-0000-0000-0000-000000000000")

    _assert_err(result, ErrorCode.PERMISSION_DENIED.value)


def test_agent_cancel_accepts_agent_dispatch_permission(permission_resolver, org, actor, info):
    permission_resolver.grant(Permission.AGENT_DISPATCH)

    with _ctx(org, actor):
        result = AgentsMutation().cancel_task(info, id="00000000-0000-0000-0000-000000000000")

    # NOT_FOUND proves the resolver passed its permission gate and performed
    # the tenant-scoped lookup.
    _assert_err(result, ErrorCode.NOT_FOUND.value)


def test_agent_task_logs_callable_and_scoped(org, other_org, actor, info, grant_core_loop, loop_recorder):
    """agentTaskLogs is callable and tenant-scoped: it returns a list (empty
    here — the task's dispatcher has no live cluster/pod) for the owning org
    and an empty list for a foreign org, never an error or a leak."""
    workload = _agent_workload_in(org, app_slug="logs-app", workload_slug="logs-agent")
    with _ctx(org, actor):
        run = AgentsMutation().run_astrolift_agent(
            info, input=RunAstroliftAgentInput(agent_slug=workload.slug)
        )
        task = AgentTask.objects.get(guid=str(_assert_ok(run).id))
        own = AgentsQuery().agent_task_logs(info, id=str(task.guid))
    assert isinstance(own, list)

    other_user = get_user_model().objects.create(username="globex2@test", email="globex2@test")
    with _ctx(other_org, other_user):
        foreign = AgentsQuery().agent_task_logs(_info(other_user), id=str(task.guid))
    assert foreign == []


# ---------------------------------------------------------------------------
# Registry + managed-service read surfaces — callable, return the loop's data.
# ---------------------------------------------------------------------------


def test_apps_queries_return_registered_app(
    org, project, cluster, actor, info, grant_core_loop, loop_recorder
):
    """astroliftApps and astroliftApp surface the registered app the loop
    created — the CLI's app-listing + app-detail reads."""
    with _ctx(org, actor):
        _assert_ok(_register_app(info, project, slug="listed-app", name="Listed App"))
        listed = RegistryQuery().astrolift_apps(info)
        single = RegistryQuery().astrolift_app(info, slug="listed-app")

    assert "listed-app" in {a.slug for a in listed}
    assert single is not None
    assert single.slug == "listed-app"


def test_managed_services_query_returns_app_services(
    org, project, cluster, actor, info, grant_core_loop, loop_recorder
):
    """astroliftManagedServices returns the app's managed services, filtered
    by environment — the CLI/UI managed-services read surface."""
    with _ctx(org, actor):
        _assert_ok(_register_app(info, project, slug="svc-app", name="Svc App"))
        app = RegisteredApp.objects.get(slug="svc-app")
        env = app.environments.get(name="production")
        ManagedService.objects.create(
            registered_app=app,
            app_environment=env,
            kind=ManagedService.Kind.POSTGRES,
            name="primary-db",
            status=ManagedService.Status.ACTIVE,
        )
        all_services = ServicesQuery().astrolift_managed_services(info, app_slug="svc-app")
        prod_services = ServicesQuery().astrolift_managed_services(
            info, app_slug="svc-app", environment_name="production"
        )
        none_services = ServicesQuery().astrolift_managed_services(
            info, app_slug="svc-app", environment_name="staging"
        )

    assert [s.name for s in all_services] == ["primary-db"]
    assert [s.name for s in prod_services] == ["primary-db"]
    assert none_services == []
