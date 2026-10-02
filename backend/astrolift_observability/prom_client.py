"""Thin adapter over ``astrolift_operations.prometheus_client`` for the
golden-signals surface (#380).

The operations module already ships a battle-tested Prometheus
transport (urllib + TTL cache + 4xx/5xx mapping). Re-using it keeps a
single client surface; this adapter just normalises the row shape the
observability resolver wants:

* ``query_range_series`` — given a PromQL expression + window, return
  one ``(label_value, [(ts, value), ...])`` series per matrix row.
  For unlabelled aggregates the series carries the empty string as
  its key.
* ``resolve_prometheus_endpoint`` — find the endpoint for an app's
  cluster the same way the operations resolver does (the operator
  sets it on ``TenantCluster.provider_config['prometheus_endpoint']``).

The error contract matches the upstream client: callers can catch
``astrolift_operations.prometheus_client.PrometheusError`` and degrade
to the "metrics not yet flowing" empty state.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from astrolift_operations import prometheus_client

# Kubernetes-internal DNS suffixes and loopback. A Service ClusterIP and
# the names that resolve to one exist only inside the cluster's network
# namespace, so a control plane running outside the cluster cannot reach
# them however healthy Prometheus is (#1711). A control plane running
# *inside* the cluster can, which is why this only ever explains a
# failure and never blocks a write.
_CLUSTER_INTERNAL_SUFFIXES = (".svc", ".svc.cluster.local", ".cluster.local")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def is_cluster_internal_endpoint(endpoint: str) -> bool:
    """Whether ``endpoint`` names an address only reachable from inside
    the cluster.

    The natural first attempt at wiring Prometheus is the in-cluster
    Service DNS name, and from a control plane deployed outside the
    cluster it fails with a connection error indistinguishable from
    "Prometheus is down". Callers use this to say which one it is.

    Pod IPs are deliberately *not* flagged: on EKS they are real VPC ENI
    addresses and route fine from the control plane, which is exactly
    what the capability probe discovers.
    """
    if not endpoint:
        return False
    host = urlsplit(endpoint if "//" in endpoint else f"//{endpoint}").hostname or ""
    host = host.strip().rstrip(".").lower()
    if not host:
        return False
    if host in _LOOPBACK_HOSTS:
        return True
    return host.endswith(_CLUSTER_INTERNAL_SUFFIXES)


def resolve_prometheus_endpoint(
    *,
    app,
    environment_name: str | None,
) -> str | None:
    """Look up the Prometheus endpoint for ``app`` (and optionally a
    specific environment).

    Returns the endpoint URL or ``None`` if:

    * the app has no environments
    * the named environment doesn't exist (when ``environment_name`` is given)
    * the env's cluster has no ``prometheus_endpoint`` in
      ``provider_config``

    The convention mirrors the existing operations resolver: the
    operator sets ``prometheus_endpoint`` on the
    ``TenantCluster.provider_config`` JSON via the cluster registration
    UI. No fallback to a platform-wide endpoint — Prometheus is
    cluster-local.
    """
    from astrolift_lifecycle.models import AppEnvironment

    qs = AppEnvironment.objects.filter(
        registered_app=app,
        deleted_at__isnull=True,
    ).select_related("tenant_cluster")
    if environment_name:
        qs = qs.filter(name=environment_name)
    env = qs.order_by("name").first()
    if env is None or env.tenant_cluster_id is None:
        return None
    # Check provider_config first (operator-set), then fall back to
    # capabilities (probe-discovered, e.g. set via bringClusterIntoManagement).
    # Mirrors the same two-source lookup in astrolift_clusters/schema/queries.py.
    cfg = env.tenant_cluster.provider_config or {}
    endpoint = (cfg.get("prometheus_endpoint") or "").strip()
    if not endpoint:
        caps = env.tenant_cluster.capabilities or {}
        endpoint = (caps.get("prometheus_endpoint") or "").strip()
    return endpoint or None


def query_range_series(
    *,
    endpoint: str,
    promql: str,
    start_unix: int,
    end_unix: int,
    step_seconds: int,
    label_key: str | None = None,
    strict: bool = False,
) -> list[tuple[str, list[tuple[float, float]]]]:
    """Run ``query_range`` and return a list of
    ``(series_label, [(ts, value), ...])`` tuples.

    ``label_key`` names the PromQL label whose value distinguishes
    rows in the matrix (e.g. ``"code"`` for the status-code breakdown).
    When ``label_key`` is ``None`` (the aggregate-everything case) the
    series carries the empty string and we expect exactly one row.

    Raises :class:`prometheus_client.PrometheusError` on transport or
    PromQL failure — callers are expected to swallow that and surface
    the "metrics not yet flowing" empty state.
    """
    rows = prometheus_client.query_range(
        endpoint=endpoint,
        query=promql,
        start_unix=start_unix,
        end_unix=end_unix,
        step_seconds=step_seconds,
        strict=strict,
    )
    import math

    out: list[tuple[str, list[tuple[float, float]]]] = []
    for row in rows:
        if label_key is None:
            key = ""
        else:
            key = row.metric_labels.get(label_key, "")
        # ``row.values`` is a tuple of (ts_unix_seconds, value) pairs.
        # Real float NaN/Inf can still land in a matrix (e.g. CloudWatch
        # exporter gauges for zero-traffic minutes, #1225) and GraphQL's
        # Float cannot represent them — drop those points ("no sample" is
        # the honest reading of NaN) rather than coercing to 0.
        out.append(
            (
                key,
                [(ts, v) for ts, v in row.values if isinstance(v, (int, float)) and math.isfinite(v)],
            )
        )
    return out


def resolve_edge_metrics(
    *,
    app,
    environment_name: str | None,
):
    """Resolve the edge-metrics mapping for ``app``'s cluster ingress
    variant (spec 08 §6.1).

    Walks the same env → tenant-cluster path as
    :func:`resolve_prometheus_endpoint`, maps the cluster's
    ``ingress_class`` to its ingress variant, and returns the variant's
    :class:`providers._sdk.edge_metrics.EdgeMetricsMapping` — or ``None``
    when the app has no cluster or the variant has no metrics pipeline
    yet (e.g. ``alb`` until the CloudWatch-exporter component lands).
    ``None`` means the RED builders fall back to the legacy
    app-instrumentation queries: status quo for that variant, recorded
    as a parity gap rather than silently wrong data.
    """
    from astrolift_lifecycle.models import AppEnvironment
    from providers._sdk.edge_metrics import edge_metrics_for_ingress_class

    qs = AppEnvironment.objects.filter(
        registered_app=app,
        deleted_at__isnull=True,
    ).select_related("tenant_cluster")
    if environment_name:
        qs = qs.filter(name=environment_name)
    env = qs.order_by("name").first()
    if env is None or env.tenant_cluster is None:
        return None
    return edge_metrics_for_ingress_class(getattr(env.tenant_cluster, "ingress_class", None))
