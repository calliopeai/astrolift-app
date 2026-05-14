"""
Tests for cron-deploy dispatch (#296).

Two layers:
  * ``cron_deploy`` policy — pure-Python; we exhaustively test
    expression / now-tuple matching.
  * ``dispatch_cron_deploys`` activity — talks to the real DB,
    creates Deployment rows, and (with the Temporal client patched
    out) records what *would* be enqueued.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from astrolift_workflows.cron_deploy import (
    CronDispatchCandidate,
    cron_matches,
    select_matches,
)


def _now(minute=0, hour=12, day=15, month=6, year=2026):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# ---- cron_matches ---------------------------------------------------


def test_wildcard_always_matches():
    assert cron_matches("* * * * *", now=_now()) is True


def test_specific_minute():
    assert cron_matches("5 * * * *", now=_now(minute=5)) is True
    assert cron_matches("5 * * * *", now=_now(minute=6)) is False


def test_step_every_15_minutes():
    for m in (0, 15, 30, 45):
        assert cron_matches("*/15 * * * *", now=_now(minute=m)) is True
    for m in (1, 14, 16, 31):
        assert cron_matches("*/15 * * * *", now=_now(minute=m)) is False


def test_range_minute_0_to_29():
    assert cron_matches("0-29 * * * *", now=_now(minute=0)) is True
    assert cron_matches("0-29 * * * *", now=_now(minute=29)) is True
    assert cron_matches("0-29 * * * *", now=_now(minute=30)) is False


def test_comma_list():
    assert cron_matches("0,15,45 * * * *", now=_now(minute=15)) is True
    assert cron_matches("0,15,45 * * * *", now=_now(minute=30)) is False


def test_combined_minute_hour():
    # Every Monday at 9:30 UTC. 2026-06-15 is a Monday.
    assert cron_matches("30 9 * * 1", now=_now(minute=30, hour=9, day=15, month=6)) is True
    # 9:31 doesn't match
    assert cron_matches("30 9 * * 1", now=_now(minute=31, hour=9, day=15, month=6)) is False
    # 2026-06-16 is a Tuesday
    assert cron_matches("30 9 * * 1", now=_now(minute=30, hour=9, day=16, month=6)) is False


def test_day_of_week_sunday_zero():
    # 2026-06-14 is a Sunday — cron weekday 0
    assert cron_matches("0 0 * * 0", now=_now(minute=0, hour=0, day=14, month=6)) is True


def test_invalid_expression_returns_false():
    """Malformed expressions never fire (defense in depth — the
    registry validator should have rejected them at write time)."""
    assert cron_matches("not a cron", now=_now()) is False
    assert cron_matches("99 * * * *", now=_now()) is False  # minute out of range


def test_naive_now_rejected():
    with pytest.raises(ValueError):
        cron_matches("* * * * *", now=datetime(2026, 1, 1))


# ---- select_matches -------------------------------------------------


def _candidate(**kw):
    base = {
        "app_id": 1,
        "app_slug": "app",
        "app_guid": "guid",
        "cron_expression": "* * * * *",
        "cron_paused": False,
        "primary_environment_name": "prod",
    }
    base.update(kw)
    return CronDispatchCandidate(**base)


def test_select_matches_fires_on_match():
    out = select_matches(candidates=[_candidate()], now=_now())
    assert len(out) == 1


def test_select_matches_skips_paused():
    out = select_matches(candidates=[_candidate(cron_paused=True)], now=_now())
    assert out == []


def test_select_matches_skips_no_env():
    out = select_matches(
        candidates=[_candidate(primary_environment_name=None)],
        now=_now(),
    )
    assert out == []


def test_select_matches_skips_non_matching_expression():
    out = select_matches(
        candidates=[_candidate(cron_expression="0 0 1 1 *")],  # New Year midnight UTC
        now=_now(minute=30, hour=9, day=15, month=6),
    )
    assert out == []


# ---- activity wiring (DB-backed) -----------------------------------


pytestmark_db = pytest.mark.django_db


@pytest.fixture
def cron_app_stack(db):
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_identity.models import Organization, Project, Team
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp

    org = Organization.objects.create(name="Acme", slug="acme-cron")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-cron")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-cron")
    plugin = ProviderPlugin(
        name="K8s", slug="k8s-cron", version="0.0.1", capabilities_manifest={}, config_schema={}
    )
    ProviderPlugin.objects.bulk_create([plugin])
    plugin = ProviderPlugin.objects.get(slug="k8s-cron")
    cluster = TenantCluster.objects.create(
        organization=org,
        name="dev",
        slug="dev-cron",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://dev.cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Cron App",
        slug="cron-app",
        trigger_mode=RegisteredApp.TriggerMode.CRON.value,
        cron_expression="* * * * *",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://hello.example.com",
    )
    return {"org": org, "app": app, "env": env, "cluster": cluster}


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


@pytest.mark.django_db
def test_dispatch_fires_deploy_for_matching_app(cron_app_stack, settings):
    """The activity walks active cron apps and creates a Deployment
    + enqueues DeployAppWorkflow for the ones whose expression
    matches the current minute."""
    from astrolift_lifecycle.models import Deployment
    from astrolift_workflows.activities.cron_deploy import (
        _dispatch_cron_deploys_sync,
    )

    settings.ASTROLIFT_TEMPORAL_ENABLED = False  # synthetic handle

    starts: list[tuple[str, str]] = []

    def fake_start(name, args, *, workflow_id, task_queue=None):
        from astrolift_workflows.client import WorkflowHandle

        starts.append((name, workflow_id))
        return WorkflowHandle(workflow_id=workflow_id, run_id="", enqueued=False)

    with patch("astrolift_workflows.client.start_workflow", fake_start):
        summary = _dispatch_cron_deploys_sync()

    assert summary.fired_count == 1
    assert summary.fired_app_slugs == ("cron-app",)
    deploy = Deployment.objects.get(registered_app=cron_app_stack["app"])
    assert deploy.trigger_kind == "scheduled"
    assert deploy.ci_actor_kind == "cron"
    # workflow_id matches the platform's single-flight pattern.
    assert starts[0][0] == "DeployAppWorkflow"
    assert starts[0][1].startswith(f"DeployAppWorkflow-{cron_app_stack['app'].guid}")


@pytest.mark.django_db
def test_dispatch_skips_paused_app(cron_app_stack, settings):
    from astrolift_lifecycle.models import Deployment
    from astrolift_workflows.activities.cron_deploy import (
        _dispatch_cron_deploys_sync,
    )

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    app = cron_app_stack["app"]
    app.cron_paused = True
    app.save(update_fields=["cron_paused"])

    from astrolift_workflows.client import WorkflowHandle

    def _stub_start(*a, workflow_id, task_queue=None, **kw):
        return WorkflowHandle(workflow_id=workflow_id, run_id="", enqueued=False)

    with patch("astrolift_workflows.client.start_workflow", _stub_start):
        summary = _dispatch_cron_deploys_sync()

    assert summary.fired_count == 0
    assert Deployment.objects.count() == 0


@pytest.mark.django_db
def test_dispatch_skips_non_cron_trigger_mode(cron_app_stack, settings):
    """An app whose trigger_mode has been flipped off cron is not
    considered, even if cron_expression is still populated."""
    from astrolift_lifecycle.models import Deployment
    from astrolift_registry.models import RegisteredApp
    from astrolift_workflows.activities.cron_deploy import (
        _dispatch_cron_deploys_sync,
    )

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    app = cron_app_stack["app"]
    app.trigger_mode = RegisteredApp.TriggerMode.MANUAL.value
    app.save(update_fields=["trigger_mode"])

    from astrolift_workflows.client import WorkflowHandle

    def _stub_start(*a, workflow_id, task_queue=None, **kw):
        return WorkflowHandle(workflow_id=workflow_id, run_id="", enqueued=False)

    with patch("astrolift_workflows.client.start_workflow", _stub_start):
        summary = _dispatch_cron_deploys_sync()

    assert summary.fired_count == 0
    assert Deployment.objects.count() == 0


@pytest.mark.django_db
def test_dispatch_skips_soft_deleted(cron_app_stack, settings):
    from astrolift_lifecycle.models import Deployment
    from astrolift_workflows.activities.cron_deploy import (
        _dispatch_cron_deploys_sync,
    )

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    app = cron_app_stack["app"]
    app.soft_delete()

    from astrolift_workflows.client import WorkflowHandle

    def _stub_start(*a, workflow_id, task_queue=None, **kw):
        return WorkflowHandle(workflow_id=workflow_id, run_id="", enqueued=False)

    with patch("astrolift_workflows.client.start_workflow", _stub_start):
        summary = _dispatch_cron_deploys_sync()

    assert summary.fired_count == 0
    assert Deployment.objects.count() == 0


@pytest.mark.django_db
def test_dispatch_uses_last_running_image_tag(cron_app_stack, settings):
    """Cron deploys re-roll the last known good image, not 'latest'."""
    from astrolift_lifecycle.models import Deployment
    from astrolift_workflows.activities.cron_deploy import (
        _dispatch_cron_deploys_sync,
    )
    from astrolift_workflows.client import WorkflowHandle

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    Deployment.objects.create(
        registered_app=cron_app_stack["app"],
        app_environment=cron_app_stack["env"],
        trigger_kind="manual",
        status=Deployment.Status.RUNNING.value,
        image_tag="v1.5.2",
        image_digest="sha256:abc",
    )

    def _stub_start(*a, workflow_id, task_queue=None, **kw):
        return WorkflowHandle(workflow_id=workflow_id, run_id="", enqueued=False)

    with patch("astrolift_workflows.client.start_workflow", _stub_start):
        _dispatch_cron_deploys_sync()

    fresh = Deployment.objects.filter(trigger_kind="scheduled").get()
    assert fresh.image_tag == "v1.5.2"
    assert fresh.image_digest == "sha256:abc"


@pytest.mark.django_db
def test_dispatch_skips_when_env_paused(cron_app_stack, settings):
    from astrolift_lifecycle.models import Deployment
    from astrolift_workflows.activities.cron_deploy import (
        _dispatch_cron_deploys_sync,
    )

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    env = cron_app_stack["env"]
    env.deploys_paused = True
    env.save(update_fields=["deploys_paused"])

    from astrolift_workflows.client import WorkflowHandle

    def _stub_start(*a, workflow_id, task_queue=None, **kw):
        return WorkflowHandle(workflow_id=workflow_id, run_id="", enqueued=False)

    with patch("astrolift_workflows.client.start_workflow", _stub_start):
        summary = _dispatch_cron_deploys_sync()

    assert summary.fired_count == 0
    assert Deployment.objects.count() == 0


# ---- schedule_registry catalog entry --------------------------------


def test_cron_tick_is_registered_at_60_seconds():
    from astrolift_workflows.schedule_registry import (
        DEFAULT_SCHEDULES,
        ScheduleKind,
        get_schedule,
    )

    sched = get_schedule(kind=ScheduleKind.CRON_DEPLOY_TICK)
    assert sched.interval_seconds == 60
    assert sched.workflow_name == "CronDeployTickWorkflow"
    assert any(s.kind == ScheduleKind.CRON_DEPLOY_TICK for s in DEFAULT_SCHEDULES)
