"""``astroliftAppHealthSummary`` — rollup correctness + query budget (#1237).

The resolver used to issue three queries per app inside a Python loop, and
it is capped at 300 apps on a dashboard-path render — up to 901 round-trips
for one page. It now gathers each rollup in a single pass and joins in
Python.

Two things are worth pinning. The query count must not scale with the app
count, because that regresses invisibly: the page still renders, just
slowly, and only under a big org. And the rollup values must be unchanged
by the rewrite — batching a per-row loop is exactly where off-by-one
attribution errors (a deployment credited to the wrong app, a soft-deleted
environment counted) creep in.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _ctx(org, actor=None):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=(actor.id if actor else None)))


def _make_app(org, project, team, slug: str) -> RegisteredApp:
    return RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name=slug,
        slug=slug,
        provisioning_status="ready",
    )


def _env(app, cluster, name: str = "prod") -> AppEnvironment:
    return AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name=name)


def _deploy(app, env, *, status: str, image_tag: str = "v1", age_days: int = 0) -> Deployment:
    d = Deployment.objects.create(registered_app=app, app_environment=env, status=status, image_tag=image_tag)
    if age_days:
        # created_at is auto-set; rewrite it so the 7-day window can be tested.
        Deployment.objects.filter(pk=d.pk).update(created_at=timezone.now() - timedelta(days=age_days))
        d.refresh_from_db()
    return d


def _rows_by_slug(fake_info):
    return {r.app_slug: r for r in LifecycleQuery().astrolift_app_health_summary(fake_info)}


def test_reports_latest_deployment_env_count_and_failure_flag(
    org, project, team, cluster, actor, fake_info, permission_resolver
):
    permission_resolver.grant(Permission.APP_READ)
    app = _make_app(org, project, team, "rollup-app")
    prod = _env(app, cluster, "prod")
    _env(app, cluster, "staging")

    _deploy(app, prod, status=Deployment.Status.FAILED.value, image_tag="v1", age_days=2)
    _deploy(app, prod, status=Deployment.Status.RUNNING.value, image_tag="v2")

    with _ctx(org, actor):
        row = _rows_by_slug(fake_info)["rollup-app"]

    assert row.environment_count == 2
    assert row.latest_deployment_status == Deployment.Status.RUNNING.value
    assert row.latest_image_tag == "v2"
    assert row.has_recent_failure is True


def test_attributes_each_rollup_to_its_own_app(
    org, project, team, cluster, actor, fake_info, permission_resolver
):
    """The batched form joins three separate result sets back onto the app
    list, which is precisely where a row can end up credited to the wrong
    app. Two apps with deliberately different rollups catch that."""
    permission_resolver.grant(Permission.APP_READ)
    a = _make_app(org, project, team, "app-a")
    b = _make_app(org, project, team, "app-b")
    a_env = _env(a, cluster, "prod")
    # app-b's only environment is soft-deleted, so it has a deployment but a
    # zero env count — deliberately the opposite shape to app-a.
    b_env = _env(b, cluster, "prod")
    AppEnvironment.objects.filter(pk=b_env.pk).update(deleted_at=timezone.now())
    _deploy(a, a_env, status=Deployment.Status.RUNNING.value, image_tag="a-v9")
    _deploy(b, b_env, status=Deployment.Status.FAILED.value, image_tag="b-v1")

    with _ctx(org, actor):
        rows = _rows_by_slug(fake_info)

    assert rows["app-a"].environment_count == 1
    assert rows["app-a"].latest_image_tag == "a-v9"
    assert rows["app-a"].has_recent_failure is False

    assert rows["app-b"].environment_count == 0
    assert rows["app-b"].latest_image_tag == "b-v1"
    assert rows["app-b"].has_recent_failure is True


def test_ignores_soft_deleted_environments_and_deployments(
    org, project, team, cluster, actor, fake_info, permission_resolver
):
    permission_resolver.grant(Permission.APP_READ)
    app = _make_app(org, project, team, "soft-deleted")
    live = _env(app, cluster, "prod")
    gone = _env(app, cluster, "old")
    AppEnvironment.objects.filter(pk=gone.pk).update(deleted_at=timezone.now())

    kept = _deploy(app, live, status=Deployment.Status.RUNNING.value, image_tag="kept")
    newer_but_deleted = _deploy(app, live, status=Deployment.Status.FAILED.value, image_tag="deleted")
    Deployment.objects.filter(pk=newer_but_deleted.pk).update(deleted_at=timezone.now())

    with _ctx(org, actor):
        row = _rows_by_slug(fake_info)["soft-deleted"]

    assert row.environment_count == 1, "counted a soft-deleted environment"
    assert live.name == "prod"
    # The soft-deleted deploy is newer; DISTINCT ON must not pick it up.
    assert row.latest_image_tag == kept.image_tag
    assert row.has_recent_failure is False


def test_old_failure_falls_outside_the_recent_window(
    org, project, team, cluster, actor, fake_info, permission_resolver
):
    permission_resolver.grant(Permission.APP_READ)
    app = _make_app(org, project, team, "old-failure")
    _deploy(app, _env(app, cluster), status=Deployment.Status.FAILED.value, age_days=30)

    with _ctx(org, actor):
        row = _rows_by_slug(fake_info)["old-failure"]

    assert row.has_recent_failure is False


def test_app_with_no_deployments_still_appears(org, project, team, actor, fake_info, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    _make_app(org, project, team, "never-deployed")

    with _ctx(org, actor):
        row = _rows_by_slug(fake_info)["never-deployed"]

    assert row.latest_deployment_status is None
    assert row.latest_image_tag == ""
    assert row.last_deployed_at is None
    assert row.environment_count == 0


def test_query_count_does_not_scale_with_app_count(
    org, project, team, cluster, actor, fake_info, permission_resolver
):
    """The #1237 regression guard.

    Measure one app, then measure ten with the same shape. The old loop
    issued three queries per app, so the second figure was ~27 higher.
    Asserting equality rather than a fixed budget keeps this robust against
    unrelated query-count churn while still failing the moment anything
    per-app creeps back into the loop.
    """
    permission_resolver.grant(Permission.APP_READ)

    def _seed(n: int, prefix: str) -> None:
        for i in range(n):
            a = _make_app(org, project, team, f"{prefix}-{i}")
            e = _env(a, cluster, f"env-{i}")
            _deploy(a, e, status=Deployment.Status.RUNNING.value, image_tag=f"v{i}")

    _seed(1, "solo")
    with _ctx(org, actor), CaptureQueriesContext(connection) as one_app:
        LifecycleQuery().astrolift_app_health_summary(fake_info)

    _seed(10, "many")
    with _ctx(org, actor), CaptureQueriesContext(connection) as eleven_apps:
        rows = LifecycleQuery().astrolift_app_health_summary(fake_info)

    assert len(rows) == 11
    assert len(eleven_apps.captured_queries) == len(one_app.captured_queries), (
        f"query count grew with app count: {len(one_app.captured_queries)} for 1 app, "
        f"{len(eleven_apps.captured_queries)} for 11"
    )
