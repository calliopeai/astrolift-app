"""
Request-scoped context primitives: ``request_id`` and OTel trace state.

Stored in ``contextvars`` so ASGI/threaded code paths see consistent
values without explicit threading. The middleware in
``core.middleware.request_id`` populates these for every HTTP request;
worker code calls ``set_request_id`` directly when starting a job.

Request IDs are ULIDs (Crockford base-32, lexicographically ordered)
so they sort by creation time when grepping logs.
"""

from __future__ import annotations

import contextvars
import dataclasses
import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def generate_ulid() -> str:
    """Generate a 26-character ULID string."""
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big") & ((1 << 80) - 1)
    value = (ms << 80) | rand

    out: list[str] = []
    for _ in range(26):
        out.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


@dataclasses.dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str
    span_id: str
    flags: int = 0


_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("astrolift_request_id", default=None)
_trace: contextvars.ContextVar[TraceContext | None] = contextvars.ContextVar("astrolift_trace", default=None)


def set_request_id(value: str | None) -> contextvars.Token:
    return _request_id.set(value)


def get_request_id() -> str | None:
    return _request_id.get()


def set_trace_context(ctx: TraceContext | None) -> contextvars.Token:
    return _trace.set(ctx)


def get_trace_context() -> TraceContext | None:
    return _trace.get()


def reset_request_id(token: contextvars.Token) -> None:
    """Reset the request-id contextvar, tolerating cross-context resets.

    Under ASGI, sync middleware gets dispatched via ``sync_to_async``
    which can land ``process_request`` and ``process_response`` in
    different task contexts. ``ContextVar.reset`` raises ValueError
    when the token came from a different context — that's harmless
    because the request task is exiting anyway and the contextvar
    goes out of scope with it.
    """
    try:
        _request_id.reset(token)
    except ValueError:
        pass


def reset_trace_context(token: contextvars.Token) -> None:
    try:
        _trace.reset(token)
    except ValueError:
        pass
