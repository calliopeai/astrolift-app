"""Strict Chat-v1 ASGI seam, without deployment or native provisioning."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import importlib
import json
import logging
import math
import re
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from k8s_native.managed.shared_model_auth import ASGIApp, Receive, Scope, Send, SharedModelAuth

COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
WALL_TIMEOUT_SECONDS = 30
_TOKEN_FILE = "/var/run/secrets/azure/tokens/azure-identity-token"
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}[a-z0-9]\Z", re.ASCII)
_DEPLOYMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)
_DIGEST = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)


class GatewayError(ValueError):
    """A generic error without credential, request or native response contents."""


class RequestError(GatewayError):
    """The supported public request subset was not satisfied."""


def _guid(value: str) -> None:
    try:
        valid = isinstance(value, str) and str(UUID(value)) == value and UUID(value).int != 0
    except ValueError:
        valid = False
    if not valid:
        raise GatewayError("Gateway source identity is invalid.")


@dataclass(frozen=True, slots=True)
class FoundryGatewaySource:
    organization_id: str
    managed_service_id: str
    tenant_id: str
    subscription_id: str
    account_name: str
    deployment_name: str
    native_model_name: str
    native_model_version: str
    source_fingerprint: str

    def __post_init__(self) -> None:
        for value in (self.organization_id, self.managed_service_id, self.tenant_id, self.subscription_id):
            _guid(value)
        if (
            not isinstance(self.account_name, str)
            or not _NAME.fullmatch(self.account_name)
            or not isinstance(self.deployment_name, str)
            or not _DEPLOYMENT.fullmatch(self.deployment_name)
            or any(
                not isinstance(value, str)
                or not 1 <= len(value) <= 128
                or not value.isascii()
                or any(ord(c) < 33 for c in value)
                for value in (self.native_model_name, self.native_model_version)
            )
            or not isinstance(self.source_fingerprint, str)
            or not _DIGEST.fullmatch(self.source_fingerprint)
            or self.native_model_version.casefold() in {"auto", "default", "latest", "unknown"}
        ):
            raise GatewayError("Gateway source declaration is invalid.")

    @property
    def model_id(self) -> str:
        return "model-" + self.managed_service_id

    @property
    def chat_url(self) -> str:
        return f"https://{self.account_name}.services.ai.azure.com/openai/v1/chat/completions?api-version=v1"


Checkpoint = Callable[[FoundryGatewaySource], Awaitable[object]]


class NativeCredential(Protocol):
    async def token(self, scope: str) -> str: ...


@dataclass(frozen=True, slots=True)
class NativeReply:
    status: int
    body: bytes = field(repr=False)


class NativeTransport(Protocol):
    async def chat(self, source: FoundryGatewaySource, *, token: str, body: bytes) -> NativeReply: ...


_PRIVATE_IO = contextvars.ContextVar("foundry_gateway_private_io", default=False)
_LOG_LOCK = threading.Lock()


class _PrivateLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _PRIVATE_IO.get()


_PRIVATE_LOG_FILTER = _PrivateLogFilter()


@contextlib.contextmanager
def _private_io() -> Iterator[None]:
    # Library DEBUG headers/errors must not bypass the gateway's sanitizer.
    with _LOG_LOCK:
        for name, logger in list(logging.root.manager.loggerDict.items()):
            if (
                (
                    name.startswith(("azure.", "httpcore.", "urllib3."))
                    or name in {"httpx", "httpcore", "azure", "urllib3"}
                )
                and isinstance(logger, logging.Logger)
                and _PRIVATE_LOG_FILTER not in logger.filters
            ):
                logger.addFilter(_PRIVATE_LOG_FILTER)
    token = _PRIVATE_IO.set(True)
    try:
        try:
            suppress = importlib.import_module("opentelemetry.instrumentation.utils").suppress_instrumentation
        except ImportError:
            yield
        else:
            with suppress():
                yield
    finally:
        _PRIVATE_IO.reset(token)


class AzureWorkloadCredential:
    """Explicit projected-token identity; no ambient/developer credential chain."""

    def __init__(self, *, tenant_id: str, client_id: str) -> None:
        _guid(tenant_id)
        _guid(client_id)
        from azure.core.pipeline.transport import RequestsTransport
        from azure.identity import WorkloadIdentityCredential

        self._credential = WorkloadIdentityCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            token_file_path=_TOKEN_FILE,
            authority="https://login.microsoftonline.com",
            transport=RequestsTransport(
                use_env_settings=False, connection_timeout=WALL_TIMEOUT_SECONDS, read_timeout=WALL_TIMEOUT_SECONDS
            ),
            retry_total=0,
            disable_instance_discovery=True,
            logging_enable=False,
        )

    async def token(self, scope: str) -> str:
        if scope != COGNITIVE_SCOPE:
            raise GatewayError("Native audience is unsupported.")
        try:
            with _private_io():
                token = await asyncio.to_thread(self._credential.get_token, COGNITIVE_SCOPE)
            if not isinstance(token.token, str) or not token.token or token.expires_on <= time.time():
                raise GatewayError("Native credential is unavailable.")
            return token.token
        except Exception:
            raise GatewayError("Native credential is unavailable.") from None

    async def close(self) -> None:
        await asyncio.to_thread(self._credential.close)


class HTTPXNativeTransport:
    """Fixed public-Azure origin; one POST, bounded bytes, no redirect or retry."""

    def __init__(self, *, transport: Any = None) -> None:
        import httpx

        self._client = httpx.AsyncClient(
            transport=transport if transport is not None else httpx.AsyncHTTPTransport(retries=0),
            follow_redirects=False,
            trust_env=False,
            timeout=WALL_TIMEOUT_SECONDS,
        )

    async def chat(self, source: FoundryGatewaySource, *, token: str, body: bytes) -> NativeReply:
        try:
            with _private_io():
                async with self._client.stream(
                    "POST",
                    source.chat_url,
                    content=body,
                    headers={
                        "authorization": "Bearer " + token,
                        "content-type": "application/json",
                        "accept": "application/json",
                        "accept-encoding": "identity",
                    },
                ) as response:
                    if response.status_code != 200:
                        return NativeReply(response.status_code, b"")
                    if (
                        response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"
                        or response.headers.get("content-encoding", "identity").lower() != "identity"
                    ):
                        raise GatewayError("Native response is unsupported.")
                    result = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(result) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise GatewayError("Native response exceeds gateway limits.")
                        result.extend(chunk)
                    return NativeReply(200, bytes(result))
        except Exception:
            raise GatewayError("Native request outcome is unavailable.") from None

    async def close(self) -> None:
        await self._client.aclose()


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RequestError("Request shape is unsupported.")
        result[key] = value
    return result


def _constant(_: str) -> Any:
    raise RequestError("Request shape is unsupported.")


def _json(body: bytes) -> dict[str, Any]:
    value = json.loads(body.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
    if not isinstance(value, dict):
        raise RequestError("Request shape is unsupported.")
    return value


def _request(body: bytes, source: FoundryGatewaySource) -> bytes:
    data = _json(body)
    if (
        set(data) - {"model", "messages", "max_completion_tokens", "temperature", "top_p", "stream", "n", "store"}
        or data.get("model") != source.model_id
        or ("stream" in data and data["stream"] is not False)
        or ("store" in data and data["store"] is not False)
        or ("n" in data and (type(data["n"]) is not int or data["n"] != 1))
        or type(data.get("max_completion_tokens")) is not int
        or not 1 <= data["max_completion_tokens"] <= 2048
    ):
        raise RequestError("Request shape is unsupported.")
    messages = data.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 128:
        raise RequestError("Request shape is unsupported.")
    for message in messages:
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message["role"] not in ("system", "user", "assistant")
            or not isinstance(message["content"], str)
            or not message["content"]
        ):
            raise RequestError("Request shape is unsupported.")
    for field_name, maximum in (("temperature", 2), ("top_p", 1)):
        if field_name in data and (
            type(data[field_name]) not in (int, float)
            or not math.isfinite(data[field_name])
            or not 0 <= data[field_name] <= maximum
        ):
            raise RequestError("Request shape is unsupported.")
    data.update(model=source.deployment_name, stream=False, n=1, store=False)
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_REQUEST_BYTES:
        raise RequestError("Request exceeds gateway limits.")
    return encoded


def _response(body: bytes, source: FoundryGatewaySource) -> dict[str, Any]:
    data = _json(body)
    choices = data.get("choices")
    if (
        not isinstance(data.get("id"), str)
        or not 1 <= len(data["id"]) <= 128
        or type(data.get("created")) is not int
        or not 0 <= data["created"] < 2**63
        or data.get("object") != "chat.completion"
        or not isinstance(choices, list)
        or len(choices) != 1
        or not isinstance(choices[0], dict)
    ):
        raise GatewayError("Native response is unsupported.")
    choice = choices[0]
    message = choice.get("message")
    if (
        type(choice.get("index")) is not int
        or choice["index"] != 0
        or not isinstance(message, dict)
        or message.get("role") != "assistant"
        or not isinstance(message.get("content"), str)
        or any(key in message for key in ("tool_calls", "function_call", "audio"))
        or choice.get("finish_reason") not in ("stop", "length", "content_filter")
    ):
        raise GatewayError("Native response is unsupported.")
    data["id"].encode("utf-8")
    message["content"].encode("utf-8")
    result = {
        "id": data["id"],
        "object": "chat.completion",
        "created": data["created"],
        "model": source.model_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": message["content"]},
                "finish_reason": choice["finish_reason"],
            }
        ],
    }
    usage = data.get("usage")
    if (
        isinstance(usage, dict)
        and all(
            type(usage.get(key)) is int and 0 <= usage[key] < 2**63
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        )
        and usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
    ):
        result["usage"] = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    return result


class FoundryChatGateway:
    """Inner route restriction applies to operators as well as subscribers."""

    def __init__(
        self,
        source: FoundryGatewaySource,
        *,
        credential: NativeCredential,
        transport: NativeTransport,
        checkpoint: Checkpoint,
    ) -> None:
        if type(source) is not FoundryGatewaySource or not callable(checkpoint):
            raise GatewayError("Gateway source admission is not configured.")
        self.source = source
        self._credential = credential
        self._transport = transport
        self._checkpoint = checkpoint

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        path, method = scope.get("path", ""), scope.get("method", "")
        status, data = 400, {"error": {"code": "unsupported_request"}}
        if (
            not scope.get("root_path")
            and not scope.get("query_string")
            and scope.get("raw_path", path.encode()) == path.encode()
            and (method, path)
            in {
                ("GET", "/health"),
                ("HEAD", "/health"),
                ("GET", "/metrics"),
                ("GET", "/v1/models"),
                ("POST", "/v1/chat/completions"),
            }
        ):
            try:
                async with asyncio.timeout(WALL_TIMEOUT_SECONDS):
                    result = await self._dispatch(scope, receive)
                if result is None:
                    return
                status, data = result
            except RequestError:
                status, data = 400, {"error": {"code": "unsupported_request"}}
            except GatewayError:
                status, data = 503, {"error": {"code": "source_unavailable"}}
            except (ValueError, UnicodeError, RecursionError):
                status, data = 400, {"error": {"code": "unsupported_request"}}
            except Exception:
                status, data = 503, {"error": {"code": "request_outcome_unconfirmed"}}
        if path == "/metrics" and status == 200:
            content, content_type = b"", b"text/plain; version=0.0.4"
        else:
            content = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            content_type = b"application/json"
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", content_type), (b"cache-control", b"no-store")],
            }
        )
        await send({"type": "http.response.body", "body": b"" if method == "HEAD" else content})

    async def _current(self) -> None:
        try:
            if await self._checkpoint(self.source) is not None:
                raise GatewayError("Current source admission is unavailable.")
        except Exception:
            raise GatewayError("Current source admission is unavailable.") from None

    async def _dispatch(self, scope: Scope, receive: Receive) -> tuple[int, dict[str, Any]] | None:
        await self._current()
        path = scope["path"]
        if path == "/metrics":
            return 200, {}
        if path == "/health":
            return 200, {"status": "gateway_source_admitted"}
        if path == "/v1/models":
            return 200, {
                "object": "list",
                "data": [{"id": self.source.model_id, "object": "model", "owned_by": "astrolift"}],
            }
        headers = scope.get("headers", ())
        content_types = [value for key, value in headers if key.lower() == b"content-type"]
        if len(content_types) != 1 or content_types[0].split(b";", 1)[0].strip().lower() != b"application/json":
            raise RequestError("Request shape is unsupported.")
        body = bytearray()
        while True:
            event = await receive()
            if event.get("type") == "http.disconnect":
                return None
            if event.get("type") != "http.request" or not isinstance(event.get("body", b""), bytes):
                raise RequestError("Request shape is unsupported.")
            chunk = event.get("body", b"")
            if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                return 413, {"error": {"code": "request_too_large"}}
            body.extend(chunk)
            if not event.get("more_body", False):
                break
        outgoing = _request(bytes(body), self.source)
        await self._current()
        try:
            with _private_io():
                token = await self._credential.token(COGNITIVE_SCOPE)
            await self._current()
            if (
                not isinstance(token, str)
                or not 1 <= len(token) <= 16384
                or not token.isascii()
                or any(ord(c) < 33 or ord(c) == 127 for c in token)
            ):
                raise GatewayError("Native credential is unavailable.")
            with _private_io():
                reply = await self._transport.chat(self.source, token=token, body=outgoing)
            await self._current()
            if type(reply.status) is not int or reply.status != 200:
                return 502, {"error": {"code": "native_request_unconfirmed"}}
            if not isinstance(reply.body, bytes) or len(reply.body) > MAX_RESPONSE_BYTES:
                raise GatewayError("Native response is unsupported.")
            return 200, _response(reply.body, self.source)
        except Exception:
            return 502, {"error": {"code": "native_request_unconfirmed"}}


def authenticated_gateway(
    source: FoundryGatewaySource, *, credential: NativeCredential, transport: NativeTransport, checkpoint: Checkpoint
) -> ASGIApp:
    """Load the existing fixed startup auth snapshot; its revision needs reconciliation."""
    guard = SharedModelAuth(
        FoundryChatGateway(source, credential=credential, transport=transport, checkpoint=checkpoint)
    )
    if guard._metrics is None:
        raise GatewayError("Gateway requires a version-2 attributed auth snapshot.")
    return guard
