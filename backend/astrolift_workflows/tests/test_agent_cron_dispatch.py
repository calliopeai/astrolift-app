"""
Tests for the agent-cron dispatch tick (spec 33, PR-4).

Two layers, mirroring ``test_cron_deploy``:

  * ``select_agent_matches`` policy — pure-Python; deterministic via an
    injected ``now`` (the same seam ``cron_matches`` already exposes).
  * ``dispatch_agent_crons`` activity — talks to the real DB, selects
    agent Workloads in Schedule mode, and (with the Temporal client
    patched out) creates AgentTask rows + records what *would* be
    enqueued.

The four acceptance cases (spec §PR-4):
  1. a schedule agent (mode=schedule, cron="0 */6 * * *") dispatches a
     **Task** at each cron match — asserted by an AgentTask created with
     ``agent_definition`` set + ``DispatchAgentTaskWorkflow`` enqueued,
     and crucially NO Deployment row minted (it's a dispatch, not a
     deploy);
  2. ``run_paused`` is honored — a paused agent does not dispatch;
  3. a service agent (family=service, replicas=N) deploys a
     Deployment+Service+HPA with HPA-owned replicas via the existing renderer;
  4. manual ``scale_workload`` still works on a service agent.

Cases 3 & 4 verify the Service-mode reuse: the render path keys on the
manifest workload ``kind == "agent"`` (which already emits the trio and
defers replica count to the HPA) and ``scale_workload`` patches the Deployment by
name regardless of run-spec family.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from astrolift_workflows.cron_deploy import (
    AgentCronDispatchCandidate,
    select_agent_matches,
)


def _now(minute=0, hour=12, day=15, month=6, year=2026):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# ---- select_agent_matches (pure policy) -----------------------------


def _agent_candidate(**kw):
    base = {
        "workload_id": 1,
        "workload_slug": "triage",
        "workload_guid": "guid",
        "organization_id": 1,
        "run_cron_expression": "* * * * *",
        "run_paused": False,
    }
    base.update(kw)
    return AgentCronDispatchCandidate(**base)


def test_agent_select_fires_on_match():
    out = select_agent_matches(candidates=[_agent_candidate()], now=_now())
    assert len(out) == 1


def test_agent_select_skips_paused():
    """Acceptance (2) at the policy layer: run_paused stops dispatch."""
    out = select_agent_matches(candidates=[_agent_candidate(run_paused=True)], now=_now())
    assert out == []


def test_agent_select_skips_non_matching_expression():
    out = select_agent_matches(
        candidates=[_agent_candidate(run_cron_expression="0 0 1 1 *")],  # New Year midnight
        now=_now(minute=30, hour=9, day=15, month=6),
    )
    assert out == []


def test_agent_select_every_6_hours_expression():
    """The exact acceptance-case expression ``0 */6 * * *`` fires at
    00:00 / 06:00 / 12:00 / 18:00 and nowhere else."""
    expr = "0 */6 * * *"
    for h in (0, 6, 12, 18):
        out = select_agent_matches(
            candidates=[_agent_candidate(run_cron_expression=expr)],
            now=_now(minute=0, hour=h),
        )
        assert len(out) == 1, f"expected fire at {h}:00"
    # Wrong minute / wrong hour do not fire.
    assert (
        select_agent_matches(
            candidates=[_agent_candidate(run_cron_expression=expr)], now=_now(minute=30, hour=6)
        )
        == []
    )
    assert (
        select_agent_matches(
            candidates=[_agent_candidate(run_cron_expression=expr)], now=_now(minute=0, hour=7)
        )
        == []
    )


def test_agent_select_skips_malformed_expression():
    """Defense in depth — the mutation validates at write time, but a
    malformed expression that somehow lands never fires."""
    out = select_agent_matches(
        candidates=[_agent_candidate(run_cron_expression="not a cron")],
        now=_now(),
    )
    assert out == []


def test_agent_select_naive_now_rejected():
    with pytest.raises(ValueError):
        select_agent_matches(candidates=[_agent_candidate()], now=datetime(2026, 1, 1))


# ---- activity wiring (DB-backed) -----------------------------------


pytestmark_db = pytest.mark.django_db


@pytest.fixture
def agent_stack(db):
    """An org with one app carrying a single agent Workload in Schedule
    mode (every minute, not paused) so the tick fires on any ``now``."""
    from astrolift_identity.models import Organization, Project, Team
    from astrolift_registry.models import RegisteredApp, Workload

    org = Organization.objects.create(name="Agent Co", slug="agent-co")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-agent")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-agent")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Triage App",
        slug="triage-app",
        provisioning_status="ready",
    )
    workload = Workload.objects.create(
        registered_app=app,
        name="Triage Agent",
        slug="triage-agent",
        kind=Workload.Kind.AGENT.value,
        run_family=Workload.RunFamily.TASK.value,
        run_mode=Workload.RunMode.SCHEDULE.value,
        run_cron_expression="* * * * *",
        run_paused=False,
    )
    return {"org": org, "app": app, "workload": workload}


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


def _stub_start(starts):
    from astrolift_workflows.client import WorkflowHandle

    def _start(name, args, *, workflow_id, task_queue=None):
        starts.append((name, list(args), workflow_id))
        return WorkflowHandle(workflow_id=workflow_id, run_id="", enqueued=False)

    return _start


@pytest.mark.django_db
def test_tick_dispatches_task_not_deploy_for_schedule_agent(agent_stack, settings):
    """Acceptance (1): a schedule agent dispatches a **Task** at a cron
    match — AgentTask(agent_definition=...) created + QUEUED +
    DispatchAgentTaskWorkflow enqueued, and NO Deployment row minted."""
    from astrolift_agents.models import AgentTask
    from astrolift_lifecycle.models import Deployment
    from astrolift_workflows.activities.cron_deploy import (
        _dispatch_agent_crons_sync,
    )

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    starts: list[tuple] = []

    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        summary = _dispatch_agent_crons_sync()

    # Exactly one agent fired.
    assert summary.fired_count == 1
    assert summary.fired_workload_slugs == ("triage-agent",)
    assert len(summary.fired_task_guids) == 1

    # A Task was created on the dispatch path — not a deploy.
    task = AgentTask.objects.get()
    assert task.agent_definition_id == agent_stack["workload"].id
    assert task.organization_id == agent_stack["org"].id
    assert task.status == AgentTask.Status.QUEUED
    assert task.queued_at is not None
    assert str(task.guid) == summary.fired_task_guids[0]
    # The cron tick started it (#2152).
    assert task.trigger_kind == "schedule"
    # The dispatch is a Task, NOT an app deploy: no Deployment row exists.
    assert Deployment.objects.count() == 0

    # The PR-1 dispatch path was invoked: DispatchAgentTaskWorkflow,
    # keyed to the task guid, carrying the task pk.
    assert len(starts) == 1
    name, args, workflow_id = starts[0]
    assert name == "DispatchAgentTaskWorkflow"
    assert workflow_id == f"DispatchAgentTaskWorkflow-{task.guid}"
    assert args[0].agent_task_id == task.pk
    assert args[0].actor.kind == "system"


@pytest.mark.django_db
def test_tick_skips_paused_agent(agent_stack, settings):
    """Acceptance (2): a paused agent does NOT dispatch — no task, no
    workflow start."""
    from astrolift_agents.models import AgentTask
    from astrolift_workflows.activities.cron_deploy import (
        _dispatch_agent_crons_sync,
    )

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    workload = agent_stack["workload"]
    workload.run_paused = True
    workload.save(update_fields=["run_paused", "updated_at", "version"])

    starts: list[tuple] = []
    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        summary = _dispatch_agent_crons_sync()

    assert summary.fired_count == 0
    assert AgentTask.objects.count() == 0
    assert starts == []


@pytest.mark.django_db
def test_tick_skips_service_family_agent(agent_stack, settings):
    """A Service-family agent is NOT a cron-dispatch candidate even if
    its run_mode is left at schedule — it deploys through the app
    Deployment path instead. This is half of the mutual-exclusivity
    guarantee (the other half — an app is never dispatched-as-task —
    holds because the agent selector only ever reads Workload rows)."""
    from astrolift_agents.models import AgentTask
    from astrolift_registry.models import Workload
    from astrolift_workflows.activities.cron_deploy import (
        _dispatch_agent_crons_sync,
    )

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    workload = agent_stack["workload"]
    workload.run_family = Workload.RunFamily.SERVICE.value
    workload.save(update_fields=["run_family", "updated_at", "version"])

    starts: list[tuple] = []
    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        summary = _dispatch_agent_crons_sync()

    assert summary.fired_count == 0
    assert AgentTask.objects.count() == 0
    assert starts == []


@pytest.mark.django_db
def test_tick_skips_once_mode_agent(agent_stack, settings):
    """An agent left in Once mode (the PR-1 default) is never picked up
    by the schedule tick — only run_mode=schedule dispatches on cron."""
    from astrolift_agents.models import AgentTask
    from astrolift_registry.models import Workload
    from astrolift_workflows.activities.cron_deploy import (
        _dispatch_agent_crons_sync,
    )

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    workload = agent_stack["workload"]
    workload.run_mode = Workload.RunMode.ONCE.value
    workload.save(update_fields=["run_mode", "updated_at", "version"])

    starts: list[tuple] = []
    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        summary = _dispatch_agent_crons_sync()

    assert summary.fired_count == 0
    assert AgentTask.objects.count() == 0


@pytest.mark.django_db
def test_tick_skips_empty_cron_expression(agent_stack, settings):
    """A schedule-mode agent with no cron expression is excluded at the
    query (mirrors the deploy tick's ``.exclude(cron_expression='')``)."""
    from astrolift_agents.models import AgentTask
    from astrolift_workflows.activities.cron_deploy import (
        _dispatch_agent_crons_sync,
    )

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    workload = agent_stack["workload"]
    workload.run_cron_expression = ""
    workload.save(update_fields=["run_cron_expression", "updated_at", "version"])

    starts: list[tuple] = []
    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        summary = _dispatch_agent_crons_sync()

    assert summary.fired_count == 0
    assert AgentTask.objects.count() == 0


@pytest.mark.django_db
def test_tick_skips_non_matching_minute(agent_stack, settings):
    """When the cron doesn't match the current minute, nothing fires.

    The tick reads ``timezone.now()`` internally, so we pin the
    workload's expression to a minute the test clock can't be on by
    using a far-future date predicate (Feb 30 never exists → the
    day-of-month atom can never match)."""
    from astrolift_agents.models import AgentTask
    from astrolift_workflows.activities.cron_deploy import (
        _dispatch_agent_crons_sync,
    )

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    workload = agent_stack["workload"]
    # 31st of February: month=2 + day=31 can never both be true.
    workload.run_cron_expression = "0 0 31 2 *"
    workload.save(update_fields=["run_cron_expression", "updated_at", "version"])

    starts: list[tuple] = []
    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        summary = _dispatch_agent_crons_sync()

    assert summary.candidates_count == 1  # it IS a candidate...
    assert summary.fired_count == 0  # ...but the minute doesn't match
    assert AgentTask.objects.count() == 0


@pytest.mark.django_db
def test_tick_skips_soft_deleted_agent(agent_stack, settings):
    from astrolift_agents.models import AgentTask
    from astrolift_workflows.activities.cron_deploy import (
        _dispatch_agent_crons_sync,
    )

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    agent_stack["workload"].soft_delete()

    starts: list[tuple] = []
    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        summary = _dispatch_agent_crons_sync()

    assert summary.fired_count == 0
    assert AgentTask.objects.count() == 0


# ---- mutual exclusivity: agent tick ignores cron-deploy apps --------


@pytest.mark.django_db
def test_agent_tick_ignores_cron_deploy_app(settings):
    """A plain (non-agent) app in ``trigger_mode='cron'`` — a cron-DEPLOY
    candidate — is never picked up by the agent tick. Proves the agent
    selector and the deploy selector can't double-fire one as the other:
    the agent tick only reads agent Workloads, never RegisteredApp
    trigger_mode."""
    from astrolift_agents.models import AgentTask
    from astrolift_identity.models import Organization, Project, Team
    from astrolift_registry.models import RegisteredApp
    from astrolift_workflows.activities.cron_deploy import (
        _dispatch_agent_crons_sync,
    )

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    org = Organization.objects.create(name="Deploy Co", slug="deploy-co")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-deploy")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-deploy")
    RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Cron Deploy App",
        slug="cron-deploy-app",
        trigger_mode=RegisteredApp.TriggerMode.CRON.value,
        cron_expression="* * * * *",
        provisioning_status="ready",
    )

    starts: list[tuple] = []
    with patch("astrolift_workflows.client.start_workflow", _stub_start(starts)):
        summary = _dispatch_agent_crons_sync()

    # No agent workloads exist → the agent tick fires nothing, even though
    # there's a matching cron-DEPLOY app in the DB.
    assert summary.candidates_count == 0
    assert summary.fired_count == 0
    assert AgentTask.objects.count() == 0


# ---- schedule_registry catalog entry --------------------------------


def test_agent_cron_tick_is_registered_at_60_seconds():
    from astrolift_workflows.schedule_registry import (
        DEFAULT_SCHEDULES,
        ScheduleKind,
        get_schedule,
    )

    sched = get_schedule(kind=ScheduleKind.AGENT_CRON_TICK)
    assert sched.interval_seconds == 60
    assert sched.workflow_name == "AgentCronTickWorkflow"
    assert any(s.kind == ScheduleKind.AGENT_CRON_TICK for s in DEFAULT_SCHEDULES)


# ---- Service-mode reuse: render + manual scale ----------------------


def test_service_agent_renders_deployment_service_hpa_with_replicas():
    """Acceptance (3): a Service-family agent deploys a
    Deployment+Service+HPA with HPA-owned replicas.

    The render path keys on the manifest workload ``kind == "agent"``,
    which emits the trio and defers replicas to the HPA (via the shared
    deployment renderer). A ``family=service`` agent flows through this
    exact path when its app deploys — so this asserts the rendered output
    a Service agent produces, with HPA bounds honoured."""
    from astrolift_manifest.normalize import NormalizationDefaults, normalize
    from astrolift_manifest.render import render_manifests
    from astrolift_manifest.types import (
        ContainerManifest,
        RawManifest,
        WorkloadManifest,
    )

    w = WorkloadManifest(
        name="always-on-agent",
        kind="agent",
        run_family="service",
        replicas=3,
        hpa_min=2,
        hpa_max=6,
        containers=(ContainerManifest(name="agent", is_primary=True, port=8080),),
    )
    manifest = normalize(RawManifest(name="svc-app", workloads=(w,)), defaults=NormalizationDefaults())
    out = render_manifests(
        manifest,
        app_slug="svc-app",
        namespace="svc-app-prod",
        image_tag="abc123",
        image_repository="ghcr.io/acme/svc",
        environment_name="prod",
    )

    kinds = sorted(r["kind"] for r in out)
    assert kinds == ["Deployment", "HorizontalPodAutoscaler", "Service"]

    dep = next(r for r in out if r["kind"] == "Deployment")
    assert "replicas" not in dep["spec"]
    # Still the agent pod annotation (it IS an agent, just always-on).
    assert dep["spec"]["template"]["metadata"]["annotations"]["astrolift.dev/workload-kind"] == "agent"

    hpa = next(r for r in out if r["kind"] == "HorizontalPodAutoscaler")
    assert hpa["spec"]["scaleTargetRef"]["name"] == "always-on-agent"
    assert hpa["spec"]["minReplicas"] == 2
    assert hpa["spec"]["maxReplicas"] == 6


@pytest.mark.django_db
def test_manual_scale_works_on_service_agent(agent_stack, settings, monkeypatch):
    """Acceptance (4): manual ``scale_workload`` still works on a Service
    agent.

    ``scale_workload`` patches ``Deployment/<workload.slug>`` directly via
    the cluster driver, independent of run-spec family — so it works for
    any agent that rendered a Deployment. We monkeypatch the cluster
    driver resolver (the canonical pattern from
    ``test_restart_scale_workload``) and assert the replica patch reaches
    the driver, named at the agent's Deployment by slug."""
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_lifecycle.services.k8s_ops import scale_workload
    from astrolift_registry.models import Workload

    workload = agent_stack["workload"]
    workload.run_family = Workload.RunFamily.SERVICE.value
    workload.save(update_fields=["run_family", "updated_at", "version"])

    plugin = ProviderPlugin(
        name="K8s", slug="k8s-scale", plugin_version="0.0.1", capabilities_manifest={}, config_schema={}
    )
    ProviderPlugin.objects.bulk_create([plugin])
    plugin = ProviderPlugin.objects.get(slug="k8s-scale")
    cluster = TenantCluster.objects.create(
        organization=agent_stack["org"],
        name="dev",
        slug="dev-scale",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://dev.cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    AppEnvironment.objects.create(
        registered_app=agent_stack["app"],
        tenant_cluster=cluster,
        name="prod",
        url="https://triage.example.com",
    )

    patches: list[tuple] = []

    class _RecordingDriver:
        def patch_workload(self, cluster_slug, namespace, kind, name, patch):
            patches.append((cluster_slug, namespace, kind, name, patch))
            return {"spec": {"replicas": patch["spec"]["replicas"]}, "status": {"readyReplicas": 0}}

    def _resolve(cluster):  # noqa: ARG001 — signature parity
        return _RecordingDriver()

    monkeypatch.setattr("core.cluster_management._driver_for_cluster", _resolve)

    result = scale_workload(workload, 4)

    assert result.ok is True
    assert result.current_replicas == 4
    # The driver received a replica patch on the agent's Deployment by slug.
    assert len(patches) == 1
    _cluster_slug, _namespace, kind, name, patch_body = patches[0]
    assert kind == "Deployment"
    assert name == workload.slug
    assert patch_body == {"spec": {"replicas": 4}}
