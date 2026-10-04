"""Evaluate authenticated subscription rates in the actual Prometheus engine."""

import json
import os
import shutil
import subprocess

import pytest

from astrolift_services.model_subscription_observations import subscription_observation_query

TARGET = {
    "managed_service": "00000000-0000-4000-8000-000000000001",
    "subscription_id": "00000000-0000-4000-8000-000000000002",
    "namespace": "owned-models",
    "service": "owned-model",
}
QUERY = subscription_observation_query(*TARGET.values())


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


def labels(values):
    return "{" + ",".join(f"{key}={json.dumps(value)}" for key, value in sorted(values.items())) + "}"


def meter(
    *, pod="one", job="primary", target=None, idle=False, errors=False, info="2+0x10", revision="1x4 2x6"
):
    identity = (target or TARGET) | {"pod": pod, "job": job, "instance": job + "-" + pod}

    def metric(name, values, **dimensions):
        return {
            "series": "astrolift_model_subscription_" + name + labels(identity | dimensions),
            "values": values,
        }

    output = [metric("info", info), metric("auth_revision", revision)]
    count = 0 if idle else 30
    output.append(
        metric(
            "requests_total",
            f"0+{count}x10",
            route="chat_completions",
            status_class="2xx",
            outcome="completed",
        )
    )
    if errors:
        output += [
            metric(
                "requests_total", "0+3x10", route="chat_completions", status_class="5xx", outcome="completed"
            ),
            metric(
                "requests_total", "0+3x10", route="completions", status_class="2xx", outcome="interrupted"
            ),
        ]
    output.append(metric("response_bytes_total", f"0+{0 if idle else 600}x10", route="chat_completions"))
    for bound in ("0.1", "+Inf"):
        output.append(
            metric("request_duration_seconds_bucket", f"0+{count}x10", route="chat_completions", le=bound)
        )
    return output


def evaluate(promtool, tmp_path, inputs, expected, *, eval_time="5m"):
    # Rate and quantile arithmetic may differ by one floating-point bit.
    assertions = []
    for key, value in expected.items():
        selected = f'({QUERY}) and on(astrolift_measurement) label_replace(vector(1),"astrolift_measurement","{key}","",".*")'
        assertions.append(
            {
                "expr": selected if value is None else f"abs(({selected}) - {value}) < bool 1e-12",
                "eval_time": eval_time,
                "exp_samples": []
                if value is None
                else [{"labels": labels(TARGET | {"astrolift_measurement": key}), "value": 1}],
            }
        )
    path = tmp_path / "subscription-expressions.json"
    path.write_text(
        json.dumps(
            {
                "evaluation_interval": "30s",
                "tests": [{"interval": "30s", "input_series": inputs, "promql_expr_test": assertions}],
            }
        )
    )
    result = subprocess.run(
        [promtool, "test", "rules", str(path)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SUCCESS" in result.stdout


def expected(requests=None, errors=None, bytes_=None, latency=None):
    return {
        "requests_per_second": requests,
        "error_requests_per_second": errors,
        "response_bytes_per_second": bytes_,
        "latency_p95": latency,
    }


def test_duplicate_scrapes_distinct_pods_stream_errors_and_rotated_revisions(promtool, tmp_path):
    inputs = meter(errors=True) + meter(errors=True, job="duplicate") + meter(pod="two")
    inputs += meter(target=TARGET | {"subscription_id": "00000000-0000-4000-8000-000000000003"}, errors=True)
    evaluate(promtool, tmp_path, inputs, expected(2.2, 0.2, 40, 0.095))


def test_idle_meter_is_observed_zero_with_no_latency(promtool, tmp_path):
    evaluate(promtool, tmp_path, meter(idle=True), expected(0, 0, 0))


@pytest.mark.parametrize(
    "change",
    [
        "no-meter",
        "legacy",
        "invalid-revision",
        "missing-pod",
        "foreign-model",
        "foreign-namespace",
        "foreign-service",
        "foreign-subscription",
    ],
)
def test_unproven_or_foreign_mapping_cannot_supply_an_owned_signal(promtool, tmp_path, change):
    options = {}
    if change == "legacy":
        options["info"] = "1+0x10"
    elif change == "invalid-revision":
        options["revision"] = "9007199254740992+0x10"
    elif change == "missing-pod":
        options["pod"] = ""
    elif change.startswith("foreign-"):
        dimension = {
            "foreign-model": "managed_service",
            "foreign-namespace": "namespace",
            "foreign-service": "service",
            "foreign-subscription": "subscription_id",
        }[change]
        options["target"] = TARGET | {dimension: "foreign"}
    inputs = meter(**options)
    if change == "no-meter":
        inputs = [item for item in inputs if "subscription_info{" not in item["series"]]
    evaluate(promtool, tmp_path, inputs, expected())


def test_uninitialized_scrape_window_does_not_invent_zero(promtool, tmp_path):
    evaluate(promtool, tmp_path, meter(idle=True), expected(), eval_time="0s")
