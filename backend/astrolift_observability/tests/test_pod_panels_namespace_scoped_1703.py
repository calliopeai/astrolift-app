"""Per-pod panels query cAdvisor by namespace, not by app (#1703).

cAdvisor series carry ``namespace`` / ``pod`` / ``container`` and no
user-defined ``app`` label. An ``app=<slug>`` matcher therefore selects nothing,
so the per-pod CPU and memory panels returned empty for every pod that has ever
existed -- not an error, just a permanently blank chart, which is why it went
unnoticed.

The app-level saturation queries in the same module already pass a namespace for
exactly this reason; these two were the ones that did not.
"""

from __future__ import annotations

from astrolift_observability import prom_queries

NS = "steadymd-qs-ops"
POD = "web-0"


def _cpu(**kw):
    return prom_queries.build_pod_cpu_usage_query(
        app_slug="qs-ops", environment_name="production", pod_name=POD, range_seconds=3600, **kw
    )


def _mem(**kw):
    return prom_queries.build_pod_memory_usage_query(
        app_slug="qs-ops", environment_name="production", pod_name=POD, range_seconds=3600, **kw
    )


def test_cpu_query_matches_on_namespace_not_app():
    plan = _cpu(namespace=NS)
    assert f'namespace="{NS}"' in plan.promql
    assert "app=" not in plan.promql, "cAdvisor emits no app label; this matches nothing"


def test_memory_query_matches_on_namespace_not_app():
    plan = _mem(namespace=NS)
    assert f'namespace="{NS}"' in plan.promql
    assert "app=" not in plan.promql


def test_the_pod_is_still_narrowed():
    """Namespace scoping must not widen the panel to every pod in the app."""
    assert f'pod="{POD}"' in _cpu(namespace=NS).promql
    assert f'pod="{POD}"' in _mem(namespace=NS).promql


def test_environment_label_is_dropped_under_namespace_scoping():
    """``environment`` is an app-instrumentation convention. Leaving it on a
    cAdvisor query reintroduces the same empty-result bug through another
    label."""
    assert "environment=" not in _cpu(namespace=NS).promql
    assert "environment=" not in _mem(namespace=NS).promql


def test_cpu_query_still_rates_and_memory_does_not():
    """CPU is a counter and memory a gauge; the namespace change must not blur
    that."""
    assert "rate(container_cpu_usage_seconds_total" in _cpu(namespace=NS).promql
    assert "rate(" not in _mem(namespace=NS).promql
