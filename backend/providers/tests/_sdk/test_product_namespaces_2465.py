"""Reads the product-deployment scan relies on (#2465).

The scan lists the namespaces the installer labels for the Workbench hub and
Zentinelle, through ``ClusterDriver.list_namespaces`` on every cloud driver,
and reads what their workloads run through the workload health rows. Two
properties matter more than the shapes:

* ``list_namespaces`` raises when the list fails. The scan marks every
  product it no longer finds as torn down, so an unreachable apiserver must
  not look like an empty cluster.
* The running images come from the pods the restart count already lists,
  the pod status's ``image`` and ``imageID`` for each container.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import pytest

from _sdk._kube_health import namespaces_from_client, workload_health_from_client
from _sdk.cluster import ClusterContext, NamespaceState, RunningImage

SELECTOR = "calliope.ai/product-contract,calliope.ai/product in (workbench,zentinelle)"


def _namespace(name: str, *, uid: str = "", labels=None, annotations=None, phase: str | None = "Active"):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, uid=uid, labels=labels, annotations=annotations),
        status=SimpleNamespace(phase=phase),
    )


class _NamespaceClient:
    """Answers ``list_namespace`` the way the typed client does, or fails."""

    def __init__(self, items=(), *, error: Exception | None = None) -> None:
        self.items = list(items)
        self.error = error
        self.selectors: list[str] = []

    def list_namespace(self, *, label_selector: str) -> Any:
        self.selectors.append(label_selector)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(items=self.items)


# ---- the shared helper ----------------------------------------------------


def test_namespaces_from_client_reads_name_uid_labels_annotations_and_phase():
    client = _NamespaceClient(
        [
            _namespace(
                "zentinelle",
                uid="uid-z",
                labels={"calliope.ai/product": "zentinelle"},
                annotations={"calliope.ai/workloads": "deployment/zentinelle-backend"},
            ),
            _namespace("junohub", uid="uid-j", labels=None, annotations=None, phase=None),
            _namespace(""),
        ]
    )

    states = namespaces_from_client(client, label_selector=SELECTOR)

    assert client.selectors == [SELECTOR]
    assert states == [
        NamespaceState(name="junohub", labels={}, annotations={}, phase="Active", uid="uid-j"),
        NamespaceState(
            name="zentinelle",
            labels={"calliope.ai/product": "zentinelle"},
            annotations={"calliope.ai/workloads": "deployment/zentinelle-backend"},
            phase="Active",
            uid="uid-z",
        ),
    ]


def test_namespaces_from_client_raises_rather_than_answering_empty():
    client = _NamespaceClient(error=RuntimeError("apiserver unreachable"))

    with pytest.raises(RuntimeError, match="apiserver unreachable"):
        namespaces_from_client(client, label_selector=SELECTOR)


class _Response:
    status = 200
    reason = "OK"

    def __init__(self, body: dict[str, Any]) -> None:
        self.data = json.dumps(body).encode()

    def getheaders(self):
        return {"Content-Type": "application/json"}

    def getheader(self, name, default=None):
        return self.getheaders().get(name, default)


def test_the_dynamic_client_lists_namespaces_by_selector_through_the_real_client(monkeypatch):
    """The request the apiserver sees, and the typed client's answer read back."""
    from kubernetes import client

    from _sdk.k8s_dynamic_client import KubernetesDynamicClient

    seen: list[tuple[str, str, list]] = []
    body = {
        "kind": "NamespaceList",
        "apiVersion": "v1",
        "metadata": {},
        "items": [
            {
                "metadata": {
                    "name": "zentinelle",
                    "uid": "0c1d",
                    "labels": {"calliope.ai/product-contract": "1", "calliope.ai/product": "zentinelle"},
                    "annotations": {"calliope.ai/endpoints": '{"console":"https://z.example.com"}'},
                },
                "status": {"phase": "Active"},
            }
        ],
    }

    def request(self, method, url, query_params=None, *args, **kwargs):
        seen.append((method, url, list(query_params or [])))
        return _Response(body)

    monkeypatch.setattr(client.ApiClient, "request", request)
    cfg = client.Configuration()
    cfg.host = "https://k8s.example.com:6443"
    dynamic = KubernetesDynamicClient.from_api_client(api_client=client.ApiClient(configuration=cfg))

    states = namespaces_from_client(dynamic, label_selector=SELECTOR)

    [(method, url, query)] = seen
    assert (method, urlsplit(url).path) == ("GET", "/api/v1/namespaces")
    assert query == [("labelSelector", SELECTOR)]
    assert states == [
        NamespaceState(
            name="zentinelle",
            labels={"calliope.ai/product-contract": "1", "calliope.ai/product": "zentinelle"},
            annotations={"calliope.ai/endpoints": '{"console":"https://z.example.com"}'},
            phase="Active",
            uid="0c1d",
        )
    ]


# ---- running images on the workload health rows ------------------------------


def _container(image: str, image_id: str, *, restarts: int = 0):
    return SimpleNamespace(image=image, image_id=image_id, restart_count=restarts, last_state=None)


class _HealthClient:
    def __init__(self, deployments, pods_by_selector, *, pods_error: Exception | None = None) -> None:
        self._deployments = deployments
        self._pods = pods_by_selector
        self._pods_error = pods_error

    def list_namespaced_deployment(self, *, namespace):
        return SimpleNamespace(items=self._deployments.get(namespace, []))

    def list_namespaced_pod(self, *, namespace, label_selector=""):
        if self._pods_error is not None:
            raise self._pods_error
        return SimpleNamespace(items=self._pods.get((namespace, label_selector), []))


def _deployment(name: str, *, replicas: int, ready: int):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        spec=SimpleNamespace(
            replicas=replicas, selector=SimpleNamespace(match_labels={"app.kubernetes.io/name": name})
        ),
        status=SimpleNamespace(ready_replicas=ready, conditions=[]),
    )


def test_workload_health_carries_the_distinct_images_the_pods_run():
    backend = "ghcr.io/calliopeai/zentinelle-backend@sha256:aaa"
    old = "ghcr.io/calliopeai/zentinelle-backend@sha256:old"
    pods = {
        ("zentinelle", "app.kubernetes.io/name=zentinelle-backend"): [
            SimpleNamespace(
                status=SimpleNamespace(
                    container_statuses=[
                        _container(backend, backend, restarts=2),
                        _container("docker.io/istio/proxyv2:1.24", "docker.io/istio/proxyv2@sha256:pxy"),
                    ],
                    init_container_statuses=[_container("busybox:1.36", "docker.io/library/busybox@sha256:b")],
                )
            ),
            SimpleNamespace(status=SimpleNamespace(container_statuses=[_container(backend, backend)])),
            SimpleNamespace(status=SimpleNamespace(container_statuses=[_container(old, old)])),
            SimpleNamespace(status=None),
        ]
    }
    client = _HealthClient({"zentinelle": [_deployment("zentinelle-backend", replicas=3, ready=3)]}, pods)

    [row] = workload_health_from_client(client, namespaces=["zentinelle"])

    assert row.running_images == (
        RunningImage(image="docker.io/istio/proxyv2:1.24", image_id="docker.io/istio/proxyv2@sha256:pxy"),
        RunningImage(image=backend, image_id=backend),
        RunningImage(image=old, image_id=old),
    )
    assert row.restart_count_24h == 2


def test_workload_health_has_no_running_images_when_the_pods_cannot_be_listed():
    client = _HealthClient(
        {"zentinelle": [_deployment("zentinelle-backend", replicas=1, ready=1)]},
        {},
        pods_error=RuntimeError("forbidden"),
    )

    [row] = workload_health_from_client(client, namespaces=["zentinelle"])

    assert (row.ready_replicas, row.restart_count_24h, row.running_images) == (1, 0, ())


# ---- every cloud driver -----------------------------------------------------


class _FakeEKS:
    def describe_cluster(self, *, name: str) -> dict[str, Any]:
        return {"cluster": {"endpoint": "https://eks.example.com", "certificateAuthority": {"data": "Q0E="}}}


class _FakeGKE:
    def get_cluster(self, *, name: str) -> Any:
        return SimpleNamespace(endpoint="1.2.3.4", master_auth=SimpleNamespace(cluster_ca_certificate="Q0E="))


class _FakeAKS:
    managed_clusters = SimpleNamespace(
        get=lambda *, resource_group_name, resource_name: SimpleNamespace(fqdn="aks.example.com")
    )


def _driver(cloud: str, factory: Any) -> Any:
    """The cloud's driver, its kubernetes client built by ``factory``."""
    if cloud == "k8s_native":
        from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig

        return K8sNativeClusterDriver(config=K8sNativeConfig(), k8s_client_factory=factory)
    if cloud == "aws":
        from aws.cluster_eks import EKSClusterDriver, EKSConfig

        return EKSClusterDriver(
            config=EKSConfig(region="us-east-1", cluster_name="prod"),
            eks_client=_FakeEKS(),
            sts_client=object(),
            ec2_client=object(),
            k8s_client_factory=factory,
        )
    if cloud == "gcp":
        from gcp.cluster_gke import GKEClusterDriver, GKEConfig

        return GKEClusterDriver(
            config=GKEConfig(
                project_id="acme", location="us-central1", cluster_name="prod", container_client=_FakeGKE()
            ),
            k8s_client_factory=factory,
        )
    from azure.cluster_aks import AKSClusterDriver, AKSConfig

    return AKSClusterDriver(
        config=AKSConfig(
            subscription_id="sub", resource_group="rg", cluster_name="prod", container_service_client=_FakeAKS()
        ),
        k8s_client_factory=factory,
    )


def _ctx(slug: str = "prod") -> ClusterContext:
    return ClusterContext(slug=slug, auth_method="kubeconfig", auth_config={}, endpoint="", ca_cert="")


CLOUDS = ["k8s_native", "aws", "gcp", "azure"]


@pytest.mark.parametrize("cloud", CLOUDS)
def test_every_cluster_driver_lists_namespaces_by_selector(cloud):
    k8s = _NamespaceClient([_namespace("zentinelle", uid="u1", labels={"calliope.ai/product": "zentinelle"})])

    states = _driver(cloud, lambda **_kw: k8s).list_namespaces(_ctx(), label_selector=SELECTOR)

    assert k8s.selectors == [SELECTOR]
    assert states == [
        NamespaceState(
            name="zentinelle", labels={"calliope.ai/product": "zentinelle"}, annotations={}, phase="Active", uid="u1"
        )
    ]


@pytest.mark.parametrize("cloud", CLOUDS)
def test_every_cluster_driver_raises_when_the_namespace_list_fails(cloud):
    """The health reads on the same drivers answer ``[]`` on failure; this
    one must not, or the scan would call every product torn down."""
    k8s = _NamespaceClient(error=RuntimeError("connection refused"))

    with pytest.raises(RuntimeError, match="connection refused"):
        _driver(cloud, lambda **_kw: k8s).list_namespaces(_ctx(), label_selector=SELECTOR)


@pytest.mark.parametrize("cloud", CLOUDS)
def test_every_cluster_driver_raises_when_it_cannot_reach_the_cluster(cloud):
    def unreachable(**_kw):
        raise ConnectionError("no route to the apiserver")

    with pytest.raises(ConnectionError, match="no route"):
        _driver(cloud, unreachable).list_namespaces(_ctx(), label_selector=SELECTOR)
