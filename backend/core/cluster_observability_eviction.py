"""Resolve a cluster's observability eviction driver (#1602 step 4).

Structural sibling of :mod:`core.cluster_log_query`, reading the same
`TenantCluster.provider_config` keys the read paths and
`astrolift_clusters.schema.mutations._OBSERVABILITY_DRIVER_KEYS` already
validate: `log_driver`/`log_config`, `metrics_driver`/`metrics_config`,
`trace_driver`/`trace_config`.

Deliberately the same keys as the read path, not new ones. A cluster
configured to query one Loki and delete from another is a bug nobody would
find until data went missing from the wrong place, and two sets of
endpoint config is how that happens.

Two guards the read resolvers do not need, both because this one deletes:

1. **Per-cluster opt-in.** Returns None unless
   `provider_config["retention_eviction_enabled"]` is exactly True. Reading
   a backend is safe by default; deleting from it is not, and a resolver
   that worked as soon as a log driver existed would make merging step 5
   the moment every configured cluster started evicting.
2. **Shared clusters need a tenant header.** A cluster with no
   organization is shared, and on a shared Loki without an
   `X-Scope-OrgID`, one org's retention policy deletes every org's data.
   Refused rather than best-effort, because the failure is silent and
   cross-tenant.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from astrolift_clusters.models import TenantCluster

logger = logging.getLogger(__name__)

# Which provider_config keys hold the backend for each stream. `metric_raw`
# and `metric_rollup` share one backend: they are two retention windows over
# the same Prometheus/Mimir, not two systems.
_STREAM_CONFIG: dict[str, tuple[str, str]] = {
    "log": ("log_driver", "log_config"),
    "metric_raw": ("metrics_driver", "metrics_config"),
    "metric_rollup": ("metrics_driver", "metrics_config"),
    "trace": ("trace_driver", "trace_config"),
}

_EVICTION_DRIVER_OVERRIDE: Any = None


def set_eviction_driver_for_tests(driver: Any) -> None:
    global _EVICTION_DRIVER_OVERRIDE
    _EVICTION_DRIVER_OVERRIDE = driver


def reset_eviction_driver_for_tests() -> None:
    global _EVICTION_DRIVER_OVERRIDE
    _EVICTION_DRIVER_OVERRIDE = None


def resolve_eviction_driver(cluster: TenantCluster, *, stream: str) -> Any | None:
    """An eviction driver for ``stream`` on ``cluster``, or None to skip.

    None means "do not attempt eviction here" and is never an error: an
    unconfigured cluster, a cluster that has not opted in, and a shared
    cluster with no tenant header are all normal states.

    Note the asymmetry with `UnsupportedEvictionDriver`, which is returned
    rather than None. They answer different questions. None means *the
    platform will not ask* -- not configured, not opted in, not safe.
    Unsupported means *the platform asked and the backend cannot* -- which
    the sweep reports, because an operator who configured retention against
    Tempo should be told it will never take effect, not have their cluster
    counted as unconfigured.
    """
    if _EVICTION_DRIVER_OVERRIDE is not None:
        return _EVICTION_DRIVER_OVERRIDE

    cfg = cluster.provider_config or {}
    if not isinstance(cfg, dict):
        return None

    # Guard 1 -- explicit opt-in. `is True` rather than truthiness: the
    # string "false" out of a JSON column or a hand-edit is truthy, and a
    # typo must not be what enables deletion.
    if cfg.get("retention_eviction_enabled") is not True:
        return None

    keys = _STREAM_CONFIG.get(stream)
    if keys is None:
        logger.warning("unknown observability stream %r; not evicting", stream)
        return None
    driver_key, config_key = keys

    driver_kind = (cfg.get(driver_key) or "").strip().lower()
    if not driver_kind:
        return None

    driver_config = cfg.get(config_key) or {}
    if not isinstance(driver_config, dict):
        logger.warning(
            "cluster %s has %s=%s but %s is not a dict; not evicting",
            cluster.slug,
            driver_key,
            driver_kind,
            config_key,
        )
        return None

    # Guard 2 -- a shared cluster must carry a tenant header.
    if getattr(cluster, "organization_id", None) is None and not (driver_config.get("org_id") or "").strip():
        logger.warning(
            "cluster %s is shared and %s carries no org_id; refusing to evict "
            "(one org's retention would delete every org's data)",
            cluster.slug,
            config_key,
        )
        return None

    if driver_kind == "loki":
        try:
            from _sdk.observability.eviction import LokiEvictionDriver
            from _sdk.observability.loki_logs import LokiConfig
        except ImportError:
            logger.exception(
                "cluster %s requests loki eviction but the SDK is unavailable",
                cluster.slug,
            )
            return None
        endpoint = (driver_config.get("endpoint") or "").strip()
        if not endpoint:
            logger.warning("cluster %s loki config has no endpoint; not evicting", cluster.slug)
            return None
        return LokiEvictionDriver(
            config=LokiConfig(
                base_url=endpoint,
                bearer_token=driver_config.get("bearer_token"),
                org_id=driver_config.get("org_id"),
                timeout_seconds=int(driver_config.get("timeout_seconds", 30)),
            )
        )

    try:
        from _sdk.observability.eviction import unsupported_driver_for
    except ImportError:
        logger.exception("SDK unavailable; not evicting on cluster %s", cluster.slug)
        return None

    # Everything else: a driver that reports what it cannot do, rather than
    # None. See the docstring for why the distinction is load-bearing.
    return unsupported_driver_for(driver_kind)
