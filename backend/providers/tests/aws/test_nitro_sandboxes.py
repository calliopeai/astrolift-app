"""AWS Nitro Sandbox contracts using the driver's injected Kubernetes transport."""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from _sdk.cluster import ClusterAuth, ClusterContext
from aws.cluster_eks import EKSClusterDriver, EKSConfig
from aws.nitro_sandboxes import POLICY_ANNOTATION, RUNTIME_KIND, discover_runtimes


class Kube:
    def __init__(self, runtime: str = "isolated-sandbox") -> None:
        self.runtime = runtime
        self.reads: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []
        self.failure: Exception | None = None

    def get(self, **kwargs: Any) -> dict[str, Any] | None:
        self.reads.append(kwargs)
        if self.failure:
            raise self.failure
        if kwargs["name"] == self.runtime:
            return {"metadata": {"name": self.runtime}, "handler": self.runtime}
        return None

    def server_side_apply(self, **kwargs: Any) -> str:
        self.writes.append(kwargs)
        return "created"

    create_manifest = server_side_apply


def driver(kube: Kube, management: Any = None) -> EKSClusterDriver:
    result = EKSClusterDriver(
        config=EKSConfig(region="us-east-1", cluster_name="test"),
        eks_client=object(),
        sts_client=object(),
        ec2_client=object(),
        management_backend=management,
    )
    result._k8s_cache["test"] = kube
    return result


def pod(runtime: str = "isolated-sandbox") -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "worker"},
        "spec": {"runtimeClassName": runtime, "containers": [{"name": "worker", "image": "worker@sha256:abc"}]},
    }


def wrap(pod: dict[str, Any], kind: str) -> dict[str, Any]:
    if kind == "Pod":
        return pod
    spec = {"template": {"metadata": pod["metadata"], "spec": pod["spec"]}}
    if kind == "CronJob":
        spec = {"jobTemplate": {"spec": spec}}
    return {"apiVersion": "apps/v1", "kind": kind, "metadata": {"name": "worker"}, "spec": spec}


@pytest.mark.parametrize("kind", ["Pod", "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_runtime_is_checked_and_manifest_preserved(kind: str, dry_run: bool) -> None:
    kube = Kube()
    manifest = wrap(pod(), kind)
    original = deepcopy(manifest)
    result = driver(kube).apply_manifests("test", "team", [manifest], dry_run=dry_run)
    assert result.ok
    assert manifest == original
    assert kube.reads == [{"kind": RUNTIME_KIND, "namespace": None, "name": "isolated-sandbox"}]
    assert kube.writes[0]["manifest"] == original
    assert kube.writes[0]["dry_run"] == dry_run


@pytest.mark.parametrize("create_only", [False, True])
def test_missing_runtime_refuses_the_whole_batch_before_any_write(create_only: bool) -> None:
    kube = Kube(runtime="")
    result = driver(kube).apply_manifests("test", "team", [pod("runc"), pod()], create_only=create_only)
    assert not result.ok
    assert not result.has_retryable
    assert kube.writes == []
    assert "RuntimeClass" in result.errors[0].exception_message


def test_ordinary_workloads_do_not_read_runtime_classes() -> None:
    kube = Kube()
    assert driver(kube).apply_manifests("test", "team", [pod("runc")]).ok
    assert kube.reads == []


def test_confidential_policy_is_required_and_preserved() -> None:
    kube = Kube("confidential-sandbox")
    manifest = pod("confidential-sandbox")
    assert not driver(kube).apply_manifests("test", "team", [manifest]).ok
    assert kube.writes == []
    manifest["metadata"]["annotations"] = {POLICY_ANNOTATION: "operator-generated-policy"}
    assert driver(kube).apply_manifests("test", "team", [manifest]).ok
    assert kube.writes[0]["manifest"]["metadata"]["annotations"][POLICY_ANNOTATION] == "operator-generated-policy"


@pytest.mark.parametrize("field,value", [("hostNetwork", True), ("hostUsers", False)])
def test_incompatible_pod_fields_fail_closed(field: str, value: bool) -> None:
    kube = Kube()
    manifest = pod()
    manifest["spec"][field] = value
    result = driver(kube).apply_manifests("test", "team", [manifest])
    assert not result.ok
    assert not result.has_retryable
    assert kube.writes == []


@pytest.mark.parametrize("probe", ["httpGet", "tcpSocket", "grpc"])
@pytest.mark.parametrize("container_type", ["containers", "initContainers"])
def test_network_isolated_pods_require_exec_probes(probe: str, container_type: str) -> None:
    kube = Kube()
    manifest = pod()
    manifest["metadata"]["annotations"] = {"sandbox.amazonaws.com/network-isolated": "true"}
    manifest["spec"][container_type] = [{"name": "worker", "startupProbe": {probe: {"port": 8080}}}]
    assert not driver(kube).apply_manifests("test", "team", [manifest]).ok
    assert kube.writes == []


@pytest.mark.parametrize("resource", ["nvidia.com/gpu", "amd.com/gpu"])
def test_gpu_passthrough_is_refused(resource: str) -> None:
    kube = Kube()
    manifest = pod()
    manifest["spec"]["containers"][0]["resources"] = {"limits": {resource: 1}}
    assert not driver(kube).apply_manifests("test", "team", [manifest]).ok
    assert kube.writes == []


def test_runtime_is_read_once_per_batch() -> None:
    kube = Kube()
    assert driver(kube).apply_manifests("test", "team", [pod(), pod()]).ok
    assert len(kube.reads) == 1


def test_runtime_read_errors_preserve_retry_classification() -> None:
    kube = Kube()
    kube.failure = TimeoutError("API unavailable")
    result = driver(kube).apply_manifests("test", "team", [pod()])
    assert result.has_retryable
    assert kube.writes == []


def test_inventory_requires_the_aws_handler() -> None:
    assert discover_runtimes([]) == {"status": "not_detected", "runtime_classes": []}
    assert discover_runtimes([{"metadata": {"name": "isolated-sandbox"}, "handler": "runc"}])["runtime_classes"] == []
    assert discover_runtimes([{"metadata": {"name": "isolated-sandbox"}, "handler": "isolated-sandbox"}]) == {
        "status": "detected",
        "runtime_classes": [{"name": "isolated-sandbox", "mode": "isolated"}],
    }


def test_probe_preserves_target_auth_and_distinguishes_unreadable() -> None:
    class Backend:
        auth: Any = None

        def list_runtime_classes(self, *, auth: Any) -> list[dict[str, Any]]:
            self.auth = auth
            return [{"metadata": {"name": "isolated-sandbox"}, "handler": "isolated-sandbox"}]

    backend = Backend()
    context = ClusterContext(slug="another", auth_method="service_account_token", auth_config={"token": "test"})
    assert driver(Kube(), backend)._probe_nitro_sandboxes(context)["status"] == "detected"
    assert backend.auth.slug == "another"
    assert backend.auth.auth_config == {"token": "test"}
    assert driver(Kube(), object())._probe_nitro_sandboxes(context)["status"] == "unknown"


@pytest.mark.parametrize("status", [200, 403])
def test_live_runtime_discovery_uses_authenticated_native_kubernetes_http(status: int) -> None:
    from kubernetes.client.exceptions import ApiException

    from k8s_native.management import LiveManagementBackend, platform_rbac_manifests

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            seen.append((self.path, self.headers.get("Authorization")))
            body = (
                {
                    "apiVersion": "node.k8s.io/v1",
                    "kind": "RuntimeClassList",
                    "items": [{"metadata": {"name": "isolated-sandbox"}, "handler": "isolated-sandbox"}],
                }
                if status == 200
                else {"apiVersion": "v1", "kind": "Status", "message": "Forbidden", "code": 403}
            )
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    auth = ClusterAuth(
        slug="target",
        auth_method="service_account_token",
        auth_config={"token": "synthetic"},
        endpoint=f"http://127.0.0.1:{server.server_port}",
    )
    try:
        backend = LiveManagementBackend()
        if status == 200:
            assert discover_runtimes(backend.list_runtime_classes(auth=auth))["status"] == "detected"
        else:
            with pytest.raises(ApiException) as error:
                backend.list_runtime_classes(auth=auth)
            assert error.value.status == 403
        assert len(seen) == 1
        assert seen[0][0].startswith("/apis/node.k8s.io/v1/runtimeclasses?")
        assert seen[0][1] == "Bearer synthetic"
        role = next(row for row in platform_rbac_manifests() if row["kind"] == "ClusterRole")
        assert {"apiGroups": ["node.k8s.io"], "resources": ["runtimeclasses"], "verbs": ["get", "list"]} in role[
            "rules"
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "field,value",
    [
        ("spec", "not-an-object"),
        ("metadata", ["not-an-object"]),
    ],
)
def test_malformed_pod_shapes_are_errors_without_partial_writes(field: str, value: Any) -> None:
    kube = Kube()
    invalid = pod()
    invalid[field] = value
    result = driver(kube).apply_manifests("test", "team", [pod("runc"), invalid])
    assert not result.ok
    assert result.errors[0].exception_type == "ValueError"
    assert "must be an object" in result.errors[0].exception_message
    assert kube.writes == []


@pytest.mark.parametrize("annotations", [[], "bad", {"sandbox.amazonaws.com/network-isolated": True}])
def test_malformed_annotations_fail_closed(annotations: Any) -> None:
    kube = Kube()
    invalid = pod()
    invalid["metadata"]["annotations"] = annotations
    assert not driver(kube).apply_manifests("test", "team", [invalid]).ok
    assert kube.writes == []


@pytest.mark.parametrize("resources", [[], "bad", {"limits": ["bad"]}, {"requests": "bad"}])
def test_malformed_resource_shapes_fail_closed(resources: Any) -> None:
    kube = Kube()
    invalid = pod()
    invalid["spec"]["containers"][0]["resources"] = resources
    assert not driver(kube).apply_manifests("test", "team", [invalid]).ok
    assert kube.writes == []


@pytest.mark.parametrize("runtime", [None, "runc", "unsupported-sandbox", ["isolated-sandbox"]])
def test_explicit_aws_sandbox_intent_cannot_use_an_unrecognized_runtime(runtime: Any) -> None:
    kube = Kube()
    invalid = pod()
    invalid["spec"]["runtimeClassName"] = runtime
    invalid["metadata"]["annotations"] = {"sandbox.amazonaws.com/network-isolated": "true"}
    assert not driver(kube).apply_manifests("test", "team", [invalid]).ok
    assert kube.writes == []


def test_controller_annotations_cannot_silently_miss_the_pod() -> None:
    kube = Kube()
    invalid = wrap(pod(), "Deployment")
    invalid["metadata"]["annotations"] = {POLICY_ANNOTATION: "misplaced"}
    assert not driver(kube).apply_manifests("test", "team", [invalid]).ok
    assert kube.writes == []


def test_nested_list_is_refused_before_other_batch_resources_are_written() -> None:
    kube = Kube()
    invalid = {"apiVersion": "v1", "kind": "List", "items": [pod()]}
    assert not driver(kube).apply_manifests("test", "team", [pod("runc"), invalid]).ok
    assert kube.writes == []


def test_custom_resources_keep_their_native_spec_shape() -> None:
    kube = Kube()
    custom = {"apiVersion": "custom.example/v1", "kind": "Custom", "metadata": {"name": "x"}, "spec": ["custom"]}
    assert driver(kube).apply_manifests("test", "team", [custom]).ok
    assert kube.reads == []


def test_unexpected_runtime_handler_is_refused() -> None:
    class WrongHandler(Kube):
        def get(self, **kwargs: Any) -> dict[str, Any]:
            return {"handler": "runc"}

    kube = WrongHandler()
    result = driver(kube).apply_manifests("test", "team", [pod()])
    assert not result.ok
    assert "unexpected handler" in result.errors[0].exception_message
    assert kube.writes == []
