"""Disposable real Prometheus and kube-state-metrics on explicitly owned Kind."""

import base64
import contextlib
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest
import yaml


@contextlib.contextmanager
def unavailable_cpu_proxy(endpoint):
    """Actual HTTP fault injection; all other queries reach the live collector."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            query = parse_qs(body.decode()).get("query", [""])[0]
            if "container_cpu_usage_seconds_total" in query:
                self.send_response(503)
                self.end_headers()
                return
            request = Request(
                endpoint + self.path, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            with urlopen(request, timeout=5) as upstream:
                data = upstream.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.fixture
def kind_prometheus(native, tmp_path, request):
    runtime, _, _, core, apps = native
    namespace = runtime.environment.k8s_namespace
    # A real scraped app exporter retains its original canonical identity even
    # when the database workload is replaced. It also publishes a legacy row.
    original_id = str(runtime.workload.guid)

    class Instrumentation(BaseHTTPRequestHandler):
        count = 0

        def do_GET(self):
            type(self).count += 1
            labels = {
                "app": runtime.app.slug,
                "environment": runtime.environment.name,
                "workload": runtime.workload.slug,
            }

            def metric_labels(values):
                return ",".join(key + "=" + json.dumps(value) for key, value in values.items())

            legacy = metric_labels(labels)
            canonical = metric_labels(
                dict(
                    labels,
                    astrolift_app_id=str(runtime.app.guid),
                    astrolift_environment_id=str(runtime.environment.guid),
                    astrolift_workload_id=original_id,
                )
            )
            body = (
                f'http_requests_total{{{legacy},code="200"}} {self.count}\n'
                + f'http_requests_total{{{canonical},code="200"}} {self.count}\n'
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    exporter = ThreadingHTTPServer(("0.0.0.0", 0), Instrumentation)
    exporter_thread = threading.Thread(target=exporter.serve_forever, daemon=True)
    exporter_thread.start()

    def stop_exporter():
        exporter.shutdown()
        exporter.server_close()
        exporter_thread.join(2)

    request.addfinalizer(stop_exporter)
    name = "signal-ksm-" + uuid4().hex[:8]
    from kubernetes import client

    api = client.RbacAuthorizationV1Api(core.api_client)
    core.create_namespaced_service_account(namespace, {"metadata": {"name": name}})
    api.create_namespaced_role(
        namespace,
        {
            "metadata": {"name": name},
            "rules": [{"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "watch"]}],
        },
    )
    api.create_namespaced_role_binding(
        namespace,
        {
            "metadata": {"name": name},
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": name},
            "subjects": [{"kind": "ServiceAccount", "name": name, "namespace": namespace}],
        },
    )
    core.create_namespaced_service(
        namespace,
        {
            "metadata": {"name": name},
            "spec": {"selector": {"signal-ksm": name}, "ports": [{"port": 8080, "targetPort": 8080}]},
        },
    )
    apps.create_namespaced_deployment(
        namespace,
        {
            "metadata": {"name": name},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"signal-ksm": name}},
                "template": {
                    "metadata": {"labels": {"signal-ksm": name}},
                    "spec": {
                        "serviceAccountName": name,
                        "containers": [
                            {
                                "name": "metrics",
                                "image": "registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.15.0",
                                "args": ["--resources=pods", f"--namespaces={namespace}"],
                                "ports": [{"containerPort": 8080}],
                            }
                        ],
                    },
                },
            },
        },
    )
    document = yaml.safe_load(Path(os.environ["ASTROLIFT_METRICS_TEST_KUBECONFIG"]).read_text())
    cluster, user = document["clusters"][0]["cluster"], document["users"][0]["user"]
    for filename, content in [
        ("ca.crt", cluster["certificate-authority-data"]),
        ("client.crt", user["client-certificate-data"]),
        ("client.key", user["client-key-data"]),
    ]:
        path = tmp_path / filename
        path.write_bytes(base64.b64decode(content))
        path.chmod(0o600)
    node = "astrolift-env-target-2217-a-control-plane"
    tls = {
        "ca_file": "/fixture/ca.crt",
        "cert_file": "/fixture/client.crt",
        "key_file": "/fixture/client.key",
        "server_name": "kubernetes",
    }
    target = [{"targets": [node + ":6443"]}]
    config = {
        "global": {"scrape_interval": "1s", "scrape_timeout": "1s"},
        "scrape_configs": [
            {
                "job_name": "fixture-app",
                "static_configs": [{"targets": ["host.docker.internal:" + str(exporter.server_port)]}],
            },
            {
                "job_name": "cadvisor",
                "scheme": "https",
                "tls_config": tls,
                "static_configs": target,
                "metrics_path": f"/api/v1/nodes/{node}/proxy/metrics/cadvisor",
            },
            {
                "job_name": "kube-state-metrics",
                "scheme": "https",
                "tls_config": tls,
                "static_configs": target,
                "metrics_path": f"/api/v1/namespaces/{namespace}/services/{name}:8080/proxy/metrics",
            },
        ],
    }
    (tmp_path / "prometheus.yml").write_text(yaml.safe_dump(config))
    container = "astrolift-signals-2219-prom-" + uuid4().hex[:8]
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            container,
            "--user",
            "0",
            "--network",
            "kind",
            "--publish",
            "127.0.0.1::9090",
            "--mount",
            f"type=bind,source={tmp_path},target=/fixture,readonly",
            "prom/prometheus:v3.13.1",
            "--config.file=/fixture/prometheus.yml",
        ],
        check=True,
        capture_output=True,
    )
    try:
        port = json.loads(
            subprocess.run(
                ["docker", "inspect", container], check=True, capture_output=True, text=True
            ).stdout
        )[0]["NetworkSettings"]["Ports"]["9090/tcp"][0]["HostPort"]
        endpoint = "http://127.0.0.1:" + port
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                with urlopen(endpoint + "/api/v1/targets", timeout=2) as response:
                    data = json.load(response)
                targets = data.get("data", {}).get("activeTargets", [])
                if len(targets) == 3 and all(row["health"] == "up" for row in targets):
                    break
            except (URLError, TimeoutError, ConnectionError):
                pass
            time.sleep(0.5)
        else:
            pytest.fail("task-owned Prometheus exporters did not become ready")
        runtime.cluster.provider_config = {"prometheus_endpoint": endpoint}
        runtime.cluster.auth_config = {
            "kubeconfig": Path(os.environ["ASTROLIFT_METRICS_TEST_KUBECONFIG"]).read_text()
        }
        from astrolift_clusters.models import ProviderPlugin

        plugin = ProviderPlugin.objects.filter(slug="k8s_native").first()
        if plugin is None:
            ProviderPlugin.objects.bulk_create(
                [
                    ProviderPlugin(
                        slug="k8s_native",
                        name="Native",
                        plugin_version="1.0.0",
                        capabilities_manifest={},
                        config_schema={},
                    )
                ]
            )
            plugin = ProviderPlugin.objects.get(slug="k8s_native")
        runtime.cluster.provider_plugin = plugin
        runtime.cluster.save()
        yield endpoint
    finally:
        subprocess.run(["docker", "rm", "--force", container], check=True, capture_output=True)
