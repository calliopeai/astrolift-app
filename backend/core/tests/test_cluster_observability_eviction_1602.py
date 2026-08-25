"""Resolving a cluster's eviction driver (#1602 step 4).

Every test here is a refusal. That is the point: this resolver is the only
thing between a retention policy and a delete call, and each way it can
decline exists because the alternative loses data.

Synthetic cluster rows (`SimpleNamespace`), matching
`test_cluster_credentials.py` -- nothing here touches the database and the
resolver reads three attributes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.cluster_observability_eviction import (
    reset_eviction_driver_for_tests,
    resolve_eviction_driver,
    set_eviction_driver_for_tests,
)

LOKI = {"endpoint": "http://loki:3100"}


def _cluster(*, org_id=1, **cfg):
    return SimpleNamespace(
        slug="prod-usw2",
        organization_id=org_id,
        provider_config=cfg or {},
    )


def _enabled(**cfg):
    return _cluster(retention_eviction_enabled=True, **cfg)


@pytest.fixture(autouse=True)
def _no_override():
    reset_eviction_driver_for_tests()
    yield
    reset_eviction_driver_for_tests()


# ---------------------------------------------------------------------------
# Guard 1 -- opt-in
# ---------------------------------------------------------------------------


def test_a_cluster_that_has_not_opted_in_is_skipped():
    """The guard that makes step 5 safe to merge. Without it, landing the
    sweep would start evicting on every cluster with a log driver, which is
    every cluster with historical logs."""
    cluster = _cluster(log_driver="loki", log_config=LOKI)

    assert resolve_eviction_driver(cluster, stream="log") is None


@pytest.mark.parametrize("value", ["true", "false", 1, 0, "", None, [], {}])
def test_only_a_real_boolean_true_opts_in(value):
    """`is True`, not truthiness. A JSON column or a hand-edit yields the
    string "false", which is truthy -- and a typo must not be the thing that
    enables deletion."""
    cluster = _cluster(retention_eviction_enabled=value, log_driver="loki", log_config=LOKI)

    assert resolve_eviction_driver(cluster, stream="log") is None


def test_an_opted_in_loki_cluster_resolves():
    driver = resolve_eviction_driver(_enabled(log_driver="loki", log_config=LOKI), stream="log")

    assert driver is not None
    assert type(driver).__name__ == "LokiEvictionDriver"


# ---------------------------------------------------------------------------
# Guard 2 -- shared clusters
# ---------------------------------------------------------------------------


def test_a_shared_cluster_without_a_tenant_header_is_refused():
    """The cross-tenant one. On a shared Loki with no X-Scope-OrgID, one
    org's retention policy deletes every org's data -- silently, and with
    no way to tell afterwards which org asked."""
    cluster = _enabled(log_driver="loki", log_config=LOKI)
    cluster.organization_id = None

    assert resolve_eviction_driver(cluster, stream="log") is None


def test_a_shared_cluster_with_a_tenant_header_resolves():
    cluster = _enabled(log_driver="loki", log_config={**LOKI, "org_id": "acme"})
    cluster.organization_id = None

    assert resolve_eviction_driver(cluster, stream="log") is not None


def test_a_dedicated_cluster_needs_no_tenant_header():
    """The header only matters when the backend is shared. Requiring it
    everywhere would make eviction unavailable on the common case."""
    assert resolve_eviction_driver(_enabled(log_driver="loki", log_config=LOKI), stream="log") is not None


# ---------------------------------------------------------------------------
# Configuration shape
# ---------------------------------------------------------------------------


def test_no_driver_configured_is_skipped():
    assert resolve_eviction_driver(_enabled(), stream="log") is None


def test_a_non_dict_config_is_skipped_rather_than_crashing():
    """Older rows and hand-edits leave nulls and scalars in JSONFields."""
    cluster = _enabled(log_driver="loki", log_config="http://loki:3100")

    assert resolve_eviction_driver(cluster, stream="log") is None


def test_a_non_dict_provider_config_is_skipped():
    cluster = SimpleNamespace(slug="legacy", organization_id=1, provider_config=None)

    assert resolve_eviction_driver(cluster, stream="log") is None


def test_loki_without_an_endpoint_is_skipped():
    """A driver pointed at "" would post a delete to a relative URL."""
    assert resolve_eviction_driver(_enabled(log_driver="loki", log_config={}), stream="log") is None


def test_an_unknown_stream_is_skipped():
    cluster = _enabled(log_driver="loki", log_config=LOKI)

    assert resolve_eviction_driver(cluster, stream="metrics") is None


# ---------------------------------------------------------------------------
# Streams and backends
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stream", ["metric_raw", "metric_rollup"])
def test_both_metric_streams_read_the_same_backend(stream):
    """They are two retention windows over one Prometheus, not two
    systems, so they must not need separate configuration."""
    cluster = _enabled(metrics_driver="prometheus", metrics_config={"endpoint": "http://prom:9090"})

    driver = resolve_eviction_driver(cluster, stream=stream)

    assert driver is not None


@pytest.mark.parametrize(
    "driver_key,config_key,kind,stream",
    [
        ("metrics_driver", "metrics_config", "prometheus", "metric_raw"),
        ("trace_driver", "trace_config", "tempo", "trace"),
        ("log_driver", "log_config", "cloudwatch_logs", "log"),
    ],
)
def test_a_backend_with_no_delete_api_returns_unsupported_not_none(driver_key, config_key, kind, stream):
    """The asymmetry worth pinning. None means the platform will not ask;
    unsupported means it asked and the backend cannot. An operator who
    configured retention against Tempo should be told it will never take
    effect, not have their cluster silently counted as unconfigured.
    """
    cluster = _enabled(**{driver_key: kind, config_key: {"endpoint": "http://x"}})

    driver = resolve_eviction_driver(cluster, stream=stream)

    assert driver is not None
    assert type(driver).__name__ == "UnsupportedEvictionDriver"


def test_the_stream_map_covers_every_policy_stream():
    """A stream the policy module knows about and this map does not would
    be silently skipped for ever."""
    from astrolift_operations.observability_retention import ALL_STREAMS
    from core.cluster_observability_eviction import _STREAM_CONFIG

    assert set(ALL_STREAMS) == set(_STREAM_CONFIG)


def test_the_config_keys_match_the_ones_the_read_path_validates():
    """A cluster configured to query one Loki and delete from another is a
    bug nobody finds until data goes missing from the wrong place. Two sets
    of endpoint config is how that happens, so the keys are asserted equal
    to the validated set rather than merely written to look the same.
    """
    from astrolift_clusters.schema.mutations import _OBSERVABILITY_DRIVER_KEYS
    from core.cluster_observability_eviction import _STREAM_CONFIG

    validated = {(driver, config) for driver, config, _ in _OBSERVABILITY_DRIVER_KEYS}

    assert set(_STREAM_CONFIG.values()) == validated


# ---------------------------------------------------------------------------
# Test override
# ---------------------------------------------------------------------------


def test_the_override_wins_so_the_sweep_can_be_tested_without_credentials():
    sentinel = object()
    set_eviction_driver_for_tests(sentinel)

    # Not opted in, no driver configured -- the override still wins, which
    # is what lets step 5's tests exercise the sweep against synthetic rows.
    assert resolve_eviction_driver(_cluster(), stream="log") is sentinel
