"""Reads that name an environment look in that environment's namespace (#1922).

A preview, or an environment created on a cluster another environment of
the app already occupies, renders into a namespace of its own. A read that
names it and still looked in the app namespace would show the primary
environment's pods, logs and metrics under the other environment's name.
A read that names no environment keeps meaning the primary one, in the app
namespace, exactly as before.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import Workload
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

APP_NS = "acme-test-hello-app"
STAGING_NS = "acme-test-hello-app-staging"


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None, META={})))


@pytest.fixture
def staging(app, env, cluster):
    app.default_tenant_cluster = cluster
    app.save(update_fields=["default_tenant_cluster"])
    return AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="staging", k8s_namespace=STAGING_NS
    )


def _tenant(org):
    return tenant_context(TenantContext(organization_id=org.id))


def test_pods_of_a_named_environment_come_from_its_namespace(org, app, env, staging, monkeypatch):
    from astrolift_lifecycle.schema import queries

    seen = []
    monkeypatch.setattr(queries, "list_app_pods", lambda **kw: seen.append(kw["namespace"]) or [])

    queries._list_pods_for_app(app.slug, org_id=org.id, environment_name="staging")
    queries._list_pods_for_app(app.slug, org_id=org.id, environment_name=None)
    queries._list_pods_for_app(app.slug, org_id=org.id, environment_name="prod")

    assert seen == [STAGING_NS, APP_NS, APP_NS]


def test_pod_warnings_of_a_named_environment_come_from_its_namespace(org, app, env, staging, monkeypatch):
    from astrolift_lifecycle.schema import queries

    seen = []
    monkeypatch.setattr(
        "core.cluster_observability.list_app_pod_warning_events",
        lambda **kw: seen.append(kw["namespace"]) or [],
    )

    queries._recent_pod_warnings_for_app(app.slug, org_id=org.id, environment_name="staging")
    queries._recent_pod_warnings_for_app(app.slug, org_id=org.id, environment_name=None)

    assert seen == [STAGING_NS, APP_NS]


def test_the_deregister_preview_lists_each_environments_namespace(
    org, app, env, staging, permission_resolver
):
    from astrolift_lifecycle.schema.queries import LifecycleQuery

    permission_resolver.grant(Permission.APP_DELETE)
    with _tenant(org):
        preview = LifecycleQuery().preview_astrolift_deregister(_info(), app_slug=app.slug)

    namespaces = {obj.namespace for obj in preview.k8s_objects if obj.kind == "Namespace"}
    assert namespaces == {APP_NS, STAGING_NS}


def test_historical_logs_of_a_named_environment_come_from_its_namespace(
    org, app, env, staging, permission_resolver
):
    from astrolift_observability.schema.log_queries import LogHistoryQuery

    permission_resolver.grant(Permission.APP_READ_LOGS)
    seen = []

    def _query(**kw):
        seen.append(kw["namespace"])
        return None

    import datetime as dt

    now = dt.datetime.now(dt.UTC)
    with patch("core.cluster_log_query.query_app_logs", side_effect=_query), _tenant(org):
        for environment_name in ("staging", None):
            LogHistoryQuery().astrolift_app_logs(
                _info(),
                app_slug=app.slug,
                environment_name=environment_name,
                since=now - dt.timedelta(minutes=5),
                until=now,
            )

    assert seen == [STAGING_NS, APP_NS]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_multi_pod_log_stream_of_a_named_environment_reads_its_namespace(permission_resolver):
    from asgiref.sync import sync_to_async

    from astrolift_lifecycle.schema.subscriptions import LifecycleSubscription

    org, app = await sync_to_async(_scaffold_async_world)()
    permission_resolver.grant(Permission.APP_READ_LOGS)
    seen = []

    async def _stream(**kw):
        seen.append(kw["namespace"])
        if False:  # pragma: no cover - an empty stream
            yield None

    with patch("core.cluster_observability.stream_app_logs_multi", _stream), _tenant(org):
        for environment_name in ("staging", None):
            gen = LifecycleSubscription().astrolift_on_app_logs(
                info=_info(), app_slug=app.slug, environment_name=environment_name, follow=False
            )
            async with asyncio.timeout(2.0):
                async for _line in gen:
                    pass

    assert seen == [STAGING_NS, APP_NS]


def _scaffold_async_world():
    """The lifecycle fixtures do not reach a transactional async test, so
    this one builds the same app with a staging environment by hand."""
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_identity.models import Organization, Project, Team
    from astrolift_registry.models import RegisteredApp

    org = Organization.objects.create(name="Acme", slug="acme-test")
    team = Team.objects.create(organization=org, name="Platform", slug="platform")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="T", slug="t-1922", plugin_version="0", capabilities_manifest={}, config_schema={}
            )
        ]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        name="c",
        slug="c-1922",
        provider_plugin=ProviderPlugin.objects.get(slug="t-1922"),
        provider_config={},
        endpoint="https://c.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    app = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="Hello",
        slug="hello-app",
        provisioning_status="ready",
        default_tenant_cluster=cluster,
    )
    AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="prod")
    AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="staging", k8s_namespace=STAGING_NS
    )
    return org, app


def test_a_log_export_of_a_named_environment_reads_its_namespace(org, app, env, staging, permission_resolver):
    from astrolift_operations.schema.mutations.exports import ExportMutations
    from astrolift_operations.schema.mutations.types import ExportAppLogsInput
    from core.cluster_observability import ClusterObservabilityError

    permission_resolver.grant(Permission.APP_LOG_EXPORT)
    seen = []

    def _lines(**kw):
        seen.append(kw["namespace"])
        raise ClusterObservabilityError("stop here")

    with (
        patch("astrolift_operations.schema.mutations.exports._materialize_app_log_lines", side_effect=_lines),
        _tenant(org),
    ):
        for environment_name in ("staging", None):
            ExportMutations().export_astrolift_app_logs(
                _info(),
                input=ExportAppLogsInput(
                    app_slug=app.slug, format="txt", pod_name="web-1", environment_name=environment_name
                ),
            )

    assert seen == [STAGING_NS, APP_NS]


def test_metrics_of_a_named_environment_are_scoped_to_its_namespace(org, app, env, staging):
    from core.cluster_observability import namespace_for_app_environment

    assert namespace_for_app_environment(app, "staging") == STAGING_NS
    assert namespace_for_app_environment(app, "prod") == APP_NS
    assert namespace_for_app_environment(app, None) == APP_NS
    assert namespace_for_app_environment(app, "no-such-env") == APP_NS


def test_golden_signals_of_a_named_environment_query_its_namespace(
    org, app, env, staging, cluster, permission_resolver
):
    from astrolift_observability import prom_client
    from astrolift_observability.schema.queries import GoldenSignalsQuery
    from astrolift_observability.schema.types import GoldenSignalKind

    cluster.provider_config = {"prometheus_endpoint": "http://prom:9090"}
    cluster.save()
    permission_resolver.grant(Permission.APP_READ)

    with patch.object(prom_client, "query_range_series", return_value=[]), _tenant(org):
        result = GoldenSignalsQuery().astrolift_app_golden_signals(
            _info(), app_slug=app.slug, environment_name="staging"
        )

    cpu = next(row for row in result.signals if row.name == GoldenSignalKind.SATURATION_CPU)
    assert f'namespace="{STAGING_NS}"' in cpu.promql


def test_a_cluster_lists_each_environments_namespace_for_its_tenant(org, app, env, staging, cluster):
    from astrolift_clusters.schema.queries import _org_app_namespaces, _primary_app_namespace

    assert _org_app_namespaces(cluster, org.id) == [APP_NS, STAGING_NS]
    assert _primary_app_namespace(cluster) == APP_NS


def test_live_replicas_of_a_named_environment_come_from_its_namespace(app, env, staging, monkeypatch):
    from astrolift_registry.schema.queries import _read_live_replicas

    seen = []

    def _status(cluster_slug, namespace, kind, name):
        seen.append(namespace)
        return SimpleNamespace(ready_replicas=1, desired_replicas=1)

    monkeypatch.setattr(
        "core.cluster_management._driver_for_cluster",
        lambda _cluster: SimpleNamespace(get_workload_status=_status),
    )
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster", lambda _cluster: SimpleNamespace(slug="c")
    )
    workload = Workload.objects.create(registered_app=app, name="web", slug="web", kind="deployment")

    _read_live_replicas(workload=workload, environment_name="staging")
    _read_live_replicas(workload=workload, environment_name=None)

    assert seen == [STAGING_NS, APP_NS]
