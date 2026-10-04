"""Actual ASGI and loopback HTTP exercise the strict gateway, not Azure inference."""

import asyncio
import contextlib
import json
import logging
import threading
import traceback
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from azure import foundry_gateway as gateway
from k8s_native.managed import shared_model_auth as auth

OPERATOR = "operator-" + "o" * 40
APP_A = "app-a-" + "a" * 40
APP_B = "app-b-" + "b" * 40
NATIVE_TOKEN = "native-token-private-marker"
PROMPT = "prompt-private-marker"
RESULT = "result-private-marker"
PRIVATE_ERROR = "native-error-private-marker"
A_ID, B_ID = str(UUID(int=10)), str(UUID(int=11))


@pytest.fixture
def source():
    return gateway.FoundryGatewaySource(
        organization_id=str(UUID(int=1)),
        managed_service_id=str(UUID(int=2)),
        tenant_id=str(UUID(int=3)),
        subscription_id=str(UUID(int=4)),
        account_name="reviewed-account",
        deployment_name="real-deployment-17",
        native_model_name="selected-model",
        native_model_version="2025-01-01",
        source_fingerprint="a" * 64,
    )


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    path = tmp_path / "keys.json"
    data = {
        "version": 2,
        "revision": 7,
        "operator_key": OPERATOR,
        "subscription_keys": [APP_A, APP_B],
        "subscription_ids": [A_ID, B_ID],
    }
    path.write_text(json.dumps(data))
    monkeypatch.setattr(auth, "KEYS_FILE", str(path))
    monkeypatch.setenv("ASTROLIFT_MODEL_AUTH_REVISION", "7")
    return path, data


def completion():
    return {
        "id": "native-completion-17",
        "object": "chat.completion",
        "created": 123,
        "model": "native-model-version",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": RESULT}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        "private_native_field": PRIVATE_ERROR,
    }


class Credential:
    def __init__(self):
        self.scopes = []
        self.error = None
        self.entered = asyncio.Event()
        self.release = None

    async def token(self, scope):
        self.scopes.append(scope)
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.error:
            raise self.error
        return NATIVE_TOKEN


class Admission:
    def __init__(self, source):
        self.source = source
        self.calls = []
        self.admitted = True

    async def __call__(self, source):
        self.calls.append(source)
        assert source == self.source
        if not self.admitted:
            raise RuntimeError(PRIVATE_ERROR)


class LoopbackTransport(httpx.AsyncBaseTransport):
    def __init__(self, port):
        self.original = []
        self.port = port
        self.delegate = httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        self.original.append((str(request.url), dict(request.headers)))
        # Only this test transport substitutes the socket destination.
        local = httpx.Request(
            request.method,
            request.url.copy_with(scheme="http", host="127.0.0.1", port=self.port),
            headers=request.headers,
            stream=request.stream,
            extensions=request.extensions,
        )
        return await self.delegate.handle_async_request(local)

    async def aclose(self):
        await self.delegate.aclose()


@pytest.fixture
def wire():
    state = SimpleNamespace(
        calls=[],
        status=200,
        body=completion(),
        content_type="application/json",
        encoding=None,
        disconnect=False,
        started=threading.Event(),
        release=None,
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            state.calls.append((self.path, dict(self.headers), self.rfile.read(int(self.headers["Content-Length"]))))
            state.started.set()
            if state.release is not None:
                state.release.wait(timeout=5)
            if state.disconnect:
                self.close_connection = True
                return
            body = state.body if isinstance(state.body, bytes) else json.dumps(state.body).encode()
            self.send_response(state.status)
            self.send_header("Content-Type", state.content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("x-private-native", PRIVATE_ERROR)
            self.send_header("Location", "https://alternate-account.services.ai.azure.com/evil")
            if state.encoding:
                self.send_header("Content-Encoding", state.encoding)
            self.end_headers()
            with contextlib.suppress(BrokenPipeError):
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    state.port = server.server_port
    yield state
    if state.release is not None:
        state.release.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
async def world(source, snapshot, wire):
    credential = Credential()
    admission = Admission(source)
    socket = LoopbackTransport(wire.port)
    transport = gateway.HTTPXNativeTransport(transport=socket)
    app = gateway.authenticated_gateway(source, credential=credential, transport=transport, checkpoint=admission)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        yield SimpleNamespace(
            source=source,
            app=app,
            client=client,
            credential=credential,
            admission=admission,
            transport=transport,
            socket=socket,
            wire=wire,
            snapshot=snapshot,
        )
    await transport.close()


def request(source):
    return {"model": source.model_id, "messages": [{"role": "user", "content": PROMPT}], "max_completion_tokens": 32}


async def post(w, key=APP_A, body=None, path="/v1/chat/completions", headers=None):
    return await w.client.post(
        path,
        json=body if body is not None else request(w.source),
        headers={"authorization": "Bearer " + key, **(headers or {})},
    )


async def test_two_apps_single_native_source_no_header_forwarding_and_private_metrics(world):
    w = world
    for key in (APP_A, APP_B):
        reply = await post(
            w,
            key,
            headers={
                "x-ms-model-deployment": "foreign",
                "api-key": "foreign-key",
                "x-ms-endpoint": "https://evil.invalid",
            },
        )
        assert reply.status_code == 200
        assert reply.json()["model"] == w.source.model_id
        assert reply.json()["choices"][0]["message"]["content"] == RESULT
        assert "private_native_field" not in reply.json()
        assert "x-private-native" not in reply.headers and "location" not in reply.headers
    assert len(w.wire.calls) == 2
    for (path, headers, body), (url, _) in zip(w.wire.calls, w.socket.original, strict=True):
        assert url == w.source.chat_url
        assert path == "/openai/v1/chat/completions?api-version=v1"
        assert headers["authorization"] == "Bearer " + NATIVE_TOKEN
        assert "api-key" not in headers and "x-ms-model-deployment" not in headers and "x-ms-endpoint" not in headers
        assert json.loads(body)["model"] == "real-deployment-17"
        assert json.loads(body)["store"] is False and json.loads(body)["n"] == 1
        assert APP_A not in body.decode() and APP_B not in body.decode()
    assert w.credential.scopes == [gateway.COGNITIVE_SCOPE] * 2
    assert len(w.admission.calls) == 8
    for key in (APP_A, APP_B):
        assert (await w.client.get("/metrics", headers={"authorization": "Bearer " + key})).status_code == 401
    metrics = await w.client.get("/metrics", headers={"authorization": "Bearer " + OPERATOR})
    assert metrics.status_code == 200
    assert A_ID in metrics.text and B_ID in metrics.text
    assert PROMPT not in metrics.text and RESULT not in metrics.text and NATIVE_TOKEN not in metrics.text


@pytest.mark.parametrize("key", [APP_A, OPERATOR])
@pytest.mark.parametrize(
    "path",
    [
        "/load",
        "/admin",
        "/v1/embeddings",
        "/v1/responses",
        "/v1/completions",
        "/v1/chat/completions?api-version=preview",
        "/v1/chat/completions?model=foreign",
        "/v1/%63hat/completions",
    ],
)
async def test_inner_route_blocks_operator_too_without_native_effect(world, key, path):
    assert (await post(world, key, path=path)).status_code in (400, 401)
    assert world.wire.calls == [] and world.credential.scopes == []


@pytest.mark.parametrize(
    "patch",
    [
        {"model": "real-deployment-17"},
        {"model": "foreign"},
        {"stream": True},
        {"stream": 0},
        {"store": True},
        {"n": 2},
        {"n": True},
        {"max_completion_tokens": True},
        {"max_completion_tokens": 0},
        {"max_completion_tokens": 2049},
        {"max_tokens": 32},
        {"base_url": "https://evil.invalid"},
        {"tools": []},
        {"metadata": {"secret": PRIVATE_ERROR}},
        {"temperature": True},
        {"temperature": 3},
        {"top_p": -1},
        {"messages": []},
        {"messages": [{"role": "tool", "content": PROMPT}]},
        {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": "https://evil.invalid"}]}]},
    ],
)
async def test_supported_subset_refuses_before_token_or_upstream(world, patch):
    body = {**request(world.source), **patch}
    assert (await post(world, body=body)).status_code == 400
    assert world.wire.calls == [] and world.credential.scopes == []


@pytest.mark.parametrize(
    "body", [b'{"model":"a","model":"b"}', b"NaN", b"[]", b'"value"', b"\xff", b'{"temperature":NaN}', b'{"messages":']
)
async def test_json_protocol_errors_zero_effect(world, body):
    result = await world.client.post(
        "/v1/chat/completions",
        content=body,
        headers={"authorization": "Bearer " + APP_A, "content-type": "application/json"},
    )
    assert result.status_code == 400
    assert world.wire.calls == [] and world.credential.scopes == []


async def test_request_limit_before_token(world):
    result = await world.client.post(
        "/v1/chat/completions",
        content=b"x" * (gateway.MAX_REQUEST_BYTES + 1),
        headers={"authorization": "Bearer " + APP_A, "content-type": "application/json"},
    )
    assert result.status_code == 413 and world.wire.calls == [] and world.credential.scopes == []


async def test_local_models_health_and_metrics_never_invoke(world):
    for path, key in [("/v1/models", APP_A), ("/health", None), ("/metrics", OPERATOR)]:
        reply = await world.client.get(path, headers={"authorization": "Bearer " + key} if key else {})
        assert reply.status_code == 200
        if path == "/v1/models":
            assert reply.json()["data"][0]["id"] == world.source.model_id
            assert "real-deployment-17" not in reply.text
    assert (await world.client.head("/health")).content == b""
    assert world.wire.calls == [] and world.credential.scopes == []


async def test_revoke_requires_new_snapshot_old_process_unchanged_other_key_kept(world):
    w = world
    path, data = w.snapshot
    data.update(revision=8, subscription_keys=[APP_B], subscription_ids=[B_ID])
    path.write_text(json.dumps(data))
    assert (await post(w, APP_A)).status_code == 200
    with pytest.raises(auth.SnapshotError):
        gateway.authenticated_gateway(w.source, credential=w.credential, transport=w.transport, checkpoint=w.admission)
    # A replacement runtime must explicitly adopt the desired auth revision.
    import os

    previous = os.environ["ASTROLIFT_MODEL_AUTH_REVISION"]
    os.environ["ASTROLIFT_MODEL_AUTH_REVISION"] = "8"
    try:
        app = gateway.authenticated_gateway(
            w.source, credential=w.credential, transport=w.transport, checkpoint=w.admission
        )
    finally:
        os.environ["ASTROLIFT_MODEL_AUTH_REVISION"] = previous
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://replacement") as client:
        for key, expected in [(APP_A, 401), (APP_B, 200)]:
            reply = await client.post(
                "/v1/chat/completions", json=request(w.source), headers={"authorization": "Bearer " + key}
            )
            assert reply.status_code == expected
    assert len(w.wire.calls) == 2


async def test_withdrawal_during_token_wait_prevents_native_send(world):
    world.credential.release = asyncio.Event()
    pending = asyncio.create_task(post(world))
    await world.credential.entered.wait()
    world.admission.admitted = False
    world.credential.release.set()
    reply = await pending
    assert reply.status_code == 502 and RESULT not in reply.text and PRIVATE_ERROR not in reply.text
    assert world.wire.calls == []


async def test_withdrawal_during_native_wait_does_not_expose_completion_or_resend(world):
    world.wire.release = threading.Event()
    pending = asyncio.create_task(post(world))
    assert await asyncio.to_thread(world.wire.started.wait, 5)
    world.admission.admitted = False
    world.wire.release.set()
    reply = await pending
    assert reply.status_code == 502 and RESULT not in reply.text and PRIVATE_ERROR not in reply.text
    assert len(world.wire.calls) == 1


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 401, 403, 429, 500])
async def test_native_error_redirect_never_retried_or_forwarded(world, status):
    world.wire.status, world.wire.body = status, PRIVATE_ERROR.encode()
    reply = await post(world)
    assert reply.status_code == 502 and PRIVATE_ERROR not in reply.text and "location" not in reply.headers
    assert len(world.wire.calls) == 1 and len(world.socket.original) == 1


async def test_lost_reply_is_unknown_one_paid_send(world):
    world.wire.disconnect = True
    reply = await post(world)
    assert reply.status_code == 502 and reply.json()["error"]["code"] == "native_request_unconfirmed"
    assert len(world.wire.calls) == 1


@pytest.mark.parametrize(
    "body,content_type,encoding",
    [
        (b"not-json-private-marker", "application/json", None),
        (b"x", "text/plain", None),
        (b"x", "application/json", "gzip"),
        (b"x" * (gateway.MAX_RESPONSE_BYTES + 1), "application/json", None),
        ({"error": PRIVATE_ERROR}, "application/json", None),
    ],
)
async def test_bad_native_payloads_are_sanitized(world, body, content_type, encoding):
    world.wire.body, world.wire.content_type, world.wire.encoding = body, content_type, encoding
    reply = await post(world)
    assert reply.status_code == 502 and PRIVATE_ERROR not in reply.text and len(world.wire.calls) == 1


async def test_timeout_cancels_await_without_native_retry(world, monkeypatch):
    monkeypatch.setattr(gateway, "WALL_TIMEOUT_SECONDS", 0.05)
    world.wire.release = threading.Event()
    reply = await post(world)
    assert reply.status_code == 503 and len(world.wire.calls) == 1


async def test_private_error_no_formatted_logs_spans_stdout_or_response(world, caplog, capsys):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    except ImportError:
        pytest.fail("Run privacy proof with installed HTTPX instrumentation; do not silently skip it.")
    HTTPXClientInstrumentor.instrument_client(world.transport._client, tracer_provider=provider)
    caplog.set_level(logging.DEBUG)
    world.wire.status, world.wire.body = 500, (PRIVATE_ERROR + PROMPT + RESULT + NATIVE_TOKEN).encode()
    reply = await post(world)
    records = "\n".join(
        record.getMessage()
        + str(record.__dict__)
        + ("".join(traceback.format_exception(*record.exc_info)) if record.exc_info else "")
        for record in caplog.records
    )
    assert reply.status_code == 502
    assert exporter.get_finished_spans() == ()
    for marker in (PRIVATE_ERROR, PROMPT, RESULT, NATIVE_TOKEN, APP_A):
        assert marker not in records and marker not in reply.text
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    HTTPXClientInstrumentor.uninstrument_client(world.transport._client)
    provider.shutdown()


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_name", "evil.invalid"),
        ("account_name", "a@evil"),
        ("account_name", "https://evil"),
        ("deployment_name", "a/b"),
        ("tenant_id", str(UUID(int=0))),
        ("organization_id", "not-guid"),
        ("source_fingerprint", "f" * 63),
        ("native_model_version", "latest"),
    ],
)
def test_source_bad_or_mutable_declarations_refused(source, field, value):
    with pytest.raises(gateway.GatewayError) as error:
        replace(source, **{field: value})
    assert value not in str(error.value)


def test_legacy_unattributed_snapshot_refused(source, snapshot):
    path, data = snapshot
    data.update(version=1)
    del data["subscription_ids"]
    path.write_text(json.dumps(data))
    with pytest.raises(gateway.GatewayError, match="version-2"):
        gateway.authenticated_gateway(source, credential=Credential(), transport=None, checkpoint=Admission(source))


def test_native_reply_repr_omits_payload():
    assert PRIVATE_ERROR not in repr(gateway.NativeReply(500, PRIVATE_ERROR.encode()))


async def test_explicit_workload_identity_constructor_and_wrong_audience_zero_effect(source):
    from azure.identity import WorkloadIdentityCredential

    credential = gateway.AzureWorkloadCredential(tenant_id=source.tenant_id, client_id=str(UUID(int=5)))
    assert isinstance(credential._credential, WorkloadIdentityCredential)
    with pytest.raises(gateway.GatewayError, match="audience"):
        await credential.token("https://ai.azure.com/.default")
    await credential.close()


async def test_private_filter_is_context_local_and_preserves_concurrent_library_logs(world, caplog):
    logger = logging.getLogger("azure.identity.concurrent_gateway_test")
    caplog.set_level(logging.DEBUG)
    entered, release = asyncio.Event(), asyncio.Event()

    async def private():
        with gateway._private_io():
            logger.debug(PRIVATE_ERROR)
            entered.set()
            await release.wait()
            logger.debug(NATIVE_TOKEN)

    pending = asyncio.create_task(private())
    await entered.wait()
    logger.info("ordinary-concurrent-telemetry")
    release.set()
    await pending
    logger.info("ordinary-after-private-call")
    messages = [record.getMessage() for record in caplog.records]
    assert "ordinary-concurrent-telemetry" in messages and "ordinary-after-private-call" in messages
    assert PRIVATE_ERROR not in messages and NATIVE_TOKEN not in messages


async def test_actual_workload_credential_failure_logging_sanitized(source, caplog, monkeypatch):
    credential = gateway.AzureWorkloadCredential(tenant_id=source.tenant_id, client_id=str(UUID(int=5)))
    caplog.set_level(logging.DEBUG)

    def fail(*_args, **_kwargs):
        raise RuntimeError(NATIVE_TOKEN + PRIVATE_ERROR)

    monkeypatch.setattr(credential._credential._client, "obtain_token_by_jwt_assertion", fail)
    # Failure runs through the actual Azure identity get_token logging decorator.
    monkeypatch.setattr(credential._credential, "_get_service_account_token", lambda: "signed-private-assertion")
    with pytest.raises(gateway.GatewayError, match="unavailable") as error:
        await credential.token(gateway.COGNITIVE_SCOPE)
    records = "\n".join(
        record.getMessage()
        + str(record.__dict__)
        + ("".join(traceback.format_exception(*record.exc_info)) if record.exc_info else "")
        for record in caplog.records
    )
    for marker in (NATIVE_TOKEN, PRIVATE_ERROR, "signed-private-assertion"):
        assert marker not in records and marker not in str(error.value)
    await credential.close()


async def test_explicit_credential_no_proxy_chain_fixed_authority(source):
    credential = gateway.AzureWorkloadCredential(tenant_id=source.tenant_id, client_id=str(UUID(int=5)))
    transport = credential._credential._client._pipeline._transport
    assert transport._use_env_settings is False
    assert credential._credential._client._authority == "https://login.microsoftonline.com"
    assert credential._credential._client._tenant_id == source.tenant_id
    await credential.close()


async def test_cancel_pending_native_call_one_send_no_completion(world):
    world.wire.release = threading.Event()
    pending = asyncio.create_task(post(world))
    assert await asyncio.to_thread(world.wire.started.wait, 5)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    world.wire.release.set()
    assert len(world.wire.calls) == 1


async def test_unknown_and_withdrawn_source_read_does_not_issue_token(world):
    world.admission.admitted = False
    assert (await post(world)).status_code == 503
    assert (await world.client.get("/health")).status_code == 503
    assert world.wire.calls == [] and world.credential.scopes == []


@pytest.mark.parametrize("key", [None, "wrong-key", APP_A])
async def test_operator_only_paths_cannot_escape_source_or_auth(world, key):
    headers = {"authorization": "Bearer " + key} if key else {}
    assert (await world.client.get("/admin/config", headers=headers)).status_code == 401
    assert world.wire.calls == []


async def test_duplicate_bearer_headers_no_inference(world):
    response = await world.client.post(
        "/v1/chat/completions",
        json=request(world.source),
        headers=[("authorization", "Bearer " + OPERATOR), ("authorization", "Bearer " + APP_A)],
    )
    assert response.status_code == 401 and world.wire.calls == []


async def test_streamed_client_disconnect_before_body_complete_no_native_effect(world):
    scope = {
        "type": "http",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "method": "POST",
        "headers": [(b"authorization", ("Bearer " + APP_A).encode()), (b"content-type", b"application/json")],
    }
    events = iter([{"type": "http.request", "body": b'{"model":', "more_body": True}, {"type": "http.disconnect"}])
    messages = []

    async def receive():
        return next(events)

    async def send(message):
        messages.append(message)

    await world.app(scope, receive, send)
    assert messages == [] and world.wire.calls == [] and world.credential.scopes == []
    metrics = await world.client.get("/metrics", headers={"authorization": "Bearer " + OPERATOR})
    assert 'outcome="disconnected"' in metrics.text


@pytest.mark.parametrize("answer", [False, True, {"admitted": False}])
async def test_checkpoint_must_explicitly_complete_or_raise_not_boolean_admission(source, snapshot, wire, answer):
    async def unknown(_source):
        return answer

    transport = gateway.HTTPXNativeTransport(transport=LoopbackTransport(wire.port))
    credential = Credential()
    app = gateway.authenticated_gateway(source, credential=credential, transport=transport, checkpoint=unknown)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        reply = await client.post(
            "/v1/chat/completions", json=request(source), headers={"authorization": "Bearer " + APP_A}
        )
        assert reply.status_code != 200 and wire.calls == [] and credential.scopes == []
    await transport.close()
