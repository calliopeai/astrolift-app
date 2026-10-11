"""Mounted, dependency-free ASGI authorization for shared Python vLLM servers."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import re
import secrets
import threading
import time
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

KEYS_FILE = "/var/run/astrolift/model-auth/keys.json"
MAX_SNAPSHOT_BYTES = 32_768
MAX_SUBSCRIPTIONS = 64
MAX_UPSTREAM_METRICS_BYTES = 2 * 1024 * 1024
_DURATION_BUCKETS = (0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 15.0, 60.0, 300.0)
_TRAFFIC_ROUTES = {
    "/v1/chat/completions": "chat_completions",
    "/v1/completions": "completions",
    "/v1/embeddings": "embeddings",
    "/score": "score",
    "/v1/score": "score",
    "/rerank": "rerank",
    "/v1/rerank": "rerank",
    "/v2/rerank": "rerank",
}
_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,256}\Z", re.ASCII)
_REVISION = re.compile(r"(?:0|[1-9][0-9]{0,18})\Z", re.ASCII)
_MODEL_ROUTES = frozenset(
    {
        ("GET", "/v1/models"),
        ("POST", "/v1/chat/completions"),
        ("POST", "/v1/completions"),
        ("POST", "/v1/embeddings"),
        ("POST", "/score"),
        ("POST", "/v1/score"),
        ("POST", "/rerank"),
        ("POST", "/v1/rerank"),
        ("POST", "/v2/rerank"),
    }
)


class SnapshotError(ValueError):
    """A generic configuration failure, without credential or file contents."""


@dataclass(frozen=True)
class KeySnapshot:
    revision: int
    operator_key: str = field(repr=False)
    subscription_keys: tuple[str, ...] = field(repr=False)
    subscription_ids: tuple[str, ...] = ()
    version: int = 1


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError("Shared model authentication snapshot is invalid.")
        result[key] = value
    return result


def load_key_snapshot(path: str | Path = KEYS_FILE, *, expected_revision: int | None = None) -> KeySnapshot:
    """Load once; a Secret update is not proof the running server changed keys."""
    if expected_revision is None:
        revision = os.environ.get("ASTROLIFT_MODEL_AUTH_REVISION", "")
        if not _REVISION.fullmatch(revision):
            raise SnapshotError("Shared model authentication revision is not configured.")
        expected_revision = int(revision)
    if type(expected_revision) is not int or not 0 <= expected_revision < 2**63:
        raise SnapshotError("Shared model authentication revision is invalid.")
    try:
        with Path(path).open("rb") as stream:
            body = stream.read(MAX_SNAPSHOT_BYTES + 1)
        if len(body) > MAX_SNAPSHOT_BYTES:
            raise SnapshotError("Shared model authentication snapshot is invalid.")
        data = json.loads(body, object_pairs_hook=_unique_object)
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise SnapshotError("Shared model authentication snapshot is unavailable or invalid.") from None
    if (
        not isinstance(data, dict)
        or set(data)
        != (
            {"version", "revision", "operator_key", "subscription_keys"}
            | ({"subscription_ids"} if data.get("version") == 2 else set())
        )
        or type(data["version"]) is not int
        or data["version"] not in (1, 2)
        or type(data["revision"]) is not int
        or data["revision"] != expected_revision
        or not isinstance(data["subscription_keys"], list)
        or len(data["subscription_keys"]) > MAX_SUBSCRIPTIONS
    ):
        raise SnapshotError("Shared model authentication snapshot is invalid or replaced.")
    keys = [data["operator_key"], *data["subscription_keys"]]
    if any(not isinstance(key, str) or not _TOKEN.fullmatch(key) for key in keys) or len(set(keys)) != len(keys):
        raise SnapshotError("Shared model authentication keys are invalid.")
    identities = data.get("subscription_ids", [])
    if data["version"] == 2:
        if not isinstance(identities, list) or len(identities) != len(keys) - 1:
            raise SnapshotError("Shared model subscription identities are invalid.")
        try:
            valid = all(
                isinstance(value, str) and str(UUID(value)) == value and UUID(value).int for value in identities
            )
        except (ValueError, AttributeError, TypeError):
            valid = False
        if not valid or len(set(identities)) != len(identities):
            raise SnapshotError("Shared model subscription identities are invalid.")
    return KeySnapshot(expected_revision, keys[0], tuple(keys[1:]), tuple(identities), data["version"])


def _token_hash(scope: dict[str, Any]) -> bytes | None:
    headers = scope.get("headers", ())
    authorization = [value for name, value in headers if name.lower() == b"authorization"]
    if len(authorization) != 1:
        return None
    try:
        scheme, separator, token = authorization[0].decode("ascii").partition(" ")
    except (UnicodeError, AttributeError):
        return None
    if scheme.lower() != "bearer" or not separator or not _TOKEN.fullmatch(token):
        return None
    return hashlib.sha256(token.encode("ascii")).digest()


class SubscriptionMetrics:
    """Process-local bounded counters, identified only by the startup snapshot."""

    def __init__(self, identities: tuple[str, ...], revision: int) -> None:
        self.identities = identities
        self.revision = revision
        self._lock = threading.Lock()
        routes = sorted(set(_TRAFFIC_ROUTES.values()))
        self.requests = {(identity, route, "2xx", "completed"): 0 for identity in identities for route in routes}
        self.bytes = {(identity, route): 0 for identity in identities for route in routes}
        self.durations = {
            (identity, route): [0, 0.0, *([0] * len(_DURATION_BUCKETS))] for identity in identities for route in routes
        }

    def record(self, identity: str, route: str, status: int | None, outcome: str, size: int, duration: float) -> None:
        status_class = f"{status // 100}xx" if type(status) is int and 100 <= status < 600 else "none"
        key = (identity, route)
        with self._lock:
            request_key = (*key, status_class, outcome)
            self.requests[request_key] = self.requests.get(request_key, 0) + 1
            self.bytes[key] += size
            sample = self.durations[key]
            sample[0] += 1
            sample[1] += duration
            for index, bound in enumerate(_DURATION_BUCKETS):
                sample[index + 2] += duration <= bound

    def render(self, *, openmetrics: bool = False) -> bytes:
        with self._lock:
            requests = sorted(self.requests.items())
            sizes = sorted(self.bytes.items())
            durations = [(key, tuple(value)) for key, value in sorted(self.durations.items())]
        lines = []
        names = {
            "astrolift_model_subscription_info": ("gauge", "Attribution snapshot schema version; not runtime health."),
            "astrolift_model_subscription_auth_revision": ("gauge", "Loaded startup credential revision."),
            "astrolift_model_subscription_requests_total": (
                "counter",
                "Authenticated subscriber POST requests by observed completion outcome.",
            ),
            "astrolift_model_subscription_response_bytes_total": (
                "counter",
                "Response body bytes accepted by the ASGI send callback; not delivery or tokens.",
            ),
            "astrolift_model_subscription_request_duration_seconds": (
                "histogram",
                "Authenticated subscriber ASGI application lifetime, including interrupted requests.",
            ),
        }

        def family_header(name: str) -> None:
            kind, description = names[name]
            family = name.removesuffix("_total") if openmetrics and kind == "counter" else name
            lines.extend((f"# HELP {family} {description}", f"# TYPE {family} {kind}"))

        family_header("astrolift_model_subscription_info")
        for identity in self.identities:
            lines.append(f'astrolift_model_subscription_info{{subscription_id="{identity}"}} 2')
        family_header("astrolift_model_subscription_auth_revision")
        for identity in self.identities:
            lines.append(f'astrolift_model_subscription_auth_revision{{subscription_id="{identity}"}} {self.revision}')
        family_header("astrolift_model_subscription_requests_total")
        for (identity, route, status_class, outcome), count in requests:
            labels = f'subscription_id="{identity}",route="{route}",status_class="{status_class}",outcome="{outcome}"'
            lines.append(f"astrolift_model_subscription_requests_total{{{labels}}} {count}")
        family_header("astrolift_model_subscription_response_bytes_total")
        for (identity, route), size in sizes:
            labels = f'subscription_id="{identity}",route="{route}"'
            lines.append(f"astrolift_model_subscription_response_bytes_total{{{labels}}} {size}")
        family_header("astrolift_model_subscription_request_duration_seconds")
        for (identity, route), sample in durations:
            labels = f'subscription_id="{identity}",route="{route}"'
            for index, bound in enumerate(_DURATION_BUCKETS):
                lines.append(
                    f'astrolift_model_subscription_request_duration_seconds_bucket{{{labels},le="{bound:g}"}} '
                    f"{sample[index + 2]}"
                )
            lines.extend(
                (
                    f'astrolift_model_subscription_request_duration_seconds_bucket{{{labels},le="+Inf"}} {sample[0]}',
                    f"astrolift_model_subscription_request_duration_seconds_count{{{labels}}} {sample[0]}",
                    f"astrolift_model_subscription_request_duration_seconds_sum{{{labels}}} {sample[1]:.9g}",
                )
            )
        return ("\n".join(lines) + "\n").encode("ascii")


def _append_metrics(start: Message, body: bytes, metrics: SubscriptionMetrics) -> tuple[Message, bytes] | None:
    if start.get("status") != 200:
        return None
    headers = start.get("headers", ())
    values = {}
    for name, value in headers:
        name = name.lower()
        if name in {b"content-type", b"content-encoding"}:
            if name in values:
                return None
            values[name] = value.lower()
    encoding = values.get(b"content-encoding", b"identity").strip()
    if encoding not in (b"identity", b"gzip"):
        return None
    decoded = body
    if encoding == b"gzip":
        try:
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            decoded = decoder.decompress(body, MAX_UPSTREAM_METRICS_BYTES + 1)
            if len(decoded) > MAX_UPSTREAM_METRICS_BYTES or not decoder.eof or decoder.unused_data:
                return None
        except zlib.error:
            return None
    content_type = values.get(b"content-type", b"").split(b";", 1)[0].strip()
    if content_type not in (b"text/plain", b"application/openmetrics-text"):
        return None
    openmetrics = content_type == b"application/openmetrics-text"
    if openmetrics:
        stripped = decoded.rstrip()
        if stripped != b"# EOF" and not stripped.endswith(b"\n# EOF"):
            return None
        decoded = stripped[:-5]
    output = (
        decoded + (b"" if not decoded or decoded.endswith(b"\n") else b"\n") + metrics.render(openmetrics=openmetrics)
    )
    if openmetrics:
        output += b"# EOF\n"
    if encoding == b"gzip":
        output = gzip.compress(output, mtime=0)
    changed = {
        **start,
        "headers": [
            (name, value)
            for name, value in headers
            if name.lower() not in {b"content-length", b"etag", b"content-md5", b"digest"}
        ]
        + [(b"content-length", str(len(output)).encode("ascii"))],
    }
    return changed, output


class SharedModelAuth:
    """vLLM's supported class middleware hook, using one startup key snapshot."""

    def __init__(self, app: ASGIApp) -> None:
        snapshot = load_key_snapshot(KEYS_FILE)
        self.app = app
        self.revision = snapshot.revision
        self._operator_hash = hashlib.sha256(snapshot.operator_key.encode("ascii")).digest()
        self._subscription_ids = snapshot.subscription_ids
        self._metrics = (
            SubscriptionMetrics(snapshot.subscription_ids, snapshot.revision) if snapshot.version == 2 else None
        )
        self._subscription_hashes = tuple(
            hashlib.sha256(key.encode("ascii")).digest() for key in snapshot.subscription_keys
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope.get("type")
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if kind != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "")
        if not scope.get("root_path") and path == "/health" and method in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return
        token = _token_hash(scope)
        operator = token is not None and secrets.compare_digest(token, self._operator_hash)
        subscriber = False
        subscription_id = None
        if token is not None:
            for index, key_hash in enumerate(self._subscription_hashes):
                matched = secrets.compare_digest(token, key_hash)
                subscriber |= matched
                if matched and self._metrics is not None:
                    subscription_id = self._subscription_ids[index]
        model_route = not scope.get("root_path") and (method, path) in _MODEL_ROUTES
        if (
            operator
            and not scope.get("root_path")
            and method == "GET"
            and path == "/metrics"
            and self._metrics is not None
        ):
            await self._scrape(scope, receive, send)
            return
        if operator or (subscriber and model_route):
            route = _TRAFFIC_ROUTES.get(path) if method == "POST" else None
            if not operator and subscription_id is not None and route is not None:
                await self._tracked(scope, receive, send, subscription_id, route)
            else:
                await self.app(scope, receive, send)
            return
        body = b'{"error":"Unauthorized"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def _tracked(self, scope: Scope, receive: Receive, send: Send, identity: str, route: str) -> None:
        started = time.perf_counter()
        status = None
        size = 0
        complete = False
        disconnected = False
        trailers = False
        outcome = "interrupted"

        async def observed_receive() -> Message:
            nonlocal disconnected
            message = await receive()
            if message.get("type") == "http.disconnect":
                disconnected = True
            return message

        async def observed_send(message: Message) -> None:
            nonlocal status, size, complete, trailers
            await send(message)
            kind = message.get("type")
            if kind == "http.response.start":
                status = message.get("status")
                trailers = message.get("trailers", False)
            elif kind == "http.response.body":
                body = message.get("body", b"")
                if isinstance(body, bytes):
                    size += len(body)
                if not message.get("more_body", False) and not trailers:
                    complete = True
            elif kind == "http.response.trailers" and not message.get("more_trailers", False):
                complete = True

        try:
            await self.app(scope, observed_receive, observed_send)
            outcome = "completed" if complete else "interrupted"
        except asyncio.CancelledError:
            outcome = "interrupted"
            raise
        except BaseException:
            outcome = "error"
            raise
        finally:
            if disconnected:
                outcome = "disconnected"
            self._metrics.record(identity, route, status, outcome, size, max(0.0, time.perf_counter() - started))

    async def _scrape(self, scope: Scope, receive: Receive, send: Send) -> None:
        pending = None
        buffer = bytearray()
        passthrough = False
        headers = [
            (name, value)
            for name, value in scope.get("headers", ())
            if name.lower() not in {b"accept", b"accept-encoding"}
        ]
        forwarded = {
            **scope,
            "headers": [*headers, (b"accept", b"text/plain; version=0.0.4"), (b"accept-encoding", b"identity")],
        }

        async def scrape_send(message: Message) -> None:
            nonlocal pending, passthrough
            if passthrough:
                await send(message)
                return
            if message.get("type") == "http.response.start" and pending is None:
                pending = message
                if message.get("trailers", False):
                    passthrough = True
                    await send(message)
                return
            body = message.get("body", b"")
            if (
                message.get("type") != "http.response.body"
                or pending is None
                or not isinstance(body, bytes)
                or len(buffer) + len(body) > MAX_UPSTREAM_METRICS_BYTES
            ):
                passthrough = True
                if pending is not None:
                    await send(pending)
                    if buffer:
                        await send({"type": "http.response.body", "body": bytes(buffer), "more_body": True})
                        buffer.clear()
                await send(message)
                return
            buffer.extend(body)
            if not message.get("more_body", False):
                appended = _append_metrics(pending, bytes(buffer), self._metrics)
                start, result = appended if appended is not None else (pending, bytes(buffer))
                await send(start)
                await send({**message, "body": result})
                buffer.clear()
                passthrough = True

        await self.app(forwarded, receive, scrape_send)
