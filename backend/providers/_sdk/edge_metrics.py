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

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EdgeMetricsMapping:
    """Prometheus coordinates for one ingress variant's edge metrics."""

    variant: str
    requests_total: str
    """Counter of requests through the edge, carrying ``status_label``."""

    duration_bucket: str
    """Request-duration histogram ``_bucket`` series (for histogram_quantile)."""

    namespace_label: str
    """Label key whose value is the app's Kubernetes namespace."""

    status_label: str
    """Label key carrying the upstream HTTP status code."""


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

# variant slug -> mapping. Grows one entry per variant as its metrics
# pipeline comes online (traefik, gateway-api, alb-via-CloudWatch-exporter,
# ...). Absence == parity gap for that variant.
EDGE_METRICS_BY_VARIANT: dict[str, EdgeMetricsMapping] = {
    NGINX_INGRESS.variant: NGINX_INGRESS,
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
