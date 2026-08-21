"""Edge-sourced golden signals (#1224, spec 08 §6.1).

The request-path golden signals (traffic / errors / latency / status
breakdown) come from the ingress controller's metrics keyed by the app's
Kubernetes namespace — an app with zero instrumentation gets full RED
panels. These tests pin:

* the variant registry (``providers._sdk.edge_metrics``) and the
  ingress_class → variant → mapping resolution,
* the edge-shaped PromQL each builder emits for the nginx_ingress
  variant (and that the legacy app-metric shape survives untouched for
  variants without a mapping),
* ``resolve_edge_metrics`` walking the app's env → cluster to pick the
  mapping off ``TenantCluster.ingress_class``.
"""

from __future__ import annotations

import pytest

from astrolift_observability import prom_client, prom_queries
from providers._sdk.edge_metrics import (
    AWS_ALB_CONTROLLER,
    EDGE_METRICS_BY_VARIANT,
    NGINX_INGRESS,
    edge_metrics_for_ingress_class,
    variant_for_ingress_class,
)

# ---------------------------------------------------------------------------
# Registry / resolution (pure)
# ---------------------------------------------------------------------------


def test_nginx_variant_registered():
    assert EDGE_METRICS_BY_VARIANT["nginx_ingress"] is NGINX_INGRESS
    assert NGINX_INGRESS.requests_total == "nginx_ingress_controller_requests"
    assert NGINX_INGRESS.duration_bucket == "nginx_ingress_controller_request_duration_seconds_bucket"
    assert NGINX_INGRESS.namespace_label == "exported_namespace"
    assert NGINX_INGRESS.status_label == "status"


@pytest.mark.parametrize(
    ("ingress_class", "variant"),
    [
        ("nginx", "nginx_ingress"),
        ("ingress-nginx", "nginx_ingress"),
        ("NGINX", "nginx_ingress"),  # case-insensitive
        ("traefik", "traefik"),
        ("alb", "aws_alb_controller"),
        ("haproxy", None),  # unknown class: parity gap, no default
        ("", None),
        (None, None),
    ],
)
def test_variant_for_ingress_class(ingress_class, variant):
    assert variant_for_ingress_class(ingress_class) == variant


def test_edge_metrics_resolution_per_variant():
    """Mapped variants resolve; unmapped ones (traefik) return None — a
    recorded parity gap, never a wrong mapping."""
    assert edge_metrics_for_ingress_class("nginx") is NGINX_INGRESS
    assert edge_metrics_for_ingress_class("alb") is AWS_ALB_CONTROLLER
    assert edge_metrics_for_ingress_class("traefik") is None


# ---------------------------------------------------------------------------
# Builders (pure)
# ---------------------------------------------------------------------------

_NS = "acme-hello-app"


def test_request_rate_edge_shape():
    plan = prom_queries.build_request_rate_query(
        app_slug="hello-app",
        environment_name="prod",
        range_seconds=3600,
        edge=NGINX_INGRESS,
        namespace=_NS,
    )
    assert plan.promql == (f'sum(rate(nginx_ingress_controller_requests{{exported_namespace="{_NS}"}}[1m]))')
    assert plan.labels == {"exported_namespace": _NS}


def test_error_rate_edge_shape_uses_variant_status_label():
    plan = prom_queries.build_error_rate_query(
        app_slug="hello-app",
        environment_name=None,
        range_seconds=3600,
        edge=NGINX_INGRESS,
        namespace=_NS,
    )
    assert 'status=~"5.."' in plan.promql
    assert "nginx_ingress_controller_requests" in plan.promql
    assert "http_requests_total" not in plan.promql
    assert "clamp_min" in plan.promql


def test_latency_edge_shape():
    plan = prom_queries.build_latency_quantile_query(
        app_slug="hello-app",
        environment_name=None,
        range_seconds=3600,
        quantile=0.95,
        edge=NGINX_INGRESS,
        namespace=_NS,
    )
    assert plan.promql == (
        "histogram_quantile(0.95, sum by (le)(rate("
        f'nginx_ingress_controller_request_duration_seconds_bucket{{exported_namespace="{_NS}"}}[1m])))'
    )


def test_status_breakdown_edge_groups_by_variant_label():
    plan = prom_queries.build_status_code_breakdown_query(
        app_slug="hello-app",
        environment_name=None,
        range_seconds=3600,
        edge=NGINX_INGRESS,
        namespace=_NS,
    )
    assert plan.promql.startswith("sum by (status) (rate(nginx_ingress_controller_requests")
    assert plan.group_label == "status"


def test_builders_without_edge_keep_legacy_shape():
    """No mapping (edge=None) → the pre-#1224 app-instrumentation PromQL,
    including its group label, byte-for-byte."""
    rate = prom_queries.build_request_rate_query(
        app_slug="hello-app",
        environment_name="prod",
        range_seconds=3600,
    )
    assert rate.promql == 'sum(rate(http_requests_total{app="hello-app",environment="prod"}[1m]))'
    breakdown = prom_queries.build_status_code_breakdown_query(
        app_slug="hello-app",
        environment_name=None,
        range_seconds=3600,
    )
    assert breakdown.group_label == "code"
    assert "sum by (code)" in breakdown.promql


def test_edge_namespace_is_sanitized():
    """The sanitizer REJECTS values that could break out of the label
    match — same contract as every other builder input."""
    from astrolift_operations.prometheus_client import PrometheusQueryError

    with pytest.raises(PrometheusQueryError):
        prom_queries.build_request_rate_query(
            app_slug="hello-app",
            environment_name=None,
            range_seconds=3600,
            edge=NGINX_INGRESS,
            namespace='bad"ns\\injection',
        )


# ---------------------------------------------------------------------------
# resolve_edge_metrics (DB)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resolve_edge_metrics_from_cluster_ingress_class():
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_identity.models import Organization, Project, Team
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp

    org = Organization.objects.create(name="Acme", slug="acme-em")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-em")
    project = Project.objects.create(organization=org, team=team, name="P", slug="p-em")
    plugin = ProviderPlugin(
        name="Test Provider",
        slug="test-provider-em",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    plugin = ProviderPlugin.objects.get(slug="test-provider-em")

    def _cluster(slug, ingress_class):
        return TenantCluster.objects.create(
            organization=org,
            name=slug,
            slug=slug,
            provider_plugin=plugin,
            provider_config={},
            ingress_class=ingress_class,
            auth_method=TenantCluster.AuthMethod.KUBECONFIG,
            auth_config={},
            lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        )

    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug="app-em",
    )
    AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=_cluster("nginx-em", "nginx"),
        name="prod",
    )
    AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=_cluster("alb-em", "alb"),
        name="staging",
    )

    assert prom_client.resolve_edge_metrics(app=app, environment_name="prod") is NGINX_INGRESS
    # alb resolves to the CloudWatch-exporter-backed mapping (#1225).
    assert prom_client.resolve_edge_metrics(app=app, environment_name="staging") is AWS_ALB_CONTROLLER
    # No matching env at all.
    assert prom_client.resolve_edge_metrics(app=app, environment_name="nope") is None


# ---------------------------------------------------------------------------
# cloudwatch_gauge style (aws_alb_controller via YACE, #1225)
# ---------------------------------------------------------------------------


def test_alb_traffic_is_period_normalized_gauge_sum():
    plan = prom_queries.build_request_rate_query(
        app_slug="hello-app",
        environment_name=None,
        range_seconds=3600,
        edge=AWS_ALB_CONTROLLER,
        namespace=_NS,
    )
    assert plan.promql == (
        f'sum(aws_applicationelb_request_count_sum{{tag_ingress_k8s_aws_stack=~"{_NS}/.*"}}) / 60'
    )


def test_alb_error_rate_uses_separate_5xx_metric_with_zero_guard():
    plan = prom_queries.build_error_rate_query(
        app_slug="hello-app",
        environment_name=None,
        range_seconds=3600,
        edge=AWS_ALB_CONTROLLER,
        namespace=_NS,
    )
    assert plan.promql == (
        f'(sum(aws_applicationelb_httpcode_target_5_xx_count_sum{{tag_ingress_k8s_aws_stack=~"{_NS}/.*"}}) or vector(0)) '
        f'/ clamp_min(sum(aws_applicationelb_request_count_sum{{tag_ingress_k8s_aws_stack=~"{_NS}/.*"}}), 1e-9)'
    )


@pytest.mark.parametrize(("quantile", "suffix"), [(0.50, "p50"), (0.90, "p90"), (0.95, "p95"), (0.99, "p99")])
def test_alb_latency_selects_precomputed_statistic(quantile, suffix):
    plan = prom_queries.build_latency_quantile_query(
        app_slug="hello-app",
        environment_name=None,
        range_seconds=3600,
        quantile=quantile,
        edge=AWS_ALB_CONTROLLER,
        namespace=_NS,
    )
    assert plan.promql == (
        f'max(aws_applicationelb_target_response_time_{suffix}{{tag_ingress_k8s_aws_stack=~"{_NS}/.*"}})'
    )


def test_alb_status_breakdown_renders_class_level_series():
    """CloudWatch has no per-code label but one counter per status CLASS —
    the breakdown collapses the metric name into a synthetic ``code`` label
    ("2xx" … "5xx") the resolver buckets directly (#1225)."""
    plan = prom_queries.build_status_code_breakdown_query(
        app_slug="hello-app",
        environment_name=None,
        range_seconds=3600,
        edge=AWS_ALB_CONTROLLER,
        namespace=_NS,
    )
    assert plan.promql == (
        "sum by (code) (label_replace("
        f'{{__name__=~"aws_applicationelb_httpcode_target_[2345]_xx_count_sum",tag_ingress_k8s_aws_stack=~"{_NS}/.*"}}, '
        '"code", "${1}xx", "__name__", ".*_(.)_xx_count_sum")) / 60'
    )
    assert plan.group_label == "code"
    assert "http_requests_total" not in plan.promql


def test_classify_status_code_passes_class_keys_through():
    from astrolift_observability.schema.queries import _classify_status_code

    assert _classify_status_code("2xx") == "2xx"
    assert _classify_status_code("5xx") == "5xx"
    assert _classify_status_code("503") == "5xx"
    assert _classify_status_code("weird") == "other"
