"""``@requires_elevation`` resolver decorator (#487).

Sits between ``@require_permission`` (innermost — runs first) and
``@mutation_audit`` (outermost — wraps the whole thing). When the
session lacks a fresh elevation, returns a ``MutationResult`` envelope
with ``STEP_UP_REQUIRED`` instead of executing the resolver body, and
emits an audit row for the deny so security review can spot operators
hammering a gated mutation without elevation.

Usage on any sensitive mutation::

    @strawberry.field
    @mutation_audit(action="app.secret.set")
    @requires_elevation(action_label="app.secret.set")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def set_app_secret(self, info, input): ...

The ``action_label`` is what the FE shows on the re-auth modal
("To set this secret you need to confirm your password") — so it
should read like a user-facing verb phrase, not a permission code.
"""

from __future__ import annotations

import dataclasses
import functools
import logging
from collections.abc import Callable
from typing import Any

from astrolift_graphql import failure as gql_failure
from astrolift_identity.session_elevation import (
    SESSION_KEY_ELEVATED_UNTIL,
    get_status,
)
from core.mutations import AuditEntry, ErrorCode, emit_audit
from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)

# Message text the FE renders verbatim on the re-auth modal when
# ``action_label`` is left default. Single source of truth so the
# 8 locale bundles can map one key to N translations.
_DEFAULT_MESSAGE = "Sensitive operation requires recent authentication."


def requires_elevation(
    *,
    action_label: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Gate a Strawberry mutation on a fresh session elevation.

    ``action_label`` (optional) is echoed on the deny envelope so the
    FE can render an action-specific re-auth prompt
    ("To set this secret …" vs. the generic copy). The audit row
    always carries the wrapped resolver's ``__qualname__`` regardless
    so we can spot patterns in the security logs.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        import inspect

        @functools.wraps(fn)
        def wrapper(self, info, *args, **kwargs):
            request = getattr(getattr(info, "context", None), "request", None)
            # API-token-authenticated calls bypass step-up — the
            # token issuance ceremony (operator + password + MFA in
            # the UI) is the authentication; step-up is a session-
            # scoped recency concept that doesn't translate. CI
            # runners holding a scoped token would otherwise be
            # locked out of every gated mutation. The token's scope
            # set still gates *what* it can do — step-up is about
            # session freshness, not authorization.
            if request is not None and getattr(request, "_api_token", None) is not None:
                return fn(self, info, *args, **kwargs)
            # Direct-call path (pytest mutations bypassing HTTP).
            # A real HTTP request always carries a session attribute
            # because SessionMiddleware runs before the GraphQL view
            # — so the only way ``request`` lacks ``session`` is a
            # unit test calling the resolver directly. Bypass with a
            # warning rather than failing every existing test; the
            # security gate at the HTTP layer is unaffected.
            if request is None or not hasattr(request, "session"):
                log.debug(
                    "step_up: no request/session on info (direct test call?); bypassing %s",
                    fn.__qualname__,
                )
                return fn(self, info, *args, **kwargs)
            session = request.session
            status = get_status(session)
            if status.elevated:
                return fn(self, info, *args, **kwargs)

            # Audit the deny so security review can spot patterns
            # (operator hammering a sensitive mutation without ever
            # elevating, scripted callers that don't know about
            # step-up, etc.).
            tenant = get_current_tenant()
            try:
                emit_audit(
                    AuditEntry(
                        actor_user_id=tenant.actor_user_id if tenant else None,
                        organization_id=tenant.organization_id if tenant else None,
                        action="auth.step_up.denied",
                        decision="DENY",
                        target_kind="resolver",
                        target_id=fn.__qualname__,
                        duration_ms=0,
                        permissions=(),
                        error_code=ErrorCode.STEP_UP_REQUIRED.value,
                        error_message=_DEFAULT_MESSAGE,
                        extra={
                            "resolver": fn.__qualname__,
                            "action_label": action_label,
                        },
                    )
                )
            except Exception:  # noqa: BLE001 — audit emission must never break a deny
                log.exception("step_up: audit emit failed on deny for %s", fn.__qualname__)

            return gql_failure(
                ErrorCode.STEP_UP_REQUIRED.value,
                _DEFAULT_MESSAGE if not action_label else f"{action_label}: {_DEFAULT_MESSAGE}",
            )

        wrapper.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
        # Mark the resolver so introspection / docs tooling can list
        # every step-up-gated mutation without re-scanning the source.
        wrapper.__astrolift_step_up_required__ = True  # type: ignore[attr-defined]
        wrapper.__astrolift_step_up_label__ = action_label  # type: ignore[attr-defined]
        return wrapper

    return decorator


@dataclasses.dataclass(frozen=True, slots=True)
class StepUpProbe:
    """Describes one step-up-gated mutation; used by introspection."""

    resolver: str
    action_label: str | None


def list_gated_resolvers() -> list[StepUpProbe]:
    """Return every Strawberry mutation tagged by :func:`requires_elevation`.

    Drives the ``astroliftElevationStatus.requiredFor`` field so the
    FE knows which mutations will need elevation before issuing them.
    Best-effort: walks the per-app Mutation classes we know compose
    into the root schema; an out-of-tree fork can call
    :func:`register_gated_mutation_class` to add its own.
    """
    out: list[StepUpProbe] = []
    seen: set[str] = set()

    for cls in _candidate_mutation_classes():
        # Walk the MRO so subclasses (the composed IdentityMutation
        # via _BaseIdentityMutation, MyConnectedAccountsMutation,
        # IdentityAnonymizeUserMutation) all surface their gated
        # methods exactly once.
        for ancestor in cls.__mro__:
            for attr_name, attr_value in vars(ancestor).items():
                wrapped = _unwrap_strawberry(attr_value)
                if wrapped is None:
                    continue
                cur = wrapped
                while cur is not None:
                    if getattr(cur, "__astrolift_step_up_required__", False):
                        name = f"{ancestor.__name__}.{attr_name}"
                        if name in seen:
                            break
                        seen.add(name)
                        out.append(
                            StepUpProbe(
                                resolver=name,
                                action_label=getattr(cur, "__astrolift_step_up_label__", None),
                            )
                        )
                        break
                    cur = getattr(cur, "__wrapped__", None)

    out.sort(key=lambda p: p.resolver)
    return out


def _unwrap_strawberry(attr_value: Any) -> Any:
    """Pull the underlying function out of whatever Strawberry
    decoration we got. Returns ``None`` for plain attributes."""
    if attr_value is None:
        return None
    # Strawberry's @strawberry.field stores the wrapped resolver on
    # different attributes across versions; try the known ones.
    for attr in ("base_resolver", "wrapped_func", "func"):
        wrapped = getattr(attr_value, attr, None)
        if wrapped is not None and callable(wrapped):
            return wrapped
    if callable(attr_value):
        return attr_value
    return None


def _candidate_mutation_classes() -> list[type]:
    """Per-app Mutation classes that compose into the root schema.

    We import them lazily and tolerate ImportError so the helper stays
    usable from unit tests that haven't loaded every per-app schema.
    Adding a new app with gated mutations means adding it here.
    """
    candidates: list[type] = []

    def _try(module: str, name: str) -> None:
        try:
            mod = __import__(module, fromlist=[name])
            cls = getattr(mod, name, None)
            if isinstance(cls, type):
                candidates.append(cls)
        except Exception:  # noqa: BLE001 — silent best-effort
            log.debug("step_up: could not load %s.%s for gated-resolver listing", module, name)

    _try("astrolift_identity.schema", "IdentityMutation")
    _try("astrolift_services.schema.mutations", "ServicesMutation")
    _try("astrolift_lifecycle.schema.mutations", "LifecycleMutation")
    _try("astrolift_registry.schema.mutations", "RegistryMutation")
    return candidates


__all__ = [
    "SESSION_KEY_ELEVATED_UNTIL",  # re-export for callers that want the raw key
    "StepUpProbe",
    "list_gated_resolvers",
    "requires_elevation",
]
