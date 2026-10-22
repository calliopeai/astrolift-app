"""Native HTTP clients: real init download and create-only Kaniko job transport.

The owned server speaks Kubernetes/archives; it never executes a build or
claims the synthetic registry digest is a runnable image.
"""

import hashlib
import io
import json
import threading
import urllib.error
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from kubernetes import client

from _sdk.build import BuildContextArchive, BuildSpec
from _sdk.k8s_dynamic_client import KubernetesDynamicClient
from k8s_native.build_archive_unpack import install
from k8s_native.build_kaniko import KanikoBuildDriver
from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig


@pytest.fixture
def wire():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("app/nested/main.py", "print('source')")
        archive.writestr("data/nested/database.sqlite", b"SQLite format 3\x00seed")
    state = {"raw": output.getvalue(), "objects": {}, "requests": [], "redirect": False, "failed": False}

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
            state["requests"].append(("GET", path, None))
            if path == "/archive":
                assert self.headers.get("Authorization") == "BuilderBuildArchive archive-private-marker"
                if state["redirect"]:
                    self.send_response(302)
                    self.send_header("Location", "/must-not-get-credential")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(state["raw"])))
                self.end_headers()
                self.wfile.write(state["raw"])
                return
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
                                "name": "batch",
                                "versions": [{"groupVersion": "batch/v1", "version": "v1"}],
                                "preferredVersion": {"groupVersion": "batch/v1", "version": "v1"},
                            }
                        ],
                    }
                )
            if path in ("/api/v1", "/apis/batch/v1"):
                version = "v1" if path == "/api/v1" else "batch/v1"
                kinds = {"Secret": "secrets"} if version == "v1" else {"Job": "jobs"}
                return self.respond(
                    {
                        "kind": "APIResourceList",
                        "groupVersion": version,
                        "resources": [
                            {"name": plural, "kind": kind, "namespaced": True, "verbs": ["get", "create", "delete"]}
                            for kind, plural in kinds.items()
                        ],
                    }
                )
            row = state["objects"].get(path)
            if row is None:
                return self.respond({"kind": "Status", "reason": "NotFound", "code": 404}, 404)
            if row["kind"] == "Job":
                row["status"] = {
                    "conditions": [{"type": "Failed" if state["failed"] else "Complete", "status": "True"}]
                }
            return self.respond(row)

        def do_POST(self):
            row = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            path = self.path.split("?")[0] + "/" + row["metadata"]["name"]
            state["requests"].append(("POST", path, row))
            if path in state["objects"]:
                return self.respond({"kind": "Status", "reason": "AlreadyExists", "code": 409}, 409)
            row["metadata"].update(uid="original-" + row["metadata"]["name"], resourceVersion="1")
            state["objects"][path] = row
            self.respond(row, 201)

        def do_PATCH(self):
            raise AssertionError("archive builds must use atomic create-only POST")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["origin"] = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def context(wire):
    raw = wire["raw"]
    return BuildContextArchive(
        url=wire["origin"] + "/archive",
        token="archive-private-marker",
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        expanded_bytes=1000,
        dockerfile="FROM python:3.12-slim\nCOPY app/ /app/\n",
        entrypoint="exec python /app/nested/main.py\n",
    )


def config(wire):
    from dataclasses import asdict

    return asdict(context(wire))


def test_real_http_download_carries_source_and_sqlite_and_checks_integrity(wire, tmp_path):
    install(config(wire), root=tmp_path)
    assert (tmp_path / "app/nested/main.py").read_text() == "print('source')"
    assert (tmp_path / "data/nested/database.sqlite").read_bytes() == b"SQLite format 3\x00seed"
    assert (tmp_path / "platform/Dockerfile").read_text().startswith("FROM python:")
    with pytest.raises(ValueError, match="integrity"):
        install(dict(config(wire), sha256="0" * 64), root=tmp_path / "bad")


def test_redirect_does_not_forward_archive_credential(wire, tmp_path):
    wire["redirect"] = True
    with pytest.raises(urllib.error.HTTPError):
        install(config(wire), root=tmp_path)
    assert not any(path == "/must-not-get-credential" for _, path, _ in wire["requests"])


@pytest.mark.parametrize("name", ["app/../escape", "/app/absolute", "platform/Dockerfile", "app//alias"])
def test_malicious_archive_path_is_rejected_over_real_http(wire, tmp_path, name):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(name, "overwrite")
    wire["raw"] = output.getvalue()
    with pytest.raises(ValueError, match="path"):
        install(config(wire), root=tmp_path)


@pytest.mark.parametrize("failed", [False, True])
def test_native_kubernetes_http_kaniko_archive_transport(wire, failed):
    wire["failed"] = failed
    configuration = client.Configuration()
    configuration.host = wire["origin"]
    configuration.retries = 0
    api = client.ApiClient(configuration=configuration)
    dynamic = KubernetesDynamicClient.from_api_client(api_client=api)
    cluster = K8sNativeClusterDriver(config=K8sNativeConfig(), k8s_client_factory=lambda **_: dynamic)
    try:
        result = KanikoBuildDriver(
            cluster_driver=cluster,
            cluster_slug="synthetic",
            namespace="project-build",
            service_account="operator-prepared",
            manage_service_account=False,
            build_id="owned-intent",
            poll_interval_seconds=0,
            timeout_seconds=5,
        ).build(
            BuildSpec(source_uri="builder-archive:source", context_archive=context(wire)),
            "registry.test/project",
            "intent-owned",
        )
        assert result.success is not failed
        writes = [row for method, path, row in wire["requests"] if method == "POST"]
        assert [row["kind"] for row in writes] == ["Secret", "Job"]
        assert all(row["metadata"]["namespace"] == "project-build" for row in writes)
        pod = writes[1]["spec"]["template"]["spec"]
        assert pod["serviceAccountName"] == "operator-prepared"
        assert pod["automountServiceAccountToken"] is False
        assert "archive-private-marker" not in json.dumps(writes[1])
        assert "--context=dir:///workspace" in pod["containers"][0]["args"]
        assert all(m["name"] != "archive" for m in pod["containers"][0]["volumeMounts"])
        assert result.digest == ""  # Registry observation, never Job status, supplies the digest.
    finally:
        api.close()
