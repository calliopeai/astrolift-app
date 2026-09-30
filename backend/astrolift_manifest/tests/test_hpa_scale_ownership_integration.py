"""Opt-in local Kubernetes proof of SSA and /scale field ownership.

ASTROLIFT_TEST_KUBECONFIG must identify an expendable local kind cluster.
This verifies the apiserver transition, not the HPA metrics algorithm.
"""

from __future__ import annotations

import copy
import os
from uuid import uuid4

import pytest

from astrolift_manifest.render import render_manifests
from astrolift_manifest.types import ContainerManifest, NormalizedManifest, WorkloadManifest


@pytest.mark.skipif(not os.environ.get("ASTROLIFT_TEST_KUBECONFIG"), reason="requires local kind kubeconfig")
def test_redeploy_releases_replica_ownership_to_scale_controller():
    from kubernetes import client, config

    from providers._sdk.k8s_dynamic_client import KubernetesDynamicClient

    config.load_kube_config(config_file=os.environ["ASTROLIFT_TEST_KUBECONFIG"])
    api_client = client.ApiClient()
    core = client.CoreV1Api(api_client)
    apps = client.AppsV1Api(api_client)
    dynamic = KubernetesDynamicClient.from_api_client(api_client=api_client)
    namespace = "astro-hpa-test-" + uuid4().hex[:12]
    core.create_namespace(client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace)))
    try:
        workload = WorkloadManifest(
            name="web",
            kind="deployment",
            replicas=3,
            hpa_min=2,
            hpa_max=6,
            hpa_target_cpu_pct=75,
            cpu_request="100m",
            containers=(ContainerManifest(name="web", is_primary=True, port=8080),),
        )
        manifests = render_manifests(
            NormalizedManifest(
                name="hpa-test",
                workloads=(workload,),
                managed_services=(),
                defaults_applied=(),
                serialized={},
            ),
            namespace=namespace,
            image_tag="before",
            image_repository="example.invalid/hpa-test",
            environment_name="prod",
        )
        deployment = next(item for item in manifests if item["kind"] == "Deployment")
        hpa = next(item for item in manifests if item["kind"] == "HorizontalPodAutoscaler")
        assert "replicas" not in deployment["spec"]
        assert hpa["spec"]["metrics"][0]["resource"]["target"]["averageUtilization"] == 75

        # Reproduce an install where the prior renderer owned the field.
        previous = copy.deepcopy(deployment)
        previous["spec"]["replicas"] = 3
        assert dynamic.server_side_apply(namespace=namespace, manifest=previous, dry_run=False) == "created"
        dynamic.server_side_apply(namespace=namespace, manifest=hpa, dry_run=False)
        apps.patch_namespaced_deployment_scale(
            "web", namespace, {"spec": {"replicas": 5}}, field_manager="horizontal-pod-autoscaler"
        )
        assert apps.read_namespaced_deployment_scale("web", namespace).spec.replicas == 5

        deployment["spec"]["template"]["spec"]["containers"][0]["image"] = "example.invalid/hpa-test:after"
        assert dynamic.server_side_apply(namespace=namespace, manifest=deployment, dry_run=False) == "updated"
        current = apps.read_namespaced_deployment("web", namespace)
        assert current.spec.replicas == 5
        assert current.spec.template.spec.containers[0].image == "example.invalid/hpa-test:after"
        for entry in current.metadata.managed_fields:
            if entry.manager == "astrolift":
                assert "f:replicas" not in entry.fields_v1.get("f:spec", {})

        assert (
            dynamic.server_side_apply(namespace=namespace, manifest=deployment, dry_run=False) == "unchanged"
        )
        assert apps.read_namespaced_deployment_scale("web", namespace).spec.replicas == 5

        # Removing manifest HPA bounds restores explicit fixed-replica intent.
        dynamic.delete(namespace=namespace, kind="HorizontalPodAutoscaler", name="web")
        deployment["spec"]["replicas"] = 3
        dynamic.server_side_apply(namespace=namespace, manifest=deployment, dry_run=False)
        assert apps.read_namespaced_deployment_scale("web", namespace).spec.replicas == 3
    finally:
        core.delete_namespace(namespace)
        api_client.close()
