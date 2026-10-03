from copy import deepcopy

from astrolift_observability import prom_queries

from _sdk.edge_metrics import envoy_metrics_for_routes
from k8s_native.edge_gateway import render_app_routes


def routes(namespace, workload="web"):
    return [
        r
        for r in render_app_routes(
            app_slug="app",
            namespace=namespace,
            workloads={workload: (["app.example.net"], 8080)},
            gated=False,
            paused=False,
        )
        if r["kind"] == "HTTPRoute"
    ]


def test_exact_routes_exclude_siblings_and_convert_envoy_milliseconds():
    owned = routes("team-app")
    sibling = routes("team-app-sibling")
    mapping = envoy_metrics_for_routes(owned + sibling, "team-app")
    assert mapping is not None
    kwargs = dict(app_slug="app", environment_name="production", range_seconds=3600, namespace="team-app", edge=mapping)
    traffic = prom_queries.build_request_rate_query(**kwargs).promql
    assert 'envoy_cluster_name=~"httproute/astrolift-edge/(team-app-web)/rule/[0-9]+"' in traffic
    assert "sibling" not in traffic
    errors = prom_queries.build_error_rate_query(**kwargs).promql
    assert 'envoy_response_code=~"5.."' in errors
    assert 'envoy_cluster_name=~"' in errors
    assert "or vector(0)" in errors
    latency = prom_queries.build_latency_quantile_query(**kwargs, quantile=0.95).promql
    assert "envoy_cluster_upstream_rq_time_bucket" in latency
    assert " / 1000) and on()" in latency
    assert 'le="+Inf"' in latency
    assert latency.endswith(" > 0)")
    breakdown = prom_queries.build_status_code_breakdown_query(**kwargs)
    assert breakdown.group_label == "envoy_response_code"
    assert "sum by (envoy_response_code)" in breakdown.promql


def test_route_labels_cannot_authorize_foreign_or_unmanaged_backends():
    owned = routes("team-app")
    foreign = deepcopy(owned)
    foreign[0]["spec"]["rules"][0]["backendRefs"][0]["namespace"] = "another-app"
    assert envoy_metrics_for_routes(foreign, "team-app") is None
    unmanaged = deepcopy(owned)
    unmanaged[0]["metadata"]["labels"].pop("astrolift.io/managed-by")
    assert envoy_metrics_for_routes(unmanaged, "team-app") is None
    assert envoy_metrics_for_routes(owned, "another-app") is None


def test_hashed_route_names_remain_exactly_owned():
    namespace = "team-" + "a" * 50
    owned = routes(namespace, "long-workload-name")
    mapping = envoy_metrics_for_routes(owned, namespace)
    assert mapping is not None
    assert owned[0]["metadata"]["name"] in mapping.namespace_value
    assert namespace + "-.*" not in mapping.namespace_value
