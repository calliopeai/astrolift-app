"""
Structured JSON logging.

Every log record is emitted as one JSON object per line so log
collectors (ECS / Loki / Cloud Logging) can index without a parsing
stage. The formatter:

* always includes ``timestamp``, ``level``, ``logger``, ``message``;
* enriches with ``request_id``, ``trace_id``, ``span_id`` from the
  request-scoped context;
* enriches with ``organization_id`` / ``actor_user_id`` from the
  tenant context;
* serializes any ``extra=`` dict the call site passed verbatim.

Configure via ``LOGGING`` in Django settings; see
``core/logging_config.py`` for a ready-to-use dict.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from core.request_context import get_request_id, get_trace_context
from core.tenancy import get_current_tenant

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


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = get_request_id()
        if request_id is not None:
            payload["request_id"] = request_id

        trace = get_trace_context()
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
            payload[key] = _safe(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = record.stack_info

        return json.dumps(payload, default=str, separators=(",", ":"))


def _safe(value: Any) -> Any:
    # Best-effort serializer: pass through json-native types, fall
    # back to repr for anything else so we never crash a log call.
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    return repr(value)


LOGGING_CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "core.logging.JsonFormatter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "core": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
