"""Edge-metrics mapping — how each ingress variant's native metrics map onto
the standard request-path golden signals (spec 08 §6.1).

The three request-path golden signals (traffic / errors / latency) are
sourced at the EDGE, never from app instrumentation: an app deployed with
zero instrumentation still gets full RED panels. Each ingress variant a
provider plugin supports declares here which of its controller's metrics
carry those signals and which label selects the app (by its Kubernetes
namespace — every Astrolift app owns a namespace, so it is the stable
join key that needs no app cooperation).

The golden-signals query layer (``astrolift_observability.prom_queries``)
resolves metric names through this mapping and never hardcodes one
variant's names. A variant with no mapping yet (e.g. ``alb`` until the
CloudWatch-exporter component lands) is a parity gap to record, not a
license to fall back to app-exposed ``http_*`` metrics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EdgeMetricsMapping:
    """Prometheus coordinates for one ingress variant's edge metrics.

    Two styles exist:

    * ``prom_histogram`` — the controller natively exports Prometheus
      counters + duration histograms (nginx, traefik). Rates come from
      ``rate()``, latency from ``histogram_quantile``, errors from the
      ``status_label`` regex.
    * ``cloudwatch_gauge`` — the edge's metrics are republished from the
      cloud's metrics API by an exporter (ALB via YACE). Series are
      per-period gauges, not counters: traffic is the per-period request
      sum divided by ``period_seconds``, errors are a separate 5xx-count
      metric (no status label), and latency quantiles are pre-computed
      statistic series selected by suffix (``_p50`` … ``_p99``).
    """

    variant: str
    requests_total: str
    """Request-count metric (counter for prom_histogram; per-period sum
    gauge for cloudwatch_gauge)."""

    duration_bucket: str
    """Request-duration histogram ``_bucket`` series (prom_histogram only)."""

    namespace_label: str
    """Label key whose value identifies the app's Kubernetes namespace."""

    status_label: str
    """Label key carrying the upstream HTTP status code (prom_histogram only)."""

    style: str = "prom_histogram"

    errors_total: str = ""
    """cloudwatch_gauge only: per-period 5xx count metric."""

    latency_stat_prefix: str = ""
    """cloudwatch_gauge only: latency metric name minus the statistic
    suffix; the builder appends ``_p50`` / ``_p90`` / ``_p95`` / ``_p99``."""

    period_seconds: int = 60
    """cloudwatch_gauge only: the export period each gauge sums over."""

    namespace_value: str = "{ns}"
    """Template for the ``namespace_label`` match value. ``{ns}`` is the
    app namespace. A template ending in a regex (e.g. ``{ns}/.*`` for the
    ALB controller's ``<namespace>/<ingress>`` stack tag) is matched with
    ``=~`` instead of ``=``."""

    status_class_metric_prefix: str = ""
    """cloudwatch_gauge only: metric-name prefix for the per-class status
    counters — the exporter publishes ``<prefix>_2_xx_count_sum`` …
    ``<prefix>_5_xx_count_sum``. The status breakdown renders class-level
    series ("2xx" … "5xx") from these; empty means the variant has no
    breakdown source."""

    latency_divisor: int = 1


def envoy_metrics_for_routes(routes: list[dict[str, Any]], namespace: str) -> EdgeMetricsMapping | None:
    """Bind shared-edge counters to exact, currently owned HTTPRoute names."""
    names = []
    for route in routes:
        metadata = route.get("metadata") or {}
        labels = metadata.get("labels") or {}
        name = str(metadata.get("name") or "")
        if (
            metadata.get("namespace") != "astrolift-edge"
            or labels.get("astrolift.io/managed-by") != "platform"
            or labels.get("astrolift.dev/namespace") != namespace
            or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", name)
        ):
            continue
        backends = [ref for rule in (route.get("spec") or {}).get("rules", []) for ref in rule.get("backendRefs", [])]
        if not backends or any(ref.get("namespace", "astrolift-edge") != namespace for ref in backends):
            continue
        names.append(re.escape(name).replace(r"\-", "-"))
    if not names:
        return None
    return EdgeMetricsMapping(
        variant="envoy_gateway",
        requests_total="envoy_cluster_upstream_rq",
        duration_bucket="envoy_cluster_upstream_rq_time_bucket",
        namespace_label="envoy_cluster_name",
        namespace_value="httproute/astrolift-edge/(" + "|".join(sorted(set(names))) + ")/rule/[0-9]+",
        status_label="envoy_response_code",
        latency_divisor=1000,
    )


# ingress-nginx controller metrics. The controller stamps the ingress
# resource's namespace on every series; scraped through the
# kube-prometheus-stack ServiceMonitor (honor_labels=false) the target's
# own ``namespace`` (the controller's) wins the label and the metric's
# moves to ``exported_namespace`` — which is therefore the app join key.
NGINX_INGRESS = EdgeMetricsMapping(
    variant="nginx_ingress",
    requests_total="nginx_ingress_controller_requests",
    duration_bucket="nginx_ingress_controller_request_duration_seconds_bucket",
    namespace_label="exported_namespace",
    status_label="status",
)

# AWS ALB controller via the platform-provisioned CloudWatch exporter
# (YACE, the ``cloudwatch-exporter`` bootstrap component, #1225). The
# controller mints one ALB per app ingress, tagged
# ``ingress.k8s.aws/stack=<namespace>/<ingress-name>`` — the exporter
# republishes that tag as the ``tag_ingress_k8s_aws_stack`` label, so the
# app join is a namespace-prefix regex on it. Latency arrives as
# pre-computed percentile statistics, not histograms.
AWS_ALB_CONTROLLER = EdgeMetricsMapping(
    variant="aws_alb_controller",
    requests_total="aws_applicationelb_request_count_sum",
    duration_bucket="",
    namespace_label="tag_ingress_k8s_aws_stack",
    status_label="",
    style="cloudwatch_gauge",
    errors_total="aws_applicationelb_httpcode_target_5_xx_count_sum",
    latency_stat_prefix="aws_applicationelb_target_response_time",
    period_seconds=60,
    namespace_value="{ns}/.*",
    status_class_metric_prefix="aws_applicationelb_httpcode_target",
)

# variant slug -> mapping. Grows one entry per variant as its metrics
# pipeline comes online (traefik, gateway-api, ...). Absence == parity
# gap for that variant.
EDGE_METRICS_BY_VARIANT: dict[str, EdgeMetricsMapping] = {
    NGINX_INGRESS.variant: NGINX_INGRESS,
    AWS_ALB_CONTROLLER.variant: AWS_ALB_CONTROLLER,
}

# ``TenantCluster.ingress_class`` -> driver variant. Mirrors the mapping the
# ingress render path uses (core.app_deploy): "nginx"/"ingress-nginx" are the
# nginx_ingress variant; unknown classes are NOT defaulted here — for metrics
# an unknown class is a parity gap, whereas the render path can safely assume
# nginx-compatible annotations.
_INGRESS_CLASS_TO_VARIANT: dict[str, str] = {
    "nginx": "nginx_ingress",
    "ingress-nginx": "nginx_ingress",
    "traefik": "traefik",
    "kong": "kong",
    "alb": "aws_alb_controller",
}


def variant_for_ingress_class(ingress_class: str | None) -> str | None:
    """Map a cluster's ``ingress_class`` to its ingress variant slug."""
    if not ingress_class:
        return None
    return _INGRESS_CLASS_TO_VARIANT.get(ingress_class.strip().lower())


def edge_metrics_for_ingress_class(ingress_class: str | None) -> EdgeMetricsMapping | None:
    """Resolve a cluster's ``ingress_class`` straight to its edge-metrics
    mapping; ``None`` when the variant has no metrics pipeline yet."""
    variant = variant_for_ingress_class(ingress_class)
    if variant is None:
        return None
    return EDGE_METRICS_BY_VARIANT.get(variant)
