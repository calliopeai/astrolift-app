"""Opt-in apiserver proof that the gateway's autoscaler takes over its replicas (#2482).

ASTROLIFT_TEST_KUBECONFIG must name an expendable local kind cluster. The
gateway's manifests go through the k8s_native driver's batch into a namespace
the test creates and deletes, never ``astrolift-system``. This proves field
ownership and what the HPA controller does without metrics (kind ships no
metrics-server), not scaling under load.
"""

from __future__ import annotations

import copy
import os
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from astrolift_operations import zentinelle_connect

pytestmark = pytest.mark.skipif(
    not os.environ.get("ASTROLIFT_TEST_KUBECONFIG"), reason="requires expendable local kind kubeconfig"
)

NAME = zentinelle_connect.GATEWAY_NAME


@pytest.fixture
def live():
    from _sdk.k8s_dynamic_client import KubernetesDynamicClient
    from k8s_native.cluster import K8sNativeClusterDriver
    from kubernetes import client, config

    config.load_kube_config(config_file=os.environ["ASTROLIFT_TEST_KUBECONFIG"])
    api = client.ApiClient()
    core = client.CoreV1Api(api)
    namespace = "astro-gateway-hpa-" + uuid4().hex[:12]
    core.create_namespace(client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace)))
    driver = K8sNativeClusterDriver.__new__(K8sNativeClusterDriver)
    dynamic = KubernetesDynamicClient.from_api_client(api_client=api)
    driver._k8s = lambda _cluster: dynamic
    manifests = zentinelle_connect.gateway_manifests(
        cluster_id="c-2482",
        zentinelle_url="https://zentinelle.invalid",
        image="registry.invalid/zentinelle-gateway:2482",
        max_replicas=6,
    )
    for manifest in manifests:
        manifest["metadata"]["namespace"] = namespace
    try:
        yield SimpleNamespace(
            core=core,
            apps=client.AppsV1Api(api),
            autoscaling=client.AutoscalingV2Api(api),
            namespace=namespace,
            driver=driver,
            manifests=manifests,
        )
    finally:
        core.delete_namespace(namespace)
        api.close()


def _apply(live, manifests):
    outcome = live.driver.apply_manifests("kind", live.namespace, manifests)
    assert not outcome.errors, [error.exception_message for error in outcome.errors]


def _deployment(live):
    return live.apps.read_namespaced_deployment(NAME, live.namespace)


def _observed(live) -> bool:
    current = _deployment(live)
    return current.status.observed_generation == current.metadata.generation


def _pods(live) -> set[str]:
    return {pod.metadata.uid for pod in live.core.list_namespaced_pod(live.namespace).items}


def _replica_owners(deployment) -> set[str]:
    return {
        entry.manager
        for entry in deployment.metadata.managed_fields
        if "f:replicas" in (entry.fields_v1 or {}).get("f:spec", {})
    }


def _wait_for(probe, what: str, seconds: float = 120) -> None:
    deadline = time.monotonic() + seconds
    while not probe():
        if time.monotonic() >= deadline:
            pytest.fail(f"timed out waiting for {what}")
        time.sleep(0.5)


def _settle(live, pods: int) -> None:
    """Wait until the Deployment controller runs ``pods`` pods and has stopped
    writing the Deployment. An apply carries the resourceVersion it read, so
    one that lands while the controller is still writing status is refused
    with a 409 (the apply client's guard against overwriting newer state)."""

    def quiet() -> bool:
        first = _deployment(live)
        if first.status.observed_generation != first.metadata.generation or len(_pods(live)) != pods:
            return False
        time.sleep(2)
        return _deployment(live).metadata.resource_version == first.metadata.resource_version

    _wait_for(quiet, f"the Deployment controller to settle at {pods} pods")


def test_a_running_gateway_keeps_its_pods_when_the_autoscaler_takes_over(live):
    deployment, service, autoscaler = live.manifests
    # The gateway as it ran before #2482: two replicas Astrolift owned, no autoscaler.
    previous = copy.deepcopy(deployment)
    previous["metadata"].pop("annotations", None)
    previous["spec"]["replicas"] = 2
    _apply(live, [previous, service])
    _settle(live, 2)
    assert _replica_owners(_deployment(live)) == {"astrolift"}
    pods = _pods(live)

    _apply(live, live.manifests)

    after = _deployment(live)
    assert after.spec.replicas == 2
    assert _replica_owners(after) == {"astrolift-hpa-handover"}
    # The new annotation bumps the generation, but the pod template is the
    # same: once the controller has seen it, the same two pods still run.
    _wait_for(lambda: _observed(live), "the Deployment controller to observe the apply")
    assert len(live.apps.list_namespaced_replica_set(live.namespace).items) == 1
    assert _pods(live) == pods
    hpa = live.autoscaling.read_namespaced_horizontal_pod_autoscaler(NAME, live.namespace)
    assert (hpa.spec.min_replicas, hpa.spec.max_replicas) == (2, 6)
    assert hpa.spec.metrics[0].resource.target.average_utilization == 60
    assert hpa.spec.behavior.scale_down.stabilization_window_seconds == 300

    # The autoscaler moves the count; applying the gateway again (a gateway
    # URL change, a new credential) leaves it where the autoscaler put it.
    live.apps.patch_namespaced_deployment_scale(
        NAME, live.namespace, {"spec": {"replicas": 4}}, field_manager="horizontal-pod-autoscaler"
    )
    _settle(live, 4)
    _apply(live, live.manifests)

    again = _deployment(live)
    assert again.spec.replicas == 4
    assert "astrolift" not in _replica_owners(again)


def test_a_new_gateway_is_raised_to_two_and_applies_again_at_the_autoscalers_count(live):
    _apply(live, live.manifests)

    # Created without replicas, the Deployment defaults to one; the HPA
    # controller raises a target below its floor without reading a metric.
    _wait_for(lambda: _deployment(live).spec.replicas == 2, "the autoscaler to raise the gateway to two")
    # The apiserver records no owner for /scale writes to a defaulted count
    # (#2495), so applying the gateway again has nothing to hand over.
    assert _replica_owners(_deployment(live)) == set()
    _settle(live, 2)
    _apply(live, live.manifests)
    assert _deployment(live).spec.replicas == 2

    live.apps.patch_namespaced_deployment_scale(
        NAME, live.namespace, {"spec": {"replicas": 4}}, field_manager="horizontal-pod-autoscaler"
    )
    _settle(live, 4)
    _apply(live, live.manifests)

    assert _deployment(live).spec.replicas == 4


def test_removing_a_gateway_removes_its_autoscaler_and_one_without_an_autoscaler(live):
    deployment, service, _autoscaler = live.manifests
    refs = zentinelle_connect._workload_refs(with_secret=False)
    _apply(live, live.manifests)

    removed = live.driver.delete_manifests("kind", live.namespace, refs)

    assert removed.ok, removed.summary()
    assert live.autoscaling.list_namespaced_horizontal_pod_autoscaler(live.namespace).items == []
    assert live.apps.list_namespaced_deployment(live.namespace).items == []

    # A gateway deployed before #2482 has no autoscaler: the client takes the
    # 404 as already deleted, so removing it still succeeds.
    previous = copy.deepcopy(deployment)
    previous["metadata"].pop("annotations", None)
    previous["spec"]["replicas"] = 2
    _apply(live, [previous, service])

    older = live.driver.delete_manifests("kind", live.namespace, refs)

    assert older.ok, older.summary()
    assert live.apps.list_namespaced_deployment(live.namespace).items == []
