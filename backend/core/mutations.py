"""
``MutationResult`` envelope and the ``@mutation_audit`` decorator.

Every Astrolift GraphQL mutation returns
``MutationResult { ok, errors, data? }``. Resolvers never raise out;
exceptions surface as ``ok: false`` with structured error entries the
client can render. This makes the schema usable from typed clients
without try/except plumbing on every call site.

``@mutation_audit`` is the standard wrapper that:

1. Catches :class:`PermissionDenied` and translates it to an error.
2. Records the (actor, action, target, decision) tuple for the audit log.
3. Wraps unexpected exceptions in a ``"INTERNAL"`` error and re-logs.

The audit log writer is pluggable so this module doesn't depend on
``AuditEvent`` (which lives in P1.T5); a no-op writer is used until
the model is wired in.
"""

from __future__ import annotations

import dataclasses
import enum
import functools
import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

from core.permissions import Permission, PermissionDenied
from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)


T = TypeVar("T")


class ErrorCode(enum.StrEnum):
    PERMISSION_DENIED = "PERMISSION_DENIED"
    VALIDATION = "VALIDATION"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    INTERNAL = "INTERNAL"
    PRECONDITION = "PRECONDITION"
    RATE_LIMITED = "RATE_LIMITED"


@dataclasses.dataclass(slots=True)
class MutationError:
    code: ErrorCode
    message: str
    field: str | None = None
    detail: dict[str, Any] | None = None


@dataclasses.dataclass(slots=True)
class MutationResult[T]:
    ok: bool
    errors: list[MutationError] = dataclasses.field(default_factory=list)
    data: T | None = None

    @classmethod
    def success(cls, data: T) -> MutationResult[T]:
        return cls(ok=True, data=data)

    @classmethod
    def failure(
        cls, code: ErrorCode, message: str, *, field: str | None = None, detail: dict | None = None
    ) -> MutationResult[T]:
        return cls(
            ok=False,
            errors=[MutationError(code=code, message=message, field=field, detail=detail)],
        )


# ---- Audit log plug-point --------------------------------------------


@dataclasses.dataclass(slots=True)
class AuditEntry:
    actor_user_id: int | None
    organization_id: int | None
    action: str
    decision: str
    target_kind: str | None
    target_id: int | str | None
    duration_ms: int
    permissions: tuple[str, ...]
    error_code: str | None = None
    error_message: str | None = None
    extra: dict[str, Any] | None = None


AuditWriter = Callable[[AuditEntry], None]


def _log_audit_entry(entry: AuditEntry) -> None:
    log.info("audit", extra={"audit": dataclasses.asdict(entry)})


_audit_writer: AuditWriter = _log_audit_entry


def register_audit_writer(writer: AuditWriter) -> None:
    global _audit_writer
    _audit_writer = writer


def emit_audit(entry: AuditEntry) -> None:
    _audit_writer(entry)


# ---- Decorator -------------------------------------------------------


def mutation_audit(
    *,
    action: str,
    target: Callable[..., tuple[str, Any] | None] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Standard wrapper for every GraphQL mutation.

    ``action`` is the dotted name recorded in the audit log
    (e.g. ``"app.deploy"``). ``target`` is an optional resolver-arg
    function that returns ``(kind, id)`` for the affected object so the
    audit row can carry it.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        import inspect

        permissions = getattr(fn, "__astrolift_permissions__", ())

        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> MutationResult:
            tenant = get_current_tenant()
            target_kind: str | None = None
            target_id: int | str | None = None
            if target is not None:
                resolved = target(*args, **kwargs)
                if resolved is not None:
                    target_kind, target_id = resolved

            t0 = time.monotonic()
            error_code: str | None = None
            error_message: str | None = None
            decision = "ALLOW"
            try:
                result = fn(*args, **kwargs)
            except PermissionDenied as exc:
                decision = "DENY"
                error_code = ErrorCode.PERMISSION_DENIED.value
                error_message = exc.reason
                result = MutationResult.failure(ErrorCode.PERMISSION_DENIED, exc.reason)
            except Exception as exc:  # surface unexpected errors as INTERNAL
                log.exception("mutation %s failed unexpectedly", action)
                error_code = ErrorCode.INTERNAL.value
                error_message = str(exc)
                result = MutationResult.failure(ErrorCode.INTERNAL, str(exc))
            else:
                if isinstance(result, MutationResult):
                    if not result.ok and result.errors:
                        first = result.errors[0]
                        error_code = first.code.value
                        error_message = first.message
                        decision = "DENY" if first.code is ErrorCode.PERMISSION_DENIED else "ALLOW"

            entry = AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action=action,
                decision=decision,
                target_kind=target_kind,
                target_id=target_id,
                duration_ms=int((time.monotonic() - t0) * 1000),
                permissions=tuple(p.value for p in permissions if isinstance(p, Permission)),
                error_code=error_code,
                error_message=error_message,
            )
            emit_audit(entry)
            return result

        wrapper.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
        return wrapper

    return decorator
