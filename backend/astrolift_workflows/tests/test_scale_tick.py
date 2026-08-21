"""
Tests for the scheduled-scaling tick (spec 33, PR-5).

Two layers, mirroring ``test_agent_cron_dispatch`` / ``test_cron_deploy``:

  * ``select_scale_matches`` policy — pure-Python; deterministic via an
    injected ``now`` (the same ``cron_matches(now=)`` seam PR-4 used). All
    the precise cron-timing, env-clamp, idempotency, and same-tick
    tie-break behaviour is asserted here without a clock or Temporal.
  * ``dispatch_scale_ticks`` activity — talks to the real DB, selects
    ``run_family='service'`` agent Workloads with a scale cron, and (with
    the cluster driver spied via the canonical
    ``core.cluster_management._driver_for_cluster`` monkeypatch) asserts
    the replica patch reaches the driver on the right Deployment.

The four acceptance cases (spec §PR-5) + the tie-break:
  1. a Service agent with scale-up cron + X and scale-down cron + 0
     patches replicas to X at the up-cron match and to 0 at the down-cron
     match;
  2. respects the env max bound (a target above the ceiling is clamped,
     never raises);
  3. idempotent if already at target (no redundant patch);
  4. NO effect on Task-family agents (the ``run_family='service'`` filter);
  + same-tick tie-break: up-cron and down-cron both match → scale-DOWN wins.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from astrolift_workflows.cron_deploy import (
    ScaleAction,
    ScaleTickCandidate,
    select_scale_matches,
)


def _now(minute=0, hour=12, day=15, month=6, year=2026):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# ---- select_scale_matches (pure policy) -----------------------------


def _scale_candidate(**kw):
    base = {
        "workload_id": 1,
        "workload_slug": "always-on",
        "workload_guid": "guid",
        "organization_id": 1,
        "scheduled_scale_to": 3,
        "scale_up_cron": "0 9 * * *",  # 09:00 up
        "scale_down_cron": "0 18 * * *",  # 18:00 down
        "current_replicas": 0,
        "max_replicas": 20,
    }
    base.update(kw)
    return ScaleTickCandidate(**base)


def test_scale_up_fires_at_up_cron_to_X():
    """Acceptance (1, up half): at the scale-up cron match, action scales
    to ``scheduled_scale_to`` (X)."""
    out = select_scale_matches(candidates=[_scale_candidate()], now=_now(hour=9, minute=0))
    assert len(out) == 1
    action = out[0]
    assert action.direction == "up"
    assert action.target_replicas == 3
    assert action.workload_slug == "always-on"


def test_scale_down_fires_at_down_cron_to_zero():
    """Acceptance (1, down half): at the scale-down cron match, action
    scales to 0 — regardless of ``scheduled_scale_to``."""
    out = select_scale_matches(
        candidates=[_scale_candidate(current_replicas=3)],
        now=_now(hour=18, minute=0),
    )
    assert len(out) == 1
    action = out[0]
    assert action.direction == "down"
    assert action.target_replicas == 0


def test_no_action_when_neither_cron_matches():
    """Off-cron minute → no action."""
    out = select_scale_matches(
        candidates=[_scale_candidate(current_replicas=3)],
        now=_now(hour=13, minute=30),
    )
    assert out == []


def test_respects_env_max_bound_clamps_up_target():
    """Acceptance (2): a ``scheduled_scale_to`` above the env ceiling is
    clamped to ``max_replicas`` — the selector never asks for an
    out-of-range count (which ``scale_workload`` would reject)."""
    out = select_scale_matches(
        candidates=[_scale_candidate(scheduled_scale_to=50, max_replicas=20, current_replicas=0)],
        now=_now(hour=9, minute=0),
    )
    assert len(out) == 1
    assert out[0].target_replicas == 20  # clamped from 50


def test_idempotent_skip_when_already_at_up_target():
    """Acceptance (3): a workload already at the (clamped) target produces
    no action — the tick issues no redundant patch."""
    out = select_scale_matches(
        candidates=[_scale_candidate(scheduled_scale_to=3, current_replicas=3)],
        now=_now(hour=9, minute=0),
    )
    assert out == []


def test_idempotent_skip_when_already_at_zero_on_down():
    """Acceptance (3), down half: already at 0 at the down-cron → no
    action."""
    out = select_scale_matches(
        candidates=[_scale_candidate(current_replicas=0)],
        now=_now(hour=18, minute=0),
    )
    assert out == []


def test_idempotent_skip_when_already_at_clamped_up_target():
    """Idempotency must compare against the CLAMPED target, not the raw
    ``scheduled_scale_to`` — current=20, requested=50, ceiling=20 → already
    at the effective target → no action (otherwise it would patch every
    tick forever)."""
    out = select_scale_matches(
        candidates=[_scale_candidate(scheduled_scale_to=50, max_replicas=20, current_replicas=20)],
        now=_now(hour=9, minute=0),
    )
    assert out == []


def test_same_tick_tie_break_scale_down_wins():
    """Same-tick tie-break: when up-cron AND down-cron both match the same
    minute (e.g. both ``0 0 * * *``), scale-DOWN wins — the safer, cheaper
    outcome, deterministic regardless of field order."""
    c = _scale_candidate(
        scale_up_cron="0 0 * * *",
        scale_down_cron="0 0 * * *",
        scheduled_scale_to=5,
        current_replicas=5,
    )
    out = select_scale_matches(candidates=[c], now=_now(hour=0, minute=0))
    assert len(out) == 1
    assert out[0].direction == "down"
    assert out[0].target_replicas == 0


def test_up_only_cron_when_down_unset():
    """A candidate with only a scale-up cron scales up at its match and is
    inert otherwise (no down ever fires)."""
    c = _scale_candidate(scale_up_cron="0 9 * * *", scale_down_cron="", current_replicas=0)
    assert len(select_scale_matches(candidates=[c], now=_now(hour=9, minute=0))) == 1
    assert select_scale_matches(candidates=[c], now=_now(hour=18, minute=0)) == []


def test_down_only_cron_when_up_unset():
    """A candidate with only a scale-down cron scales to 0 at its match and
    never scales up."""
    c = _scale_candidate(scale_up_cron="", scale_down_cron="0 18 * * *", current_replicas=3)
    assert select_scale_matches(candidates=[c], now=_now(hour=9, minute=0)) == []
    out = select_scale_matches(candidates=[c], now=_now(hour=18, minute=0))
    assert len(out) == 1 and out[0].target_replicas == 0


def test_up_cron_match_but_no_target_skips():
    """Up-cron matches but ``scheduled_scale_to`` is None → no target to
    scale to → skipped (the down path is unaffected, its target is 0)."""
    c = _scale_candidate(scheduled_scale_to=None, current_replicas=0)
    out = select_scale_matches(candidates=[c], now=_now(hour=9, minute=0))
    assert out == []


def test_malformed_cron_never_fires():
    """Defense in depth — a malformed expression that somehow lands never
    matches (``cron_matches`` defensively refuses it)."""
    c = _scale_candidate(scale_up_cron="not a cron", scale_down_cron="also bad")
    assert select_scale_matches(candidates=[c], now=_now()) == []


def test_select_scale_naive_now_rejected():
    with pytest.raises(ValueError):
        select_scale_matches(candidates=[_scale_candidate()], now=datetime(2026, 1, 1))


def test_scale_action_is_frozen():
    """Actions are immutable value objects (frozen dataclass)."""
    a = ScaleAction(
        workload_id=1,
        workload_slug="s",
        workload_guid="g",
        organization_id=1,
        direction="up",
        target_replicas=2,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.target_replicas = 3  # type: ignore[misc]


# ---- activity wiring (DB-backed, cluster driver spied) --------------


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


class _RecordingDriver:
    """Minimal stand-in for ClusterDriver — records each patch_workload
    call and echoes the requested replica count back the way the live
    driver's read-back does."""

    def __init__(self):
        self.patches: list[tuple] = []

    def patch_workload(self, cluster_slug, namespace, kind, name, patch):
        self.patches.append((cluster_slug, namespace, kind, name, patch))
        return {"spec": {"replicas": patch["spec"]["replicas"]}, "status": {"readyReplicas": 0}}


@pytest.fixture
def service_agent_stack(db):
    """An org with one app carrying a Service-family agent Workload bound
    to a cluster + env, so ``scale_workload`` resolves a driver. The scale
    crons are ``* * * * *`` (always match) so the tick fires on any clock;
    precise cron timing is covered by the pure-policy tests above."""
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_identity.models import Organization, Project, Team
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp, Workload

    org = Organization.objects.create(name="Scale Co", slug="scale-co")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-scale")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-scale")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Always-On App",
        slug="always-on-app",
        provisioning_status="ready",
    )
    plugin = ProviderPlugin(
        name="K8s", slug="k8s-scaletick", plugin_version="0.0.1", capabilities_manifest={}, config_schema={}
    )
    ProviderPlugin.objects.bulk_create([plugin])
    plugin = ProviderPlugin.objects.get(slug="k8s-scaletick")
    cluster = TenantCluster.objects.create(
        organization=org,
        name="dev",
        slug="dev-scaletick",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://dev.cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://always-on.example.com",
    )
    workload = Workload.objects.create(
        registered_app=app,
        name="Always-On Agent",
        slug="always-on-agent",
        kind=Workload.Kind.AGENT.value,
        run_family=Workload.RunFamily.SERVICE.value,
        replicas=0,
        scheduled_scale_to=3,
        scale_up_cron="* * * * *",
        scale_down_cron="",
    )
    return {"org": org, "app": app, "cluster": cluster, "workload": workload}


@pytest.fixture
def install_driver(monkeypatch):
    """Install a recording driver in place of the cluster-management
    resolver (the canonical pattern from ``test_restart_scale_workload``).
    Returns the driver so the test can assert the patches it received."""
    driver = _RecordingDriver()

    def _resolve(cluster):  # noqa: ARG001 — signature parity
        return driver

    monkeypatch.setattr("core.cluster_management._driver_for_cluster", _resolve)
    return driver


@pytest.mark.django_db
def test_tick_scales_service_agent_up_to_X(service_agent_stack, install_driver):
    """Acceptance (1, up): the tick patches the Service agent's Deployment
    to ``scheduled_scale_to`` (X) at an up-cron match, and persists the new
    desired count on the workload."""
    from astrolift_registry.models import Workload
    from astrolift_workflows.activities.cron_deploy import _dispatch_scale_ticks_sync

    summary = _dispatch_scale_ticks_sync()

    assert summary.candidates_count == 1
    assert summary.scaled_count == 1
    assert summary.scaled_workload_slugs == ("always-on-agent",)
    assert summary.scaled_to == (3,)

    # The driver received a replica patch on the agent's Deployment by slug.
    assert len(install_driver.patches) == 1
    _cluster_slug, _ns, kind, name, patch_body = install_driver.patches[0]
    assert kind == "Deployment"
    assert name == "always-on-agent"
    assert patch_body == {"spec": {"replicas": 3}}

    # New desired count persisted so a redeploy won't revert + next-tick
    # idempotency holds.
    wl = Workload.objects.get(pk=service_agent_stack["workload"].pk)
    assert wl.replicas == 3


@pytest.mark.django_db
def test_tick_scales_service_agent_down_to_zero(service_agent_stack, install_driver):
    """Acceptance (1, down): at a down-cron match the tick patches to 0."""
    from astrolift_registry.models import Workload
    from astrolift_workflows.activities.cron_deploy import _dispatch_scale_ticks_sync

    workload = service_agent_stack["workload"]
    # Currently up at 3; down-cron always matches, up-cron never (clear it).
    workload.replicas = 3
    workload.scale_up_cron = ""
    workload.scale_down_cron = "* * * * *"
    workload.save(update_fields=["replicas", "scale_up_cron", "scale_down_cron", "updated_at", "version"])

    summary = _dispatch_scale_ticks_sync()

    assert summary.scaled_count == 1
    assert summary.scaled_to == (0,)
    assert install_driver.patches[0][4] == {"spec": {"replicas": 0}}
    wl = Workload.objects.get(pk=workload.pk)
    assert wl.replicas == 0


@pytest.mark.django_db
def test_tick_respects_env_max_bound(service_agent_stack, install_driver):
    """Acceptance (2): ``scheduled_scale_to`` above the env ceiling is
    clamped — the driver is patched to the ceiling, and the tick does NOT
    raise (which an unclamped ``scale_workload`` would)."""
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import Workload
    from astrolift_workflows.activities.cron_deploy import _dispatch_scale_ticks_sync

    # Tighten the env ceiling to 4 and ask for 50.
    env = AppEnvironment.objects.get(registered_app=service_agent_stack["app"])
    env.deploy_config = {"max_replicas": 4}
    env.save(update_fields=["deploy_config"])
    workload = service_agent_stack["workload"]
    workload.scheduled_scale_to = 50
    workload.save(update_fields=["scheduled_scale_to", "updated_at", "version"])

    summary = _dispatch_scale_ticks_sync()

    assert summary.scaled_count == 1
    assert summary.scaled_to == (4,)  # clamped from 50 to env ceiling
    assert install_driver.patches[0][4] == {"spec": {"replicas": 4}}
    wl = Workload.objects.get(pk=workload.pk)
    assert wl.replicas == 4


@pytest.mark.django_db
def test_tick_idempotent_when_already_at_target(service_agent_stack, install_driver):
    """Acceptance (3): a Service agent already at X produces no patch — the
    candidate is counted but ``scale_workload`` is never invoked."""
    from astrolift_workflows.activities.cron_deploy import _dispatch_scale_ticks_sync

    workload = service_agent_stack["workload"]
    workload.replicas = 3  # already at scheduled_scale_to
    workload.save(update_fields=["replicas", "updated_at", "version"])

    summary = _dispatch_scale_ticks_sync()

    assert summary.candidates_count == 1  # it IS a candidate...
    assert summary.scaled_count == 0  # ...but already at target
    assert install_driver.patches == []  # no redundant driver patch


@pytest.mark.django_db
def test_tick_no_effect_on_task_family_agent(service_agent_stack, install_driver):
    """Acceptance (4): a Task-family agent is never scaled by this tick,
    even with scale crons set — the ``run_family='service'`` filter
    excludes it."""
    from astrolift_registry.models import Workload
    from astrolift_workflows.activities.cron_deploy import _dispatch_scale_ticks_sync

    workload = service_agent_stack["workload"]
    workload.run_family = Workload.RunFamily.TASK.value
    workload.save(update_fields=["run_family", "updated_at", "version"])

    summary = _dispatch_scale_ticks_sync()

    assert summary.candidates_count == 0  # Task agent is not a candidate
    assert summary.scaled_count == 0
    assert install_driver.patches == []


@pytest.mark.django_db
def test_tick_same_tick_tie_break_scales_down(service_agent_stack, install_driver):
    """Same-tick tie-break (DB layer): both crons match the same minute →
    scale-DOWN wins → the driver is patched to 0, not to X."""
    from astrolift_workflows.activities.cron_deploy import _dispatch_scale_ticks_sync

    workload = service_agent_stack["workload"]
    workload.replicas = 3  # up, so down→0 is a real change (not idempotent-skipped)
    workload.scheduled_scale_to = 5
    workload.scale_up_cron = "* * * * *"
    workload.scale_down_cron = "* * * * *"
    workload.save(
        update_fields=[
            "replicas",
            "scheduled_scale_to",
            "scale_up_cron",
            "scale_down_cron",
            "updated_at",
            "version",
        ]
    )

    summary = _dispatch_scale_ticks_sync()

    assert summary.scaled_count == 1
    assert summary.scaled_to == (0,)  # DOWN wins
    assert install_driver.patches[0][4] == {"spec": {"replicas": 0}}


@pytest.mark.django_db
def test_tick_skips_service_agent_without_scale_cron(service_agent_stack, install_driver):
    """A Service agent with neither scale cron set is not a candidate."""
    from astrolift_workflows.activities.cron_deploy import _dispatch_scale_ticks_sync

    workload = service_agent_stack["workload"]
    workload.scale_up_cron = ""
    workload.scale_down_cron = ""
    workload.save(update_fields=["scale_up_cron", "scale_down_cron", "updated_at", "version"])

    summary = _dispatch_scale_ticks_sync()

    assert summary.candidates_count == 0
    assert summary.scaled_count == 0


@pytest.mark.django_db
def test_tick_skips_soft_deleted_service_agent(service_agent_stack, install_driver):
    from astrolift_workflows.activities.cron_deploy import _dispatch_scale_ticks_sync

    service_agent_stack["workload"].soft_delete()

    summary = _dispatch_scale_ticks_sync()

    assert summary.candidates_count == 0
    assert summary.scaled_count == 0
    assert install_driver.patches == []


@pytest.mark.django_db
def test_tick_ignores_cron_deploy_app(install_driver):
    """Mutual exclusivity (vs the deploy selector): a plain app in
    ``trigger_mode='cron'`` is never a scale-tick candidate — the scale
    tick only ever reads ``run_family='service'`` agent Workloads, never
    ``RegisteredApp.trigger_mode``."""
    from astrolift_identity.models import Organization, Project, Team
    from astrolift_registry.models import RegisteredApp
    from astrolift_workflows.activities.cron_deploy import _dispatch_scale_ticks_sync

    org = Organization.objects.create(name="Deploy Co2", slug="deploy-co2")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-deploy2")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-deploy2")
    RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Cron Deploy App2",
        slug="cron-deploy-app2",
        trigger_mode=RegisteredApp.TriggerMode.CRON.value,
        cron_expression="* * * * *",
        provisioning_status="ready",
    )

    summary = _dispatch_scale_ticks_sync()

    assert summary.candidates_count == 0
    assert summary.scaled_count == 0


@pytest.mark.django_db
def test_tick_ignores_task_schedule_agent(service_agent_stack, install_driver):
    """Mutual exclusivity (vs the agent-dispatch selector): a Task-family
    Schedule-mode agent (the agent-cron tick's candidate) is invisible to
    the scale tick even with scale crons set — different ``run_family``."""
    from astrolift_identity.models import Organization, Project, Team
    from astrolift_registry.models import RegisteredApp, Workload
    from astrolift_workflows.activities.cron_deploy import _dispatch_scale_ticks_sync

    # A separate Task/Schedule agent that the agent-cron tick would dispatch.
    org = Organization.objects.create(name="Task Co", slug="task-co")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-task")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-task")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Task App",
        slug="task-app",
        provisioning_status="ready",
    )
    Workload.objects.create(
        registered_app=app,
        name="Task Agent",
        slug="task-agent",
        kind=Workload.Kind.AGENT.value,
        run_family=Workload.RunFamily.TASK.value,
        run_mode=Workload.RunMode.SCHEDULE.value,
        run_cron_expression="* * * * *",
        # Even if scale crons are (mistakenly) set on a Task agent, it must
        # not be scaled — run_family is the discriminator.
        scale_up_cron="* * * * *",
        scheduled_scale_to=2,
    )

    summary = _dispatch_scale_ticks_sync()

    # Only the Service agent from the fixture is a candidate; the Task agent
    # is excluded.
    assert summary.candidates_count == 1
    assert summary.scaled_workload_slugs == ("always-on-agent",)


# ---- schedule_registry catalog entry --------------------------------


def test_scale_tick_is_registered_at_60_seconds():
    from astrolift_workflows.schedule_registry import (
        DEFAULT_SCHEDULES,
        ScheduleKind,
        get_schedule,
    )

    sched = get_schedule(kind=ScheduleKind.SCALE_TICK)
    assert sched.interval_seconds == 60
    assert sched.workflow_name == "AgentScaleTickWorkflow"
    assert any(s.kind == ScheduleKind.SCALE_TICK for s in DEFAULT_SCHEDULES)
