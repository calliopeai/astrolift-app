"""Pure-builder tests for ``astrolift_observability.prom_queries``.

Each builder returns a ``QueryPlan`` — we assert the rendered PromQL
matches the expected literal for representative inputs so a refactor
that changes spacing / quoting / label ordering trips a test.
"""

from __future__ import annotations

import pytest

from astrolift_observability import prom_queries
from astrolift_operations.prometheus_client import PrometheusQueryError

# ----------------------------------------------------------------------
# rate-window + step helpers
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "range_seconds,expected_window",
    [
        (15 * 60, "1m"),  # 15m → 1m
        (60 * 60, "1m"),  # 1h  → 1m
        (6 * 60 * 60, "5m"),  # 6h  → 5m
        (24 * 60 * 60, "5m"),  # 24h → 5m
        (7 * 86400, "15m"),  # 7d  → 15m
        (30 * 86400, "1h"),  # 30d → 1h
    ],
)
def test_pick_rate_window(range_seconds: int, expected_window: str) -> None:
    assert prom_queries.pick_rate_window(range_seconds) == expected_window


@pytest.mark.parametrize(
    "range_seconds,expected_min_step",
    [
        (15 * 60, 15),  # tiny window clamps to floor
        (60 * 60, 15),
        (6 * 60 * 60, 60),  # 6h / 360 = 60s
        (24 * 60 * 60, 240),  # 24h / 360 = 240s
    ],
)
def test_pick_step_seconds(range_seconds: int, expected_min_step: int) -> None:
    step = prom_queries.pick_step_seconds(range_seconds, max_points=360)
    assert step == expected_min_step


def test_pick_step_seconds_rejects_non_positive() -> None:
    with pytest.raises(ValueError):
        prom_queries.pick_step_seconds(0)


# ----------------------------------------------------------------------
# request-rate / traffic
# ----------------------------------------------------------------------


def test_request_rate_query_renders_expected_promql() -> None:
    plan = prom_queries.build_request_rate_query(
        app_slug="hello-world",
        environment_name="prod",
        range_seconds=60 * 60,
    )
    assert plan.rate_window == "1m"
    assert plan.labels == {"app": "hello-world", "environment": "prod"}
    assert plan.promql == ('sum(rate(http_requests_total{app="hello-world",environment="prod"}[1m]))')


def test_request_rate_query_without_environment() -> None:
    plan = prom_queries.build_request_rate_query(
        app_slug="hello-world",
        environment_name=None,
        range_seconds=60 * 60,
    )
    assert plan.labels == {"app": "hello-world"}
    assert plan.promql == 'sum(rate(http_requests_total{app="hello-world"}[1m]))'


# ----------------------------------------------------------------------
# error-rate
# ----------------------------------------------------------------------


def test_error_rate_query_uses_clamp_min_and_5xx_matcher() -> None:
    plan = prom_queries.build_error_rate_query(
        app_slug="hello-world",
        environment_name="prod",
        range_seconds=60 * 60,
    )
    assert plan.promql == (
        'sum(rate(http_requests_total{app="hello-world",environment="prod",code=~"5.."}[1m])) '
        '/ clamp_min(sum(rate(http_requests_total{app="hello-world",environment="prod"}[1m])), 1e-9)'
    )


# ----------------------------------------------------------------------
# latency quantiles
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "quantile,expected_q_str",
    [
        (0.50, "0.5"),
        (0.90, "0.9"),
        (0.99, "0.99"),
    ],
)
def test_latency_quantile_query(quantile: float, expected_q_str: str) -> None:
    plan = prom_queries.build_latency_quantile_query(
        app_slug="hello-world",
        environment_name="prod",
        range_seconds=60 * 60,
        quantile=quantile,
    )
    assert plan.promql == (
        f"histogram_quantile({expected_q_str}, "
        f"sum by (le)(rate(http_request_duration_seconds_bucket"
        f'{{app="hello-world",environment="prod"}}[1m])))'
    )


def test_latency_quantile_query_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        prom_queries.build_latency_quantile_query(
            app_slug="hello-world",
            environment_name=None,
            range_seconds=60 * 60,
            quantile=1.5,
        )


# ----------------------------------------------------------------------
# CPU saturation
# ----------------------------------------------------------------------


def test_cpu_saturation_query_renders_expected_promql() -> None:
    plan = prom_queries.build_cpu_saturation_query(
        app_slug="hello-world",
        environment_name="prod",
        range_seconds=6 * 60 * 60,  # 6h → 5m rate window
    )
    assert plan.rate_window == "5m"
    assert plan.promql == (
        'sum(rate(container_cpu_usage_seconds_total{app="hello-world",environment="prod"}[5m])) '
        "/ (sum(kube_pod_container_resource_limits"
        '{app="hello-world",environment="prod",resource="cpu"}) > 0)'
    )


# ----------------------------------------------------------------------
# status-code breakdown
# ----------------------------------------------------------------------


def test_status_code_breakdown_query_aggregates_by_code() -> None:
    plan = prom_queries.build_status_code_breakdown_query(
        app_slug="hello-world",
        environment_name="prod",
        range_seconds=60 * 60,
    )
    assert plan.promql == (
        'sum by (code) (rate(http_requests_total{app="hello-world",environment="prod"}[1m]))'
    )


# ----------------------------------------------------------------------
# label-value sanitization
# ----------------------------------------------------------------------


def test_builder_rejects_unsafe_app_slug() -> None:
    """A rogue caller can't smuggle a quote into the PromQL label
    match — the sanitizer rejects anything outside the allow-list."""
    with pytest.raises(PrometheusQueryError):
        prom_queries.build_request_rate_query(
            app_slug='hello"; drop table apps; --',
            environment_name=None,
            range_seconds=60 * 60,
        )


def test_builder_rejects_unsafe_environment_name() -> None:
    with pytest.raises(PrometheusQueryError):
        prom_queries.build_request_rate_query(
            app_slug="hello-world",
            environment_name='prod"',
            range_seconds=60 * 60,
        )


# ----------------------------------------------------------------------
# workload scoping (#422)
# ----------------------------------------------------------------------


def test_request_rate_query_appends_workload_label() -> None:
    """A non-null ``workload_slug`` adds a ``workload="..."`` matcher
    so the Prometheus query narrows from app-roll-up to one workload."""
    plan = prom_queries.build_request_rate_query(
        app_slug="hello-world",
        environment_name="prod",
        range_seconds=60 * 60,
        workload_slug="api",
    )
    assert plan.labels == {
        "app": "hello-world",
        "environment": "prod",
        "workload": "api",
    }
    # Keys render in sorted order so the diff is predictable.
    assert plan.promql == (
        'sum(rate(http_requests_total{app="hello-world",environment="prod",workload="api"}[1m]))'
    )


def test_error_rate_query_with_workload() -> None:
    plan = prom_queries.build_error_rate_query(
        app_slug="hello-world",
        environment_name="prod",
        range_seconds=60 * 60,
        workload_slug="worker",
    )
    assert plan.promql == (
        'sum(rate(http_requests_total{app="hello-world",environment="prod",workload="worker",code=~"5.."}[1m])) '
        '/ clamp_min(sum(rate(http_requests_total{app="hello-world",environment="prod",workload="worker"}[1m])), 1e-9)'
    )


def test_latency_quantile_with_workload() -> None:
    plan = prom_queries.build_latency_quantile_query(
        app_slug="hello-world",
        environment_name="prod",
        range_seconds=60 * 60,
        quantile=0.99,
        workload_slug="api",
    )
    assert plan.promql == (
        "histogram_quantile(0.99, "
        "sum by (le)(rate(http_request_duration_seconds_bucket"
        '{app="hello-world",environment="prod",workload="api"}[1m])))'
    )


def test_cpu_saturation_with_workload() -> None:
    plan = prom_queries.build_cpu_saturation_query(
        app_slug="hello-world",
        environment_name="prod",
        range_seconds=60 * 60,
        workload_slug="scheduler",
    )
    assert plan.promql == (
        'sum(rate(container_cpu_usage_seconds_total{app="hello-world",environment="prod",workload="scheduler"}[1m])) '
        "/ (sum(kube_pod_container_resource_limits"
        '{app="hello-world",environment="prod",workload="scheduler",resource="cpu"}) > 0)'
    )


def test_status_code_breakdown_with_workload() -> None:
    plan = prom_queries.build_status_code_breakdown_query(
        app_slug="hello-world",
        environment_name="prod",
        range_seconds=60 * 60,
        workload_slug="api",
    )
    assert plan.promql == (
        'sum by (code) (rate(http_requests_total{app="hello-world",environment="prod",workload="api"}[1m]))'
    )


def test_builder_rejects_unsafe_workload_slug() -> None:
    """The workload slug rides the same sanitizer as the app + env
    labels — a quote / backslash can't break out of the matcher."""
    with pytest.raises(PrometheusQueryError):
        prom_queries.build_request_rate_query(
            app_slug="hello-world",
            environment_name="prod",
            range_seconds=60 * 60,
            workload_slug='api"; delete from workloads; --',
        )


# ----------------------------------------------------------------------
# Workload resource-usage builders (#430)
# ----------------------------------------------------------------------


def test_workload_cpu_usage_query_uses_1m_rate_window() -> None:
    plan = prom_queries.build_workload_cpu_usage_query(
        app_slug="hello-world",
        environment_name="prod",
        workload_slug="api",
    )
    assert plan.rate_window == "1m"
    assert plan.labels == {"app": "hello-world", "environment": "prod", "workload": "api"}
    assert plan.promql == (
        'sum(rate(container_cpu_usage_seconds_total{app="hello-world",environment="prod",workload="api"}[1m]))'
    )


def test_workload_memory_usage_query_uses_working_set_bytes() -> None:
    plan = prom_queries.build_workload_memory_usage_query(
        app_slug="hello-world",
        environment_name="prod",
        workload_slug="api",
    )
    assert plan.rate_window == "instant"
    assert plan.promql == (
        'sum(container_memory_working_set_bytes{app="hello-world",environment="prod",workload="api"})'
    )


def test_workload_resource_request_query_cpu() -> None:
    plan = prom_queries.build_workload_resource_request_query(
        app_slug="hello-world",
        environment_name="prod",
        workload_slug="api",
        resource="cpu",
    )
    assert plan.promql == (
        "sum(kube_pod_container_resource_requests"
        '{app="hello-world",environment="prod",workload="api",resource="cpu"})'
    )


def test_workload_resource_limit_query_memory() -> None:
    plan = prom_queries.build_workload_resource_limit_query(
        app_slug="hello-world",
        environment_name="prod",
        workload_slug="api",
        resource="memory",
    )
    assert plan.promql == (
        "sum(kube_pod_container_resource_limits"
        '{app="hello-world",environment="prod",workload="api",resource="memory"})'
    )


def test_workload_request_query_rejects_bad_resource() -> None:
    with pytest.raises(ValueError):
        prom_queries.build_workload_resource_request_query(
            app_slug="hello-world",
            environment_name="prod",
            workload_slug="api",
            resource="disk",
        )


@pytest.mark.parametrize(
    ("metric", "fragment"),
    [
        ("tokens_per_second", "rate(vllm:generation_tokens_total{"),
        ("requests_running", "sum(vllm:num_requests_running{"),
        ("requests_waiting", "sum(vllm:num_requests_waiting{"),
        ("ttft_p95", "histogram_quantile(0.95"),
        ("kv_cache_usage", "vllm:kv_cache_usage_perc{"),
    ],
)
def test_model_endpoint_metrics_are_scoped_to_the_service(metric, fragment):
    from astrolift_observability.prom_queries import build_managed_service_model_endpoint_query

    plan = build_managed_service_model_endpoint_query(
        service_guid="abc-123", metric=metric, range_seconds=3600
    )
    assert fragment in plan.promql
    assert 'managed_service="abc-123"' in plan.promql


def test_unknown_model_endpoint_metric_is_refused():
    from astrolift_observability.prom_queries import build_managed_service_model_endpoint_query

    with pytest.raises(ValueError):
        build_managed_service_model_endpoint_query(service_guid="g", metric="nope", range_seconds=60)
