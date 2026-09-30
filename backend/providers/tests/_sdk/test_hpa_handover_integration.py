"""Opt-in apiserver proofs; only expendable task-owned kind namespaces are written."""

from __future__ import annotations

import copy
import os
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from astrolift_manifest.render import render_manifests
from astrolift_manifest.types import ContainerManifest, NormalizedManifest, WorkloadManifest

from _sdk.k8s_dynamic_client import KubernetesDynamicClient

pytestmark = pytest.mark.skipif(
    not os.environ.get("ASTROLIFT_TEST_KUBECONFIG"), reason="requires expendable local kind kubeconfig"
)


@pytest.fixture
def live():
    from kubernetes import client, config

    config.load_kube_config(config_file=os.environ["ASTROLIFT_TEST_KUBECONFIG"])
    api = client.ApiClient()
    core, apps = client.CoreV1Api(api), client.AppsV1Api(api)
    namespace = "astro-handover-" + uuid4().hex[:12]
    core.create_namespace(client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace)))
    dynamic = KubernetesDynamicClient.from_api_client(api_client=api)
    workload = WorkloadManifest(
        name="web",
        kind="deployment",
        replicas=3,
        hpa_min=2,
        hpa_max=6,
        cpu_request="100m",
        containers=(ContainerManifest(name="web", is_primary=True, port=8080),),
    )
    manifests = render_manifests(
        NormalizedManifest(
            name="handover-test", workloads=(workload,), managed_services=(), defaults_applied=(), serialized={}
        ),
        namespace=namespace,
        image_tag="after",
        image_repository="example.invalid/handover-test",
        environment_name="prod",
    )
    deployment = next(item for item in manifests if item["kind"] == "Deployment")
    previous = copy.deepcopy(deployment)
    previous["metadata"].pop("annotations")
    previous["spec"]["replicas"] = 3
    previous["spec"]["template"]["spec"]["containers"][0]["image"] = "example.invalid/handover-test:before"
    dynamic.server_side_apply(namespace=namespace, manifest=previous, dry_run=False)
    deadline = time.monotonic() + 10
    while True:
        initial = apps.read_namespaced_deployment("web", namespace)
        if initial.status.observed_generation == initial.metadata.generation and initial.status.replicas == 3:
            break
        if time.monotonic() >= deadline:
            pytest.fail("local Deployment controller did not observe the initial replica count")
        time.sleep(0.05)
    try:
        yield SimpleNamespace(
            apps=apps,
            namespace=namespace,
            dynamic=dynamic,
            deployment=deployment,
            previous=previous,
            manifests=manifests,
        )
    finally:
        core.delete_namespace(namespace)
        api.close()


def owners(deployment):
    return {
        entry.manager
        for entry in deployment.metadata.managed_fields
        if "f:replicas" in entry.fields_v1.get("f:spec", {})
    }


def image(deployment):
    return deployment.spec.template.spec.containers[0].image


@pytest.mark.parametrize("provider", ["native", "eks", "gke", "aks"])
def test_first_hpa_enable_preserves_replicas_through_actual_provider_batch(live, provider):
    from aws.cluster_eks import EKSClusterDriver
    from azure.cluster_aks import AKSClusterDriver
    from gcp.cluster_gke import GKEClusterDriver
    from k8s_native.cluster import K8sNativeClusterDriver

    # Exercise each real batch dispatcher after its separately tested cloud-auth shim.
    driver_class = {
        "native": K8sNativeClusterDriver,
        "eks": EKSClusterDriver,
        "gke": GKEClusterDriver,
        "aks": AKSClusterDriver,
    }[provider]
    driver = driver_class.__new__(driver_class)
    driver._k8s = lambda _cluster: live.dynamic
    before = live.apps.read_namespaced_deployment("web", live.namespace)
    assert before.spec.replicas == 3 and owners(before) == {"astrolift"}
    assert "astrolift.dev/replica-owner" not in (before.metadata.annotations or {})

    outcome = driver.apply_manifests("local-proof", live.namespace, live.manifests)

    assert not outcome.errors, [error.exception_message for error in outcome.errors]
    after = live.apps.read_namespaced_deployment("web", live.namespace)
    assert after.spec.replicas == 3
    assert owners(after) == {"astrolift-hpa-handover"}
    assert image(after).endswith(":after")
    live.apps.patch_namespaced_deployment_scale(
        "web", live.namespace, {"spec": {"replicas": 5}}, field_manager="horizontal-pod-autoscaler"
    )
    assert (
        live.dynamic.server_side_apply(namespace=live.namespace, manifest=live.deployment, dry_run=False) == "unchanged"
    )
    after_scale = live.apps.read_namespaced_deployment("web", live.namespace)
    assert after_scale.spec.replicas == 5
    assert owners(after_scale) == {"horizontal-pod-autoscaler"}


def test_hpa_dry_run_persists_neither_handover_nor_template(live):
    before = live.apps.read_namespaced_deployment("web", live.namespace)

    live.dynamic.server_side_apply(namespace=live.namespace, manifest=live.deployment, dry_run=True)

    after = live.apps.read_namespaced_deployment("web", live.namespace)
    assert after.metadata.uid == before.metadata.uid
    assert after.metadata.resource_version == before.metadata.resource_version
    assert owners(after) == {"astrolift"}
    assert after.spec.replicas == 3 and image(after).endswith(":before")


@pytest.mark.parametrize("stage", ["handover", "full-apply"])
@pytest.mark.parametrize("race", ["scale", "replacement"])
def test_real_resource_version_and_uid_races_refuse_without_overwriting(live, monkeypatch, stage, race):
    from kubernetes.client.exceptions import ApiException

    resource = live.dynamic._resource_for("apps/v1", "Deployment")
    apply = resource.server_side_apply
    triggered = False
    replacement_uid = None

    def racing_apply(**kwargs):
        nonlocal triggered, replacement_uid
        target_manager = "astrolift-hpa-handover" if stage == "handover" else "astrolift"
        if not triggered and kwargs["field_manager"] == target_manager:
            triggered = True
            if race == "scale":
                live.apps.patch_namespaced_deployment_scale(
                    "web", live.namespace, {"spec": {"replicas": 5}}, field_manager="horizontal-pod-autoscaler"
                )
            else:
                live.apps.delete_namespaced_deployment("web", live.namespace)
                replacement = copy.deepcopy(live.previous)
                replacement["spec"]["replicas"] = 7
                replacement_uid = live.apps.create_namespaced_deployment(live.namespace, replacement).metadata.uid
        return apply(**kwargs)

    monkeypatch.setattr(resource, "server_side_apply", racing_apply)
    monkeypatch.setattr(live.dynamic, "_resource_for", lambda *_args: resource)
    with pytest.raises(ApiException) as refused:
        live.dynamic.server_side_apply(namespace=live.namespace, manifest=live.deployment, dry_run=False)
    assert refused.value.status in (409, 422)
    assert triggered
    after = live.apps.read_namespaced_deployment("web", live.namespace)
    assert image(after).endswith(":before")
    if race == "scale":
        assert after.spec.replicas == 5
        assert owners(after) == {"horizontal-pod-autoscaler"}
    else:
        assert after.spec.replicas == 7 and after.metadata.uid == replacement_uid


def test_removing_hpa_restores_explicit_fixed_replica_intent(live):
    live.dynamic.server_side_apply(namespace=live.namespace, manifest=live.deployment, dry_run=False)
    hpa = next(item for item in live.manifests if item["kind"] == "HorizontalPodAutoscaler")
    live.dynamic.server_side_apply(namespace=live.namespace, manifest=hpa, dry_run=False)
    live.dynamic.delete(namespace=live.namespace, kind="HorizontalPodAutoscaler", name="web")
    fixed = copy.deepcopy(live.deployment)
    fixed["metadata"].pop("annotations")
    fixed["spec"]["replicas"] = 4

    live.dynamic.server_side_apply(namespace=live.namespace, manifest=fixed, dry_run=False)

    after = live.apps.read_namespaced_deployment("web", live.namespace)
    assert after.spec.replicas == 4 and owners(after) == {"astrolift"}
