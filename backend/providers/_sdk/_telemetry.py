"""``@driver_op`` -- the single decorator that instruments every driver method.

This module is the answer to the May-18 instrumentation audit (#586-#623):
``backend/providers`` shipped without structured logging, Prometheus metrics,
OpenTelemetry spans, Temporal heartbeats, or audit events. Driver authors had
no single place to wire that machinery; the audit's recommendation -- and the
output of this module -- is one decorator that wraps every method and emits
all of them with a uniform schema.

Wiring summary
--------------

* **Structured log** -- ``logging.getLogger("astrolift.providers")`` info
  line on entry, exception line on failure. ``extra={...}`` carries
  ``cloud/driver/method/args``; arg-value redaction is controlled by
  ``redact_args``. No f-strings; the dict shape matches what the operations
  pipeline already ingests on the resolver side.

* **Prometheus metrics** -- one counter
  (``astrolift_provider_driver_op_total``) and one histogram
  (``astrolift_provider_driver_op_seconds``) on
  ``prometheus_client.REGISTRY``. ``backend/config/views.py:metrics_view``
  exports the same registry via ``generate_latest()``, so the new metrics
  are scrapable from the existing nginx-fronted ``/metrics`` route without
  any plumbing change.

* **OpenTelemetry span** -- ``opentelemetry.trace.get_tracer(__name__)``;
  the no-op tracer covers environments where OTEL isn't initialised
  (e.g. plain unit tests).

* **Temporal heartbeat** -- ``temporalio.activity.heartbeat`` at method
  entry when ``activity.in_activity()`` is truthy. Drivers also run from
  Django request paths and CLI tools; the check is wrapped in ``try``/
  ``except`` so the off-Temporal cases stay no-ops. Long-running loops
  inside drivers also call ``activity.heartbeat`` directly via
  ``maybe_heartbeat`` (see below).

* **Audit event** -- when ``audit=True``, emits a
  ``core.mutations.AuditEntry`` via ``emit_audit``. The action name defaults
  to ``f"provider.{driver}.{method}"``; ``sensitive_kind`` overrides it for
  the SOC2/HIPAA-sensitive surfaces (secrets, dns, tls, cluster teardown,
  workload-identity bind). Import is soft so the providers tree stays
  installable in isolation (it was originally a standalone repo before the
  consolidation; the soft import keeps that property).

* **Exception path** -- re-raises the original exception unmodified (never
  wraps; preserves the type for retry-class inspection by the activity
  caller). Records the exception on the span, bumps the
  ``outcome="error"`` counter, and emits an audit row with the error code.

Sync vs async
-------------

The decorator dispatches via ``inspect.iscoroutinefunction``. The async
wrapper awaits the coroutine inside the span/heartbeat context; the sync
wrapper handles plain return. Both treat ``self`` as the first positional
arg (this decorator is method-only).
"""

from __future__ import annotations

import dataclasses
import functools
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from typing import Any, Literal, TypeVar

# Prometheus, OTEL, and temporalio are optional at import time so the SDK
# stays useful when the consumer hasn't installed everything. In the
# astrolift-app docker image (and the providers test suite) all three are
# present; in a slimmed-down third-party install the metrics + traces +
# heartbeats degrade to no-ops cleanly.
try:
    from prometheus_client import REGISTRY, Counter, Histogram
except ImportError:  # pragma: no cover -- prometheus_client is in pyproject.toml
    REGISTRY = None  # type: ignore[assignment]
    Counter = None  # type: ignore[assignment,misc]
    Histogram = None  # type: ignore[assignment,misc]

try:
    from opentelemetry import trace as _otel_trace

    _tracer = _otel_trace.get_tracer("astrolift.providers")
    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover -- OTEL is in the backend Pipfile
    _otel_trace = None  # type: ignore[assignment]
    _tracer = None  # type: ignore[assignment]
    _OTEL_AVAILABLE = False

try:
    from temporalio import activity as _temporal_activity

    _TEMPORAL_AVAILABLE = True
except ImportError:  # pragma: no cover -- temporalio is in the backend Pipfile
    _temporal_activity = None  # type: ignore[assignment]
    _TEMPORAL_AVAILABLE = False


log = logging.getLogger("astrolift.providers")


# Two singletons live on ``prometheus_client.REGISTRY`` so the existing
# ``/metrics`` view exposes them. The ``_metric()`` helpers tolerate a
# previously-registered instance: the providers tree is re-imported in some
# test runs (per-test-fresh-app patterns), and ``Counter()`` raises on
# duplicate registration otherwise.
_COUNTER_NAME = "astrolift_provider_driver_op_total"
_HISTOGRAM_NAME = "astrolift_provider_driver_op_seconds"
_LABELS = ("cloud", "driver", "method", "outcome")
_HISTOGRAM_LABELS = ("cloud", "driver", "method")


def _get_or_create_counter() -> Any:
    if Counter is None:  # pragma: no cover
        return None
    existing = getattr(REGISTRY, "_names_to_collectors", {}).get(_COUNTER_NAME)
    if existing is not None:
        return existing
    return Counter(
        _COUNTER_NAME,
        "Driver method invocations by provider driver + outcome.",
        _LABELS,
    )


def _get_or_create_histogram() -> Any:
    if Histogram is None:  # pragma: no cover
        return None
    existing = getattr(REGISTRY, "_names_to_collectors", {}).get(_HISTOGRAM_NAME)
    if existing is not None:
        return existing
    return Histogram(
        _HISTOGRAM_NAME,
        "Driver method wall-clock latency in seconds, by provider driver.",
        _HISTOGRAM_LABELS,
    )


_op_counter = _get_or_create_counter()
_op_histogram = _get_or_create_histogram()


_REDACTED = "<redacted>"
_CloudLiteral = Literal["aws", "gcp", "azure", "k8s_native"] | None
F = TypeVar("F", bound=Callable[..., Any])
_heartbeat_sink: ContextVar[Callable[[str], None] | None] = ContextVar("provider_heartbeat_sink", default=None)


@contextmanager
def heartbeat_sink(callback: Callable[[str], None]):
    """Route this operation's heartbeats to its caller's execution context."""
    token = _heartbeat_sink.set(callback)
    try:
        yield
    finally:
        _heartbeat_sink.reset(token)


def maybe_heartbeat(detail: str = "") -> None:
    """Emit a Temporal heartbeat when running inside an activity, no-op otherwise.

    Drivers call this from inside long-running loops (manifest apply,
    rollout poll, namespace teardown) to keep the activity alive past the
    default ``start_to_close_timeout``. Outside Temporal -- CLI tools,
    Django request handlers, unit tests -- the function returns silently.
    """
    sink = _heartbeat_sink.get()
    if sink is not None:
        with suppress(Exception):
            sink(detail)
        return
    if not _TEMPORAL_AVAILABLE:
        return
    try:
        if _temporal_activity.in_activity():
            if detail:
                _temporal_activity.heartbeat(detail)
            else:
                _temporal_activity.heartbeat()
    except Exception:
        pass


def _redact_kwargs(kwargs: dict[str, Any], redact_args: tuple[str, ...]) -> dict[str, Any]:
    if not redact_args:
        return kwargs
    return {k: (_REDACTED if k in redact_args else v) for k, v in kwargs.items()}


def _audit_emit(
    *,
    action: str,
    decision: str,
    error_code: str | None,
    error_message: str | None,
    duration_ms: int,
    extra: dict[str, Any] | None,
    redact_errors: bool = False,
) -> None:
    """Emit an audit event via ``core.mutations.emit_audit`` if available.

    The providers tree is import-isolated from the backend at the package
    level (``backend/providers`` was originally a standalone repo). Soft
    import keeps the dependency arrow one-way: providers can run without
    ``core``; with ``core`` present the audit row lands on the configured
    writer (DB by default, console in tests).
    """
    try:
        from core.mutations import AuditEntry, emit_audit
        from core.tenancy import get_current_tenant
    except ImportError:  # pragma: no cover -- only happens when SDK is used standalone
        return

    try:
        tenant = get_current_tenant()
    except Exception:
        tenant = None

    try:
        entry = AuditEntry(
            actor_user_id=getattr(tenant, "actor_user_id", None) if tenant else None,
            organization_id=getattr(tenant, "organization_id", None) if tenant else None,
            action=action,
            decision=decision,
            target_kind=None,
            target_id=None,
            duration_ms=duration_ms,
            permissions=(),
            error_code=error_code,
            error_message=error_message,
            extra=extra,
        )
        emit_audit(entry)
    except Exception:
        # A writer failure can chain the original native exception while
        # handling it; private operations must suppress that traceback too.
        log.warning("driver_op audit emit failed for action=%s", action, exc_info=not redact_errors)


@dataclasses.dataclass(frozen=True)
class _OpContext:
    """Resolved arguments handed from ``@driver_op`` to its wrappers."""

    cloud: _CloudLiteral
    driver: str
    method: str
    audit: bool
    sensitive_kind: str | None
    redact_args: tuple[str, ...]
    redact_errors: bool
    heartbeat: bool

    @property
    def action(self) -> str:
        return self.sensitive_kind or f"provider.{self.driver}.{self.method}"

    @property
    def metric_cloud(self) -> str:
        return self.cloud or "unknown"


def _record_success(ctx: _OpContext, started: float, audit_extra: dict[str, Any] | None) -> None:
    duration_s = time.monotonic() - started
    if _op_counter is not None:
        _op_counter.labels(ctx.metric_cloud, ctx.driver, ctx.method, "success").inc()
    if _op_histogram is not None:
        _op_histogram.labels(ctx.metric_cloud, ctx.driver, ctx.method).observe(duration_s)
    if ctx.audit:
        _audit_emit(
            action=ctx.action,
            decision="ALLOW",
            error_code=None,
            error_message=None,
            duration_ms=int(duration_s * 1000),
            extra=audit_extra,
            redact_errors=ctx.redact_errors,
        )


def _record_error(ctx: _OpContext, started: float, exc: BaseException, audit_extra: dict[str, Any] | None) -> None:
    duration_s = time.monotonic() - started
    if _op_counter is not None:
        _op_counter.labels(ctx.metric_cloud, ctx.driver, ctx.method, "error").inc()
    if _op_histogram is not None:
        _op_histogram.labels(ctx.metric_cloud, ctx.driver, ctx.method).observe(duration_s)
    if ctx.audit:
        _audit_emit(
            action=ctx.action,
            decision="ERROR",
            error_code=type(exc).__name__,
            error_message="Private driver operation failed." if ctx.redact_errors else str(exc),
            duration_ms=int(duration_s * 1000),
            extra=audit_extra,
            redact_errors=ctx.redact_errors,
        )


def _log_entry(ctx: _OpContext, safe_kwargs: dict[str, Any]) -> None:
    log.info(
        "driver_op start",
        extra={
            "cloud": ctx.metric_cloud,
            "driver": ctx.driver,
            "method": ctx.method,
            "driver_args": safe_kwargs,
        },
    )


def _log_error(ctx: _OpContext, safe_kwargs: dict[str, Any], exc: BaseException) -> None:
    logger = log.error if ctx.redact_errors else log.exception
    logger(
        "driver_op error",
        extra={
            "cloud": ctx.metric_cloud,
            "driver": ctx.driver,
            "method": ctx.method,
            "driver_args": safe_kwargs,
            "exception_type": type(exc).__name__,
        },
    )


def _start_span(ctx: _OpContext) -> Any:
    """Return an OTEL span context manager, or a no-op when OTEL is absent.

    A null context manager keeps the wrapper code path uniform so the
    success/failure branches can both ``span.record_exception`` /
    ``span.set_status`` without a guard.
    """
    if not _OTEL_AVAILABLE or _tracer is None:
        return _NullSpanCM()
    if ctx.redact_errors:
        return _tracer.start_as_current_span(
            f"{ctx.driver}.{ctx.method}",
            attributes={"cloud": ctx.metric_cloud, "driver": ctx.driver, "method": ctx.method},
            record_exception=False,
            set_status_on_exception=False,
        )
    return _tracer.start_as_current_span(
        f"{ctx.driver}.{ctx.method}",
        attributes={"cloud": ctx.metric_cloud, "driver": ctx.driver, "method": ctx.method},
    )


class _NullSpanCM:
    """Stand-in span context manager used when OTEL isn't installed."""

    def __enter__(self) -> _NullSpanCM:
        return self

    def __exit__(self, *_exc: Any) -> Literal[False]:
        return False

    def record_exception(self, _exc: BaseException) -> None:
        return None

    def set_status(self, _status: Any) -> None:
        return None

    def set_attribute(self, _key: str, _value: Any) -> None:
        return None


def _set_error_status(span: Any, exc: BaseException, *, redact_errors: bool = False) -> None:
    """Mark an OTEL span errored, tolerating the no-op span.

    Real OTEL spans want ``Status(StatusCode.ERROR)``; the null span ignores
    the call. The import lives inside the function so a no-OTEL install
    doesn't try to resolve ``opentelemetry.trace.Status`` at import time.
    """
    if not _OTEL_AVAILABLE:
        return
    try:
        from opentelemetry.trace import Status, StatusCode

        span.set_status(Status(StatusCode.ERROR, "Private driver operation failed." if redact_errors else str(exc)))
        if not redact_errors:
            span.record_exception(exc)
    except Exception:
        pass


def driver_op(
    *,
    driver: str,
    cloud: _CloudLiteral = None,
    audit: bool = False,
    sensitive_kind: str | None = None,
    redact_args: tuple[str, ...] = (),
    redact_errors: bool = False,
    heartbeat: bool = True,
) -> Callable[[F], F]:
    """Instrument a driver method with logging, metrics, traces, heartbeat, and audit.

    Apply to every public method on every driver. ``cloud`` is the plugin
    slug (``aws|gcp|azure|k8s_native``) or ``None`` for SDK-level helpers;
    ``driver`` is the driver-kind string (``cluster``, ``secrets``,
    ``dns``, etc.) that appears in metric labels + span names.

    ``audit=True`` emits a ``core.mutations.AuditEntry`` for the call (one
    per call, both success and failure). ``sensitive_kind`` overrides the
    action name from ``provider.{driver}.{method}`` to the caller-supplied
    string (``secret.read``, ``dns.create_record``, etc.).

    ``redact_args`` is a tuple of keyword-arg names whose values must not
    appear in logs / spans. Use for bearer tokens, secret payload kvs, etc.

    ``redact_errors=True`` omits exception messages and tracebacks from this
    operation's logs, span and audit. The original exception still reaches
    the caller; other enclosing telemetry must enforce its own privacy policy.

    ``heartbeat=False`` opts a method out of the entry-heartbeat (e.g. for
    pure-CPU helpers where the entry signal would just add noise). Long
    loops inside a method should still call :func:`maybe_heartbeat`
    directly.
    """

    def decorator(fn: F) -> F:
        ctx = _OpContext(
            cloud=cloud,
            driver=driver,
            method=fn.__name__,
            audit=audit,
            sensitive_kind=sensitive_kind,
            redact_args=redact_args,
            redact_errors=redact_errors,
            heartbeat=heartbeat,
        )

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
                safe_kwargs = _redact_kwargs(kwargs, ctx.redact_args)
                _log_entry(ctx, safe_kwargs)
                if ctx.heartbeat:
                    maybe_heartbeat(f"{ctx.driver}.{ctx.method}")
                started = time.monotonic()
                span = _start_span(ctx)
                with span as active_span:
                    try:
                        result = await fn(self, *args, **kwargs)
                    except BaseException as exc:
                        _log_error(ctx, safe_kwargs, exc)
                        _set_error_status(
                            active_span if ctx.redact_errors else span, exc, redact_errors=ctx.redact_errors
                        )
                        _record_error(ctx, started, exc, {"driver_args": safe_kwargs})
                        raise
                    _record_success(ctx, started, {"driver_args": safe_kwargs})
                    return result

            async_wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
            async_wrapper.__astrolift_driver_op__ = ctx  # type: ignore[attr-defined]
            return async_wrapper  # type: ignore[return-value]

        if inspect.isasyncgenfunction(fn):

            @functools.wraps(fn)
            async def asyncgen_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
                safe_kwargs = _redact_kwargs(kwargs, ctx.redact_args)
                _log_entry(ctx, safe_kwargs)
                if ctx.heartbeat:
                    maybe_heartbeat(f"{ctx.driver}.{ctx.method}")
                started = time.monotonic()
                span = _start_span(ctx)
                with span as active_span:
                    try:
                        agen = fn(self, *args, **kwargs)
                        async for item in agen:
                            yield item
                    except BaseException as exc:
                        _log_error(ctx, safe_kwargs, exc)
                        _set_error_status(
                            active_span if ctx.redact_errors else span, exc, redact_errors=ctx.redact_errors
                        )
                        _record_error(ctx, started, exc, {"driver_args": safe_kwargs})
                        raise
                    _record_success(ctx, started, {"driver_args": safe_kwargs})

            asyncgen_wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
            asyncgen_wrapper.__astrolift_driver_op__ = ctx  # type: ignore[attr-defined]
            return asyncgen_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def sync_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            safe_kwargs = _redact_kwargs(kwargs, ctx.redact_args)
            _log_entry(ctx, safe_kwargs)
            if ctx.heartbeat:
                maybe_heartbeat(f"{ctx.driver}.{ctx.method}")
            started = time.monotonic()
            span = _start_span(ctx)
            with span as active_span:
                try:
                    result = fn(self, *args, **kwargs)
                except BaseException as exc:
                    _log_error(ctx, safe_kwargs, exc)
                    _set_error_status(active_span if ctx.redact_errors else span, exc, redact_errors=ctx.redact_errors)
                    _record_error(ctx, started, exc, {"driver_args": safe_kwargs})
                    raise
                _record_success(ctx, started, {"driver_args": safe_kwargs})
                return result

        sync_wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        sync_wrapper.__astrolift_driver_op__ = ctx  # type: ignore[attr-defined]
        return sync_wrapper  # type: ignore[return-value]

    return decorator


# Public helpers used by long-loop driver code to emit per-iteration
# heartbeats. Re-exported from ``_sdk`` for convenience.
__all__ = [
    "driver_op",
    "maybe_heartbeat",
]


# ``Awaitable`` is imported lazily through ``collections.abc`` only when
# the project upgrades to a typing-only re-export; today only sync + async
# call sites exist and the import isn't load-bearing. Suppress unused-import
# warnings without churning the top of the file.
_ = Awaitable
