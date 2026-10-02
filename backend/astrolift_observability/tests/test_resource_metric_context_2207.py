"""Real PostgreSQL reviewed resource metrics; no owner/name/environment fallback."""

import pytest
from graphql import GraphQLError

from astrolift_lifecycle.models import AppEnvironment
from astrolift_observability import prom_client
from astrolift_observability.schema.queries import GoldenSignalsQuery
from astrolift_services.models import ManagedService
from astrolift_services.schema.resource_reads import context_revision, context_row
from astrolift_services.tests.test_resource_reads_2207 import grant, subject
from astrolift_services.tests.test_resource_reads_2207 import no_search as no_search
from astrolift_services.tests.test_resource_reads_2207 import world as resource_world_factory
from core.permissions import Permission, PermissionDenied
from core.tests.utils.scope_world import make_info

pytestmark = pytest.mark.django_db
query = GoldenSignalsQuery()


@pytest.fixture
def world():
    return resource_world_factory.__wrapped__()


@pytest.fixture
def target(world):
    grant(world, Permission.APP_READ, kind="ORG", target=world.org.pk)
    world.cluster.provider_config = {"prometheus_endpoint": "http://recorded-cluster.invalid"}
    world.cluster.save()
    world.environment = AppEnvironment.objects.create(
        registered_app=world.medops_app, name="production", tenant_cluster=world.cluster
    )
    world.service = ManagedService.objects.create(
        registered_app=world.medops_app,
        app_environment=world.environment,
        name="database",
        kind="postgres",
        config={"credential": "RESOURCE_METRIC_PRIVATE_MARKER"},
    )
    with subject(world):
        world.revision = context_revision(context_row(world.service.guid))
    return world


def metrics(w):
    return query.astrolift_app_managed_service_metrics(
        make_info(w.user), managed_service_id=str(w.service.guid), expected_context_revision=w.revision
    )


@pytest.mark.parametrize("change", ["owner", "environment", "cluster", "deleted", "replacement"])
def test_changed_binding_refuses_before_endpoint_or_collector(target, monkeypatch, change):
    calls = []
    monkeypatch.setattr(
        prom_client, "resolve_prometheus_endpoint", lambda **kwargs: calls.append("name-resolution")
    )
    monkeypatch.setattr(prom_client, "query_range_series", lambda **kwargs: calls.append(kwargs))
    if change == "owner":
        target.service.registered_app = target.platform_app
        target.service.save()
    elif change == "environment":
        target.service.app_environment = AppEnvironment.objects.create(
            registered_app=target.medops_app, name="replacement", tenant_cluster=target.cluster
        )
        target.service.save()
    elif change == "cluster":
        target.cluster.region = "changed-region"
        target.cluster.save()
    else:
        target.service.delete()
        if change == "replacement":
            ManagedService.objects.create(
                registered_app=target.medops_app,
                app_environment=target.environment,
                name="database",
                kind="postgres",
            )
    with subject(target), pytest.raises(GraphQLError) as error:
        metrics(target)
    assert error.value.extensions["code"] in {"STALE_TARGET", "TARGET_UNAVAILABLE"}
    assert calls == []
    assert "RESOURCE_METRIC_PRIVATE_MARKER" not in str(error.value)


def test_pinned_read_uses_recorded_cluster_without_environment_name_resolution(target, monkeypatch):
    calls = []
    monkeypatch.setattr(
        prom_client, "resolve_prometheus_endpoint", lambda **kwargs: pytest.fail("Name fallback")
    )

    def collect(**kwargs):
        calls.append(kwargs)
        return [("", [(1700000000.0, 3.0)])]

    monkeypatch.setattr(prom_client, "query_range_series", collect)
    with subject(target):
        result = metrics(target)
    assert str(result.managed_service_id) == str(target.service.guid)
    assert len(calls) == 5
    assert {item["endpoint"] for item in calls} == {"http://recorded-cluster.invalid"}
    assert all(str(target.service.guid) in item["promql"] for item in calls)


def test_concurrent_move_during_read_never_follows_new_cluster_or_publishes_samples(target, monkeypatch):
    calls = []

    def collect(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            target.environment.name = "moved-environment"
            target.environment.save()
        return [("", [(1700000000.0, 3.0)])]

    monkeypatch.setattr(prom_client, "query_range_series", collect)
    with subject(target), pytest.raises(GraphQLError) as error:
        metrics(target)
    assert error.value.extensions["code"] == "STALE_TARGET"
    assert {item["endpoint"] for item in calls} == {"http://recorded-cluster.invalid"}


def test_revoked_role_refuses_before_collector(target, monkeypatch):
    from astrolift_identity.models import RoleBinding

    calls = []
    monkeypatch.setattr(prom_client, "query_range_series", lambda **kwargs: calls.append(kwargs))
    RoleBinding.objects.filter(user=target.user).delete()
    with subject(target), pytest.raises(PermissionDenied):
        metrics(target)
    assert calls == []
