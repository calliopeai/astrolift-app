"""Actual boto3 HTTPS and native downloader proof, without cloud or inference."""

import base64
import hashlib
import json
import ssl
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config

from _sdk.local_model_artifact import (
    ArtifactStoreUnavailable,
    ModelFile,
    VersionedModelStore,
    model_manifest,
    model_object_key,
)
from k8s_native.managed.local_model_delivery import delivery_resources, hydrate, runtime_sources


def contents():
    header = json.dumps(
        {"fixture": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}, separators=(",", ":")
    ).encode()
    return {
        "config.json": b'{"model_type":"llama"}',
        "tokenizer.json": b'{"version":"1.0"}',
        "model.safetensors": len(header).to_bytes(8, "little") + header + b"\x00\x00\x00\x00",
    }


@pytest.fixture
def model_s3_wire(tmp_path, monkeypatch):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        capture_output=True,
        check=True,
    )
    state = {"objects": {}, "current": {}, "calls": [], "versioning": "Enabled", "public_block": True, "sequence": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            state["calls"].append(("GET", parsed.path, urllib.parse.parse_qs(parsed.query)))
            if parsed.query == "publicAccessBlock":
                value = "true" if state["public_block"] else "false"
                body = (
                    '<PublicAccessBlockConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                    + "".join(
                        f"<{key}>{value}</{key}>"
                        for key in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")
                    )
                    + "</PublicAccessBlockConfiguration>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.query == "versioning":
                body = (
                    '<VersioningConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Status>'
                    + state["versioning"]
                    + "</Status></VersioningConfiguration>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.object(head=False)

        def do_HEAD(self):
            parsed = urllib.parse.urlsplit(self.path)
            state["calls"].append(("HEAD", parsed.path, urllib.parse.parse_qs(parsed.query)))
            self.object(head=True)

        def object(self, *, head):
            parsed = urllib.parse.urlsplit(self.path)
            version = urllib.parse.parse_qs(parsed.query).get("versionId", [state["current"].get(parsed.path)])[0]
            body = state["objects"].get((parsed.path, version))
            if body is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if head and state.get("after_head"):
                state["after_head"]()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("x-amz-version-id", version)
            self.send_header("x-amz-checksum-sha256", base64.b64encode(hashlib.sha256(body).digest()).decode())
            self.send_header("x-amz-checksum-type", "FULL_OBJECT")
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def do_PUT(self):
            parsed = urllib.parse.urlsplit(self.path)
            state["calls"].append(("PUT", parsed.path, urllib.parse.parse_qs(parsed.query)))
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("x-amz-checksum-sha256") != base64.b64encode(hashlib.sha256(body).digest()).decode():
                self.send_response(400)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            state["sequence"] += 1
            version = "version-" + str(state["sequence"])
            state["objects"][(parsed.path, version)] = body
            state["current"][parsed.path] = version
            self.send_response(200)
            self.send_header("x-amz-version-id", version)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"https://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    client = boto3.client(
        "s3",
        endpoint_url=url,
        region_name="us-east-1",
        aws_access_key_id="owned-fixture",
        aws_secret_access_key="owned-fixture-only",
        verify=str(cert),
        config=Config(signature_version="s3v4", connect_timeout=1, read_timeout=1, retries={"total_max_attempts": 1}),
    )
    yield {**state, "state": state, "client": client, "url": url, "cert": cert}
    client.close()
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def uploaded_plan(wire):
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(wire["cert"])))
    )
    files, digest = model_manifest(
        [ModelFile(name, len(body), hashlib.sha256(body).hexdigest()) for name, body in contents().items()]
    )
    checks = []
    store = VersionedModelStore(
        client=wire["client"], bucket="owned-models", checkpoint=lambda: checks.append("authority")
    )
    store.require_versioning()
    plan = {"manifest_sha256": digest, "files": []}
    for file in files:
        key = model_object_key(str(uuid4()), str(uuid4()), str(uuid4()))
        url, headers = store.upload(key, file)
        signed = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["X-Amz-SignedHeaders"][0]
        assert "content-length" in signed and "x-amz-checksum-sha256" in signed
        with opener.open(
            urllib.request.Request(url, data=contents()[file["name"]], headers=headers, method="PUT"), timeout=2
        ) as response:
            assert response.status == 200
        version = store.verified_version(key, file)
        plan["files"].append({**file, "url": store.download(key, file, version)})
    assert len(checks) >= len(wire["state"]["calls"])
    return plan


def test_actual_signed_upload_versioned_delivery_and_atomic_hydration(model_s3_wire, tmp_path):
    plan = uploaded_plan(model_s3_wire)
    # Same-key replacement cannot redirect a download pinned to the accepted version.
    state = model_s3_wire["state"]
    for path in state["current"]:
        state["objects"][(path, "foreign-replacement")] = b"other bytes"
        state["current"][path] = "foreign-replacement"
    path = hydrate(plan, tmp_path / "models")
    assert {file.name: file.read_bytes() for file in path.iterdir()} == contents()
    calls = len(state["calls"])
    assert hydrate(plan, tmp_path / "models") == path
    assert len(state["calls"]) == calls
    assert all(call[2].get("versionId") for call in state["calls"] if call[0] == "HEAD" and "versionId" in call[2])


def test_unversioned_store_and_wrong_checksum_refuse(model_s3_wire):
    store = VersionedModelStore(client=model_s3_wire["client"], bucket="owned-models")
    model_s3_wire["state"]["versioning"] = "Suspended"
    with pytest.raises(ArtifactStoreUnavailable, match="versioned"):
        store.require_versioning()
    with pytest.raises(ArtifactStoreUnavailable) as caught:
        store.verified_version("missing", {"size_bytes": 1, "sha256": "a" * 64})
    assert model_s3_wire["url"] not in str(caught.value)


def test_public_bucket_refused_before_upload_authorization(model_s3_wire):
    model_s3_wire["state"]["public_block"] = False
    store = VersionedModelStore(client=model_s3_wire["client"], bucket="owned-models")
    with pytest.raises(ArtifactStoreUnavailable, match="public-access"):
        store.require_versioning()
    assert not any(call[0] in ("HEAD", "PUT") for call in model_s3_wire["state"]["calls"])


def test_corruption_does_not_publish_and_retry_is_stable(model_s3_wire, tmp_path):
    plan = uploaded_plan(model_s3_wire)
    path = urllib.parse.urlsplit(plan["files"][1]["url"]).path
    version = urllib.parse.parse_qs(urllib.parse.urlsplit(plan["files"][1]["url"]).query)["versionId"][0]
    old = model_s3_wire["state"]["objects"][(path, version)]
    model_s3_wire["state"]["objects"][(path, version)] = b"broken"
    with pytest.raises(ValueError):
        hydrate(plan, tmp_path / "models")
    assert not (tmp_path / "models" / plan["manifest_sha256"]).exists()
    assert not list((tmp_path / "models").iterdir())
    model_s3_wire["state"]["objects"][(path, version)] = old
    assert hydrate(plan, tmp_path / "models").is_dir()


def test_runtime_source_is_self_contained_and_private_error_is_sanitized(tmp_path, capsys):
    files = runtime_sources()
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    plan = {"manifest_sha256": "a" * 64, "files": [{"url": "https://signed-private-marker.invalid"}]}
    (tmp_path / "delivery.json").write_text(json.dumps(plan))
    result = subprocess.run(
        [
            sys.executable,
            str(tmp_path / "local_model_delivery.py"),
            str(tmp_path / "delivery.json"),
            str(tmp_path / "models"),
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 1
    assert "signed-private-marker" not in result.stdout + result.stderr
    assert "unverified" in result.stderr and "Traceback" not in result.stderr
    resources = delivery_resources(
        namespace="owned",
        name="model",
        service_id=str(uuid4()),
        image="vllm/runtime@sha256:" + "a" * 64,
        pvc_name="model-cache",
        manifest_sha256="a" * 64,
        private_plan=plan,
    )
    assert resources["model_mount"]["readOnly"] is True
    assert "signed-private-marker" not in json.dumps(resources["init_container"])


def test_actual_standalone_hydrator_downloads_private_versions(model_s3_wire, tmp_path):
    plan = uploaded_plan(model_s3_wire)
    for name, content in runtime_sources().items():
        (tmp_path / name).write_text(content)
    (tmp_path / "delivery.json").write_text(json.dumps(plan))
    result = subprocess.run(
        [
            sys.executable,
            str(tmp_path / "local_model_delivery.py"),
            str(tmp_path / "delivery.json"),
            str(tmp_path / "models"),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    assert {
        file.name: file.read_bytes() for file in (tmp_path / "models" / plan["manifest_sha256"]).iterdir()
    } == contents()


def test_cached_model_cannot_bypass_remote_code_refusal(tmp_path):
    bodies = {**contents(), "config.json": b'{"model_type":"llama","auto_map":{"AutoModel":"evil.Code"}}'}
    manifest, digest = model_manifest(
        [ModelFile(name, len(body), hashlib.sha256(body).hexdigest()) for name, body in bodies.items()]
    )
    directory = tmp_path / digest
    directory.mkdir()
    for name, body in bodies.items():
        (directory / name).write_bytes(body)
    with pytest.raises(ValueError, match="remote code"):
        hydrate({"manifest_sha256": digest, "files": manifest}, tmp_path)


@pytest.mark.parametrize("size", [5_000_000_001, True, 0])
def test_single_put_limit_refused_without_network(size):
    files = [
        ModelFile(name, size if name.endswith(".safetensors") else len(body), hashlib.sha256(body).hexdigest())
        for name, body in contents().items()
    ]
    with pytest.raises(ValueError, match="5 GB"):
        model_manifest(files)
