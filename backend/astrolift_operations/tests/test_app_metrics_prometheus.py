"""End-to-end tests for the Prometheus path on
``astroliftAppMetrics`` (#297).

These layer on top of ``test_app_metrics_query.py`` (which covers
the synthetic fallback). Here we wire a cluster's
``provider_config['prometheus_endpoint']`` and assert the resolver
issues the right PromQL, parses the response into AppMetricsType,
and labels the source correctly.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_operations import prometheus_client
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    prometheus_client.clear_cache_for_tests()
    yield
    prometheus_client.clear_cache_for_tests()


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None)))


def _scaffold(prometheus_endpoint: str | None, *, observability_kind: str | None = None):
    org = Organization.objects.create(name="Acme", slug="acme-prom")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-prom")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-prom")
    plugin = ProviderPlugin(
        name="K8s",
        slug="k8s-prom",
        version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    plugin = ProviderPlugin.objects.get(slug="k8s-prom")
    provider_config: dict = {}
    if prometheus_endpoint:
        provider_config["prometheus_endpoint"] = prometheus_endpoint
    if observability_kind:
        provider_config["observability_kind"] = observability_kind
    cluster = TenantCluster.objects.create(
        organization=org,
        name="dev",
        slug="dev-prom",
        provider_plugin=plugin,
        provider_config=provider_config,
        endpoint="https://k8s.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-prom",
        k8s_namespace="acme-hello",
        provisioning_status="ready",
    )
    AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://hello.example.com",
    )
    return org, app


def _tenant(org):
    return tenant_context(TenantContext(organization_id=org.id))


class _FakeResp:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


def test_resolver_falls_back_to_synthetic_without_endpoint(permission_resolver):
    org, app = _scaffold(prometheus_endpoint=None)
    permission_resolver.grant(Permission.APP_READ_METRICS)
    with _tenant(org):
        result = OperationsQuery().astrolift_app_metrics(_info(), app_slug=app.slug)
    assert result is not None
    assert result.source == "synthetic"


def test_resolver_issues_promql_against_endpoint(monkeypatch, permission_resolver):
    org, app = _scaffold(prometheus_endpoint="http://prom.acme:9090")
    permission_resolver.grant(Permission.APP_READ_METRICS)

    captured_queries: list[str] = []

    def fake_urlopen(req, timeout=5.0):
        captured_queries.append(req.full_url)
        # Distinguish instant vs range so we return the right shape.
        if "query_range" in req.full_url:
            return _FakeResp(
                {
                    "status": "success",
                    "data": {
                        "resultType": "matrix",
                        "result": [
                            {
                                "metric": {"app": "hello-prom"},
                                "values": [
                                    [1700000000, "12.5"],
                                ],
                            }
                        ],
                    },
                }
            )
        # Instant vector for a fixed scalar — the resolver sums series.
        return _FakeResp(
            {
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [{"metric": {}, "value": [1700000000, "10.0"]}],
                },
            }
        )

    monkeypatch.setattr(
        "astrolift_operations.prometheus_client.urllib.request.urlopen",
        fake_urlopen,
    )
    with _tenant(org):
        result = OperationsQuery().astrolift_app_metrics(
            _info(),
            app_slug=app.slug,
            time_range="1h",
        )
    assert result is not None
    assert result.source == "prometheus"
    assert result.request_rate == 10.0  # from instant query
    # All five instant queries + three range queries fired.
    instant = [u for u in captured_queries if "/api/v1/query?" in u]
    ranged = [u for u in captured_queries if "query_range" in u]
    assert len(instant) == 5
    assert len(ranged) == 3
    # The app label is on every query — namespace too since k8s_namespace is set.
    joined = "\n".join(captured_queries)
    assert "app%3D%22hello-prom%22" in joined  # urlencoded app="hello-prom"
    assert "namespace%3D%22acme-hello%22" in joined


def test_resolver_marks_source_otel_when_cluster_says_so(monkeypatch, permission_resolver):
    org, app = _scaffold(
        prometheus_endpoint="http://otel.acme:4318",
        observability_kind="otel",
    )
    permission_resolver.grant(Permission.APP_READ_METRICS)

    def fake_urlopen(req, timeout=5.0):
        if "query_range" in req.full_url:
            return _FakeResp({"status": "success", "data": {"resultType": "matrix", "result": []}})
        return _FakeResp({"status": "success", "data": {"resultType": "vector", "result": []}})

    monkeypatch.setattr(
        "astrolift_operations.prometheus_client.urllib.request.urlopen",
        fake_urlopen,
    )
    with _tenant(org):
        result = OperationsQuery().astrolift_app_metrics(_info(), app_slug=app.slug)
    assert result is not None
    assert result.source == "otel"


def test_resolver_falls_back_to_synthetic_on_unreachable_endpoint(monkeypatch, permission_resolver):
    org, app = _scaffold(prometheus_endpoint="http://does-not-resolve")
    permission_resolver.grant(Permission.APP_READ_METRICS)
    from urllib.error import URLError

    def fake_urlopen(req, timeout=5.0):
        raise URLError("no route to host")

    monkeypatch.setattr(
        "astrolift_operations.prometheus_client.urllib.request.urlopen",
        fake_urlopen,
    )
    with _tenant(org):
        result = OperationsQuery().astrolift_app_metrics(_info(), app_slug=app.slug)
    assert result is not None
    assert result.source == "synthetic"


def test_resolver_falls_back_to_synthetic_on_promql_error(monkeypatch, permission_resolver):
    org, app = _scaffold(prometheus_endpoint="http://prom.acme:9090")
    permission_resolver.grant(Permission.APP_READ_METRICS)

    def fake_urlopen(req, timeout=5.0):
        # Prometheus returns 200 + status=error on parse failures —
        # the resolver should still fall back.
        return _FakeResp({"status": "error", "error": "parse error"})

    monkeypatch.setattr(
        "astrolift_operations.prometheus_client.urllib.request.urlopen",
        fake_urlopen,
    )
    with _tenant(org):
        result = OperationsQuery().astrolift_app_metrics(_info(), app_slug=app.slug)
    assert result is not None
    assert result.source == "synthetic"


def test_resolver_cache_dedupes_within_window(monkeypatch, permission_resolver):
    """Two calls inside the cache TTL share Prometheus requests."""
    org, app = _scaffold(prometheus_endpoint="http://prom.acme:9090")
    permission_resolver.grant(Permission.APP_READ_METRICS)

    counter = {"n": 0}

    def fake_urlopen(req, timeout=5.0):
        counter["n"] += 1
        if "query_range" in req.full_url:
            return _FakeResp({"status": "success", "data": {"resultType": "matrix", "result": []}})
        return _FakeResp({"status": "success", "data": {"resultType": "scalar", "result": [1, "1.0"]}})

    monkeypatch.setattr(
        "astrolift_operations.prometheus_client.urllib.request.urlopen",
        fake_urlopen,
    )
    with _tenant(org):
        OperationsQuery().astrolift_app_metrics(_info(), app_slug=app.slug, time_range="1h")
        first = counter["n"]
        OperationsQuery().astrolift_app_metrics(_info(), app_slug=app.slug, time_range="1h")

    # Second call hit the TTL cache — counter shouldn't have moved.
    assert counter["n"] == first
