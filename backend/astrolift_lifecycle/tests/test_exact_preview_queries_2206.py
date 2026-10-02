"""Exact preview reads preserve tenant ownership and opt into bounded pricing."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import PreviewEnvironment
from astrolift_lifecycle.schema import queries
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def info():
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=None, META={})))


@pytest.fixture
def world(app, env, org, actor, permission_resolver):
    env.name = "arbitrary-target"
    env.k8s_namespace = "exact-preview-namespace"
    env.save()
    preview = PreviewEnvironment.objects.create(
        registered_app=app,
        app_environment=env,
        pr_number=1,
        branch="same-branch",
        namespace=env.k8s_namespace,
        hostname="reused.example.invalid",
        status="running",
    )
    permission_resolver.grant(Permission.APP_READ)
    return SimpleNamespace(
        preview=preview,
        app=app,
        env=env,
        tenant=TenantContext(organization_id=org.pk, actor_user_id=actor.pk),
    )


def read(world, id=None, **kwargs):
    with tenant_context(world.tenant):
        return queries.LifecycleQuery().astrolift_preview_environment(
            info(), id=id or world.preview.guid, **kwargs
        )


def forbidden_runtime(monkeypatch):
    calls = []
    monkeypatch.setattr(queries, "list_app_pods", lambda **kwargs: calls.append("pods") or [])
    monkeypatch.setattr(
        "astrolift_lifecycle.preview_cost.estimate_daily_cost", lambda **kwargs: calls.append("pricing")
    )
    return calls


def test_exact_older_than_two_hundred_never_walks_or_prices_catalog(world, monkeypatch):
    PreviewEnvironment.objects.bulk_create(
        [
            PreviewEnvironment(
                registered_app=world.app,
                app_environment=world.env,
                pr_number=n,
                branch="same-branch",
                namespace=world.env.k8s_namespace,
                hostname="reused.example.invalid",
                status="running",
            )
            for n in range(2, 204)
        ]
    )
    calls = forbidden_runtime(monkeypatch)
    monkeypatch.setattr(queries, "_preview_environments_qs", lambda **kwargs: pytest.fail("catalog walk"))
    with CaptureQueriesContext(connection) as captured:
        result = read(world)
    assert result.id == str(world.preview.guid)
    assert result.environment.environment_id == str(world.env.guid)
    assert result.environment.environment_name == "arbitrary-target"
    assert result.environment_status == "available"
    assert result.runtime_status == "not_requested"
    assert calls == []
    assert len(captured) < 12


@pytest.mark.parametrize("change", ["missing", "deleted_preview", "foreign_tenant", "foreign_environment"])
def test_missing_deleted_and_foreign_identity_never_substitutes_another_preview(world, change):
    id = world.preview.guid
    if change == "missing":
        id = uuid4()
    elif change == "deleted_preview":
        world.preview.soft_delete()
    else:
        foreign_org = Organization.objects.create(name="Other preview owner", slug="other-preview-owner")
        foreign_team = Team.objects.create(organization=foreign_org, name="Foreign team", slug="foreign")
        foreign_project = Project.objects.create(
            organization=foreign_org, team=foreign_team, name="Foreign project", slug="foreign"
        )
        foreign_app = RegisteredApp.objects.create(
            organization=foreign_org,
            team=foreign_team,
            project=foreign_project,
            name="Same app",
            slug=world.app.slug,
        )
        if change == "foreign_tenant":
            world.preview.registered_app = foreign_app
            world.preview.save()
        else:
            world.env.registered_app = foreign_app
            world.env.save()
    assert read(world, id) is None


def test_retired_environment_is_an_owned_historical_binding(world):
    world.env.soft_delete()
    result = read(world)
    assert result.id == str(world.preview.guid)
    assert result.environment_status == "retired"
    assert result.environment.environment_id == str(world.env.guid)


def test_denied_permission_never_reads_or_prices_preview(world, permission_resolver, monkeypatch):
    permission_resolver.deny(Permission.APP_READ)
    calls = forbidden_runtime(monkeypatch)
    with pytest.raises(PermissionDenied):
        read(world)
    assert calls == []


def test_basic_catalog_page_never_calls_runtime_even_with_cost_fields(world, monkeypatch):
    calls = forbidden_runtime(monkeypatch)
    with tenant_context(world.tenant):
        page = queries.LifecycleQuery().astrolift_preview_environments_page(info(), limit=3)
        legacy = queries.LifecycleQuery().astrolift_preview_environments(info())
    assert page.items[0].runtime_status == legacy[0].runtime_status == "not_requested"
    assert page.items[0].estimated_daily_cost_usd is None
    assert calls == []


def test_explicit_runtime_cost_prices_one_canonical_target(world, monkeypatch):
    calls = []
    monkeypatch.setattr(
        queries, "list_app_pods", lambda **kwargs: calls.append(("pods", kwargs)) or [object()]
    )
    monkeypatch.setattr(
        "astrolift_lifecycle.preview_cost.aggregate_pod_resources",
        lambda pods: SimpleNamespace(cpu_cores=1.0, memory_bytes=1024.0, pod_count=1),
    )
    monkeypatch.setattr(
        "astrolift_lifecycle.preview_cost.estimate_daily_cost",
        lambda **kwargs: calls.append(("pricing", kwargs))
        or SimpleNamespace(daily_usd=2.3, notes=["explicit provider estimate"], approximate=True),
    )
    result = read(world, include_runtime_cost=True)
    assert result.runtime_status == "available"
    assert result.estimated_daily_cost_usd == 2.3
    assert [kind for kind, _ in calls] == ["pods", "pricing"]
    assert calls[0][1]["namespace"] == world.env.k8s_namespace
    assert calls[0][1]["cluster"].pk == world.env.tenant_cluster_id


@pytest.mark.parametrize("failure", ["retired", "pod_transport"])
def test_requested_runtime_unavailability_is_explicit_never_fabricated_zero_cost(world, monkeypatch, failure):
    calls = forbidden_runtime(monkeypatch)
    if failure == "retired":
        world.env.soft_delete()
    else:

        def unavailable(**kwargs):
            calls.append("pods")
            raise RuntimeError("unavailable transport")

        monkeypatch.setattr(queries, "list_app_pods", unavailable)
    result = read(world, include_runtime_cost=True)
    assert result.runtime_status == "unavailable"
    assert result.estimated_daily_cost_usd is None
    assert result.estimated_cost_notes
    assert calls == ([] if failure == "retired" else ["pods"])
