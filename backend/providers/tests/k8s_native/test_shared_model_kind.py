"""Opt-in Kubernetes rollout proof with the mounted guard and a controlled server.

This does not download model weights, certify vLLM inference or prove CNI policy
enforcement. Only an explicitly selected expendable kind cluster is modified.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import time
from dataclasses import replace
from uuid import uuid4

import pytest

from _sdk.k8s_dynamic_client import KubernetesDynamicClient
from _sdk.managed_service import ClusterModelPlacement, ModelConsumer, ProvisionSpec, ServiceHandle, UpdateSpec
from k8s_native.cluster import K8sNativeClusterDriver
from k8s_native.managed.model_endpoint_vllm import VLLMConfig, VLLMDriver
from k8s_native.managed.shared_model_runtime import AUTH_REVISION, RUNTIME_PATH

pytestmark = pytest.mark.skipif(
    not os.environ.get("ASTROLIFT_MODEL_TEST_KUBECONFIG"), reason="requires an expendable local kind cluster"
)

SERVER = """import asyncio
from astrolift_shared_model_auth import SharedModelAuth

async def endpoint(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"controlled-server"})

guard = SharedModelAuth(endpoint)

async def connection(reader, writer):
    try:
        request = (await reader.readline()).decode("ascii").strip().split(" ")
        headers = []
        while line := await reader.readline():
            if line == b"\\r\\n":
                break
            name, value = line.rstrip(b"\\r\\n").split(b":", 1)
            headers.append((name.lower(), value.strip()))
        messages = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            messages.append(message)
        await guard({"type": "http", "method": request[0], "path": request[1], "headers": headers}, receive, send)
        status = messages[0]["status"]
        body = b"".join(message.get("body", b"") for message in messages[1:])
        head = f"HTTP/1.1 {status} Response\\r\\nContent-Length: {len(body)}\\r\\n"
        writer.write((head + "Connection: close\\r\\n\\r\\n").encode() + body)
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()

async def main():
    server = await asyncio.start_server(connection, "0.0.0.0", 8000)
    async with server:
        await server.serve_forever()

asyncio.run(main())
"""


class MemorySecrets:
    def __init__(self):
        self.data = {}

    def get(self, path):
        return self.data.get(path)

    def upsert(self, path, value):
        self.data[path] = value


class ControlledCluster:
    """Use the real native batch adapter, replacing only heavy model execution."""

    def __init__(self, native):
        self.native = native

    def get_manifest(self, *args):
        return self.native.get_manifest(*args)

    def list_manifests(self, *args):
        return self.native.list_manifests(*args)

    def apply_manifests(self, cluster, namespace, manifests):
        controlled = copy.deepcopy(manifests)
        for manifest in controlled:
            if manifest["kind"] == "ConfigMap":
                manifest["data"]["proof_server.py"] = SERVER
            if manifest["kind"] == "Deployment":
                container = manifest["spec"]["template"]["spec"]["containers"][0]
                container["image"] = "python:3.12-alpine"
                container["imagePullPolicy"] = "Never"
                container["command"] = ["python3", f"{RUNTIME_PATH}/proof_server.py"]
                container["args"] = []
        return self.native.apply_manifests(cluster, namespace, controlled)


def eventually(check, timeout=45):
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() >= deadline:
            pytest.fail("Local controlled model did not reach the expected state")
        time.sleep(0.1)


def status_from_pod(core, namespace, pod_name, token, path):
    """Keep the token out of process argv and exec through stdin into the pod."""
    code = """import json,sys,urllib.request,urllib.error
data=json.load(sys.stdin)
request=urllib.request.Request('http://127.0.0.1:8000'+data['path'],headers={'Authorization':'Bearer '+data['token']})
try:
    response=urllib.request.urlopen(request,timeout=3)
    print(response.status)
except urllib.error.HTTPError as error:
    print(error.code)
"""
    outcome = subprocess.run(
        [
            "kubectl",
            "--kubeconfig",
            os.environ["ASTROLIFT_MODEL_TEST_KUBECONFIG"],
            "exec",
            "-i",
            "-n",
            namespace,
            pod_name,
            "--",
            "python3",
            "-c",
            code,
        ],
        input=json.dumps({"path": path, "token": token}),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert outcome.returncode == 0, "Controlled pod request failed"
    return int(outcome.stdout.strip())


def test_actual_pod_snapshot_and_independent_revocation():
    from kubernetes import client, config

    kubeconfig = os.environ["ASTROLIFT_MODEL_TEST_KUBECONFIG"]
    _, active = config.list_kube_config_contexts(config_file=kubeconfig)
    assert active["name"].startswith("kind-"), "Only expendable kind clusters are supported"
    config.load_kube_config(config_file=kubeconfig)
    api = client.ApiClient()
    core = client.CoreV1Api(api)
    node = core.list_node().items[0]
    architecture = node.metadata.labels["kubernetes.io/arch"]
    core.patch_node(node.metadata.name, {"metadata": {"labels": {"astrolift.dev/model-proof": "2213"}}})
    native = K8sNativeClusterDriver.__new__(K8sNativeClusterDriver)
    dynamic = KubernetesDynamicClient.from_api_client(api_client=api)
    native._k8s = lambda _cluster: dynamic
    cluster, secrets = ControlledCluster(native), MemorySecrets()
    org, cid, sid = (str(uuid4()) for _ in range(3))
    consumers = []
    for app in ("app-a", "app-b"):
        subscription = str(uuid4())
        ref = f"services/{org}/{sid}/subscriptions/{subscription}#api_key"
        secrets.upsert(ref.partition("#")[0], {"api_key": uuid4().hex + uuid4().hex})
        consumers.append(ModelConsumer(subscription, f"proof-{app}", app, "production", ref))
    placement = ClusterModelPlacement(org, cid, sid, 1, tuple(consumers))
    spec = ProvisionSpec(
        org,
        "proof",
        "",
        "",
        "",
        "",
        cid,
        "proof",
        "custom",
        config={
            "model": "Qwen/Qwen3-0.6B",
            "model_revision": "b" * 40,
            "frontend": "python",
            "compute_mode": "cpu",
            "gpu": 0,
            "cpu": "1",
            "memory": "8Gi",
            "cpu_kv_cache_gib": 4,
        },
        managed_service_id=sid,
        cluster_model=placement,
    )
    driver = VLLMDriver(
        config=VLLMConfig(
            cluster_driver=cluster,
            secrets_backend=secrets,
            shared_runtimes={
                "cpu": {
                    "image": "example.invalid/vllm@sha256:" + "a" * 64,
                    "version": "0.15.1",
                    "architecture": architecture,
                    "hardware_certified": True,
                    "node_selector": {"astrolift.dev/model-proof": "2213"},
                }
            },
        )
    )
    namespace = driver._namespace(spec)
    try:
        result = driver.provision(spec)
        assert result.ok, result.message
        handle = ServiceHandle(result.handle, managed_service_id=sid)
        eventually(lambda: driver.status(handle).state == "available")
        pods = core.list_namespaced_pod(namespace).items
        assert len(pods) == 1
        original_pod = pods[0].metadata.name
        first, second = (secrets.get(consumer.credential_ref.partition("#")[0])["api_key"] for consumer in consumers)
        assert status_from_pod(core, namespace, original_pod, first, "/v1/models") == 200
        assert status_from_pod(core, namespace, original_pod, second, "/v1/models") == 200
        assert status_from_pod(core, namespace, original_pod, first, "/metrics") == 401
        stored_secret = core.list_namespaced_secret(namespace).items[0]
        import base64

        snapshot = json.loads(base64.b64decode(stored_secret.data["keys.json"]))
        assert status_from_pod(core, namespace, original_pod, snapshot["operator_key"], "/metrics") == 200
        # Updating projected credentials alone must not pretend the old process restarted.
        snapshot.update(revision=2, subscription_keys=[second])
        core.patch_namespaced_secret(
            stored_secret.metadata.name, namespace, {"stringData": {"keys.json": json.dumps(snapshot)}}
        )
        assert status_from_pod(core, namespace, original_pod, first, "/v1/models") == 200
        updated = driver.update(
            UpdateSpec(
                result.handle,
                config=spec.config,
                managed_service_id=sid,
                cluster_model=replace(placement, revision=2, consumers=(consumers[1],)),
            )
        )
        assert updated.ok, updated.message
        eventually(lambda: driver.status(handle).state == "available")
        pods = core.list_namespaced_pod(namespace).items
        assert len(pods) == 1 and pods[0].metadata.name != original_pod
        assert pods[0].metadata.annotations[AUTH_REVISION] == "2"
        assert status_from_pod(core, namespace, pods[0].metadata.name, first, "/v1/models") == 401
        assert status_from_pod(core, namespace, pods[0].metadata.name, second, "/v1/models") == 200
        assert status_from_pod(core, namespace, pods[0].metadata.name, snapshot["operator_key"], "/metrics") == 200
        policy = client.NetworkingV1Api(api).list_namespaced_network_policy(namespace).items[0]
        allowed = [
            peer.pod_selector.match_labels.get("astrolift.dev/app")
            for rule in policy.spec.ingress
            for peer in rule._from
            if peer.pod_selector
        ]
        assert allowed == ["app-b"]
    finally:
        core.delete_namespace(namespace)
        api.close()
