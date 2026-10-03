"""Actual Kubernetes HTTP failures must not export private delivery grants."""

import json
import logging
import threading
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from kubernetes import client
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from _sdk import _telemetry
from _sdk.k8s_dynamic_client import KubernetesDynamicClient
from _sdk.managed_service import UpdateSpec
from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig
from k8s_native.managed.model_endpoint_vllm import VLLMDriver

from .test_local_model_runtime import local_setup


@pytest.fixture
def delivery_failure_http():
    original, recording, _, spec = local_setup()
    original = VLLMDriver(config=replace(original._config, metrics={}))
    initial = original.provision(spec)
    assert initial.ok
    plurals = {
        "Namespace": "namespaces",
        "Secret": "secrets",
        "Service": "services",
        "ConfigMap": "configmaps",
        "PersistentVolumeClaim": "persistentvolumeclaims",
        "Deployment": "deployments",
        "NetworkPolicy": "networkpolicies",
    }
    objects, requests = {}, []
    for (_, namespace, name), row in recording.objects.items():
        root = "/api/v1" if row["apiVersion"] == "v1" else "/apis/" + row["apiVersion"]
        prefix = f"{root}/namespaces/{namespace}" if row["kind"] != "Namespace" else root
        objects[f"{prefix}/{plurals[row['kind']]}/{name}"] = row
    private = original._config.local_model_delivery["files"][0]["url"]

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
                    {"name": plural, "kind": kind, "namespaced": kind != "Namespace", "verbs": ["get", "patch"]}
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

        def do_PATCH(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            requests.append(body)
            if body["kind"] == "Secret" and "delivery.json" in body.get("stringData", {}):
                return self.respond(
                    {
                        "kind": "Status",
                        "reason": "Forbidden",
                        "message": "delivery rejected " + json.dumps(body),
                        "code": 403,
                    },
                    403,
                )
            self.respond(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = client.Configuration()
    config.host = f"http://127.0.0.1:{server.server_port}"
    config.debug = False
    config.retries = 0
    api = client.ApiClient(configuration=config)
    dynamic = KubernetesDynamicClient.from_api_client(api_client=api)
    cluster = K8sNativeClusterDriver(config=K8sNativeConfig(), k8s_client_factory=lambda **_: dynamic)
    try:
        yield replace(original._config, cluster_driver=cluster), spec, initial.handle, requests, private
    finally:
        api.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("action", ["provision", "update"])
def test_private_delivery_http_error_is_absent_from_result_and_telemetry(
    delivery_failure_http, action, monkeypatch, caplog, capsys, request
):
    cfg, spec, handle, requests, private = delivery_failure_http
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    request.addfinalizer(provider.shutdown)
    monkeypatch.setattr(_telemetry, "_tracer", provider.get_tracer("delivery-privacy-proof"))
    audits = []
    monkeypatch.setattr(_telemetry, "_audit_emit", lambda **event: audits.append(event))
    caplog.set_level(logging.INFO, logger="astrolift.providers")
    driver = VLLMDriver(config=cfg)
    result = (
        driver.provision(spec)
        if action == "provision"
        else driver.update(
            UpdateSpec(
                handle=handle,
                config=spec.config,
                managed_service_id=spec.managed_service_id,
                cluster_model=spec.cluster_model,
            )
        )
    )
    assert not result.ok
    assert result.errors == ["local_model_apply_failed"]
    submitted = next(row for row in requests if "delivery.json" in row.get("stringData", {}))
    assert private in submitted["stringData"]["delivery.json"]
    raw = cfg.cluster_driver.apply_manifests(spec.tenant_cluster_id, driver._namespace(spec), [submitted])
    assert not raw.ok and private in " ".join(raw.summary())
    output = capsys.readouterr()
    telemetry = json.dumps(
        [
            asdict(result),
            [record.__dict__ for record in caplog.records],
            [span.to_json() for span in exporter.get_finished_spans()],
            audits,
            output.out,
            output.err,
        ],
        default=str,
    )
    assert private not in telemetry
    assert "private-download-marker" not in telemetry
    assert caplog.records and exporter.get_finished_spans()
    if action == "provision":
        assert audits and not result.ready
