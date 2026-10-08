"""Exercise private artifact transport against the real database and HTTP surface."""

import base64
import io
import json
import zipfile
from threading import Thread
from wsgiref.simple_server import make_server

import pytest
from django.test import Client

from astrolift_lifecycle.builder_artifacts import pack, reference
from astrolift_lifecycle.tests import test_builder_api as fixtures
from astrolift_lifecycle.tests.test_builder_api import (
    _running_dev_env,
    _sync,
)
from astrolift_workflows.activities.dev_environment import _render_runtime

org = fixtures.org
other_org = fixtures.other_org
team = fixtures.team
user = fixtures.user
provider_plugin = fixtures.provider_plugin
cluster = fixtures.cluster
api_token = fixtures.api_token
auth_headers = fixtures.auth_headers
workflow_starts = fixtures.workflow_starts
pytestmark = pytest.mark.django_db


def test_large_nested_binary_bundle_and_small_manifests(org, cluster, user, auth_headers, workflow_starts):
    dev = _running_dev_env(org, cluster, user)
    data = b"\x00asm" + bytes(range(256)) * 16000
    response = _sync(
        Client(),
        dev,
        {
            "files": {
                "libraries/engine.wasm": {"content": base64.b64encode(data).decode(), "encoding": "base64"},
                "index.html": "ok",
            }
        },
        auth_headers,
    )
    assert response.status_code == 200, response.content
    dev.refresh_from_db()
    resources = _render_runtime(dev, namespace="test", name="app", hostname="app.test")
    assert not any(r["kind"] == "ConfigMap" for r in resources)
    assert len(json.dumps(resources)) < 16000
    pod = next(r for r in resources if r["kind"] == "Deployment")["spec"]["template"]["spec"]
    assert pod["initContainers"][0]["name"] == "fetch-artifact"
    assert all(m["name"] != "artifact" for m in pod["containers"][0]["volumeMounts"])
    ref = reference(dev)
    r = Client().get(ref["url"], HTTP_AUTHORIZATION="BuilderArtifact " + ref["token"])
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(b"".join(r.streaming_content))) as archive:
        assert archive.read("app/libraries/engine.wasm") == data


def test_artifact_grant_is_scoped_immutable_and_revocable(org, cluster, user, other_org):
    dev = _running_dev_env(org, cluster, user)
    dev.files = {"main.js": "first"}
    ref = reference(dev)
    client = Client()
    assert client.get(ref["url"]).status_code == 403
    assert (
        client.get(ref["url"], HTTP_AUTHORIZATION="BuilderArtifact " + ref["token"] + "x").status_code == 403
    )
    other = _running_dev_env(other_org, cluster, user)
    wrong = ref["url"].replace(str(dev.guid), str(other.guid))
    assert client.get(wrong, HTTP_AUTHORIZATION="BuilderArtifact " + ref["token"]).status_code == 403
    dev.files = {"main.js": "second"}
    updated = reference(dev)
    assert updated["sha256"] != ref["sha256"]
    r = client.get(ref["url"], HTTP_AUTHORIZATION="BuilderArtifact " + ref["token"])
    with zipfile.ZipFile(io.BytesIO(b"".join(r.streaming_content))) as archive:
        assert archive.read("app/main.js") == b"first"
    dev.soft_delete()
    assert client.get(ref["url"], HTTP_AUTHORIZATION="BuilderArtifact " + ref["token"]).status_code == 404


def test_actual_init_download_integrity_and_persistent_seed(org, cluster, user, tmp_path):
    dev = _running_dev_env(org, cluster, user)
    dev.files = {"nested/main.js": "hello"}
    dev.data_file_path = "data.sqlite"
    dev.data_file = b"seed"
    ref = reference(dev)
    raw = pack(dev)

    def serve(environ, start_response):
        assert environ["HTTP_AUTHORIZATION"] == "BuilderArtifact " + ref["token"]
        start_response("200 OK", [("Content-Length", str(len(raw)))])
        return [raw]

    server = make_server("127.0.0.1", 0, serve)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        ref["url"] = f"http://127.0.0.1:{server.server_port}/artifact"
        from astrolift_lifecycle.builder_unpack import install

        app, data = tmp_path / "app", tmp_path / "data"
        install(ref, app, data)
        assert (app / "nested/main.js").read_text() == "hello"
        assert (data / "data.sqlite").read_bytes() == b"seed"
        (data / "data.sqlite").write_bytes(b"user writes")
        install(ref, app, data)
        assert (data / "data.sqlite").read_bytes() == b"user writes"
        with pytest.raises(ValueError, match="integrity"):
            install(dict(ref, sha256="0" * 64), app, data)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_capabilities_require_bearer_and_report_configured_capacity(auth_headers):
    client = Client()
    assert client.get("/api/builder/v1/capabilities/").status_code == 401
    result = client.get("/api/builder/v1/capabilities/", **auth_headers)
    assert result.status_code == 200, result.content
    assert result.json()["max_file_bytes"] == 64 * 1024 * 1024
    assert result.json()["nested_paths"] is True


def test_promoted_app_retains_artifact_after_preview_removal(org, cluster, user, team):
    from astrolift_registry.models import RegisteredApp

    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        name="Portable",
        slug="portable",
        source_kind=RegisteredApp.SourceKind.DIRECT_UPLOAD,
    )
    dev = _running_dev_env(org, cluster, user)
    dev.promoted_app = app
    dev.save()
    ref = reference(dev)
    dev.soft_delete()
    response = Client().get(ref["url"], HTTP_AUTHORIZATION="BuilderArtifact " + ref["token"])
    assert response.status_code == 200
    assert b"".join(response.streaming_content).startswith(b"PK")
    app.soft_delete()
    assert Client().get(ref["url"], HTTP_AUTHORIZATION="BuilderArtifact " + ref["token"]).status_code == 404


def test_each_runtime_revision_keeps_its_own_download_reference(org, cluster, user):
    dev = _running_dev_env(org, cluster, user)
    dev.files = {"app.js": "first"}
    first = _render_runtime(dev, namespace="test", name="app", hostname="app.test")
    dev.files = {"app.js": "second"}
    second = _render_runtime(dev, namespace="test", name="app", hostname="app.test")
    secrets = [
        next(r for r in resources if r["kind"] == "Secret")["metadata"]["name"]
        for resources in (first, second)
    ]
    assert secrets[0] != secrets[1]
    for resources, secret in zip((first, second), secrets, strict=True):
        pod = next(r for r in resources if r["kind"] == "Deployment")["spec"]["template"]["spec"]
        assert next(v for v in pod["volumes"] if v["name"] == "artifact")["secret"]["secretName"] == secret
