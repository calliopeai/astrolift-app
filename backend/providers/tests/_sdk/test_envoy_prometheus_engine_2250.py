"""Evaluate real generated ingress expressions in Prometheus, without a server."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest
from astrolift_observability import prom_queries
from scripts.prepare_promtool_test_artifacts import install

from _sdk.edge_metrics import envoy_metrics_for_routes
from k8s_native.edge_gateway import render_app_routes


@pytest.mark.parametrize("target", ["darwin-arm64", "linux-amd64", "linux-arm64", "unreviewed"])
def test_unverified_engine_archive_is_refused_before_creating_executable(tmp_path, target):
    destination = tmp_path / "engine"
    with pytest.raises(RuntimeError, match="reviewed checksum"):
        install(b"unverified executable archive", directory=destination, target=target)
    assert not destination.exists()


@pytest.fixture(scope="module")
def promtool():
    binary = os.environ.get("PROMTOOL_BINARY") or shutil.which("promtool")
    if not binary:
        if os.environ.get("CI"):
            pytest.fail("CI must prepare the verified Prometheus test engine")
        pytest.skip("Set PROMTOOL_BINARY after running scripts/prepare_promtool_test_artifacts.py")
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10, check=True)
    assert "version 2.55.0" in version.stdout
    return binary


def _routes(namespace, workloads):
    return [
        resource
        for resource in render_app_routes(
            app_slug="app",
            namespace=namespace,
            workloads={name: (["app.example.net"], 8080) for name in workloads},
            gated=False,
            paused=False,
        )
        if resource["kind"] == "HTTPRoute"
    ]


def _scenario():
    owned = _routes("team-app", ["web", "api"])
    sibling = _routes("team-app-sibling", ["web"])
    foreign = _routes("foreign-app", ["web"])
    foreign[0]["metadata"]["labels"]["astrolift.dev/namespace"] = "team-app"
    unmanaged = _routes("team-app", ["unmanaged"])
    unmanaged[0]["metadata"]["labels"].pop("astrolift.io/managed-by")
    mapping = envoy_metrics_for_routes(owned + sibling + foreign + unmanaged, "team-app")
    assert mapping is not None
    kwargs = dict(app_slug="app", environment_name="production", range_seconds=3600, namespace="team-app", edge=mapping)
    expressions = {
        "traffic": prom_queries.build_request_rate_query(**kwargs).promql,
        "errors": prom_queries.build_error_rate_query(**kwargs).promql,
        "status": prom_queries.build_status_code_breakdown_query(**kwargs).promql,
        **{
            f"p{int(quantile * 100)}": prom_queries.build_latency_quantile_query(**kwargs, quantile=quantile).promql
            for quantile in [0.50, 0.90, 0.95, 0.99]
        },
    }
    return owned, sibling + foreign + unmanaged, expressions


def _series(route, *, counts, buckets, proxy="one"):
    cluster = f"httproute/astrolift-edge/{route['metadata']['name']}/rule/0"
    labels = f"envoy_cluster_name={json.dumps(cluster)},instance={json.dumps(proxy)}"
    out = []
    for code, count in counts.items():
        out.append(
            {
                "series": f'envoy_cluster_upstream_rq{{{labels},envoy_response_code="{code}"}}',
                "values": f"0+{count}x8 {count * 8}+0x8",
            }
        )
    # Aggregate totals coexist with the per-status family and must not be counted twice.
    out.append({"series": f"envoy_cluster_upstream_rq_total{{{labels}}}", "values": "0+9999x16"})
    for bound, count in buckets.items():
        out.append(
            {
                "series": f'envoy_cluster_upstream_rq_time_bucket{{{labels},le="{bound}"}}',
                "values": f"0+{count}x8 {count * 8}+0x8",
            }
        )
    return out


def _evaluate(promtool, tmp_path, series, assertions):
    path = tmp_path / "generated-expressions.json"
    path.write_text(
        json.dumps(
            {
                "evaluation_interval": "15s",
                "tests": [{"interval": "15s", "input_series": series, "promql_expr_test": assertions}],
            }
        )
    )
    result = subprocess.run([promtool, "test", "rules", str(path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SUCCESS" in result.stdout


def _assertions(expressions, *, eval_time, active):
    # Cumulative bucket rates are 1/s at 200 ms and 3/s at 400 ms.
    # Use an explicit tiny numerical tolerance because Promtool 2.55 compares
    # floats exactly and rate/interpolation can differ by one floating-point bit.
    values = {
        "traffic": 3,
        "errors": 2 / 15,
        **{f"p{int(q * 100)}": (200 + 200 * ((q * 3 - 1) / 2)) / 1000 for q in [0.50, 0.90, 0.95, 0.99]},
    }
    tests = [
        {
            "expr": f"abs(({expr}) - {values[name]}) < bool 1e-12" if active else expr,
            "eval_time": eval_time,
            "exp_samples": (
                [{"labels": "{}", "value": 1 if active else 0}] if active or name in ["traffic", "errors"] else []
            ),
        }
        for name, expr in expressions.items()
        if name != "status"
    ]
    tests.append(
        {
            "expr": expressions["status"],
            "eval_time": eval_time,
            "exp_samples": [
                {"labels": '{envoy_response_code="200"}', "value": 2.2 if active else 0},
                {"labels": '{envoy_response_code="302"}', "value": 0.2 if active else 0},
                {"labels": '{envoy_response_code="404"}', "value": 0.2 if active else 0},
                {"labels": '{envoy_response_code="500"}', "value": 0.4 if active else 0},
            ],
        }
    )
    return tests


def test_owned_routes_analytical_rates_statuses_and_millisecond_quantiles(promtool, tmp_path):
    owned, foreign, expressions = _scenario()
    series = _series(
        owned[0], counts={"200": 18, "302": 3, "404": 3, "500": 6}, buckets={"100": 0, "200": 10, "400": 30, "+Inf": 30}
    )
    series += _series(owned[1], counts={"200": 15}, buckets={"100": 0, "200": 5, "400": 15, "+Inf": 15}, proxy="two")
    for route in foreign:
        series += _series(route, counts={"500": 9000}, buckets={"100": 0, "200": 0, "400": 0, "+Inf": 9000})
    _evaluate(promtool, tmp_path, series, _assertions(expressions, eval_time="1m", active=True))


def test_idle_windows_keep_measured_zero_and_omit_undefined_latency(promtool, tmp_path):
    owned, _, expressions = _scenario()
    series = _series(
        owned[0], counts={"200": 18, "302": 3, "404": 3, "500": 6}, buckets={"100": 0, "200": 10, "400": 30, "+Inf": 30}
    )
    series += _series(owned[1], counts={"200": 15}, buckets={"100": 0, "200": 5, "400": 15, "+Inf": 15}, proxy="two")
    _evaluate(
        promtool,
        tmp_path,
        series,
        _assertions(expressions, eval_time="1m", active=True) + _assertions(expressions, eval_time="4m", active=False),
    )


def test_foreign_routes_cannot_supply_any_owned_signal(promtool, tmp_path):
    _, foreign, expressions = _scenario()
    series = []
    for route in foreign:
        series += _series(route, counts={"500": 9000}, buckets={"100": 0, "200": 0, "400": 0, "+Inf": 9000})
    _evaluate(
        promtool,
        tmp_path,
        series,
        [{"expr": expr, "eval_time": "1m", "exp_samples": []} for expr in expressions.values()],
    )
