from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

import pytest

from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import HttpTransport, RequestsTransport
from azure.foundry_gateway import FoundryGatewaySource
from azure.foundry_gateway_source import (
    API_VERSION,
    ARM_SCOPE,
    MAX_RESPONSE_BYTES,
    FoundrySourceMetadata,
    FoundrySourceObserver,
    FoundrySourceTarget,
    OptionalText,
    SourceReason,
)

SOURCE = FoundryGatewaySource(
    organization_id="11111111-1111-4111-8111-111111111111",
    managed_service_id="22222222-2222-4222-8222-222222222222",
    tenant_id="33333333-3333-4333-8333-333333333333",
    subscription_id="44444444-4444-4444-8444-444444444444",
    account_name="aa",
    deployment_name="native-deployment",
    native_model_name="gpt-4o",
    native_model_version="2024-08-06",
    source_fingerprint="0" * 64,
)
TARGET = FoundrySourceTarget(SOURCE, "model-rg", "eastus", "OpenAI")
ORIGIN = "https://aa.services.ai.azure.com/"
MARKER = "PRIVATE-ARM-TOKEN-BODY-HEADER-MARKER"


def metadata() -> FoundrySourceMetadata:
    return FoundrySourceMetadata(
        account_id=TARGET.account_id,
        deployment_id=TARGET.deployment_id,
        region="eastus",
        kind="AIServices",
        account_etag='"account-etag"',
        deployment_etag='"deployment-etag"',
        account_created_at="2025-01-01T00:00:00Z",
        deployment_created_at="2025-01-02T00:00:00Z",
        custom_subdomain=OptionalText(True, "aa"),
        endpoint=OptionalText(True, ORIGIN),
        endpoints=(ORIGIN,),
        endpoints_present=True,
        endpoints_fingerprint=hashlib.sha256(
            json.dumps({"OpenAI Language Model Instance API": ORIGIN}, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        account_capabilities_present=False,
        account_capabilities_fingerprint=None,
        local_auth_disabled=True,
        account_state=OptionalText(True, "Succeeded"),
        deployment_state=OptionalText(True, "Succeeded"),
        model_format="OpenAI",
        model_name="gpt-4o",
        model_version="2024-08-06",
        upgrade_policy=OptionalText(True, "NoAutoUpgrade"),
        parent_deployment=OptionalText(True, None),
        spillover_deployment=OptionalText(True, None),
        model_source=OptionalText(True, None),
        model_source_account=OptionalText(True, None),
        default_project=OptionalText(True, None),
        regional_routing_present=True,
        regional_routing_fingerprint=None,
        chat_completion=True,
    )


def reviewed(meta: FoundrySourceMetadata | None = None) -> FoundrySourceTarget:
    return dataclasses.replace(
        TARGET,
        source=dataclasses.replace(SOURCE, source_fingerprint=(meta or metadata()).fingerprint(TARGET)),
    )


def account() -> dict:
    return {
        "id": TARGET.account_id,
        "name": SOURCE.account_name,
        "type": "Microsoft.CognitiveServices/accounts",
        "kind": "AIServices",
        "location": "eastus",
        "etag": '"account-etag"',
        "systemData": {"createdAt": "2025-01-01T00:00:00Z", "createdBy": MARKER},
        "tags": {"private": MARKER},
        "properties": {
            "customSubDomainName": "aa",
            "endpoint": ORIGIN,
            "endpoints": {"OpenAI Language Model Instance API": ORIGIN},
            "disableLocalAuth": True,
            "defaultProject": None,
            "locations": None,
            "provisioningState": "Succeeded",
        },
    }


def deployment() -> dict:
    return {
        "id": TARGET.deployment_id,
        "name": SOURCE.deployment_name,
        "type": "Microsoft.CognitiveServices/accounts/deployments",
        "etag": '"deployment-etag"',
        "systemData": {"createdAt": "2025-01-02T00:00:00Z", "createdBy": MARKER},
        "properties": {
            "model": {
                "format": "OpenAI",
                "name": "gpt-4o",
                "version": "2024-08-06",
                "source": None,
                "sourceAccount": None,
            },
            "versionUpgradeOption": "NoAutoUpgrade",
            "parentDeploymentName": None,
            "spilloverDeploymentName": None,
            "provisioningState": "Succeeded",
            "capabilities": {"chatCompletion": "true"},
        },
    }


class Wire:
    def __init__(self) -> None:
        self.account = account()
        self.deployment = deployment()
        self.calls: list[tuple[str, str]] = []
        self.options: list[dict] = []
        self.scopes: list[tuple] = []
        self.hook = lambda index: None
        self.code = 200
        self.body: bytes | None = None
        self.headers: dict[str, str] = {}
        self.closed = False
        self.authority = True
        self.factories = 0
        self.token_error = False
        self.checks = 0

    def checkpoint(self, target):
        self.checks += 1
        assert target == self.target
        if not self.authority:
            raise ValueError(MARKER)

    def credential(self):
        self.factories += 1
        wire = self
        logger = logging.getLogger("azure.identity.test")

        class Credential:
            def get_token(self, *scopes, **kwargs):
                wire.scopes.append(scopes)
                logger.debug("token %s", MARKER)
                if wire.token_error:
                    raise ValueError(MARKER)
                return AccessToken(MARKER, int(time.time()) + 3600)

            def close(self):
                pass

        return Credential()


@pytest.fixture
def wire():
    wire = Wire()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            index = len(wire.calls)
            wire.calls.append((self.command, self.path))
            wire.hook(index)
            data = wire.account if unquote(urlsplit(self.path).path) == wire.target.account_id else wire.deployment
            body = wire.body if wire.body is not None else json.dumps(data).encode()
            self.send_response(wire.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Private-Response", MARKER)
            for key, value in wire.headers.items():
                self.send_header(key, value)
            if "Content-Length" not in wire.headers:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class LocalWire(HttpTransport):
        def __init__(self):
            self.real = RequestsTransport(use_env_settings=False)

        def open(self):
            self.real.open()

        def close(self):
            wire.closed = True
            self.real.close()

        def __enter__(self):
            self.open()
            return self

        def __exit__(self, *args):
            self.close()

        def send(self, request, **kwargs):
            url = urlsplit(request.url)
            assert url.netloc == "management.azure.com"
            assert request.method == "GET"
            assert request.headers["Authorization"] == "Bearer " + MARKER
            assert request.headers["Accept-Encoding"] == "identity"
            assert kwargs["stream"] is True
            assert 0 < kwargs["connection_timeout"] <= 5
            assert 0 < kwargs["read_timeout"] <= 5
            wire.options.append(kwargs)
            original = request.url
            request.url = f"http://127.0.0.1:{server.server_port}{url.path}?{url.query}"
            try:
                return self.real.send(request, **kwargs)
            finally:
                request.url = original

    wire.transport_factory = LocalWire
    wire.target = reviewed()
    yield wire
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def observe(wire, target=None):
    wire.target = target or wire.target
    return FoundrySourceObserver(wire.target, wire.checkpoint, wire.credential, wire.transport_factory).observe()


def test_actual_sdk_fixed_exact_four_gets_matches_review_without_inference_claim(wire):
    result = observe(wire)
    assert result.metadata_verified and result.metadata == metadata()
    assert result.fingerprint == wire.target.source.source_fingerprint
    assert result.reasons == (SourceReason.RUNTIME_DECLARATION_REQUIRED,)
    assert result.native_gets == 4 and wire.closed
    assert wire.scopes == [(ARM_SCOPE,)]
    assert wire.calls == [
        ("GET", path + "?api-version=" + API_VERSION)
        for path in [TARGET.account_id, TARGET.deployment_id, TARGET.account_id, TARGET.deployment_id]
    ]
    assert MARKER not in repr(result)


def test_native_sdk_canonical_url_encoding_preserves_exact_arm_resource_identity(wire):
    target = dataclasses.replace(TARGET, resource_group="model(rg)")
    meta = dataclasses.replace(metadata(), account_id=target.account_id, deployment_id=target.deployment_id)
    target = dataclasses.replace(
        target, source=dataclasses.replace(SOURCE, source_fingerprint=meta.fingerprint(target))
    )
    wire.account["id"] = target.account_id
    wire.deployment["id"] = target.deployment_id
    result = observe(wire, target)
    assert result.metadata_verified and result.metadata == meta
    assert len(wire.calls) == 4 and all("model%28rg%29" in path for _, path in wire.calls)


@pytest.mark.parametrize("code", [301, 302, 307, 401, 403, 404, 429, 500, 503])
def test_denial_redirect_error_no_retry_no_body_or_header_projection(wire, code, caplog):
    wire.code = code
    wire.body = json.dumps({"error": {"message": MARKER}}).encode()
    wire.headers["Location"] = "https://evil.invalid/" + MARKER
    with caplog.at_level(logging.DEBUG):
        result = observe(wire)
    assert not result.metadata_verified and result.reasons == (SourceReason.NATIVE_UNAVAILABLE,)
    assert result.native_gets == 1 and len(wire.calls) == 1
    assert MARKER not in repr(result) + caplog.text + repr([r.__dict__ for r in caplog.records])


def test_checkpoint_before_credential_and_client_discovery(wire):
    wire.authority = False
    result = observe(wire)
    assert result.reasons == (SourceReason.AUTHORITY_UNAVAILABLE,)
    assert wire.factories == 0 and wire.scopes == [] and wire.calls == []


@pytest.mark.parametrize("stage", ["credential_factory", "token"])
def test_authority_withdrawn_during_factory_or_token_wait_prevents_native_read(wire, stage):
    original = wire.credential

    def factory():
        credential = original()
        if stage == "credential_factory":
            wire.authority = False
        else:
            get_token = credential.get_token

            def withdrawing(*scopes, **kwargs):
                result = get_token(*scopes, **kwargs)
                wire.authority = False
                return result

            credential.get_token = withdrawing
        return credential

    wire.credential = factory
    result = observe(wire)
    assert result.reasons == (SourceReason.AUTHORITY_UNAVAILABLE,) and not result.metadata_verified
    assert wire.calls == []


@pytest.mark.parametrize("index", range(4))
@pytest.mark.parametrize("code", [200, 403, 302])
def test_held_native_response_authority_withdrawal_refuses_success_and_next_get(wire, index, code):
    arrived = threading.Event()
    release = threading.Event()

    def hold(current):
        if current == index:
            arrived.set()
            assert release.wait(3)
            wire.code = code

    wire.hook = hold
    results = []
    task = threading.Thread(target=lambda: results.append(observe(wire)))
    task.start()
    try:
        assert arrived.wait(3)
        wire.authority = False
    finally:
        release.set()
        task.join(3)
    assert not task.is_alive()
    assert results[0].reasons == (SourceReason.AUTHORITY_UNAVAILABLE,)
    assert not results[0].metadata_verified
    assert len(wire.calls) == index + 1


@pytest.mark.parametrize(
    "kind,key,value",
    [
        ("account", "id", TARGET.account_id.replace("model-rg", "foreign")),
        ("account", "name", "foreign"),
        ("account", "kind", "OpenAI"),
        ("account", "location", "westus"),
        ("account", "type", "other"),
        ("deployment", "id", TARGET.deployment_id.replace("native-deployment", "other")),
        ("deployment", "name", "other"),
        ("deployment", "type", "other"),
    ],
)
def test_exact_resource_identity_retarget_fails_closed(wire, kind, key, value):
    getattr(wire, kind)[key] = value
    result = observe(wire)
    assert not result.metadata_verified and result.reasons == (SourceReason.SOURCE_CHANGED,)
    assert len(wire.calls) == 2


@pytest.mark.parametrize(
    "key,value",
    [
        ("name", "other"),
        ("format", "Meta"),
        ("version", None),
        ("version", "default"),
        ("version", "latest"),
        ("version", "other"),
    ],
)
def test_reviewed_explicit_model_tuple_is_not_inferred_from_native_default(wire, key, value):
    wire.deployment["properties"]["model"][key] = value
    result = observe(wire)
    assert result.reasons == (SourceReason.SOURCE_CHANGED,) and not result.metadata_verified


@pytest.mark.parametrize("kind,index", [("account", 2), ("deployment", 3)])
def test_repeated_observation_detects_changed_etag_or_resource(wire, kind, index):
    wire.hook = lambda current: getattr(wire, kind).update(etag='"changed"') if current == index else None
    result = observe(wire)
    assert result.reasons == (SourceReason.SOURCE_CHANGED,) and result.native_gets == 4


def test_review_fingerprint_binds_org_service_tenant_and_metadata(wire):
    target = dataclasses.replace(
        wire.target, source=dataclasses.replace(wire.target.source, tenant_id=SOURCE.organization_id)
    )
    result = observe(wire, target)
    assert result.reasons == (SourceReason.SOURCE_CHANGED,) and result.metadata is None


def test_changed_endpoint_api_mapping_is_not_hidden_by_same_origin_values(wire):
    wire.account["properties"]["endpoints"] = {"Different protocol": ORIGIN}
    result = observe(wire)
    assert not result.metadata_verified and result.reasons == (SourceReason.SOURCE_CHANGED,)


def test_account_capability_labels_are_only_bound_as_private_digest(wire):
    capabilities = [{"name": MARKER, "value": "true"}]
    wire.account["properties"]["capabilities"] = capabilities
    digest = hashlib.sha256(json.dumps(capabilities, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    meta = dataclasses.replace(metadata(), account_capabilities_present=True, account_capabilities_fingerprint=digest)
    target = reviewed(meta)
    result = observe(wire, target)
    assert result.metadata_verified and MARKER not in repr(result)
    wire.account["properties"]["capabilities"][0]["value"] = "false"
    changed = observe(wire, target)
    assert not changed.metadata_verified and changed.reasons == (SourceReason.SOURCE_CHANGED,)


@pytest.mark.parametrize(
    "key,field_name",
    [
        ("parentDeploymentName", "parent_deployment"),
        ("spilloverDeploymentName", "spillover_deployment"),
        ("versionUpgradeOption", "upgrade_policy"),
    ],
)
def test_optional_native_omission_preserved_without_invented_routing_default(wire, key, field_name):
    del wire.deployment["properties"][key]
    meta = dataclasses.replace(metadata(), **{field_name: OptionalText(False, None)})
    result = observe(wire, reviewed(meta))
    assert result.metadata_verified and getattr(result.metadata, field_name) == OptionalText(False, None)
    assert SourceReason.ROUTING_UNVERIFIED in result.reasons
    assert SourceReason.RUNTIME_DECLARATION_REQUIRED in result.reasons


@pytest.mark.parametrize(
    "key,field_name,value",
    [
        ("parentDeploymentName", "parent_deployment", "parent"),
        ("spilloverDeploymentName", "spillover_deployment", "fallback"),
        ("versionUpgradeOption", "upgrade_policy", "OnceCurrentVersionExpired"),
    ],
)
def test_observed_mutable_routing_is_not_admitted(wire, key, field_name, value):
    wire.deployment["properties"][key] = value
    meta = dataclasses.replace(metadata(), **{field_name: OptionalText(True, value)})
    result = observe(wire, reviewed(meta))
    assert result.metadata_verified and SourceReason.MUTABLE_ROUTING in result.reasons


@pytest.mark.parametrize("value", [None, "other", "https://evil.invalid/"])
def test_custom_subdomain_must_match_fixed_gateway_account_origin(wire, value):
    wire.account["properties"]["customSubDomainName"] = value
    meta = dataclasses.replace(metadata(), custom_subdomain=OptionalText(True, value))
    result = observe(wire, reviewed(meta))
    assert result.metadata_verified and SourceReason.ENDPOINT_UNVERIFIED in result.reasons


def test_legacy_endpoint_and_unknown_chat_capability_do_not_prove_chat_v1(wire):
    wire.account["properties"]["endpoint"] = "https://aa.cognitiveservices.azure.com/"
    wire.account["properties"]["endpoints"] = {}
    wire.deployment["properties"]["capabilities"] = {"futureChat": "true"}
    meta = dataclasses.replace(
        metadata(),
        endpoint=OptionalText(True, "https://aa.cognitiveservices.azure.com/"),
        endpoints=(),
        endpoints_fingerprint=hashlib.sha256(b"{}").hexdigest(),
        chat_completion=None,
    )
    result = observe(wire, reviewed(meta))
    assert result.metadata_verified
    assert SourceReason.ENDPOINT_UNVERIFIED in result.reasons
    assert SourceReason.CHAT_CAPABILITY_UNVERIFIED in result.reasons
    assert SourceReason.RUNTIME_DECLARATION_REQUIRED in result.reasons


@pytest.mark.parametrize("body", [b"not-json", b"[]", b'{"id":1,"id":2}', b'{"id":'])
def test_malformed_json_is_sanitized(wire, body):
    wire.body = body
    result = observe(wire)
    assert not result.metadata_verified and MARKER not in repr(result)


@pytest.mark.parametrize(
    "headers,body",
    [
        ({"Content-Length": str(MAX_RESPONSE_BYTES + 1)}, b"{}"),
        ({}, b"x" * (MAX_RESPONSE_BYTES + 1)),
        ({"Content-Encoding": "gzip"}, b"{}"),
    ],
)
def test_actual_native_streaming_response_byte_and_encoding_bounds(wire, headers, body):
    wire.headers.update(headers)
    wire.body = body
    result = observe(wire)
    assert result.reasons == (SourceReason.INVALID_RESPONSE,) and len(wire.calls) == 1


@pytest.mark.parametrize("where", ["endpoints", "capabilities"])
def test_native_metadata_collection_bounds(wire, where):
    if where == "endpoints":
        wire.account["properties"][where] = {str(i): ORIGIN for i in range(17)}
    else:
        wire.deployment["properties"][where] = {str(i): "true" for i in range(65)}
    result = observe(wire)
    assert not result.metadata_verified and result.reasons == (SourceReason.INVALID_RESPONSE,)


def test_credential_error_sanitized_and_no_native_get(wire, caplog):
    wire.token_error = True
    with caplog.at_level(logging.DEBUG):
        result = observe(wire)
    assert result.reasons == (SourceReason.NATIVE_UNAVAILABLE,) and not wire.calls
    assert MARKER not in repr(result) + caplog.text + repr([r.__dict__ for r in caplog.records])


def test_native_private_logs_suppressed_ordinary_concurrent_logs_retained(wire, caplog):
    logger = logging.getLogger("azure.identity.test")
    held = threading.Event()
    release = threading.Event()

    def hold(index):
        if index == 0:
            held.set()
            assert release.wait(3)

    wire.hook = hold
    result = []
    with caplog.at_level(logging.DEBUG):
        task = threading.Thread(target=lambda: result.append(observe(wire)))
        task.start()
        try:
            assert held.wait(3)
            logger.debug("ordinary concurrent event")
        finally:
            release.set()
            task.join(3)
    assert result[0].metadata_verified
    assert "ordinary concurrent event" in caplog.text
    assert MARKER not in caplog.text + repr([r.__dict__ for r in caplog.records])


def test_deadline_elapsed_during_held_response_refuses_observation(wire, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("azure.foundry_gateway_source.time.monotonic", lambda: clock[0])
    wire.hook = lambda index: clock.__setitem__(0, 131.0)
    result = observe(wire)
    assert result.reasons == (SourceReason.NATIVE_UNAVAILABLE,) and len(wire.calls) == 1


@pytest.mark.parametrize("group", ["a/../b", "a%2fb", "a?x", "a#x", "a.", "", "a" * 91])
def test_invalid_target_never_constructs_native_client(group):
    with pytest.raises(ValueError):
        FoundrySourceTarget(SOURCE, group, "eastus", "OpenAI")


def test_observer_repr_does_not_expose_native_ports_or_body(wire):
    observer = FoundrySourceObserver(wire.target, wire.checkpoint, wire.credential, wire.transport_factory)
    observer._last_body = MARKER.encode()
    assert MARKER not in repr(observer) and "credential_factory" not in repr(observer)


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("account", "endpoint", "https://aa.services.ai.azure.com/?token=" + MARKER),
        ("account", "endpoint", "https://aa.services.ai.azure.com/" + MARKER),
        ("account", "futureRouting", {"secret": MARKER}),
        ("deployment", "futureRouting", {"secret": MARKER}),
    ],
)
def test_unknown_routing_or_private_endpoint_contents_never_projected(wire, section, key, value, caplog):
    getattr(wire, section)["properties"][key] = value
    with caplog.at_level(logging.DEBUG):
        result = observe(wire)
    assert result.reasons == (SourceReason.INVALID_RESPONSE,) and not result.metadata_verified
    assert MARKER not in repr(result) + caplog.text + repr([r.__dict__ for r in caplog.records])


def test_minimum_sdk_does_not_hide_raw_native_spillover_field(wire):
    wire.deployment["properties"]["spilloverDeploymentName"] = "foreign-deployment"
    meta = dataclasses.replace(metadata(), spillover_deployment=OptionalText(True, "foreign-deployment"))
    result = observe(wire, reviewed(meta))
    assert result.metadata_verified and result.metadata.spillover_deployment.value == "foreign-deployment"
    assert SourceReason.MUTABLE_ROUTING in result.reasons


def test_no_retry_after_transport_lost_response_and_no_raw_error(wire, caplog):
    class Failure(HttpTransport):
        def open(self):
            pass

        def close(self):
            wire.closed = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def send(self, request, **kwargs):
            wire.calls.append((request.method, request.url))
            raise ValueError(MARKER)

    wire.transport_factory = Failure
    with caplog.at_level(logging.DEBUG):
        result = observe(wire)
    assert result.reasons == (SourceReason.NATIVE_UNAVAILABLE,) and result.native_gets == 1
    assert len(wire.calls) == 1 and wire.closed
    assert MARKER not in repr(result) + caplog.text


def test_same_observer_concurrent_read_refuses_without_second_credential_or_get(wire):
    entered = threading.Event()
    release = threading.Event()
    wire.hook = lambda index: (entered.set(), release.wait(3)) if index == 0 else None
    observer = FoundrySourceObserver(wire.target, wire.checkpoint, wire.credential, wire.transport_factory)
    results = []
    worker = threading.Thread(target=lambda: results.append(observer.observe()))
    worker.start()
    try:
        assert entered.wait(3)
        concurrent = observer.observe()
        assert not concurrent.metadata_verified and wire.factories == 1
    finally:
        release.set()
        worker.join(3)
    assert results[0].metadata_verified and len(wire.calls) == 4


@pytest.mark.parametrize("failure", [None, "credential", "denied", "malformed"])
def test_real_arm_sdk_tracing_suppressed_without_mutating_unrelated_spans(wire, failure):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from azure.core.settings import settings

    previous = settings.tracing_implementation()
    previous_enabled = settings.tracing_enabled()
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    settings.tracing_implementation = None
    settings.tracing_enabled = True
    previous_provider = trace.get_tracer_provider()
    trace._TRACER_PROVIDER = provider
    try:
        if failure == "credential":
            wire.token_error = True
        elif failure == "denied":
            wire.code = 403
            wire.body = json.dumps({"error": {"message": MARKER}}).encode()
        elif failure == "malformed":
            wire.body = MARKER.encode()
        result = observe(wire)
        with trace.get_tracer("ordinary").start_as_current_span("ordinary operation"):
            pass
        spans = exporter.get_finished_spans()
        assert result.metadata_verified is (failure is None)
        assert [s.name for s in spans] == ["ordinary operation"]
        assert MARKER not in repr([(s.name, s.attributes, s.events, s.status) for s in spans])
    finally:
        settings.tracing_implementation = previous
        settings.tracing_enabled = previous_enabled
        trace._TRACER_PROVIDER = previous_provider
        provider.shutdown()
