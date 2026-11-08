"""Keep private endpoint paths and HTTP headers outside logs and client spans."""

from __future__ import annotations

import contextvars
import importlib
import logging
import threading
from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

_ACTIVE = contextvars.ContextVar("nvke_private_http", default=False)
_LOCK = threading.Lock()
_INSTALLED = False


def _install() -> None:
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return
        previous = logging.getLogRecordFactory()

        def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = previous(*args, **kwargs)
            if _ACTIVE.get() and record.name.startswith(("httpx", "httpcore")):
                record.msg, record.args = "Private NVKE HTTP operation.", ()
                record.exc_info, record.exc_text, record.stack_info = None, None, None
            return record

        logging.setLogRecordFactory(factory)
        _INSTALLED = True


@contextmanager
def private_http() -> Iterator[None]:
    _install()
    mark = _ACTIVE.set(True)
    try:
        with ExitStack() as stack:
            try:
                sentry = importlib.import_module("sentry_sdk")
            except ImportError:
                pass
            else:
                if sentry.get_client().is_active():
                    # An empty DSN prevents environment fallback; only this call's scope changes.
                    quiet = sentry.Client(dsn="", default_integrations=False, auto_enabling_integrations=False)
                    stack.callback(quiet.close)
                    scope = stack.enter_context(sentry.new_scope())
                    scope.set_client(quiet)
            try:
                context = importlib.import_module("opentelemetry.context")
            except ImportError:
                pass
            else:
                current = context.get_current()
                for key in (context._SUPPRESS_INSTRUMENTATION_KEY, context._SUPPRESS_HTTP_INSTRUMENTATION_KEY):
                    current = context.set_value(key, True, current)
                token = context.attach(current)
                stack.callback(context.detach, token)
            yield
    finally:
        _ACTIVE.reset(mark)
