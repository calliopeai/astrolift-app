"""Real HTTP and PostgreSQL GraphQL preserve independent signal availability."""

import time
from types import SimpleNamespace

import pytest

from astrolift_observability.tests.test_workload_signal_scope_2219 import read
from astrolift_observability.workload_resources import ResourceUnavailable, measure_resource
from astrolift_operations.tests import test_prometheus_warning_2219 as wire_fixtures
from astrolift_operations.tests.test_prometheus_warning_2219 import MARKER
from core.permissions import Permission
from core.tests.utils.scope_world import bind_role, make_user
from providers._sdk.workload_metrics import MetricContainer

pytest_plugins = ["core.tests.test_metric_runtime_identity_2219"]


@pytest.fixture
def warning_wire():
    yield from wire_fixtures.warning_wire.__wrapped__()


@pytest.mark.parametrize("phase", ["usage", "limits"])
def test_usage_or_limit_warning_refuses_complete_physical_measurement(warning_wire, phase):
    warning_wire.warnings = [MARKER]
    warning_wire.warn_when = lambda query: ("kube_pod_container_resource_limits" in query) == (
        phase == "limits"
    )
    with pytest.raises(ResourceUnavailable, match="^QUERY_ERROR$") as error:
        measure_resource(
            endpoint=warning_wire.endpoint,
            namespace=warning_wire.namespace,
            members=(warning_wire.member,),
            resource="memory",
            range_seconds=300,
            start_unix=1000,
            end_unix=1100,
            step_seconds=15,
        )
    assert MARKER not in str(error.value)
    assert len(warning_wire.requests) == (1 if phase == "usage" else 2)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "phase,unavailable", [("red", "TRAFFIC"), ("usage", "SATURATION_CPU"), ("limits", "SATURATION_CPU")]
)
def test_graphql_warning_refuses_only_affected_signal_without_disclosing_body(
    runtime, warning_wire, monkeypatch, caplog, phase, unavailable
):
    user = make_user("warning-reader-2219")
    bind_role(
        user, permissions=[Permission.APP_READ], kind="APP", scope_id=runtime.app.pk, slug="warning-reader"
    )
    runtime.cluster.provider_config = {"prometheus_endpoint": warning_wire.endpoint}
    runtime.cluster.save()
    warning_wire.namespace = runtime.environment.k8s_namespace
    warning_wire.member = MetricContainer(
        "pod-1", "019eb737-0100-7000-8000-000000000001", "worker", "a" * 64, time.time() - 600
    )
    warning_wire.warnings = [MARKER]
    if phase == "red":
        warning_wire.warn_when = lambda query: "http_requests_total" in query and "code=" not in query
    elif phase == "usage":
        warning_wire.warn_when = lambda query: "container_cpu_usage_seconds_total" in query
    else:
        warning_wire.warn_when = (
            lambda query: "kube_pod_container_resource_limits" in query and 'resource="cpu"' in query
        )

    def members(**selectors):
        assert selectors["app_id"] == str(runtime.app.guid)
        assert selectors["environment_id"] == str(runtime.environment.guid)
        assert selectors["workload_id"] == str(runtime.workload.guid)
        assert selectors["namespace"] == runtime.environment.k8s_namespace
        return (warning_wire.member,)

    monkeypatch.setattr(
        "astrolift_observability.golden_signals._driver_for_cluster",
        lambda cluster: SimpleNamespace(metric_containers=members),
    )
    monkeypatch.setattr("astrolift_observability.golden_signals._auth_for_cluster", lambda cluster: None)
    result = read(runtime, user)
    assert result.errors is None
    panel = result.data["astroliftAppGoldenSignals"]
    assert panel["reason"] == "OK"
    signals = {row["name"]: row for row in panel["signals"]}
    row = signals[unavailable]
    assert row["samples"] == []
    assert row["measurement"]["available"] is False
    assert row["measurement"]["unavailableReason"] == "QUERY_ERROR"
    assert row["measurement"]["usageSamples"] == row["measurement"]["limitSamples"] == []
    assert signals["SATURATION_MEMORY"]["measurement"]["available"] is True
    assert signals["ERRORS"]["measurement"]["available"] is True
    assert MARKER not in str(result.data) + str(result.errors) + caplog.text
