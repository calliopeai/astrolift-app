"""Tests for ObservabilityProfile policy (#9, spec 08 §3.4 + §12)."""

from __future__ import annotations

import pytest

from astrolift_operations.observability_profile import (
    RETENTION_AUDIT,
    RETENTION_EVENTS,
    RETENTION_LOGS,
    RETENTION_METRICS,
    RETENTION_TRACES,
    RETENTION_WORKFLOWS,
    LogDriver,
    MetricsDriver,
    ObservabilityProfileError,
    ObservabilityProfileSpec,
    RetentionConfig,
    TraceDriver,
    collect_profile_issues,
    validate_log_driver_config,
    validate_metrics_driver_config,
    validate_profile,
    validate_trace_driver_config,
)

# ---- retention windows ---------------------------------------------


def test_retention_defaults_locked():
    """Spec §12: lock the default retention values so any change
    is a deliberate code review."""
    assert RETENTION_LOGS.default_days == 30
    assert RETENTION_METRICS.default_days == 30
    assert RETENTION_TRACES.default_days == 14
    assert RETENTION_EVENTS.default_days == 90
    assert RETENTION_AUDIT.default_days == 365
    assert RETENTION_WORKFLOWS.default_days == 90


def test_retention_max_bounds():
    """Audit max = 7 years for compliance regimes."""
    assert RETENTION_AUDIT.max_days == 2555  # 7 years


def test_retention_config_defaults():
    cfg = RetentionConfig()
    assert cfg.logs_days == 30
    assert cfg.audit_days == 365


def test_retention_config_rejects_negative():
    with pytest.raises(ObservabilityProfileError, match="logs_days"):
        RetentionConfig(logs_days=0)
    with pytest.raises(ObservabilityProfileError, match="logs_days"):
        RetentionConfig(logs_days=-5)


def test_retention_config_rejects_over_max():
    with pytest.raises(ObservabilityProfileError, match="exceeds max"):
        RetentionConfig(traces_days=200)
    with pytest.raises(ObservabilityProfileError, match="exceeds max"):
        RetentionConfig(audit_days=10000)


def test_retention_config_within_bounds():
    """Org admin dialing UP within bounds should be accepted."""
    cfg = RetentionConfig(
        logs_days=180,
        metrics_days=180,
        traces_days=60,
        events_days=365,
        audit_days=2555,
        workflows_days=180,
    )
    assert cfg.logs_days == 180
    assert cfg.audit_days == 2555


# ---- log driver validation -----------------------------------------


def test_log_driver_loki_minimal_config():
    kind = validate_log_driver_config(
        driver="loki",
        config={"endpoint": "http://loki:3100"},
    )
    assert kind == LogDriver.LOKI


def test_log_driver_unknown():
    with pytest.raises(ObservabilityProfileError, match="unknown log driver"):
        validate_log_driver_config(driver="splunk", config={})


def test_log_driver_missing_required_key():
    with pytest.raises(ObservabilityProfileError, match="endpoint"):
        validate_log_driver_config(driver="loki", config={})


def test_log_driver_opensearch_requires_index_prefix():
    with pytest.raises(ObservabilityProfileError, match="index_prefix"):
        validate_log_driver_config(
            driver="opensearch",
            config={"endpoint": "https://os:9200"},
        )


def test_log_driver_cloudwatch_requires_region_and_log_group():
    with pytest.raises(ObservabilityProfileError, match="log_group"):
        validate_log_driver_config(
            driver="cloudwatch_logs",
            config={"region": "us-east-1"},
        )


def test_log_driver_config_must_be_mapping():
    with pytest.raises(ObservabilityProfileError, match="mapping"):
        validate_log_driver_config(driver="loki", config="not a dict")  # type: ignore[arg-type]


# ---- multiplexer driver validation ---------------------------------


def test_log_multiplexer_two_children():
    kind = validate_log_driver_config(
        driver="multiplexer",
        config={
            "children": [
                {"driver": "loki", "config": {"endpoint": "http://loki:3100"}},
                {
                    "driver": "cloudwatch_logs",
                    "config": {
                        "region": "us-east-1",
                        "log_group": "/astrolift/apps",
                    },
                },
            ]
        },
    )
    assert kind == LogDriver.MULTIPLEXER


def test_multiplexer_empty_children_rejected():
    """Empty stack is meaningless. Refuse loudly."""
    with pytest.raises(ObservabilityProfileError, match="cannot be empty"):
        validate_log_driver_config(
            driver="multiplexer",
            config={"children": []},
        )


def test_multiplexer_children_not_a_list():
    with pytest.raises(ObservabilityProfileError, match="must be a list"):
        validate_log_driver_config(
            driver="multiplexer",
            config={"children": "not a list"},
        )


def test_multiplexer_no_nesting():
    """Defensive: multiplexer-of-multiplexers would let cycles
    in by negligence. Forbid."""
    with pytest.raises(
        ObservabilityProfileError,
        match="multiplexers themselves",
    ):
        validate_log_driver_config(
            driver="multiplexer",
            config={
                "children": [
                    {
                        "driver": "multiplexer",
                        "config": {
                            "children": [
                                {"driver": "loki", "config": {"endpoint": "x"}},
                            ],
                        },
                    },
                ]
            },
        )


def test_multiplexer_child_validates_recursively():
    """Loki child without endpoint → multiplexer fails too."""
    with pytest.raises(ObservabilityProfileError, match="endpoint"):
        validate_log_driver_config(
            driver="multiplexer",
            config={
                "children": [
                    {"driver": "loki", "config": {}},
                ]
            },
        )


def test_multiplexer_child_must_have_driver_and_config():
    with pytest.raises(ObservabilityProfileError, match="missing"):
        validate_log_driver_config(
            driver="multiplexer",
            config={"children": [{"driver": "loki"}]},
        )


# ---- metrics driver validation -------------------------------------


def test_metrics_driver_managed_prometheus():
    """Memory rule: default to Prometheus; managed_prometheus is
    a recognized driver."""
    kind = validate_metrics_driver_config(
        driver="managed_prometheus",
        config={"workspace_url": "https://aps-workspaces.us-east-1..."},
    )
    assert kind == MetricsDriver.MANAGED_PROMETHEUS


def test_metrics_driver_datadog_requires_api_key():
    with pytest.raises(ObservabilityProfileError, match="api_key_secret_ref"):
        validate_metrics_driver_config(
            driver="datadog",
            config={"site": "datadoghq.com"},
        )


def test_metrics_driver_unknown():
    with pytest.raises(ObservabilityProfileError, match="unknown"):
        validate_metrics_driver_config(driver="influxdb", config={})


# ---- trace driver validation ---------------------------------------


def test_trace_driver_tempo():
    kind = validate_trace_driver_config(
        driver="tempo",
        config={"endpoint": "http://tempo:3200"},
    )
    assert kind == TraceDriver.TEMPO


def test_trace_driver_jaeger():
    kind = validate_trace_driver_config(
        driver="jaeger",
        config={"endpoint": "http://jaeger-collector:14250"},
    )
    assert kind == TraceDriver.JAEGER


def test_trace_driver_unknown():
    with pytest.raises(ObservabilityProfileError, match="unknown"):
        validate_trace_driver_config(driver="zipkin", config={})


# ---- whole-profile validation --------------------------------------


def _profile(**overrides) -> ObservabilityProfileSpec:
    base = dict(
        log_driver="loki",
        log_config={"endpoint": "http://loki:3100"},
        metrics_driver="managed_prometheus",
        metrics_config={"workspace_url": "https://aps..."},
        trace_driver="tempo",
        trace_config={"endpoint": "http://tempo:3200"},
    )
    base.update(overrides)
    return ObservabilityProfileSpec(**base)


def test_validate_profile_happy_path():
    validate_profile(profile=_profile())


def test_validate_profile_first_failure_short_circuits():
    """Single-failure validate_profile raises on first bad driver."""
    with pytest.raises(ObservabilityProfileError):
        validate_profile(profile=_profile(log_driver="splunk"))


# ---- multi-failure aggregation -------------------------------------


def test_collect_profile_issues_clean_profile():
    issues = collect_profile_issues(profile=_profile())
    assert issues == ()


def test_collect_profile_issues_multiple():
    """Operator gets all errors at once, not whack-a-mole."""
    issues = collect_profile_issues(
        profile=_profile(
            log_driver="splunk",
            metrics_driver="influxdb",
            trace_driver="zipkin",
        )
    )
    assert len(issues) == 3
    assert any("log_driver" in i for i in issues)
    assert any("metrics_driver" in i for i in issues)
    assert any("trace_driver" in i for i in issues)


def test_collect_profile_issues_partial_failure():
    """Some valid, some invalid → only invalid ones appear."""
    issues = collect_profile_issues(
        profile=_profile(
            log_driver="opensearch",
            log_config={"endpoint": "https://os:9200"},  # missing index_prefix
            # metrics + trace are fine
        )
    )
    assert len(issues) == 1
    assert "index_prefix" in issues[0]
