"""Opt-in real cluster DNS/connection proof; no database operators are installed."""

import json
import os
import subprocess
import uuid

import pytest

from _sdk.managed_service import ServiceHandle
from k8s_native.managed._handle import pack
from tests.k8s_native.test_binding_namespaces_2092 import CASES


def test_generated_bindings_connect_to_the_recorded_namespace_with_custom_dns_domain():
    kubeconfig = os.environ.get("ASTROLIFT_BINDING_DNS_KUBECONFIG")
    if not kubeconfig:
        pytest.skip("requires an explicit disposable astrolift-binding-dns-2092 kubeconfig")

    def kubectl(*args, body=None, timeout=90):
        result = subprocess.run(
            ["kubectl", "--kubeconfig", kubeconfig, *args],
            input=json.dumps(body) if body else None,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=True,
        )
        return result.stdout

    assert kubectl("config", "current-context").strip() == "kind-astrolift-binding-dns-2092"
    suffix = uuid.uuid4().hex[:8]
    service_ns, consumer_ns = f"service-{suffix}", f"consumer-{suffix}"
    names = []
    endpoints = []
    resources = []
    for namespace, marker in ((service_ns, "service-scope"), (consumer_ns, "wrong-consumer-scope")):
        resources.extend(
            [
                {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}},
                {
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": "server", "namespace": namespace, "labels": {"app": "server"}},
                    "spec": {
                        "containers": [
                            {
                                "name": "server",
                                "image": "python:3.12-alpine",
                                "imagePullPolicy": "Never",
                                "command": [
                                    "python",
                                    "-u",
                                    "-c",
                                    "from http.server import HTTPServer,BaseHTTPRequestHandler\n"
                                    "class H(BaseHTTPRequestHandler):\n"
                                    " def do_GET(self):\n  self.send_response(200);self.end_headers();self.wfile.write("
                                    + repr(marker.encode())
                                    + ")\n"
                                    "HTTPServer(('0.0.0.0',8080),H).serve_forever()",
                                ],
                            }
                        ]
                    },
                },
            ]
        )
    for index, (driver_cls, config_cls, key, tail, prefix, _ending) in enumerate(CASES):
        name = f"binding-{index}"
        handle = pack(kind="service", cluster_id="proof-cluster", namespace=service_ns, name=name)
        binding = driver_cls(config=config_cls()).binding(ServiceHandle(handle))
        literal = binding.env_vars[key].literal
        if key in ("POSTGRES_HOST", "REDIS_HOST", "MYSQL_HOST", "RABBITMQ_HOST"):
            host = literal
        else:
            from urllib.parse import urlsplit

            host = urlsplit(literal if "://" in literal else f"tcp://{literal}").hostname
        port = {"POSTGRES_HOST": 5432, "REDIS_HOST": 6379, "MYSQL_HOST": 3306, "RABBITMQ_HOST": 5672}.get(
            key, 4222 if prefix == "nats://" else 9092 if not prefix else 27017
        )
        short = name + tail
        names.append(short)
        endpoints.append((host, port, short))
        for namespace in (service_ns, consumer_ns):
            resources.append(
                {
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": {"name": short, "namespace": namespace},
                    "spec": {
                        "selector": {"app": "server"},
                        "ports": [{"name": "tcp", "port": port, "targetPort": 8080}],
                    },
                }
            )
    try:
        kubectl("apply", "-f", "-", body={"apiVersion": "v1", "kind": "List", "items": resources})
        for namespace in (service_ns, consumer_ns):
            kubectl("wait", "--for=condition=Ready", "pod/server", "-n", namespace, "--timeout=60s")
        program = (
            "import urllib.request\n"
            "assert 'private.test' in open('/etc/resolv.conf').read()\n"
            f"endpoints={endpoints!r}\n"
            "for host,port,short in endpoints:\n"
            " assert urllib.request.urlopen(f'http://{host}:{port}',timeout=5).read()==b'service-scope'\n"
            " assert urllib.request.urlopen(f'http://{short}:{port}',timeout=5).read()==b'wrong-consumer-scope'\n"
            "print('7 generated bindings reached recorded service namespace; '"
            "'7 short names reached wrong consumer namespace; custom DNS domain private.test')\n"
        )
        pod = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": "client", "namespace": consumer_ns},
            "spec": {
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": "client",
                        "image": "python:3.12-alpine",
                        "imagePullPolicy": "Never",
                        "command": ["python", "-u", "-c", program],
                    }
                ],
            },
        }
        kubectl("apply", "-f", "-", body=pod)
        kubectl("wait", "--for=jsonpath={.status.phase}=Succeeded", "pod/client", "-n", consumer_ns, "--timeout=60s")
        output = kubectl("logs", "client", "-n", consumer_ns)
        assert "7 generated bindings reached recorded service namespace" in output
        print(output.strip())
    finally:
        for namespace in (consumer_ns, service_ns):
            kubectl("delete", "namespace", namespace, "--ignore-not-found=true", "--wait=false")
