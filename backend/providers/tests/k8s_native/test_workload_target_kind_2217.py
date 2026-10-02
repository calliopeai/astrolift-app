"""Real native-client identity proof against two task-owned local Kind APIs."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import yaml
from kubernetes import client, config

from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig, _build_k8s_client


@pytest.fixture
def cluster_configs():
    paths = [os.environ.get(f"ASTROLIFT_WORKLOAD_TEST_KUBECONFIG_{letter}") for letter in "AB"]
    if not all(paths):
        pytest.skip("requires two task-owned local Kind kubeconfigs")
    result = []
    for letter, path in zip("ab", paths, strict=True):
        with open(path) as stream:
            document = yaml.safe_load(stream)
        context = f"kind-astrolift-env-target-2217-{letter}"
        assert document["current-context"] == context, "refusing a non-task-owned context"
        configuration = client.Configuration()
        config.load_kube_config(config_file=path, context=context, client_configuration=configuration)
        assert configuration.host.startswith("https://127.0.0.1:"), "refusing a non-local cluster"
        result.append(K8sNativeConfig(kubeconfig_path=path, context=context))
    return result


def test_concurrent_native_clients_preserve_distinct_cluster_identity(cluster_configs):
    default_host = client.Configuration.get_default_copy().host

    def connect(configuration):
        helper = _build_k8s_client(
            kubeconfig_path=configuration.kubeconfig_path,
            context=configuration.context,
            in_cluster=False,
        )
        try:
            return helper.get(kind="Namespace", namespace=None, name="kube-system")["metadata"]["uid"]
        finally:
            helper._api_client.close()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(connect, [cluster_configs[index % 2] for index in range(16)]))
    assert len(set(results[::2])) == 1
    assert len(set(results[1::2])) == 1
    assert results[0] != results[1]
    assert client.Configuration.get_default_copy().host == default_host


def test_native_patch_keeps_identical_names_separate_between_clusters(cluster_configs):
    namespace = f"native-target-{uuid4().hex[:10]}"
    apis = []
    try:
        for configuration in cluster_configs:
            helper = _build_k8s_client(
                kubeconfig_path=configuration.kubeconfig_path,
                context=configuration.context,
                in_cluster=False,
            )
            apis.append(helper)
            helper.server_side_apply(
                namespace=None,
                manifest={
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {"name": namespace},
                },
                dry_run=False,
            )
            helper.server_side_apply(
                namespace=namespace,
                manifest={
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {"name": "web"},
                    "spec": {
                        "replicas": 0,
                        "selector": {"matchLabels": {"app": "native-target"}},
                        "template": {
                            "metadata": {"labels": {"app": "native-target"}},
                            "spec": {
                                "containers": [
                                    {"name": "main", "image": "registry.k8s.io/pause:3.10", "imagePullPolicy": "Never"}
                                ]
                            },
                        },
                    },
                },
                dry_run=False,
            )
        before = apis[0].get(kind="Deployment", namespace=namespace, name="web")
        result = K8sNativeClusterDriver(config=cluster_configs[1]).patch_workload(
            "selected",
            namespace,
            "Deployment",
            "web",
            {"spec": {"replicas": 2}},
        )
        assert result["spec"]["replicas"] == 2
        assert (
            apis[0].get(kind="Deployment", namespace=namespace, name="web")["metadata"]["resourceVersion"]
            == before["metadata"]["resourceVersion"]
        )
        assert apis[0].get(kind="Deployment", namespace=namespace, name="web")["spec"]["replicas"] == 0
    finally:
        for helper in apis:
            try:
                helper.delete(kind="Namespace", namespace=None, name=namespace)
            finally:
                helper._api_client.close()
