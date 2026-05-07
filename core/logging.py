"""
Structured logging for the Astrolift control plane.

Every log record can be emitted as one JSON object per line so log
collectors (ECS / Loki / Cloud Logging / Datadog) can index without a
parsing stage. Operators choose the wire format via ``LOG_FORMAT``:

* ``json`` — :class:`JsonFormatter`. Default in non-local envs.
* ``text`` — :class:`TextFormatter`. Human-readable, default for
  ``Local`` / ``LocalPG`` / ``LocalVerbose``.
* ``ecs``  — backwards-compatible Elastic Common Schema output via
  :class:`ECSFormatter`, kept for environments that still ship to
  ECS-aware collectors.

Both structured formats include the same fields:

* ``timestamp``, ``level``, ``logger``, ``message``;
* ``request_id`` from :mod:`core.request_context`;
* ``trace_id`` / ``span_id`` (priority: live OTel span if recording,
  else the W3C traceparent the request middleware parsed);
* ``organization_id`` / ``actor_user_id`` from the tenant context;
* whatever the call site passed via ``extra=``.

Sensitive fields (anything whose key matches ``password``, ``token``,
``secret``, ``api_key``, ``authorization``, etc.) are redacted before
serialization — both at the top level and inside nested dicts/lists.

Configuration knobs (via :func:`build_logging_config`):

* ``LOG_LEVEL``       (default ``INFO``)
* ``LOG_FORMAT``      (``json`` | ``text`` | ``ecs``; default per env)
* ``LOG_DESTINATION`` (``stdout`` | ``stderr``; default ``stdout``)

Use :func:`build_logging_config` to render the dict you assign to
Django's ``LOGGING`` setting, or call :func:`get_request_id` /
:func:`get_trace_context` from any module that wants to enrich its own
log lines without poking the contextvars directly.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from core.request_context import (
    TraceContext,
    get_request_id,
    get_trace_context,
)
from core.tenancy import get_current_tenant

__all__ = [
    "JsonFormatter",
    "TextFormatter",
    "ECSFormatter",
    "SensitiveFieldRedactor",
    "build_logging_config",
    "get_request_id",
    "get_trace_context",
    "TraceContext",
]


_RESERVED = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
    "asctime",
    "taskName",
}

_DEFAULT_SENSITIVE_PATTERNS = (
    r"password",
    r"passwd",
    r"secret",
    r"token",
    r"api[_-]?key",
    r"auth(?:orization)?",
    r"credential",
    r"private[_-]?key",
    r"session[_-]?id",
)

_REDACTED = "***REDACTED***"


class SensitiveFieldRedactor:
    """Mask values whose key looks sensitive.

    Operates recursively on dicts/lists/tuples so nested ``extra``
    payloads don't leak. Matching is case-insensitive substring against
    the configured patterns; the value is replaced with a constant
    sentinel rather than dropped so consumers can still tell a field
    was present.
    """

    def __init__(self, patterns: tuple[str, ...] = _DEFAULT_SENSITIVE_PATTERNS) -> None:
        self._regex = re.compile("|".join(patterns), re.IGNORECASE)

    def is_sensitive(self, key: str) -> bool:
        return bool(self._regex.search(key))

    def redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: (_REDACTED if self.is_sensitive(str(k)) else self.redact(v))
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            redacted = [self.redact(v) for v in value]
            return type(value)(redacted) if isinstance(value, tuple) else redacted
        return value


_DEFAULT_REDACTOR = SensitiveFieldRedactor()


def _resolve_trace() -> TraceContext | None:
    # Priority 1: a live OTel span. Covers in-process spans that the
    # request middleware can't see (Temporal workflows, deferred
    # tasks, manually-started spans).
    try:
        from opentelemetry import trace as _ot_trace
    except Exception:
        _ot_trace = None  # type: ignore[assignment]

    if _ot_trace is not None:
        try:
            span = _ot_trace.get_current_span()
            ctx = span.get_span_context() if span is not None else None
            if ctx is not None and ctx.is_valid:
                return TraceContext(
                    trace_id=format(ctx.trace_id, "032x"),
                    span_id=format(ctx.span_id, "016x"),
                    flags=int(getattr(ctx, "trace_flags", 0) or 0),
                )
        except Exception:
            # OTel must never crash a log call.
            pass

    # Priority 2: the contextvar populated from W3C traceparent.
    return get_trace_context()


def _build_payload(record: logging.LogRecord, redactor: SensitiveFieldRedactor) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
        "level": record.levelname,
        "logger": record.name,
        "message": record.getMessage(),
    }

    request_id = get_request_id()
    if request_id is not None:
        payload["request_id"] = request_id

    trace = _resolve_trace()
    if trace is not None:
        payload["trace_id"] = trace.trace_id
        payload["span_id"] = trace.span_id

    tenant = get_current_tenant()
    if tenant is not None:
        if tenant.organization_id is not None:
            payload["organization_id"] = tenant.organization_id
        if tenant.actor_user_id is not None:
            payload["actor_user_id"] = tenant.actor_user_id

    for key, value in record.__dict__.items():
        if key in _RESERVED or key.startswith("_"):
            continue
        if redactor.is_sensitive(key):
            payload[key] = _REDACTED
        else:
            payload[key] = redactor.redact(_safe(value))

    if record.exc_info:
        # Formatter is set later; we use the default exc_text that
        # logging.Formatter produces.
        payload["exception"] = logging.Formatter().formatException(record.exc_info)
    if record.stack_info:
        payload["stack"] = record.stack_info

    return payload


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per record."""

    def __init__(self, redactor: SensitiveFieldRedactor | None = None) -> None:
        super().__init__()
        self._redactor = redactor or _DEFAULT_REDACTOR

    def format(self, record: logging.LogRecord) -> str:
        payload = _build_payload(record, self._redactor)
        return json.dumps(payload, default=str, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    """Human-readable single-line output for local dev."""

    def __init__(self, redactor: SensitiveFieldRedactor | None = None) -> None:
        super().__init__()
        self._redactor = redactor or _DEFAULT_REDACTOR

    def format(self, record: logging.LogRecord) -> str:
        payload = _build_payload(record, self._redactor)
        head = f"{payload['timestamp']} {payload['level']:<5} {payload['logger']}: {payload['message']}"

        extras: list[str] = []
        for key, value in payload.items():
            if key in {"timestamp", "level", "logger", "message", "exception", "stack"}:
                continue
            extras.append(f"{key}={_short(value)}")

        line = head if not extras else f"{head}  {' '.join(extras)}"

        if "exception" in payload:
            line = f"{line}\n{payload['exception']}"
        if "stack" in payload:
            line = f"{line}\n{payload['stack']}"
        return line


class ECSFormatter(logging.Formatter):
    """ECS-shaped JSON for environments still pinned to ECS collectors.

    Wraps ``ecs_logging.StdlibFormatter`` and injects the same trace +
    request_id fields the JsonFormatter exposes. Importing
    ``ecs_logging`` is deferred so test environments that don't ship
    the package don't pay the import cost.
    """

    def __init__(self) -> None:
        super().__init__()
        import ecs_logging

        self._inner = ecs_logging.StdlibFormatter()

    def format(self, record: logging.LogRecord) -> str:
        # Stamp request_id onto the record so the inner formatter
        # surfaces it; ECS uses ``trace.id`` / ``span.id`` so we set
        # those via the post-format hook.
        rid = get_request_id()
        if rid is not None and not hasattr(record, "request_id"):
            record.request_id = rid

        line = self._inner.format(record)
        try:
            data = json.loads(line)
        except ValueError:
            return line

        trace = _resolve_trace()
        if trace is not None:
            data.setdefault("trace", {})["id"] = trace.trace_id
            data.setdefault("span", {})["id"] = trace.span_id

        tenant = get_current_tenant()
        if tenant is not None:
            if tenant.organization_id is not None:
                data["organization_id"] = tenant.organization_id
            if tenant.actor_user_id is not None:
                data["actor_user_id"] = tenant.actor_user_id

        return json.dumps(data, default=str, separators=(",", ":"))


def _safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    return repr(value)


def _short(value: Any) -> str:
    s = json.dumps(value, default=str, separators=(",", ":")) if not isinstance(value, str) else value
    return s if len(s) <= 256 else s[:253] + "..."


_VALID_FORMATS = ("json", "text", "ecs")


def build_logging_config(
    *,
    level: str = "INFO",
    format_: str = "json",
    destination: str = "stdout",
) -> dict[str, Any]:
    """Render a Django ``LOGGING`` dict.

    Parameters
    ----------
    level: log level for the root logger and the ``django`` / ``core``
        / ``astrolift_*`` loggers. Lowest level wins.
    format_: ``json``, ``text``, or ``ecs``.
    destination: ``stdout`` or ``stderr``.
    """

    fmt = (format_ or "json").lower()
    if fmt not in _VALID_FORMATS:
        fmt = "json"

    dest = (destination or "stdout").lower()
    stream = "ext://sys.stderr" if dest == "stderr" else "ext://sys.stdout"

    formatters: dict[str, dict[str, Any]] = {
        "json": {"()": "core.logging.JsonFormatter"},
        "text": {"()": "core.logging.TextFormatter"},
    }
    if fmt == "ecs":
        formatters["ecs"] = {"()": "core.logging.ECSFormatter"}

    handler_formatter = fmt
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": formatters,
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": handler_formatter,
                "stream": stream,
            },
        },
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            "django": {"handlers": ["console"], "level": level, "propagate": False},
            "django.request": {"handlers": ["console"], "level": level, "propagate": False},
            "core": {"handlers": ["console"], "level": level, "propagate": False},
            "astrolift": {"handlers": ["console"], "level": level, "propagate": False},
            "astrolift_operations": {"handlers": ["console"], "level": level, "propagate": False},
            "astrolift_identity": {"handlers": ["console"], "level": level, "propagate": False},
        },
    }


# Backwards-compatible default config (used when call sites import the
# module-level constant). Mirrors what build_logging_config() returns
# for the json/INFO/stdout combination.
LOGGING_CONFIG: dict[str, Any] = build_logging_config()


