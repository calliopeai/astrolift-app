"""Exact preview logs/history refuse stale binding before any provider read."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from graphql import GraphQLError

from astrolift_lifecycle.models import AppEnvironment, Deployment, PreviewEnvironment
from astrolift_lifecycle.preview_targets import preview_binding
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_observability.schema.log_queries import LogHistoryQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(app, env, org, actor, permission_resolver):
    env.name = "canonical-arbitrary-name"
    env.k8s_namespace = "canonical-preview-namespace"
    env.save()
    preview = PreviewEnvironment.objects.create(
        registered_app=app,
        app_environment=env,
        pr_number=1,
        branch="shared-branch",
        namespace=env.k8s_namespace,
        hostname="reused.example.invalid",
        status="failed",
    )
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.APP_READ_LOGS)
    _, target = preview_binding(preview)
    return SimpleNamespace(
        app=app,
        env=env,
        preview=preview,
        target=target,
        tenant=TenantContext(organization_id=org.pk, actor_user_id=actor.pk),
        info=SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=actor, META={}))),
    )


def route(world, kind, **changes):
    common = {
        "expected_environment_id": world.target.environment_id,
        "if_match_preview_version": world.target.preview_version,
        "if_match_environment_version": world.target.environment_version,
    }
    common.update(changes)
    with tenant_context(world.tenant):
        if kind == "logs":
            now = timezone.now()
            return LogHistoryQuery().astrolift_app_logs(
                world.info,
                app_slug=world.app.slug,
                preview_id=world.preview.guid,
                since=now - timedelta(minutes=5),
                until=now,
                **common,
            )
        return LifecycleQuery().astrolift_preview_deployments_page(
            world.info,
            id=world.preview.guid,
            **common,
        )


def test_logs_use_canonical_namespace_not_url_or_environment_name(world, monkeypatch):
    seen = []
    monkeypatch.setattr("core.cluster_log_query.query_app_logs", lambda **kw: seen.append(kw) or None)
    monkeypatch.setattr(
        "astrolift_observability.schema.log_queries.resolve_environment",
        lambda **kw: pytest.fail("fallback cluster resolution"),
    )
    world.preview.hostname = world.env.url = "https://reused-production.example.invalid"
    route(world, "logs")
    assert len(seen) == 1
    assert seen[0]["namespace"] == world.target.namespace
    assert seen[0]["cluster"].pk == world.env.tenant_cluster_id
    assert seen[0]["app_slug"] == world.app.slug


@pytest.mark.parametrize("kind", ["logs", "deployments"])
@pytest.mark.parametrize(
    "change",
    [
        "environment_id",
        "preview_version",
        "environment_version",
        "torn_down",
        "deleted_environment",
        "namespace",
        "replaced_fk",
    ],
)
def test_stale_or_retired_review_never_retargets(world, monkeypatch, kind, change):
    provider_calls = []
    monkeypatch.setattr("core.cluster_log_query.query_app_logs", lambda **kw: provider_calls.append(kw))
    changes = {}
    if change == "environment_id":
        changes["expected_environment_id"] = str(uuid4())
    elif change == "preview_version":
        changes["if_match_preview_version"] = 0
    elif change == "environment_version":
        changes["if_match_environment_version"] = 0
    elif change == "torn_down":
        PreviewEnvironment.objects.filter(pk=world.preview.pk).update(status="torn_down")
    elif change == "deleted_environment":
        world.env.soft_delete()
    elif change == "namespace":
        AppEnvironment.objects.filter(pk=world.env.pk).update(k8s_namespace="other-namespace")
    else:
        world.env.soft_delete()
        replacement = AppEnvironment.objects.create(
            registered_app=world.app,
            tenant_cluster=world.env.tenant_cluster,
            name=world.env.name,
            k8s_namespace=world.env.k8s_namespace,
            url=world.env.url,
        )
        # Even a direct FK edit with no version bump must refuse the old identity.
        PreviewEnvironment.objects.filter(pk=world.preview.pk).update(app_environment=replacement)
    with pytest.raises(GraphQLError) as refused:
        route(world, kind, **changes)
    assert refused.value.extensions == {"code": "PRECONDITION"}
    assert provider_calls == []


@pytest.mark.parametrize(
    "field", ["expected_environment_id", "if_match_preview_version", "if_match_environment_version"]
)
def test_partial_log_proof_refuses_before_any_cluster_read(world, monkeypatch, field):
    monkeypatch.setattr("core.cluster_log_query.query_app_logs", lambda **kw: pytest.fail("provider read"))
    with pytest.raises(GraphQLError):
        route(world, "logs", **{field: None})


def test_environment_name_cannot_override_exact_log_target(world, monkeypatch):
    monkeypatch.setattr("core.cluster_log_query.query_app_logs", lambda **kw: pytest.fail("provider read"))
    with pytest.raises(GraphQLError):
        route(world, "logs", environment_name="production")


def deployment(world, environment=None):
    return Deployment.objects.create(
        registered_app=world.app,
        app_environment=environment or world.env,
        trigger_kind="manual",
        status="running",
        branch="shared-branch",
        pr_number=1,
    )


def test_deployment_history_uses_exact_fk_and_constant_query_count(world):
    first = deployment(world)
    production = AppEnvironment.objects.create(
        registered_app=world.app,
        tenant_cluster=world.env.tenant_cluster,
        name="production",
        url=world.env.url,
    )
    excluded = deployment(world, production)
    with CaptureQueriesContext(connection) as one_queries:
        first_page = route(world, "deployments")
    assert [row.id for row in first_page.items] == [str(first.guid)]
    for _ in range(20):
        deployment(world)
    with CaptureQueriesContext(connection) as many_queries:
        page = route(world, "deployments", limit=30)
    assert len(page.items) == 21
    assert str(excluded.guid) not in {row.id for row in page.items}
    assert {row.environment_id for row in page.items} == {str(world.env.guid)}
    assert len(many_queries) == len(one_queries)
