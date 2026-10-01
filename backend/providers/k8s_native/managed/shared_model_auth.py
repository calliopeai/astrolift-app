"""Mounted, dependency-free ASGI authorization for shared Python vLLM servers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

KEYS_FILE = "/var/run/astrolift/model-auth/keys.json"
MAX_SNAPSHOT_BYTES = 32_768
MAX_SUBSCRIPTIONS = 64
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
        or set(data) != {"version", "revision", "operator_key", "subscription_keys"}
        or type(data["version"]) is not int
        or data["version"] != 1
        or type(data["revision"]) is not int
        or data["revision"] != expected_revision
        or not isinstance(data["subscription_keys"], list)
        or len(data["subscription_keys"]) > MAX_SUBSCRIPTIONS
    ):
        raise SnapshotError("Shared model authentication snapshot is invalid or replaced.")
    keys = [data["operator_key"], *data["subscription_keys"]]
    if any(not isinstance(key, str) or not _TOKEN.fullmatch(key) for key in keys) or len(set(keys)) != len(keys):
        raise SnapshotError("Shared model authentication keys are invalid.")
    return KeySnapshot(expected_revision, keys[0], tuple(keys[1:]))


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


class SharedModelAuth:
    """vLLM's supported class middleware hook, using one startup key snapshot."""

    def __init__(self, app: ASGIApp) -> None:
        snapshot = load_key_snapshot(KEYS_FILE)
        self.app = app
        self.revision = snapshot.revision
        self._operator_hash = hashlib.sha256(snapshot.operator_key.encode("ascii")).digest()
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
        if token is not None:
            for key_hash in self._subscription_hashes:
                subscriber |= secrets.compare_digest(token, key_hash)
        model_route = not scope.get("root_path") and (method, path) in _MODEL_ROUTES
        if operator or (subscriber and model_route):
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
