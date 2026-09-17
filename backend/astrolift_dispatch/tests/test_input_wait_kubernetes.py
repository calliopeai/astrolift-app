"""Opt-in against a disposable cluster: ASTROLIFT_TEST_KUBECONFIG must be explicit."""

import os
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from astrolift_dispatch.spawners.k8s_job import K8sJobSpawner


@pytest.mark.skipif(
    not os.getenv("ASTROLIFT_TEST_KUBECONFIG"), reason="requires a disposable Kubernetes cluster"
)
def test_question_deadline_preserves_live_job_past_original_timeout(monkeypatch):
    from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig

    from core import cluster_management

    driver = K8sNativeClusterDriver(
        config=K8sNativeConfig(kubeconfig_path=os.environ["ASTROLIFT_TEST_KUBECONFIG"])
    )
    namespace = f"input-wait-{uuid4().hex[:12]}"
    task = SimpleNamespace(guid=uuid4(), external_id="agent-task-fixture", timeout_seconds=15)
    driver.ensure_namespace("fixture", namespace, {}, {})
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": task.external_id,
            "namespace": namespace,
            "labels": {"astrolift.dev/task-id": str(task.guid)},
        },
        "spec": {
            "activeDeadlineSeconds": task.timeout_seconds,
            "backoffLimit": 0,
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "fixture",
                            "image": os.environ.get(
                                "ASTROLIFT_TEST_WAIT_IMAGE", "registry.k8s.io/pause:3.10"
                            ),
                            "imagePullPolicy": "IfNotPresent",
                        }
                    ],
                }
            },
        },
    }
    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda _: driver)
    monkeypatch.setattr(cluster_management, "_context_for_cluster", lambda _: SimpleNamespace(slug="fixture"))
    try:
        result = driver.apply_manifests("fixture", namespace, [job])
        assert result.ok, result.summary()
        before = driver.get_manifest("fixture", namespace, "Job", task.external_id)
        spawner = K8sJobSpawner(None, namespace)
        spawner.reserve_input_wait(task, 30)
        spawner.reserve_input_wait(task, 30)
        after = driver.get_manifest("fixture", namespace, "Job", task.external_id)
        assert after["metadata"]["uid"] == before["metadata"]["uid"]
        assert after["spec"]["template"] == before["spec"]["template"]
        assert after["spec"]["activeDeadlineSeconds"] == 45
        time.sleep(17)
        live = driver.get_manifest("fixture", namespace, "Job", task.external_id)
        assert live["status"].get("active") == 1
        assert not any(c.get("status") == "True" for c in live["status"].get("conditions", []))
    finally:
        result = driver.delete_manifests(
            "fixture",
            namespace,
            [
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {"name": namespace},
                }
            ],
            propagation_policy="Foreground",
        )
        assert result.ok
