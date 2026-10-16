"""App-instrumentation metric queries carry the app's namespace (#2418 audit).

The RED, status-code and per-route queries over an app's own instrumentation
selected ``app="<slug>"`` (plus ``environment`` / ``workload``) alone. Those
are labels a workload sets on itself, and a slug is unique only within one
org, so on a shared cluster Prometheus another tenant's same-slug app was
read, and mixed in, as this one. The scrape target's namespace now narrows
every one of them, and the legacy ``astroliftAppMetrics`` resolver resolves
the namespace instead of skipping it when the app has no explicit override.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from astrolift_observability import prom_queries
from astrolift_observability.schema.queries import GoldenSignalsQuery
from astrolift_operations.schema.queries import OperationsQuery
from core.cluster_observability import namespace_for_app
from core.permissions import Permission

from .test_endpoint_metrics_748 import _ctx, _info, _scaffold

_COMMON = {"app_slug": "web", "environment_name": "prod", "range_seconds": 3600, "namespace": "acme-web"}


@pytest.mark.parametrize(
    ("builder", "extra"),
    [
        (prom_queries.build_request_rate_query, {}),
        (prom_queries.build_error_rate_query, {}),
        (prom_queries.build_latency_quantile_query, {"quantile": 0.9}),
        (prom_queries.build_status_code_breakdown_query, {}),
        (prom_queries.build_endpoint_request_rate_query, {}),
        (prom_queries.build_endpoint_error_rate_query, {}),
        (prom_queries.build_endpoint_latency_quantile_query, {"quantile": 0.9}),
    ],
)
def test_every_app_instrumentation_selector_carries_the_namespace(builder, extra) -> None:
    plan = builder(**_COMMON, **extra)

    selectors = plan.promql.count("{")
    assert selectors >= 1
    assert plan.promql.count('namespace="acme-web"') == selectors
    assert 'app="web"' in plan.promql


def test_workload_identity_labels_keep_the_namespace() -> None:
    plan = prom_queries.build_request_rate_query(
        **_COMMON, workload_slug="api", identity_labels={"astrolift_workload_id": "w-1"}
    )

    assert 'astrolift_workload_id="w-1"' in plan.promql
    assert 'namespace="acme-web"' in plan.promql


@pytest.mark.django_db
def test_endpoint_metrics_query_the_apps_namespace(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    calls: list[str] = []

    def _qs(*, endpoint, promql, start_unix, end_unix, step_seconds, label_key=None):
        calls.append(promql)
        return []

    with _ctx(org), patch("astrolift_observability.prom_client.query_range_series", side_effect=_qs):
        GoldenSignalsQuery().astrolift_app_endpoint_metrics(_info(), app_slug=app.slug)

    namespace = namespace_for_app(app)
    assert len(calls) == 5
    assert all(f'namespace="{namespace}"' in promql for promql in calls)


@pytest.mark.django_db
def test_app_metrics_scope_to_the_resolved_namespace_without_an_override(permission_resolver, monkeypatch):
    org, app = _scaffold()
    assert not app.k8s_namespace
    permission_resolver.grant(Permission.APP_READ_METRICS)
    urls: list[str] = []

    class _Resp:
        def __init__(self, payload):
            self._body = json.dumps(payload).encode()

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=5.0):
        urls.append(req.full_url)
        kind = "matrix" if "query_range" in req.full_url else "vector"
        return _Resp({"status": "success", "data": {"resultType": kind, "result": []}})

    monkeypatch.setattr("astrolift_operations.prometheus_client.urllib.request.urlopen", fake_urlopen)
    from astrolift_operations import prometheus_client

    prometheus_client.clear_cache_for_tests()
    with _ctx(org):
        OperationsQuery().astrolift_app_metrics(_info(), app_slug=app.slug, time_range="1h")

    encoded = f"namespace%3D%22{namespace_for_app(app)}%22"
    assert urls
    assert all(encoded in url for url in urls)
