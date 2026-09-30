"""Actual ASGI requests distinguish subscriber inference from operator telemetry."""

import json
import os
import shutil
import subprocess
import sys

import httpx
import pytest

from k8s_native.managed import shared_model_auth as auth

OPERATOR = "operator-" + "o" * 40
APP_A = "app-a-" + "a" * 40
APP_B = "app-b-" + "b" * 40


def test_mounted_module_runs_without_provider_package_or_site_dependencies(tmp_path):
    """Exercise the actual bare module import used by the mounted vLLM hook."""
    shutil.copyfile(auth.__file__, tmp_path / "astrolift_shared_model_auth.py")
    snapshot_path = tmp_path / "keys.json"
    snapshot_path.write_text(
        json.dumps({"version": 1, "revision": 7, "operator_key": OPERATOR, "subscription_keys": [APP_A]})
    )
    script = r"""
import asyncio
import importlib.util
import json
import pathlib
import sys

directory = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location(
    "astrolift_shared_model_auth", directory / "astrolift_shared_model_auth.py"
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module.KEYS_FILE = str(directory / "keys.json")
keys = json.loads((directory / "keys.json").read_text())

async def endpoint(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"actual-endpoint"})

async def receive():
    return {"type": "http.request", "body": b"", "more_body": False}

async def verify():
    guard = module.SharedModelAuth(endpoint)
    for method, path, key, expected in (
        ("POST", "/v1/chat/completions", keys["subscription_keys"][0], 200),
        ("GET", "/metrics", keys["subscription_keys"][0], 401),
        ("GET", "/metrics", keys["operator_key"], 200),
    ):
        messages = []
        async def send(message):
            messages.append(message)
        await guard(
            {"type": "http", "method": method, "path": path,
             "headers": [(b"authorization", ("Bearer " + key).encode("ascii"))]},
            receive, send,
        )
        assert messages[0]["status"] == expected
    assert "k8s_native" not in sys.modules
    assert "_sdk" not in sys.modules

asyncio.run(verify())
"""
    environment = os.environ.copy()
    environment["ASTROLIFT_MODEL_AUTH_REVISION"] = "7"
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(tmp_path)],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    path = tmp_path / "keys.json"
    data = {"version": 1, "revision": 7, "operator_key": OPERATOR, "subscription_keys": [APP_A, APP_B]}
    path.write_text(json.dumps(data))
    monkeypatch.setattr(auth, "KEYS_FILE", str(path))
    monkeypatch.setenv("ASTROLIFT_MODEL_AUTH_REVISION", "7")
    return path, data


class Endpoint:
    def __init__(self):
        self.calls = []

    async def __call__(self, scope, receive, send):
        self.calls.append(scope)
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"actual-endpoint"})


async def request(app, method, path, token=None, *, headers=None, root_path=""):
    transport = httpx.ASGITransport(app=app, root_path=root_path)
    async with httpx.AsyncClient(transport=transport, base_url="http://model.local") as client:
        return await client.request(
            method,
            path,
            headers=headers if headers is not None else {"Authorization": f"Bearer {token}"} if token else {},
        )


@pytest.mark.parametrize("token", [APP_A, APP_B, OPERATOR])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/models"),
        ("POST", "/v1/chat/completions"),
        ("POST", "/v1/completions"),
        ("POST", "/v1/embeddings"),
        ("POST", "/score"),
        ("POST", "/v1/score"),
        ("POST", "/rerank"),
        ("POST", "/v1/rerank"),
        ("POST", "/v2/rerank"),
    ],
)
async def test_real_asgi_model_requests_accept_independent_subscription_keys(snapshot, token, method, path):
    endpoint = Endpoint()
    response = await request(auth.SharedModelAuth(endpoint), method, path, token)
    assert response.status_code == 200
    assert len(endpoint.calls) == 1


@pytest.mark.parametrize("token", [None, APP_A, APP_B])
@pytest.mark.parametrize(
    "path", ["/metrics", "/metrics/", "/load", "/server_info", "/tokenizer_info", "/docs", "/unknown"]
)
async def test_subscriber_and_anonymous_requests_cannot_reach_operator_endpoint(snapshot, token, path):
    endpoint = Endpoint()
    response = await request(auth.SharedModelAuth(endpoint), "GET", path, token)
    assert response.status_code == 401
    assert response.json() == {"error": "Unauthorized"}
    assert response.headers["cache-control"] == "no-store"
    assert endpoint.calls == []
    assert all(key not in response.text for key in (OPERATOR, APP_A, APP_B))


@pytest.mark.parametrize("path", ["/metrics", "/load", "/server_info"])
async def test_operator_can_scrape_and_inspect_without_subscriber_credentials(snapshot, path):
    endpoint = Endpoint()
    response = await request(auth.SharedModelAuth(endpoint), "GET", path, OPERATOR)
    assert response.status_code == 200
    assert len(endpoint.calls) == 1


@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_exact_health_probe_is_public(snapshot, method):
    endpoint = Endpoint()
    response = await request(auth.SharedModelAuth(endpoint), method, "/health")
    assert response.status_code == 200
    assert len(endpoint.calls) == 1


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/health"),
        ("GET", "/health/"),
        ("OPTIONS", "/metrics"),
        ("OPTIONS", "/v1/chat/completions"),
        ("GET", "/v1/chat/completions"),
        ("POST", "/v1/load_lora_adapter"),
        ("POST", "/v1/unload_lora_adapter"),
        ("GET", "/v1/responses/other-app-response"),
        ("POST", "/v1/chat/completions/"),
        ("POST", "/v1/chat/completions%2f..%2f..%2fmetrics"),
    ],
)
async def test_unknown_paths_methods_and_operator_actions_do_not_inherit_v1_access(snapshot, method, path):
    endpoint = Endpoint()
    response = await request(auth.SharedModelAuth(endpoint), method, path, APP_A)
    assert response.status_code == 401
    assert endpoint.calls == []


@pytest.mark.parametrize(
    "headers",
    [
        [("Authorization", f"Bearer {APP_A}"), ("Authorization", f"Bearer {OPERATOR}")],
        {"Authorization": f"Basic {OPERATOR}"},
        {"Authorization": f"Bearer  {APP_A}"},
        {"Authorization": f"Bearer {APP_A} "},
        {"Authorization": "Bearer " + "x" * 257},
        {"Authorization": "Bearer " + "x" * 31},
        {"Authorization": "Bearer " + "x" * 40},
    ],
)
async def test_ambiguous_or_invalid_credentials_refuse_without_endpoint_call(snapshot, headers):
    endpoint = Endpoint()
    response = await request(auth.SharedModelAuth(endpoint), "POST", "/v1/chat/completions", headers=headers)
    assert response.status_code == 401
    assert endpoint.calls == []


async def test_root_path_does_not_bypass_operator_route_boundary(snapshot):
    endpoint = Endpoint()
    guard = auth.SharedModelAuth(endpoint)
    response = await request(guard, "GET", "/metrics", APP_A, root_path="/metrics")
    assert response.status_code == 401
    response = await request(guard, "POST", "/v1/chat/completions", APP_A, root_path="/prefix")
    assert response.status_code == 401
    assert endpoint.calls == []


async def test_key_file_update_requires_new_running_snapshot_and_preserves_other_app(snapshot, monkeypatch):
    path, data = snapshot
    old = auth.SharedModelAuth(Endpoint())
    data["revision"] = 8
    data["subscription_keys"] = [APP_B]
    path.write_text(json.dumps(data))
    assert (await request(old, "POST", "/v1/chat/completions", APP_A)).status_code == 200
    with pytest.raises(auth.SnapshotError):
        auth.SharedModelAuth(Endpoint())
    monkeypatch.setenv("ASTROLIFT_MODEL_AUTH_REVISION", "8")
    new = auth.SharedModelAuth(Endpoint())
    assert new.revision == 8
    assert (await request(new, "POST", "/v1/chat/completions", APP_A)).status_code == 401
    assert (await request(new, "POST", "/v1/chat/completions", APP_B)).status_code == 200
    assert (await request(new, "GET", "/metrics", APP_B)).status_code == 401
    assert (await request(new, "GET", "/metrics", OPERATOR)).status_code == 200


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"version": 2},
        {"revision": True},
        {"revision": -1},
        {"revision": 8},
        {"operator_key": "short"},
        {"operator_key": APP_A},
        {"subscription_keys": [APP_A, APP_A]},
        {"subscription_keys": "not-a-list"},
        {"subscription_keys": [None]},
        {"subscription_keys": ["a" * 257]},
        {"subscription_keys": ["app-" + str(i).zfill(40) for i in range(65)]},
        {"unexpected": "field"},
    ],
)
def test_invalid_key_snapshots_refuse_startup_without_disclosing_tokens(snapshot, change):
    path, data = snapshot
    path.write_text(json.dumps({**data, **change}))
    with pytest.raises(auth.SnapshotError) as caught:
        auth.SharedModelAuth(Endpoint())
    assert all(key not in str(caught.value) for key in (OPERATOR, APP_A, APP_B))


@pytest.mark.parametrize("body", [b"not-json", b"[]", b"{}", b"\xff", b" " * (auth.MAX_SNAPSHOT_BYTES + 1)])
def test_unavailable_and_malformed_snapshots_refuse_startup(snapshot, body):
    path, _data = snapshot
    path.write_bytes(body)
    with pytest.raises(auth.SnapshotError):
        auth.SharedModelAuth(Endpoint())
    path.unlink()
    with pytest.raises(auth.SnapshotError):
        auth.SharedModelAuth(Endpoint())


def test_duplicate_json_fields_are_not_silently_reinterpreted(snapshot):
    path, data = snapshot
    path.write_text(json.dumps(data)[:-1] + ',"revision":7}')
    with pytest.raises(auth.SnapshotError):
        auth.SharedModelAuth(Endpoint())


@pytest.mark.parametrize("revision", [None, "", "-1", "7.0", "07", "true", str(2**63), "7" * 100])
def test_expected_revision_requires_bounded_explicit_integer(snapshot, monkeypatch, revision):
    if revision is None:
        monkeypatch.delenv("ASTROLIFT_MODEL_AUTH_REVISION")
    else:
        monkeypatch.setenv("ASTROLIFT_MODEL_AUTH_REVISION", revision)
    with pytest.raises(auth.SnapshotError):
        auth.SharedModelAuth(Endpoint())


def test_snapshot_repr_never_contains_credentials(snapshot):
    path, _data = snapshot
    value = auth.load_key_snapshot(path, expected_revision=7)
    assert value.operator_key == OPERATOR
    assert value.subscription_keys == (APP_A, APP_B)
    assert all(key not in repr(value) for key in (OPERATOR, APP_A, APP_B))


@pytest.mark.parametrize("count", [0, 64])
def test_operator_only_and_full_subscription_snapshots_are_supported(snapshot, count):
    path, data = snapshot
    data["subscription_keys"] = ["app-" + str(index).zfill(40) for index in range(count)]
    path.write_text(json.dumps(data))
    loaded = auth.load_key_snapshot(path, expected_revision=7)
    assert len(loaded.subscription_keys) == count
    assert loaded.operator_key == OPERATOR


async def test_websocket_rejected_and_lifespan_passed_through(snapshot):
    endpoint = Endpoint()
    guard = auth.SharedModelAuth(endpoint)
    messages = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        messages.append(message)

    await guard({"type": "websocket", "path": "/v1/chat/completions"}, receive, send)
    assert messages == [{"type": "websocket.close", "code": 1008}]
    assert endpoint.calls == []
    await guard({"type": "lifespan"}, receive, send)
    assert endpoint.calls == [{"type": "lifespan"}]
