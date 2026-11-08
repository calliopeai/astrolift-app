"""Real local Kubernetes observation of synthetic provider objects, not provider reconciliation."""

from __future__ import annotations

import base64
import ssl
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import httpx
import pytest

from nvke.client import Endpoint
from nvke.infrastructure import GROUP, providers
from nvke.infrastructure_client import METADATA_ACCEPT, InfrastructureClient
from tests.nvke.test_datastore_integration import live as live_fixture


@pytest.fixture
def live(tmp_path):
    yield from live_fixture.__wrapped__(tmp_path)


def test_real_management_api_observes_provider_metadata_without_secret_values(live, tmp_path: Path):
    _datastore, raw, namespace = live
    nico = providers()[0]
    crds = [
        ("NicoCluster", "nicoclusters"),
        ("NicoMachineTemplate", "nicomachinetemplates"),
        ("NicoIdentity", "nicoidentities"),
    ]
    created = []
    secret_name = "nico-credentials"
    deployment_name = "synthetic-nico-manager"
    requests = []
    tls = ssl.create_default_context(cafile=str(tmp_path / "ca.pem"))
    tls.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")

    class RecordingTransport(httpx.HTTPTransport):
        def handle_request(self, request):
            requests.append((request.method, request.url.path, request.headers.get("accept")))
            return super().handle_request(request)

    try:
        for kind, plural in crds:
            name = plural + "." + GROUP
            path = "/apis/apiextensions.k8s.io/v1/customresourcedefinitions"
            assert raw.get(path + "/" + name).status_code == 404, "refusing to replace an existing provider CRD"
            response = raw.post(
                path,
                json={
                    "apiVersion": "apiextensions.k8s.io/v1",
                    "kind": "CustomResourceDefinition",
                    "metadata": {"name": name},
                    "spec": {
                        "group": GROUP,
                        "scope": "Namespaced",
                        "names": {"plural": plural, "singular": plural.removesuffix("s"), "kind": kind},
                        "versions": [
                            {
                                "name": "v1alpha1",
                                "served": True,
                                "storage": True,
                                "schema": {
                                    "openAPIV3Schema": {"type": "object", "x-kubernetes-preserve-unknown-fields": True}
                                },
                                "subresources": {"status": {}},
                            }
                        ],
                    },
                },
            )
            assert response.status_code == 201, "synthetic provider CRD creation failed"
            created.append((path + "/" + name, response.json()["metadata"]["uid"]))
            deadline = time.monotonic() + 20
            while True:
                observed = raw.get(path + "/" + name).json()
                if any(
                    c["type"] == "Established" and c["status"] == "True"
                    for c in observed.get("status", {}).get("conditions") or []
                ):
                    break
                assert time.monotonic() < deadline, "synthetic provider CRD not established"
                time.sleep(0.05)
        secret_path = f"/api/v1/namespaces/{namespace}/secrets"
        secret = raw.post(
            secret_path,
            json={
                "metadata": {"name": secret_name},
                "data": {"token": base64.b64encode(b"PRIVATE_SYNTHETIC_CREDENTIAL").decode()},
            },
        )
        assert secret.status_code == 201
        metadata = raw.get(secret_path + "/" + secret_name, headers={"Accept": METADATA_ACCEPT})
        assert metadata.status_code == 200 and metadata.json()["kind"] == "PartialObjectMetadata"
        assert "data" not in metadata.json() and "stringData" not in metadata.json()
        deployment_path = f"/apis/apps/v1/namespaces/{namespace}/deployments"
        response = raw.post(
            deployment_path,
            json={
                "metadata": {"name": deployment_name, "labels": {"app.kubernetes.io/name": nico.app_name}},
                "spec": {
                    "replicas": 1,
                    "selector": {"matchLabels": {"app": deployment_name}},
                    "template": {
                        "metadata": {"labels": {"app": deployment_name}},
                        "spec": {
                            "containers": [
                                {
                                    "name": "manager",
                                    "image": "registry.example.test/synthetic-never-run:fixture",
                                    "args": [
                                        "--provider-credentials-namespace",
                                        namespace,
                                        "--provider-credentials-secret-name=" + secret_name,
                                    ],
                                }
                            ]
                        },
                    },
                },
            },
        )
        assert response.status_code == 201
        deployment = response.json()
        deployment["status"] = {
            "observedGeneration": deployment["metadata"]["generation"],
            "readyReplicas": 1,
            "replicas": 1,
            "conditions": [{"type": "Available", "status": "True"}],
        }
        assert raw.put(deployment_path + "/" + deployment_name + "/status", json=deployment).status_code == 200
        identity_path = f"/apis/{GROUP}/v1alpha1/namespaces/{namespace}/nicoidentities"
        response = raw.post(
            identity_path,
            json={
                "apiVersion": GROUP + "/v1alpha1",
                "kind": "NicoIdentity",
                "metadata": {"name": "nico-default"},
                "spec": {"credentialsRef": {"name": secret_name}},
            },
        )
        assert response.status_code == 201
        identity = response.json()
        identity["status"] = {
            "lastCheckedTime": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "conditions": [
                {
                    "type": "Ready",
                    "status": "True",
                    "observedGeneration": identity["metadata"]["generation"],
                    "message": "PRIVATE_SYNTHETIC_NATIVE_MESSAGE",
                }
            ],
        }
        response = raw.put(identity_path + "/nico-default/status", json=identity)
        assert response.status_code == 200
        with InfrastructureClient(
            Endpoint("datastore", str(raw.base_url)), transport=RecordingTransport(verify=tls, trust_env=False)
        ) as client:
            observation = client.inspect()
            status = observation.providers[0]
            assert observation.complete and status.state == "CONFIGURED"
            assert status.manager_namespace == namespace and status.secret_namespace == namespace
            assert status.credentials.health == "HEALTHY"
            assert observation.providers[1].state == "NOT-INSTALLED"
            assert "PRIVATE" not in repr(asdict(observation))
            identity = response.json()
            identity["status"]["conditions"][0]["observedGeneration"] = identity["metadata"]["generation"] + 1
            assert raw.put(identity_path + "/nico-default/status", json=identity).status_code == 200
            assert client.inspect().providers[0].credentials.health == "PENDING"
        assert all(method == "GET" for method, _path, _accept in requests)
        secret_requests = [(path, accept) for _method, path, accept in requests if "/secrets/" in path]
        assert secret_requests and all(
            path == secret_path + "/" + secret_name and accept == METADATA_ACCEPT for path, accept in secret_requests
        )
    finally:
        raw.delete(f"/apis/apps/v1/namespaces/{namespace}/deployments/{deployment_name}")
        raw.delete(f"/api/v1/namespaces/{namespace}/secrets/{secret_name}")
        for path, uid in reversed(created):
            response = raw.request("DELETE", path, json={"preconditions": {"uid": uid}})
            assert response.status_code in (200, 202, 404), "owned synthetic CRD cleanup failed"
