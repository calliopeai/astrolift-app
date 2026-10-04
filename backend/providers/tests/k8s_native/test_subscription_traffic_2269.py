"""Actual ASGI and owned TCP traffic with only synthetic mounted credentials."""

from __future__ import annotations

import asyncio
import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID

import httpx
import pytest

from k8s_native.managed import shared_model_auth as auth

OPERATOR = "operator-" + "o" * 40
APP_A = "app-a-" + "a" * 40
APP_B = "app-b-" + "b" * 40
FOREIGN = "foreign-" + "f" * 40
ID_A = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
ID_B = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
CANARY = "PRIVATE-PROMPT-AND-OUTPUT-CANARY"


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    path = tmp_path / "keys.json"
    data = {
        "version": 2,
        "revision": 7,
        "operator_key": OPERATOR,
        "subscription_keys": [APP_A, APP_B],
        "subscription_ids": [ID_A, ID_B],
    }
    path.write_text(json.dumps(data))
    monkeypatch.setattr(auth, "KEYS_FILE", str(path))
    monkeypatch.setenv("ASTROLIFT_MODEL_AUTH_REVISION", "7")
    return path, data


class Endpoint:
    def __init__(
        self, *, status=200, chunks=None, disconnect=False, error=False, cancel=False, encoding=None, openmetrics=False
    ):
        self.status = status
        self.chunks = chunks or [CANARY.encode()]
        self.disconnect = disconnect
        self.error = error
        self.cancel = cancel
        self.encoding = encoding
        self.openmetrics = openmetrics
        self.scopes = []

    async def __call__(self, scope, receive, send):
        self.scopes.append(scope)
        if scope["path"] == "/metrics":
            body = b"# TYPE upstream_fixture gauge\nupstream_fixture 42\n"
            content_type = b"text/plain; version=0.0.4"
            if self.openmetrics:
                body += b"# EOF\n"
                content_type = b"application/openmetrics-text; version=1.0.0; charset=utf-8"
            headers = [(b"content-type", content_type), (b"etag", b"stale-value")]
            if self.encoding == "gzip":
                body = gzip.compress(body)
            if self.encoding:
                headers.append((b"content-encoding", self.encoding.encode()))
            headers.append((b"content-length", str(len(body)).encode()))
            await send({"type": "http.response.start", "status": 200, "headers": headers})
            await send({"type": "http.response.body", "body": body[:10], "more_body": True})
            await send({"type": "http.response.body", "body": body[10:]})
            return
        await receive()
        await send({"type": "http.response.start", "status": self.status, "headers": []})
        for index, chunk in enumerate(self.chunks):
            await send(
                {
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": index < len(self.chunks) - 1 or self.disconnect or self.error or self.cancel,
                }
            )
        if self.disconnect:
            await receive()
        if self.error:
            raise RuntimeError("Synthetic app failure")
        if self.cancel:
            raise asyncio.CancelledError


async def request(app, method, path, token=None, *, headers=None, body=CANARY):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://owned-model.local") as client:
        return await client.request(
            method, path, content=body, headers=headers or {"Authorization": f"Bearer {token}"} if token else headers
        )


def value(text, name, identity, **labels):
    required = {"subscription_id": identity, **labels}
    rows = [
        row
        for row in text.splitlines()
        if row.startswith(name + "{")
        and all(f'{key}="{val}"' in row.partition("}")[0] for key, val in required.items())
    ]
    assert len(rows) == 1, rows
    return float(rows[0].rpartition(" ")[2])


async def metrics(guard):
    response = await request(guard, "GET", "/metrics", OPERATOR)
    assert response.status_code == 200
    return response.text


async def test_startup_v2_zero_series_prove_known_subscription_without_claiming_requests(snapshot):
    guard = auth.SharedModelAuth(Endpoint())
    text = await metrics(guard)
    for identity in (ID_A, ID_B):
        assert value(text, "astrolift_model_subscription_info", identity) == 2
        assert value(text, "astrolift_model_subscription_auth_revision", identity) == 7
        assert value(text, "astrolift_model_subscription_requests_total", identity, route="chat_completions") == 0
        assert value(text, "astrolift_model_subscription_response_bytes_total", identity, route="chat_completions") == 0
        assert (
            value(
                text, "astrolift_model_subscription_request_duration_seconds_count", identity, route="chat_completions"
            )
            == 0
        )


@pytest.mark.parametrize(("status", "status_class"), [(200, "2xx"), (429, "4xx"), (503, "5xx")])
async def test_status_class_and_server_key_identity_override_forged_headers(snapshot, status, status_class, caplog):
    guard = auth.SharedModelAuth(Endpoint(status=status))
    headers = {"Authorization": f"Bearer {APP_A}", "X-Subscription-Id": ID_B, "X-App-Id": CANARY}
    response = await request(guard, "POST", "/v1/chat/completions", headers=headers)
    assert response.status_code == status
    text = await metrics(guard)
    assert (
        value(
            text,
            "astrolift_model_subscription_requests_total",
            ID_A,
            route="chat_completions",
            status_class=status_class,
            outcome="completed",
        )
        == 1
    )
    assert value(text, "astrolift_model_subscription_requests_total", ID_B, route="chat_completions") == 0
    assert value(text, "astrolift_model_subscription_response_bytes_total", ID_A, route="chat_completions") == len(
        CANARY
    )
    assert (
        value(text, "astrolift_model_subscription_request_duration_seconds_count", ID_A, route="chat_completions") == 1
    )
    for private in (APP_A, APP_B, OPERATOR, CANARY, "X-App-Id", "X-Subscription-Id"):
        assert private not in text
        assert private not in caplog.text


@pytest.mark.parametrize(
    ("method", "path", "key", "status"),
    [
        ("GET", "/v1/models", APP_A, 200),
        ("POST", "/v1/chat/completions", OPERATOR, 200),
        ("POST", "/v1/chat/completions", FOREIGN, 401),
        ("POST", "/v1/load_lora_adapter", APP_A, 401),
        ("GET", "/metrics", APP_A, 401),
    ],
)
async def test_non_subscription_inference_never_falsely_attributes_traffic(snapshot, method, path, key, status):
    guard = auth.SharedModelAuth(Endpoint())
    assert (await request(guard, method, path, key)).status_code == status
    text = await metrics(guard)
    assert (
        value(text, "astrolift_model_subscription_request_duration_seconds_count", ID_A, route="chat_completions") == 0
    )
    assert FOREIGN not in text


async def test_streaming_many_chunks_counts_once_with_actual_successful_body_bytes(snapshot):
    chunks = [b"data: first\n\n", b"data: second\n\n", b"data: [DONE]\n\n"]
    guard = auth.SharedModelAuth(Endpoint(chunks=chunks))
    response = await request(guard, "POST", "/v1/completions", APP_B)
    assert response.content == b"".join(chunks)
    text = await metrics(guard)
    assert value(text, "astrolift_model_subscription_requests_total", ID_B, route="completions") == 1
    assert value(text, "astrolift_model_subscription_response_bytes_total", ID_B, route="completions") == sum(
        map(len, chunks)
    )
    assert (
        value(
            text, "astrolift_model_subscription_request_duration_seconds_bucket", ID_B, route="completions", le="+Inf"
        )
        == 1
    )


@pytest.mark.parametrize(
    ("option", "outcome"), [("disconnect", "disconnected"), ("error", "error"), ("cancel", "interrupted")]
)
async def test_disconnected_failed_and_canceled_streams_count_once_without_replacing_failure(snapshot, option, outcome):
    guard = auth.SharedModelAuth(Endpoint(**{option: True}))
    sent = []
    calls = 0

    async def receive():
        nonlocal calls
        calls += 1
        return {"type": "http.request", "body": CANARY.encode()} if calls == 1 else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": [(b"authorization", ("Bearer " + APP_A).encode())],
    }
    if option == "error":
        with pytest.raises(RuntimeError, match="Synthetic app failure"):
            await guard(scope, receive, send)
    elif option == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await guard(scope, receive, send)
    else:
        await guard(scope, receive, send)
    text = await metrics(guard)
    assert (
        value(text, "astrolift_model_subscription_requests_total", ID_A, route="chat_completions", outcome=outcome) == 1
    )
    assert (
        value(text, "astrolift_model_subscription_request_duration_seconds_count", ID_A, route="chat_completions") == 1
    )
    assert value(text, "astrolift_model_subscription_response_bytes_total", ID_A, route="chat_completions") == len(
        CANARY
    )
    assert sent[-1]["more_body"] is True


@pytest.mark.parametrize("encoding", [None, "gzip"])
@pytest.mark.parametrize("openmetrics", [False, True])
async def test_scrape_append_preserves_upstream_negotiation_framing_and_encoding(snapshot, encoding, openmetrics):
    endpoint = Endpoint(encoding=encoding, openmetrics=openmetrics)
    guard = auth.SharedModelAuth(endpoint)
    response = await request(guard, "GET", "/metrics", OPERATOR)
    assert "upstream_fixture 42\n" in response.text
    assert value(response.text, "astrolift_model_subscription_info", ID_A) == 2
    assert "etag" not in response.headers
    assert (
        (response.text.count("# EOF") == 1 and response.text.endswith("# EOF\n"))
        if openmetrics
        else "# EOF" not in response.text
    )
    assert (b"accept-encoding", b"identity") in endpoint.scopes[-1]["headers"]
    assert response.headers.get("content-encoding") == encoding
    if encoding is None:
        assert int(response.headers["content-length"]) == len(response.content)
    else:
        assert int(response.headers["content-length"]) == len(gzip.compress(response.content, mtime=0))


@pytest.mark.parametrize(
    "change",
    [
        {"subscription_ids": []},
        {"subscription_ids": [ID_A, ID_A]},
        {"subscription_ids": [ID_A.upper(), ID_B]},
        {"subscription_ids": ["bad", ID_B]},
        {"subscription_ids": [None, ID_B]},
        {"subscription_ids": [{}, ID_B]},
        {"subscription_ids": ["00000000-0000-0000-0000-000000000000", ID_B]},
        {"subscription_ids": "not-list"},
        {"subscription_keys": [APP_A, APP_A]},
        {"operator_key": APP_A},
        {"unexpected": True},
    ],
)
def test_malformed_v2_refuses_without_private_material(snapshot, change):
    path, data = snapshot
    path.write_text(json.dumps({**data, **change}))
    with pytest.raises(auth.SnapshotError) as caught:
        auth.SharedModelAuth(Endpoint())
    assert all(private not in str(caught.value) for private in (APP_A, APP_B, OPERATOR, ID_A, ID_B))


async def test_v1_legacy_snapshot_authenticates_but_never_emits_false_attribution(snapshot):
    path, data = snapshot
    data.pop("subscription_ids")
    data["version"] = 1
    path.write_text(json.dumps(data))
    guard = auth.SharedModelAuth(Endpoint())
    assert (await request(guard, "POST", "/v1/completions", APP_A)).status_code == 200
    text = await metrics(guard)
    assert "upstream_fixture 42" in text
    assert "astrolift_model_subscription" not in text


async def test_rotation_requires_new_loaded_snapshot_and_does_not_reassign_old_traffic(snapshot, monkeypatch):
    path, data = snapshot
    old = auth.SharedModelAuth(Endpoint())
    assert (await request(old, "POST", "/v1/completions", APP_A)).status_code == 200
    data.update(revision=8, subscription_keys=[APP_B], subscription_ids=[ID_B])
    path.write_text(json.dumps(data))
    assert value(await metrics(old), "astrolift_model_subscription_info", ID_A) == 2
    assert value(await metrics(old), "astrolift_model_subscription_auth_revision", ID_A) == 7
    monkeypatch.setenv("ASTROLIFT_MODEL_AUTH_REVISION", "8")
    new = auth.SharedModelAuth(Endpoint())
    assert (await request(new, "POST", "/v1/completions", APP_A)).status_code == 401
    assert (await request(new, "POST", "/v1/completions", APP_B)).status_code == 200
    text = await metrics(new)
    assert ID_A not in text
    assert value(text, "astrolift_model_subscription_auth_revision", ID_B) == 8
    assert value(text, "astrolift_model_subscription_requests_total", ID_B, route="completions") == 1


def test_owned_native_http_post_and_operator_scrape_have_no_header_attribution(snapshot):
    guard = auth.SharedModelAuth(Endpoint())

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch()

        def log_message(self, *args):
            pass

        def dispatch(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))

            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}

            async def send(message):
                if message["type"] == "http.response.start":
                    self.send_response(message["status"])
                    for name, value in message.get("headers", []):
                        self.send_header(name.decode(), value.decode())
                    self.end_headers()
                elif message["type"] == "http.response.body":
                    self.wfile.write(message.get("body", b""))
                    self.wfile.flush()

            asyncio.run(
                guard(
                    {
                        "type": "http",
                        "method": self.command,
                        "path": self.path,
                        "headers": [(name.lower().encode(), value.encode()) for name, value in self.headers.items()],
                    },
                    receive,
                    send,
                )
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            response = client.post(
                "/v1/chat/completions",
                content=CANARY,
                headers={"Authorization": f"Bearer {APP_A}", "X-Subscription-Id": ID_B},
            )
            assert response.status_code == 200
            assert client.get("/metrics", headers={"Authorization": f"Bearer {APP_A}"}).status_code == 401
            text = client.get("/metrics", headers={"Authorization": f"Bearer {OPERATOR}"}).text
            assert value(text, "astrolift_model_subscription_requests_total", ID_A, route="chat_completions") == 1
            assert value(text, "astrolift_model_subscription_requests_total", ID_B, route="chat_completions") == 0
            assert CANARY not in text
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


async def test_pre_response_failure_and_failed_send_record_one_honest_error(snapshot):
    async def failed_before_start(scope, receive, send):
        raise RuntimeError("before start")

    guard = auth.SharedModelAuth(failed_before_start)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": [(b"authorization", ("Bearer " + APP_A).encode())],
    }

    async def receive():
        return {"type": "http.request", "body": CANARY.encode()}

    async def send(message):
        pass

    with pytest.raises(RuntimeError, match="before start"):
        await guard(scope, receive, send)
    text = guard._metrics.render().decode()
    assert (
        value(
            text,
            "astrolift_model_subscription_requests_total",
            ID_A,
            route="chat_completions",
            status_class="none",
            outcome="error",
        )
        == 1
    )
    assert value(text, "astrolift_model_subscription_response_bytes_total", ID_A, route="chat_completions") == 0
    guard = auth.SharedModelAuth(Endpoint())

    async def disconnected_send(message):
        if message["type"] == "http.response.body":
            raise BrokenPipeError("Synthetic failed send")

    with pytest.raises(BrokenPipeError):
        await guard(scope, receive, disconnected_send)
    text = guard._metrics.render().decode()
    assert (
        value(text, "astrolift_model_subscription_requests_total", ID_A, route="chat_completions", outcome="error") == 1
    )
    assert value(text, "astrolift_model_subscription_response_bytes_total", ID_A, route="chat_completions") == 0


@pytest.mark.parametrize(
    "case", ["br", "gzip-bomb", "bad-gzip", "openmetrics-no-eof", "duplicate-encoding", "non-text", "non-200"]
)
def test_untransformable_upstream_metrics_are_never_reinterpreted(snapshot, case):
    metrics = auth.SubscriptionMetrics((ID_A,), 7)
    headers = [(b"content-type", b"text/plain")]
    body = b"upstream_fixture 1\n"
    status = 200
    if case == "br":
        headers.append((b"content-encoding", b"br"))
    elif case == "gzip-bomb":
        headers.append((b"content-encoding", b"gzip"))
        body = gzip.compress(b" " * (auth.MAX_UPSTREAM_METRICS_BYTES + 1))
    elif case == "bad-gzip":
        headers.append((b"content-encoding", b"gzip"))
    elif case == "openmetrics-no-eof":
        headers = [(b"content-type", b"application/openmetrics-text")]
    elif case == "duplicate-encoding":
        headers.extend([(b"content-encoding", b"identity"), (b"content-encoding", b"gzip")])
    elif case == "non-text":
        headers = [(b"content-type", b"application/json")]
    else:
        status = 503
    start = {"type": "http.response.start", "status": status, "headers": headers}
    assert auth._append_metrics(start, body, metrics) is None


async def test_oversized_metrics_passthrough_preserves_exact_original_bytes_and_headers(snapshot):
    body = b"# fixture\n" + b" " * auth.MAX_UPSTREAM_METRICS_BYTES
    start = {
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode())],
    }

    async def upstream(scope, receive, send):
        await send(start)
        await send({"type": "http.response.body", "body": body[:20], "more_body": True})
        await send({"type": "http.response.body", "body": body[20:]})

    guard = auth.SharedModelAuth(upstream)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    await guard(
        {
            "type": "http",
            "method": "GET",
            "path": "/metrics",
            "headers": [(b"authorization", ("Bearer " + OPERATOR).encode())],
        },
        receive,
        send,
    )
    assert sent[0] == start
    assert b"".join(message["body"] for message in sent[1:]) == body
    assert b"astrolift_model_subscription" not in body


def test_all64_valid_subscriptions_and_empty_v2_have_bounded_initialized_series(snapshot):
    path, data = snapshot
    for count in (0, 64):
        data.update(
            subscription_keys=["subscriber-" + str(index).zfill(40) for index in range(count)],
            subscription_ids=[str(UUID(int=index + 1)) for index in range(count)],
        )
        path.write_text(json.dumps(data))
        guard = auth.SharedModelAuth(Endpoint())
        text = guard._metrics.render().decode()
        assert text.count("astrolift_model_subscription_info{") == count
        assert len(guard._metrics.bytes) == count * 5
        assert len(guard._metrics.requests) == count * 5
        assert len(text) < 1024 * 1024
    data["subscription_keys"].append("extra-" + "x" * 40)
    data["subscription_ids"].append(ID_A)
    path.write_text(json.dumps(data))
    with pytest.raises(auth.SnapshotError):
        auth.SharedModelAuth(Endpoint())


@pytest.mark.parametrize(("path", "route"), sorted(auth._TRAFFIC_ROUTES.items()))
async def test_every_fixed_inference_route_is_recorded_under_canonical_label(snapshot, path, route):
    guard = auth.SharedModelAuth(Endpoint())
    assert (await request(guard, "POST", path, APP_A)).status_code == 200
    text = await metrics(guard)
    assert value(text, "astrolift_model_subscription_requests_total", ID_A, route=route) == 1
    assert f'route="{path}"' not in text


def test_v2_mounted_module_runs_under_bare_python_without_site_packages(snapshot, tmp_path):
    import os
    import shutil
    import subprocess
    import sys

    shutil.copyfile(auth.__file__, tmp_path / "astrolift_shared_model_auth.py")
    script = r"""
import asyncio, importlib.util, pathlib, sys
root = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("astrolift_shared_model_auth", root / "astrolift_shared_model_auth.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module.KEYS_FILE = str(root / "keys.json")
snapshot = module.load_key_snapshot(module.KEYS_FILE, expected_revision=7)
assert snapshot.version == 2 and len(snapshot.subscription_ids) == 2
async def endpoint(scope, receive, send):
    await send({"type":"http.response.start", "status":200, "headers":[(b"content-type",b"text/plain")]})
    await send({"type":"http.response.body", "body":b"upstream 1\n"})
async def receive(): return {"type":"http.request", "body":b""}
async def main():
    guard = module.SharedModelAuth(endpoint)
    sent = []
    async def send(message): sent.append(message)
    scope = {"type":"http", "method":"GET", "path":"/metrics",
             "headers":[(b"authorization", ("Bearer " + snapshot.operator_key).encode())]}
    await guard(scope, receive, send)
    assert b"astrolift_model_subscription_info" in sent[-1]["body"]
asyncio.run(main())
assert "k8s_native" not in sys.modules and "_sdk" not in sys.modules
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


@pytest.mark.parametrize("openmetrics", [False, True])
@pytest.mark.parametrize("count", [0, 2, 64])
def test_actual_prometheus_and_openmetrics_parsers_accept_complete_exposition(snapshot, openmetrics, count):
    from prometheus_client.openmetrics.parser import text_string_to_metric_families as parse_openmetrics
    from prometheus_client.parser import text_string_to_metric_families

    identities = tuple(str(UUID(int=index + 1)) for index in range(count))
    observations = auth.SubscriptionMetrics(identities, 7)
    if identities:
        observations.record(identities[0], "chat_completions", 200, "completed", 24, 0.2)
    content_type = b"application/openmetrics-text" if openmetrics else b"text/plain"
    body = b"# TYPE upstream_fixture gauge\nupstream_fixture 42\n" + (b"# EOF\n" if openmetrics else b"")
    start = {"status": 200, "headers": [(b"content-type", content_type)]}
    _start, result = auth._append_metrics(start, body, observations)
    parse = parse_openmetrics if openmetrics else text_string_to_metric_families
    families = list(parse(result.decode()))
    assert len(families) == 6
    assert families[0].name == "upstream_fixture"
    samples = [sample for family in families for sample in family.samples]
    assert len([sample for sample in samples if sample.name == "astrolift_model_subscription_info"]) == count
    if identities:
        requests = [
            sample
            for sample in samples
            if sample.name == "astrolift_model_subscription_requests_total"
            and sample.labels["subscription_id"] == identities[0]
            and sample.labels["route"] == "chat_completions"
        ]
        assert len(requests) == 1 and requests[0].value == 1
