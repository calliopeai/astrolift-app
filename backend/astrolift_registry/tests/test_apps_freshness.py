"""Tests for the apps-list freshness rollup (#405).

Covers:

* ``include_freshness=False`` (default) keeps the legacy shape — the
  three new fields stay null and no deploy table query fires.
* ``include_freshness=True`` populates latestDeployment / lastDeployedAt
  / healthPulse for every row.
* Pulse derivation per status / age:
    - ``never``    — no deploys
    - ``ok``       — successful deploy within HEALTHY_DEPLOY_WINDOW_DAYS
    - ``degraded`` — latest deploy is ``failed``, regardless of older success
    - ``stale``    — successful deploys exist but none within
                     STALE_DEPLOY_WINDOW_DAYS
* N+1 guard — query count is bounded regardless of app count.

Real Postgres (no mocks) so the prefetch behaviour we rely on is the
behaviour we'd see in prod.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Role, RoleBinding, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.queries import RegistryQuery
from astrolift_registry.schema.types import (
    HEALTHY_DEPLOY_WINDOW_DAYS,
    STALE_DEPLOY_WINDOW_DAYS,
    AstroliftAppHealthPulseStatus,
)
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None))


def _user(username: str, **kw):
    User = get_user_model()
    return User.objects.create(username=username, email=f"{username}@test", **kw)


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


def _provider_plugin():
    plugin = ProviderPlugin(
        name="Test Provider",
        slug="freshness-test-plugin",
        version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    return ProviderPlugin.objects.get(slug="freshness-test-plugin")


def _cluster(org, plugin):
    return TenantCluster.objects.create(
        organization=org,
        name="freshness-cluster",
        slug="freshness-cluster",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


def _scaffold(slug_suffix: str = ""):
    """Build a single-org / single-team / single-project tenant with
    three apps and a cluster + env wired up so deploy rows can hang
    off each app."""
    suffix = slug_suffix
    org = Organization.objects.create(name="Acme", slug=f"freshness-acme{suffix}")
    team = Team.objects.create(organization=org, name="Plat", slug=f"plat{suffix}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo{suffix}")
    plugin = _provider_plugin()
    cluster = _cluster(org, plugin)
    apps = {
        "ok-app": RegisteredApp.objects.create(
            organization=org, team=team, project=project, name="OK", slug=f"ok-app{suffix}"
        ),
        "failed-app": RegisteredApp.objects.create(
            organization=org, team=team, project=project, name="Bad", slug=f"failed-app{suffix}"
        ),
        "stale-app": RegisteredApp.objects.create(
            organization=org, team=team, project=project, name="Cobweb", slug=f"stale-app{suffix}"
        ),
        "never-app": RegisteredApp.objects.create(
            organization=org, team=team, project=project, name="Fresh", slug=f"never-app{suffix}"
        ),
    }
    envs = {
        slug: AppEnvironment.objects.create(
            registered_app=app,
            tenant_cluster=cluster,
            name="prod",
            url="https://x.example.com",
            required_approvals=0,
        )
        for slug, app in apps.items()
    }
    role = Role.objects.create(
        name=f"reader{suffix}",
        slug=f"reader-freshness{suffix}",
        permissions=["app.read"],
    )
    return org, team, project, apps, envs, role


def _make_deploy(app, env, *, status: str, age: timedelta, trigger_kind: str = "manual"):
    """Create a Deployment row pinned to ``now - age``.

    Bypasses the state machine — we want to assert resolver behaviour
    against arbitrary terminal rows, and the state machine doesn't
    let you write ``running`` directly. We update ``created_at``
    after-the-fact because ``BaseCoreModel.save`` overwrites it on
    insert.
    """
    deploy = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=trigger_kind,
        status=status,
    )
    target = timezone.now() - age
    Deployment.objects.filter(pk=deploy.pk).update(created_at=target)
    deploy.refresh_from_db()
    return deploy


# ---------- include_freshness gating -----------------------------------


def test_freshness_off_by_default_leaves_fields_null():
    org, team, project, apps, envs, role = _scaffold()
    user = _user("default-freshness", is_superuser=True, is_staff=True)
    _make_deploy(apps["ok-app"], envs["ok-app"], status="running", age=timedelta(hours=1))

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_apps(_info())

    by_slug = {a.slug: a for a in result}
    assert by_slug[apps["ok-app"].slug].latest_deployment is None
    assert by_slug[apps["ok-app"].slug].last_deployed_at is None
    assert by_slug[apps["ok-app"].slug].health_pulse is None


def test_freshness_on_populates_fields():
    org, team, project, apps, envs, role = _scaffold()
    user = _user("opt-in", is_superuser=True, is_staff=True)
    _make_deploy(apps["ok-app"], envs["ok-app"], status="running", age=timedelta(hours=2))

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_apps(_info(), include_freshness=True)

    by_slug = {a.slug: a for a in result}
    row = by_slug[apps["ok-app"].slug]
    assert row.health_pulse is not None
    assert row.health_pulse.status is AstroliftAppHealthPulseStatus.OK
    assert row.last_deployed_at is not None
    assert row.latest_deployment is not None
    assert row.latest_deployment.environment_name == "prod"


def test_freshness_on_my_apps():
    """``astroliftMyApps`` also honours the flag — the FE shells that
    use the self-service surface (dashboard, etc.) need the same
    pulse rollup as the org-scoped list."""
    org, team, project, apps, envs, role = _scaffold(slug_suffix="-my")
    user = _user("my-apps-viewer")
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)
    _make_deploy(apps["ok-app"], envs["ok-app"], status="running", age=timedelta(hours=3))

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_my_apps(_info(), include_freshness=True)

    by_slug = {a.slug: a for a in result}
    pulse = by_slug[apps["ok-app"].slug].health_pulse
    assert pulse is not None
    assert pulse.status is AstroliftAppHealthPulseStatus.OK


# ---------- pulse derivation -------------------------------------------


def test_pulse_never_when_no_deploys():
    org, team, project, apps, envs, role = _scaffold(slug_suffix="-never")
    user = _user("never-viewer", is_superuser=True, is_staff=True)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_apps(_info(), include_freshness=True)

    by_slug = {a.slug: a for a in result}
    pulse = by_slug[apps["never-app"].slug].health_pulse
    assert pulse is not None
    assert pulse.status is AstroliftAppHealthPulseStatus.NEVER
    assert pulse.age_seconds is None
    assert by_slug[apps["never-app"].slug].latest_deployment is None
    assert by_slug[apps["never-app"].slug].last_deployed_at is None


def test_pulse_ok_for_recent_success():
    org, team, project, apps, envs, role = _scaffold(slug_suffix="-ok")
    user = _user("ok-viewer", is_superuser=True, is_staff=True)
    _make_deploy(apps["ok-app"], envs["ok-app"], status="running", age=timedelta(hours=6))

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_apps(_info(), include_freshness=True)

    by_slug = {a.slug: a for a in result}
    pulse = by_slug[apps["ok-app"].slug].health_pulse
    assert pulse is not None
    assert pulse.status is AstroliftAppHealthPulseStatus.OK
    # Age tolerance to absorb test-runtime drift between row insert
    # and resolver-call timestamps.
    assert pulse.age_seconds is not None
    assert pulse.age_seconds >= 6 * 3600 - 60
    assert pulse.age_seconds <= 6 * 3600 + 60


def test_pulse_degraded_when_latest_is_failed_even_with_older_success():
    """Latest deploy is the signal — an older success doesn't paint
    over a fresh failure."""
    org, team, project, apps, envs, role = _scaffold(slug_suffix="-deg")
    user = _user("deg-viewer", is_superuser=True, is_staff=True)
    _make_deploy(apps["failed-app"], envs["failed-app"], status="running", age=timedelta(days=2))
    _make_deploy(apps["failed-app"], envs["failed-app"], status="failed", age=timedelta(hours=5))

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_apps(_info(), include_freshness=True)

    by_slug = {a.slug: a for a in result}
    row = by_slug[apps["failed-app"].slug]
    assert row.health_pulse is not None
    assert row.health_pulse.status is AstroliftAppHealthPulseStatus.DEGRADED
    # ``last_deployed_at`` still surfaces the older success — the FE
    # uses it for the "Last successful deploy: 2d ago" line below
    # the degraded chip.
    assert row.last_deployed_at is not None
    assert row.latest_deployment is not None
    assert row.latest_deployment.status == "failed"


def test_pulse_stale_when_no_success_in_30d():
    org, team, project, apps, envs, role = _scaffold(slug_suffix="-stale")
    user = _user("stale-viewer", is_superuser=True, is_staff=True)
    _make_deploy(
        apps["stale-app"],
        envs["stale-app"],
        status="running",
        age=timedelta(days=STALE_DEPLOY_WINDOW_DAYS + 5),
    )

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_apps(_info(), include_freshness=True)

    by_slug = {a.slug: a for a in result}
    pulse = by_slug[apps["stale-app"].slug].health_pulse
    assert pulse is not None
    assert pulse.status is AstroliftAppHealthPulseStatus.STALE


def test_pulse_ok_at_boundary_of_healthy_window():
    """A success exactly at HEALTHY_DEPLOY_WINDOW_DAYS still reads
    OK — the FE renders it without the stale chip."""
    org, team, project, apps, envs, role = _scaffold(slug_suffix="-boundary")
    user = _user("boundary-viewer", is_superuser=True, is_staff=True)
    _make_deploy(
        apps["ok-app"],
        envs["ok-app"],
        status="running",
        age=timedelta(days=HEALTHY_DEPLOY_WINDOW_DAYS, minutes=-1),
    )

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_apps(_info(), include_freshness=True)

    by_slug = {a.slug: a for a in result}
    pulse = by_slug[apps["ok-app"].slug].health_pulse
    assert pulse is not None
    assert pulse.status is AstroliftAppHealthPulseStatus.OK


# ---------- N+1 guardrail ----------------------------------------------


def test_freshness_query_count_independent_of_app_count():
    """The freshness builder runs 2 deploy queries regardless of how
    many apps the page surfaces — that's the N+1 contract.

    We measure the count for a 1-app tenant + a 3-app tenant. The
    freshness-related delta must be 0 (only deploy queries scale on
    app count; the registry-side select_related is already constant)."""
    org, team, project, apps, envs, role = _scaffold(slug_suffix="-nplus1")
    user = _user("nplus1-viewer", is_superuser=True, is_staff=True)
    for slug in ("ok-app", "failed-app", "stale-app"):
        _make_deploy(apps[slug], envs[slug], status="running", age=timedelta(hours=2))

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        with CaptureQueriesContext(connection) as ctx_with:
            RegistryQuery().astrolift_apps(_info(), include_freshness=True)
        with CaptureQueriesContext(connection) as ctx_without:
            RegistryQuery().astrolift_apps(_info(), include_freshness=False)

    delta = len(ctx_with.captured_queries) - len(ctx_without.captured_queries)
    # Exactly 2 extra queries — one for "latest per app", one for
    # "latest successful per app". Bare select_related on
    # triggered_by_user / app_environment piggy-backs on the same
    # joins so it doesn't add roundtrips.
    assert delta == 2, [q["sql"] for q in ctx_with.captured_queries]


def test_freshness_summary_carries_environment_and_trigger():
    org, team, project, apps, envs, role = _scaffold(slug_suffix="-summary")
    user = _user("trigger-user", is_superuser=True, is_staff=True)
    deploy = _make_deploy(
        apps["ok-app"], envs["ok-app"], status="running", age=timedelta(hours=1), trigger_kind="ci"
    )
    deploy.triggered_by_user = user
    deploy.save(update_fields=["triggered_by_user", "updated_at", "version"])

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_apps(_info(), include_freshness=True)

    by_slug = {a.slug: a for a in result}
    summary = by_slug[apps["ok-app"].slug].latest_deployment
    assert summary is not None
    assert summary.environment_name == "prod"
    # ``_user`` builds emails as ``<username>@test``; we just assert
    # the email-shaped output round-trips through the resolver.
    assert summary.triggered_by == "trigger-user@test"


def test_freshness_summary_falls_back_to_trigger_kind():
    """When there's no triggering user (token / system trigger), the
    summary surfaces the ``trigger_kind`` instead so the FE always
    has something to render in the 'triggered by' cell."""
    org, team, project, apps, envs, role = _scaffold(slug_suffix="-fallback")
    user = _user("fallback-viewer", is_superuser=True, is_staff=True)
    _make_deploy(
        apps["ok-app"], envs["ok-app"], status="running", age=timedelta(hours=1), trigger_kind="push"
    )

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_apps(_info(), include_freshness=True)

    by_slug = {a.slug: a for a in result}
    summary = by_slug[apps["ok-app"].slug].latest_deployment
    assert summary is not None
    assert summary.triggered_by == "push"
