"""Historical log queries against the cluster's log-aggregator backend (#482).

Companion to :mod:`core.cluster_observability` — the live per-pod
subscription path streams through ``ClusterDriver.stream_logs`` via the
kubelet API, but historical (time-range) queries need a real aggregator
backend (Loki / CloudWatch / Stackdriver / Azure Monitor) because the
kubelet API only retains the last few container restarts of output.

The aggregator is configured on ``TenantCluster.provider_config`` by
the operator at cluster-registration time:

  {
    "log_driver": "loki",
    "log_config": {"endpoint": "http://loki:3100", "bearer_token": "..."}
  }

When no driver is configured the resolver returns an empty page with
``historical_available=False`` so the FE renders the "live tail only on
this cluster" badge instead of conflating it with "no lines in window."

Pluggable test backend:
Tests install a fake driver via :func:`set_log_query_driver_for_tests`;
the production path goes through :func:`resolve_log_query_driver` which
consults the cluster row's ``provider_config``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from _sdk.log_stream import HistoricalLogsUnavailable, LogPage

if TYPE_CHECKING:
    from astrolift_clusters.models import TenantCluster

logger = logging.getLogger(__name__)


_LOG_QUERY_DRIVER_OVERRIDE: Any = None


def set_log_query_driver_for_tests(driver: Any) -> None:
    """Install a test driver — tests construct a fake that returns
    canned :class:`LogPage` payloads. Reset with
    :func:`reset_log_query_driver_for_tests`."""
    global _LOG_QUERY_DRIVER_OVERRIDE
    _LOG_QUERY_DRIVER_OVERRIDE = driver


def reset_log_query_driver_for_tests() -> None:
    global _LOG_QUERY_DRIVER_OVERRIDE
    _LOG_QUERY_DRIVER_OVERRIDE = None


def resolve_log_query_driver(cluster: TenantCluster) -> Any | None:
    """Return a log-aggregator driver instance bound to ``cluster``, or
    ``None`` when the cluster row has no driver configured.

    Test overrides win over the cluster's wired config — that lets the
    test harness exercise the resolver against synthetic cluster rows
    that don't carry real Loki credentials.
    """
    if _LOG_QUERY_DRIVER_OVERRIDE is not None:
        return _LOG_QUERY_DRIVER_OVERRIDE

    cfg = cluster.provider_config or {}
    driver_kind = (cfg.get("log_driver") or "").strip().lower()
    if not driver_kind:
        return None

    log_config = cfg.get("log_config") or {}
    if not isinstance(log_config, dict):
        logger.warning(
            "cluster %s has log_driver=%s but log_config is not a dict; skipping",
            cluster.slug,
            driver_kind,
        )
        return None

    # The aggregator drivers live under the SDK observability namespace;
    # import lazily so the schema-export command (no SDK installed) still
    # works and so unconfigured clusters don't pay the import cost.
    if driver_kind == "loki":
        try:
            from _sdk.observability.loki_logs import (
                LokiConfig,
                LokiLogStreamDriver,
            )
        except ImportError:
            logger.exception(
                "cluster %s requests log_driver=loki but SDK is unavailable",
                cluster.slug,
            )
            return None
        endpoint = (log_config.get("endpoint") or "").strip()
        if not endpoint:
            logger.warning(
                "cluster %s log_driver=loki missing endpoint; skipping",
                cluster.slug,
            )
            return None
        return LokiLogStreamDriver(
            config=LokiConfig(
                base_url=endpoint,
                bearer_token=log_config.get("bearer_token"),
                org_id=log_config.get("org_id"),
                timeout_seconds=int(log_config.get("timeout_seconds", 30)),
                page_size=int(log_config.get("page_size", 1000)),
            )
        )

    if driver_kind == "cloudwatch_logs":
        try:
            from _sdk.observability.cloudwatch_logs import (
                CloudWatchLogsConfig,
                CloudWatchLogsQueryDriver,
            )
        except ImportError:
            logger.exception(
                "cluster %s requests log_driver=cloudwatch_logs but SDK is unavailable",
                cluster.slug,
            )
            return None
        log_group = (log_config.get("log_group") or "").strip()
        region = (log_config.get("region") or "").strip()
        if not log_group or not region:
            logger.warning(
                "cluster %s log_driver=cloudwatch_logs missing log_group/region; skipping",
                cluster.slug,
            )
            return None
        return CloudWatchLogsQueryDriver(
            config=CloudWatchLogsConfig(
                log_group=log_group,
                region=region,
                log_stream_name_prefix=(log_config.get("log_stream_name_prefix") or None),
                role_arn=(log_config.get("role_arn") or None),
            )
        )

    # Other aggregator drivers (stackdriver, azure_monitor_logs,
    # otlp_http) plug in here as their SDK shims land. Returning None
    # for unknown drivers preserves the "historical unavailable" UI
    # state rather than crashing the resolver — the operator still sees
    # live tail.
    logger.info(
        "cluster %s log_driver=%s has no SDK driver wired yet; returning None",
        cluster.slug,
        driver_kind,
    )
    return None


def query_app_logs(
    *,
    cluster: TenantCluster,
    namespace: str,
    app_slug: str,
    workload_slug: str | None,
    since_iso: str,
    until_iso: str,
    limit: int,
    cursor: str,
    level: str | None,
    search: str | None,
) -> LogPage | None:
    """Run a paginated historical query for ``app_slug`` against
    ``cluster``'s configured log aggregator.

    Returns ``None`` when the cluster has no aggregator driver
    configured — the resolver layer maps that to
    ``historicalAvailable=false``. Raises
    :class:`HistoricalLogsUnavailable` only when the driver explicitly
    rejects the query (e.g. kubelet-only backend); other failures
    bubble up as the driver's own exception type so the resolver can
    log + swallow.
    """
    driver = resolve_log_query_driver(cluster)
    if driver is None:
        return None

    selector = build_app_selector(
        namespace=namespace,
        app_slug=app_slug,
        workload_slug=workload_slug,
    )
    try:
        return driver.query_logs(
            selector,
            since_iso,
            until_iso,
            limit=limit,
            cursor=cursor,
            level=level,
            search=search,
        )
    except HistoricalLogsUnavailable:
        return None


def build_app_selector(
    *,
    namespace: str,
    app_slug: str,
    workload_slug: str | None,
) -> str:
    """Compose the LogQL-flavoured stream selector for an app/workload.

    Mirrors the labels the manifest renderer attaches to every pod
    (``astrolift.io/app`` + ``astrolift.io/workload``) so the query
    targets exactly the rendered fleet rather than relying on naming
    conventions.

    The string is opinionated for Loki today; aggregator-specific
    drivers translate to their native query language when more backends
    land. The shape is intentionally simple (label= equality, no
    parenthesization) so the translation surface stays narrow.
    """
    parts = [
        f'namespace="{_q(namespace)}"',
        f'app="{_q(app_slug)}"',
    ]
    if workload_slug:
        parts.append(f'workload="{_q(workload_slug)}"')
    return "{" + ",".join(parts) + "}"


def _q(value: str) -> str:
    """Quote a label value safely for the selector string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')
