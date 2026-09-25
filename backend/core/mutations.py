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
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, get_current_tenant

log = logging.getLogger(__name__)

# Thread-local so MutationAuditExtension can read the @mutation_audit action
# name after the resolver runs.  Set by the wrapper, read by the extension's
# on_operation() hook after ``yield``.  Safe for WSGI (one thread per request).
_mutation_action_local: threading.local = threading.local()


T = TypeVar("T")


class ErrorCode(enum.StrEnum):
    PERMISSION_DENIED = "PERMISSION_DENIED"
    VALIDATION = "VALIDATION"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    INTERNAL = "INTERNAL"
    PRECONDITION = "PRECONDITION"
    RATE_LIMITED = "RATE_LIMITED"
    # #487 — step-up auth. Returned by ``@requires_elevation`` when
    # the session lacks a fresh elevation. The FE intercepts this
    # code, opens the ``StepUpPrompt`` modal, elevates the session,
    # and retries the original mutation.
    STEP_UP_REQUIRED = "STEP_UP_REQUIRED"
    # #497 — optimistic concurrency. Returned when a mutation supplied
    # ``ifMatchVersion`` and the persisted row's version no longer
    # matches. The error envelope carries ``currentVersion`` so the FE
    # can refetch the latest state and rebuild the form. The mutation
    # is a no-op when this code fires.
    VERSION_MISMATCH = "VERSION_MISMATCH"


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


def attributable_organization_id(tenant: TenantContext | None) -> int | None:
    """The tenant org, if the actor may be filed under it; otherwise ``None``.

    ``X-Astrolift-Organization`` names the tenant by guid with no membership
    check, so an outsider's refused mutation would otherwise land in that
    org's own audit views (#1955). The actor has to be an active member of
    the org (the rule bearer tokens follow, #1910) or an active superuser.
    Both audit writers ask this before the mutation runs: its answer is the
    actor's standing when they made the request, not after, say, deleting
    the org.
    """
    if tenant is None or tenant.organization_id is None or tenant.actor_user_id is None:
        return None
    from django.contrib.auth import get_user_model

    from astrolift_identity.api_tokens import is_active_org_member

    if is_active_org_member(tenant.actor_user_id, tenant.organization_id):
        return tenant.organization_id
    if get_user_model().objects.filter(pk=tenant.actor_user_id, is_superuser=True, is_active=True).exists():
        return tenant.organization_id
    return None


# ---- Decorator -------------------------------------------------------


# Error codes that mean the caller was refused, as opposed to an allowed call
# that failed: audited as DENY so refusals reach the security alert rules.
_REFUSAL_CODES = frozenset(
    {
        "PERMISSION_DENIED",
        "STEP_UP_REQUIRED",
        "SECRET_APPROVAL_REQUIRED",
        "ATTESTATION_REQUIRED",
    }
)


def mutation_audit(
    *,
    action: str,
    target: Callable[..., tuple[str, Any] | None] | None = None,
    extras: Callable[[Any], dict[str, Any] | None] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Standard wrapper for every GraphQL mutation.

    ``action`` is the dotted name recorded in the audit log
    (e.g. ``"app.deploy"``). ``target`` is an optional resolver-arg
    function that returns ``(kind, id)`` for the affected object so the
    audit row can carry it.

    ``extras`` is an optional post-call hook: a callable that receives
    the resolver's ``MutationResult`` and returns a ``dict`` of
    additional fields to attach to the ``AuditEntry.extra`` payload.
    Useful when the issue's audit-trail requirement names specific
    counts or identifiers that aren't part of the standard envelope —
    e.g. #389's force-redeploy wants ``deployments_cancelled`` and
    ``k8s_objects_deleted`` on the audit row.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        import inspect

        permissions = getattr(fn, "__astrolift_permissions__", ())

        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> MutationResult:
            # Publish action name so MutationAuditExtension (schema-level
            # extension that writes to the DB audit log) can record the
            # dot-notation action instead of the raw GraphQL operation name.
            _mutation_action_local.action = action

            tenant = get_current_tenant()
            try:
                organization_id = attributable_organization_id(tenant)
            except Exception:  # noqa: BLE001 — audit must never raise
                log.warning("mutation %s: could not attribute the audit row", action, exc_info=True)
                organization_id = None
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
                # Resolvers return either the ``MutationResult`` dataclass or
                # (nearly all of them, via ``gql_failure``) the Strawberry
                # ``MutationResultType``, whose ``code`` is a plain string.
                # Read both, or every refused mutation is recorded as an ALLOW
                # with no error code (#1968).
                errors = getattr(result, "errors", None) or []
                if getattr(result, "ok", True) is False and errors:
                    first = errors[0]
                    code = getattr(first, "code", None)
                    error_code = getattr(code, "value", code)
                    error_message = getattr(first, "message", None)
                    decision = "DENY" if error_code in _REFUSAL_CODES else "ALLOW"

            # Resolvers may return either the bare ``MutationResult``
            # dataclass or the Strawberry ``MutationResultType`` —
            # duck-type on ``.ok`` so the extras hook handles both.
            extra_payload: dict[str, Any] | None = None
            if extras is not None and result is not None and hasattr(result, "ok"):
                try:
                    extra_payload = extras(result)
                except Exception:  # noqa: BLE001 — audit must never raise
                    log.warning(
                        "mutation %s: extras() raised; dropping extras payload",
                        action,
                        exc_info=True,
                    )

            entry = AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=organization_id,
                action=action,
                decision=decision,
                target_kind=target_kind,
                target_id=target_id,
                duration_ms=int((time.monotonic() - t0) * 1000),
                permissions=tuple(p.value for p in permissions if isinstance(p, Permission)),
                error_code=error_code,
                error_message=error_message,
                extra=extra_payload,
            )
            emit_audit(entry)
            return result

        wrapper.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
        return wrapper

    return decorator
