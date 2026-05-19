"""Trace client adapter for the App > Traces surface (#749).

Mirrors ``prom_client.py`` and ``core.cluster_log_query``: reads
``TenantCluster.provider_config`` for trace driver + config, builds
the appropriate SDK driver, and exposes a thin query interface to the
GraphQL resolvers.

Only the ``tempo`` driver is wired here; the protocol allows for Jaeger,
X-Ray, etc. — a new ``elif driver_kind == "jaeger":`` block plugs in
without touching the resolver.
"""

from __future__ import annotations

import logging

from astrolift_lifecycle.models import AppEnvironment

logger = logging.getLogger(__name__)


class TraceClientError(Exception):
    """Raised when the trace backend returns an error."""


def resolve_trace_driver(*, app, environment_name: str | None = None):
    """Return a ``TraceDriver`` instance for ``app``'s cluster, or ``None``.

    Returns ``None`` when:
    - the app has no environments
    - the cluster has no ``trace_driver`` in ``provider_config``
    - the driver kind is unsupported
    """
    qs = AppEnvironment.objects.filter(
        registered_app=app,
        deleted_at__isnull=True,
    ).select_related("tenant_cluster")
    if environment_name:
        qs = qs.filter(name=environment_name)
    env = qs.order_by("name").first()
    if env is None or env.tenant_cluster_id is None:
        return None

    cfg = env.tenant_cluster.provider_config or {}
    driver_kind = (cfg.get("trace_driver") or "").strip().lower()
    if not driver_kind:
        return None

    trace_config = cfg.get("trace_config") or {}
    if not isinstance(trace_config, dict):
        logger.warning(
            "cluster %s has trace_driver=%s but trace_config is not a dict; skipping",
            env.tenant_cluster.slug,
            driver_kind,
        )
        return None

    if driver_kind == "tempo":
        try:
            from _sdk.observability.tempo_traces import TempoConfig, TempoTraceDriver
        except ImportError:
            logger.warning("tempo_traces SDK not installed; trace queries disabled")
            return None
        endpoint = (trace_config.get("endpoint") or "").strip()
        if not endpoint:
            return None
        return TempoTraceDriver(
            config=TempoConfig(
                base_url=endpoint,
                bearer_token=trace_config.get("bearer_token") or None,
                org_id=trace_config.get("org_id") or None,
            )
        )

    logger.warning("unknown trace driver %r; skipping", driver_kind)
    return None
