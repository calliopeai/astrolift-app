"""Actual Kubernetes client HTTP: admission, conditional deletion and retained data."""

import copy
import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from kubernetes import client

from _sdk.k8s_dynamic_client import KubernetesDynamicClient
from _sdk.managed_service import DeprovisionSpec
from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig
from k8s_native.managed.model_endpoint_vllm import VLLMDriver

from .test_local_model_runtime import local_setup


@pytest.mark.parametrize("scenario", ["owned", "foreign", "replacement"])
def test_owned_delivery_cleanup_over_actual_kubernetes_http(scenario, tmp_path, capsys):
    original, recording, secrets, spec = local_setup()
    original = VLLMDriver(config=replace(original._config, metrics={}))
    provisioned = original.provision(spec)
    assert provisioned.ok
    namespace, name = original._namespace(spec), original._resource_name(spec)
    objects = {}
    plurals = {
        "Deployment": "deployments",
        "Secret": "secrets",
        "Service": "services",
        "ConfigMap": "configmaps",
        "NetworkPolicy": "networkpolicies",
        "PersistentVolumeClaim": "persistentvolumeclaims",
    }
    for (_kind, ns, object_name), row in recording.objects.items():
        if row["kind"] not in plurals:
            continue
        root = "/api/v1" if row["apiVersion"] == "v1" else "/apis/" + row["apiVersion"]
        row = copy.deepcopy(row)
        row["metadata"].update(uid=str(uuid4()), resourceVersion="7")
        objects[f"{root}/namespaces/{ns}/{plurals[row['kind']]}/{object_name}"] = row
    delivery_path = f"/api/v1/namespaces/{namespace}/secrets/{name}-model-delivery"
    auth_path = f"/api/v1/namespaces/{namespace}/secrets/{name}-vllm"
    delivery = objects[delivery_path]
    if scenario == "foreign":
        delivery["metadata"]["labels"]["astrolift.io/managed-service-id"] = str(uuid4())
    deletes = []
    expected_uid = delivery["metadata"]["uid"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, data, status=200):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/version":
                return self.respond({"major": "1", "minor": "31", "gitVersion": "v1.31.0"})
            if path == "/api":
                return self.respond({"kind": "APIVersions", "versions": ["v1"]})
            if path == "/apis":
                return self.respond(
                    {
                        "kind": "APIGroupList",
                        "groups": [
                            {
                                "name": group,
                                "versions": [{"groupVersion": group + "/v1", "version": "v1"}],
                                "preferredVersion": {"groupVersion": group + "/v1", "version": "v1"},
                            }
                            for group in ("apps", "networking.k8s.io")
                        ],
                    }
                )
            if path in ("/api/v1", "/apis/apps/v1", "/apis/networking.k8s.io/v1"):
                version = "v1" if path == "/api/v1" else path.removeprefix("/apis/")
                resources = [
                    {"name": plural, "kind": kind, "namespaced": True, "verbs": ["get", "delete"]}
                    for kind, plural in plurals.items()
                    if (
                        "apps/v1"
                        if kind == "Deployment"
                        else "networking.k8s.io/v1"
                        if kind == "NetworkPolicy"
                        else "v1"
                    )
                    == version
                ]
                return self.respond({"kind": "APIResourceList", "groupVersion": version, "resources": resources})
            return (
                self.respond(objects[path])
                if path in objects
                else self.respond({"kind": "Status", "reason": "NotFound", "code": 404}, 404)
            )

        def do_DELETE(self):
            path = self.path.split("?")[0]
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            deletes.append((path, body))
            if path == delivery_path:
                assert body["preconditions"] == {"uid": expected_uid, "resourceVersion": "7"}
                if scenario == "replacement":
                    delivery["metadata"]["uid"] = str(uuid4())
                    return self.respond({"kind": "Status", "reason": "Conflict", "code": 409}, 409)
            objects.pop(path, None)
            self.respond({"kind": "Status", "status": "Success"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    configuration = client.Configuration()
    configuration.host = f"http://127.0.0.1:{server.server_port}"
    configuration.debug = False
    configuration.retries = 0
    api = client.ApiClient(configuration=configuration)
    dynamic = KubernetesDynamicClient.from_api_client(api_client=api)
    cluster = K8sNativeClusterDriver(config=K8sNativeConfig(), k8s_client_factory=lambda **_: dynamic)
    driver = VLLMDriver(config=replace(original._config, cluster_driver=cluster))
    try:
        credential_snapshot = copy.deepcopy(secrets.data)
        result = driver.deprovision(DeprovisionSpec(provisioned.handle, managed_service_id=spec.managed_service_id))
        assert result.ok == (scenario == "owned")
        if scenario == "foreign":
            assert not deletes and auth_path in objects and secrets.data == credential_snapshot
        elif scenario == "replacement":
            assert delivery_path in objects and secrets.data == credential_snapshot
            assert len([path for path, _ in deletes if path == delivery_path]) == 1
        else:
            assert delivery_path not in objects
            assert any(path == delivery_path for path, _ in deletes)
            assert not any("persistentvolumeclaims" in path for path, _ in deletes)
            assert any(row["kind"] == "PersistentVolumeClaim" for row in objects.values())
        output = capsys.readouterr()
        assert "private-download-marker" not in result.message + output.out + output.err
    finally:
        api.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
