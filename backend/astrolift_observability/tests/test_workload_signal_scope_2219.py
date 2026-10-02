"""Real PostgreSQL, Kubernetes ownership, exporters and public GraphQL types."""

import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
import strawberry
from django.utils import timezone

from astrolift_lifecycle.models import AppEnvironment
from astrolift_observability.schema.queries import GoldenSignalsQuery
from astrolift_registry.models import Workload
from core.app_deploy import render_resources_for_deployment
from core.permissions import Permission
from core.testing.prometheus_kind import unavailable_cpu_proxy
from core.tests.test_metric_runtime_identity_2219 import membership
from core.tests.utils.scope_world import as_tenant, bind_role, make_user

pytestmark = pytest.mark.django_db
pytest_plugins = ["core.tests.test_metric_runtime_identity_2219", "core.testing.prometheus_kind"]

QUERY = """query Signals($app: String!, $environment: String!, $workload: String!) {
  astroliftAppGoldenSignals(appSlug: $app, environmentName: $environment,
    workloadSlug: $workload, rangeSeconds: 300) {
    reason signals { name unit samples { ts value } promql reason measurement {
      effectiveScope source identityBasis available unavailableReason membershipObservedAt measurementStart
      target { organizationId appId appSlug environmentId environmentName clusterId namespace workloadId workloadSlug }
      containers { podName podUid containerName containerId }
      usageUnit usageSamples { ts value } limitSamples { ts value }
    } }
  }
}"""


def read(world, user, *, environment=None, workload=None):
    schema = strawberry.Schema(query=GoldenSignalsQuery)
    context = SimpleNamespace(user=user, request=SimpleNamespace(user=user))
    with as_tenant(world, user):
        return schema.execute_sync(
            QUERY,
            variable_values={
                "app": world.app.slug,
                "environment": environment or world.environment.name,
                "workload": workload or world.workload.slug,
            },
            context_value=context,
        )


@pytest.fixture
def reader(runtime):
    user = make_user("signals-2219")
    bind_role(
        user, permissions=[Permission.APP_READ], kind="APP", scope_id=runtime.app.pk, slug="signal-reader"
    )
    return user


def test_real_exporters_publish_exact_owned_workload_usage_limits_and_reject_replacement(
    native, kind_prometheus, reader
):
    world = native[0]
    # CPU rates require an actual complete window since this physical container started.
    deadline = time.monotonic() + 100
    while time.time() < max(row.started_at for row in membership(native)) + 65:
        assert time.monotonic() < deadline
        time.sleep(0.5)
    result = read(world, reader)
    assert not result.errors
    panel = result.data["astroliftAppGoldenSignals"]
    signals = {row["name"]: row for row in panel["signals"]}
    expected = membership(native)[0]
    for name, unit in [("SATURATION_CPU", "cores"), ("SATURATION_MEMORY", "bytes")]:
        row = signals[name]
        measured = row["measurement"]
        assert measured["available"], (name, measured["unavailableReason"])
        assert measured["effectiveScope"] == "WORKLOAD"
        assert measured["source"] == "CADVISOR_KUBE_STATE_METRICS"
        assert measured["identityBasis"] == "VERIFIED_RUNTIME"
        assert measured["target"] == {
            "organizationId": str(world.org.guid),
            "appId": str(world.app.guid),
            "appSlug": world.app.slug,
            "environmentId": str(world.environment.guid),
            "environmentName": world.environment.name,
            "clusterId": str(world.cluster.guid),
            "namespace": world.environment.k8s_namespace,
            "workloadId": str(world.workload.guid),
            "workloadSlug": world.workload.slug,
        }
        assert measured["containers"] == [
            {
                "podName": expected.pod_name,
                "podUid": expected.pod_uid,
                "containerName": expected.container_name,
                "containerId": expected.container_id,
            }
        ]
        assert measured["usageUnit"] == unit
        assert len(row["samples"]) == len(measured["usageSamples"]) == len(measured["limitSamples"]) > 0
        for ratio, usage, limit in zip(
            row["samples"], measured["usageSamples"], measured["limitSamples"], strict=True
        ):
            assert ratio["ts"] == usage["ts"] == limit["ts"]
            assert limit["value"] > 0
            assert ratio["value"] == pytest.approx(usage["value"] / limit["value"])
    # Actual canonical request instrumentation is independent of resource data.
    assert panel["reason"] == "OK"
    assert signals["TRAFFIC"]["measurement"]["available"]
    assert signals["LATENCY_P50"]["measurement"]["unavailableReason"] == "NOT_INSTRUMENTED"
    assert 'astrolift_workload_id="' + str(world.workload.guid) + '"' in signals["TRAFFIC"]["promql"]
    sibling = read(world, reader, workload=world.other.slug)
    assert not sibling.errors
    sibling_memory = next(
        row
        for row in sibling.data["astroliftAppGoldenSignals"]["signals"]
        if row["name"] == "SATURATION_MEMORY"
    )
    assert sibling_memory["measurement"]["available"]
    assert sibling_memory["measurement"]["containers"][0]["podUid"] != expected.pod_uid
    with unavailable_cpu_proxy(kind_prometheus) as endpoint:
        world.cluster.provider_config = {"prometheus_endpoint": endpoint}
        world.cluster.save(update_fields=["provider_config"])
        failed = read(world, reader)
        assert not failed.errors
        mixed = {row["name"]: row for row in failed.data["astroliftAppGoldenSignals"]["signals"]}
        assert failed.data["astroliftAppGoldenSignals"]["reason"] == "OK"
        assert mixed["SATURATION_CPU"]["samples"] == []
        assert mixed["SATURATION_CPU"]["measurement"]["unavailableReason"] == "QUERY_ERROR"
        assert mixed["SATURATION_MEMORY"]["measurement"]["available"]
    world.cluster.provider_config = {"prometheus_endpoint": kind_prometheus}
    world.cluster.save(update_fields=["provider_config"])
    # A different recorded environment in the SAME cluster has different
    # physical members. The fixture KSM intentionally watches only the first
    # namespace: its limits must never be borrowed by the second environment.
    _, _, _, core, apps = native
    namespace = "signals-2219-other-" + uuid4().hex[:8]
    core.create_namespace({"metadata": {"name": namespace}})
    other_environment = AppEnvironment.objects.create(
        registered_app=world.app,
        tenant_cluster=world.cluster,
        name="other",
        k8s_namespace=namespace,
    )
    try:
        world.deployment.app_environment = other_environment
        for resource in render_resources_for_deployment(world.deployment):
            if resource["kind"] == "Deployment":
                apps.create_namespaced_deployment(namespace, resource)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            pods = core.list_namespaced_pod(namespace).items
            if len(pods) == 2 and all(
                pod.status.container_statuses and pod.status.container_statuses[0].state.running
                for pod in pods
            ):
                break
            time.sleep(0.5)
        else:
            pytest.fail("second owned environment did not start")
        # Kubelet housekeeping can lag pod admission; wait for actual usage.
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            second = read(world, reader, environment=other_environment.name)
            assert not second.errors
            memory = next(
                row
                for row in second.data["astroliftAppGoldenSignals"]["signals"]
                if row["name"] == "SATURATION_MEMORY"
            )
            if memory["measurement"]["unavailableReason"] == "MISSING_LIMITS":
                break
            time.sleep(1)
        assert memory["measurement"]["target"]["environmentId"] == str(other_environment.guid)
        assert memory["measurement"]["target"]["namespace"] == namespace
        assert memory["measurement"]["containers"][0]["podUid"] != expected.pod_uid
        assert memory["measurement"]["unavailableReason"] == "MISSING_LIMITS"
        assert memory["samples"] == []
    finally:
        world.deployment.app_environment = world.environment
        core.delete_namespace(namespace)
    # A real Kubernetes bearer with no RoleBindings is denied by the API.
    import os
    from pathlib import Path

    import yaml

    document = yaml.safe_load(Path(os.environ["ASTROLIFT_METRICS_TEST_KUBECONFIG"]).read_text())
    service_account = "signal-denied-" + uuid4().hex[:8]
    core.create_namespaced_service_account(
        world.environment.k8s_namespace, {"metadata": {"name": service_account}}
    )
    token = core.create_namespaced_service_account_token(
        service_account,
        world.environment.k8s_namespace,
        {"spec": {"audiences": ["https://kubernetes.default.svc.cluster.local"], "expirationSeconds": 600}},
    ).status.token
    original_auth = world.cluster.auth_config
    world.cluster.auth_method = "service_account_token"
    world.cluster.auth_config = {
        "token": token,
        "ca_cert": document["clusters"][0]["cluster"]["certificate-authority-data"],
    }
    world.cluster.endpoint = core.api_client.configuration.host
    world.cluster.save()
    rejected = read(world, reader)
    assert not rejected.errors
    for row in rejected.data["astroliftAppGoldenSignals"]["signals"]:
        if row["name"].startswith("SATURATION"):
            assert row["measurement"]["unavailableReason"] == "PERMISSION_DENIED"
            assert not row["samples"]
    world.cluster.auth_method = "kubeconfig"
    world.cluster.auth_config = original_auth
    world.cluster.save()
    world.workload.deleted_at = timezone.now()
    world.workload.save(update_fields=["deleted_at"])
    replacement = Workload.objects.create(
        registered_app=world.app, slug="api", name="Replacement", kind="deployment"
    )
    replaced = read(world, reader)
    assert not replaced.errors
    for row in replaced.data["astroliftAppGoldenSignals"]["signals"]:
        assert row["measurement"]["target"]["workloadId"] == str(replacement.guid)
        assert row["samples"] == []
        assert row["measurement"]["unavailableReason"] == "OWNERSHIP_UNVERIFIED"
    from astrolift_operations.prometheus_client import query_instant

    assert (
        query_instant(
            endpoint=kind_prometheus,
            query=f'count(http_requests_total{{workload="api",astrolift_workload_id="{world.workload.guid}"}})',
        )
        > 0
    )
    assert (
        query_instant(
            endpoint=kind_prometheus,
            query='count(http_requests_total{workload="api",astrolift_workload_id=""})',
        )
        > 0
    )
    # Redeploy the new database incarnation through the production renderer.
    # Old logical and canonical HTTP counters remain in the real collector.
    for resource in render_resources_for_deployment(world.deployment):
        if resource["kind"] == "Deployment":
            apps.patch_namespaced_deployment(
                resource["metadata"]["name"], world.environment.k8s_namespace, resource
            )
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        refreshed = read(world, reader)
        assert not refreshed.errors
        traffic = next(
            row for row in refreshed.data["astroliftAppGoldenSignals"]["signals"] if row["name"] == "TRAFFIC"
        )
        if traffic["measurement"]["unavailableReason"] == "NOT_INSTRUMENTED":
            break
        time.sleep(0.5)
    assert traffic["samples"] == []
    assert traffic["measurement"]["unavailableReason"] == "NOT_INSTRUMENTED"
    assert 'astrolift_workload_id="' + str(replacement.guid) + '"' in traffic["promql"]


def test_missing_target_and_permission_never_claim_a_sibling_measurement(runtime, reader):
    denied = make_user("signals-denied-2219")
    result = read(runtime, denied)
    assert result.errors and result.data is None
    result = read(runtime, reader, environment="not-recorded")
    assert not result.errors
    for row in result.data["astroliftAppGoldenSignals"]["signals"]:
        assert row["measurement"]["unavailableReason"] == "ENVIRONMENT_NOT_FOUND"
        assert row["measurement"]["target"]["environmentId"] is None
        assert row["samples"] == []
    runtime.cluster.provider_config = {"prometheus_endpoint": "http://127.0.0.1:1"}
    runtime.cluster.save(update_fields=["provider_config"])
    unsupported = read(runtime, reader)
    assert not unsupported.errors
    for row in unsupported.data["astroliftAppGoldenSignals"]["signals"]:
        if row["name"].startswith("SATURATION"):
            assert row["measurement"]["unavailableReason"] == "NOT_SUPPORTED_BY_PROVIDER"
