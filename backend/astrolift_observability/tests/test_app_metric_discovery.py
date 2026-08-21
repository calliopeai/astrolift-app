"""Discovering an app's own metric names (#1226).

The panel this feeds asks "what does my app expose". That is not the same
question as "what series carry my namespace" — the platform puts cAdvisor,
kube-state-metrics and ingress-controller series in there too, and those
already have dedicated panels.
"""

from __future__ import annotations

import pytest

from astrolift_observability.prom_queries import (
    build_namespace_series_match,
    is_app_owned_metric,
)


@pytest.mark.parametrize(
    "name",
    [
        "http_requests_total",
        "orders_processed_total",
        "queue_depth",
        # Boilerplate, but the app's own process emits it and an operator
        # debugging a goroutine leak wants it.
        "go_goroutines",
        "process_resident_memory_bytes",
        # Near-misses that must not be caught by a prefix rule.
        "uptime_seconds",
        "upstream_latency_seconds",
        "container_ship_departures_total",
        "node_count",
        "node_health_score",
    ],
)
def test_an_apps_own_metric_is_kept(name):
    assert is_app_owned_metric(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "container_cpu_usage_seconds_total",
        "container_memory_working_set_bytes",
        "kube_pod_container_resource_limits",
        "node_cpu_seconds_total",
        "scrape_duration_seconds",
        "prometheus_tsdb_head_series",
        "promhttp_metric_handler_requests_total",
        "nginx_ingress_controller_requests",
        "traefik_service_requests_total",
        "aws_applicationelb_request_count_sum",
        "up",
        "ALERTS",
    ],
)
def test_a_platform_family_is_dropped(name):
    assert is_app_owned_metric(name) is False


def test_up_is_exact_not_a_prefix():
    # A prefix rule on "up" would eat the app's own uptime metrics, which
    # is the silent-hiding failure this filter is biased against.
    assert is_app_owned_metric("up") is False
    assert is_app_owned_metric("uptime_seconds") is True
    assert is_app_owned_metric("upload_bytes_total") is True


def test_the_match_pins_the_namespace():
    # Scoping happens at Prometheus, not by filtering a cluster-wide list:
    # an unrestricted __name__ enumeration returns tens of thousands.
    match = build_namespace_series_match("astrolift-shop-prod")

    assert "astrolift-shop-prod" in match
    assert "namespace" in match
