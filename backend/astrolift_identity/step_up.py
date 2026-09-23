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
    METHOD_PASSWORD,
    METHOD_SSO,
    METHOD_WEBAUTHN,
    SESSION_KEY_ELEVATED_UNTIL,
    get_status,
)
from astrolift_identity.sessions import SESSION_LOGIN_METHOD_KEY
from core.mutations import AuditEntry, ErrorCode, emit_audit
from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)

# Message text the FE renders verbatim on the re-auth modal when
# ``action_label`` is left default. Single source of truth so the
# 8 locale bundles can map one key to N translations.
_DEFAULT_MESSAGE = "Sensitive operation requires recent authentication."


def is_elevated_and_attested(info: Any) -> bool:
    """True when ``info``'s call satisfies the gate ``@requires_elevation`` enforces.

    Same rule, in order: API-token calls are exempt (token issuance —
    operator + password + MFA in the UI — is the authentication;
    step-up is a session-scoped recency concept that doesn't
    translate); the Constance ``REQUIRE_STEP_UP_AUTH`` off-switch
    (default off) makes the whole gate a no-op until an install opts
    in; a resolver invoked with no request/session (a unit test
    bypassing HTTP) is treated as satisfied since there's no session
    to elevate; otherwise the session must be freshly elevated and,
    when the install requires device attestation, attested too.

    Exposed so a read path that must *redact* rather than hard-fail
    (e.g. masking a secret value on a field ``@requires_elevation``
    doesn't wrap, #1920) asks the identical question the mutation
    gate asks — one predicate, so the two surfaces can never drift
    apart.
    """
    request = getattr(getattr(info, "context", None), "request", None)
    if request is not None and getattr(request, "_api_token", None) is not None:
        return True
    try:
        from constance import config as constance_config

        if not getattr(constance_config, "REQUIRE_STEP_UP_AUTH", False):
            return True
    except Exception:  # noqa: BLE001 — see requires_elevation for rationale
        pass
    if request is None or not hasattr(request, "session"):
        return True
    session = request.session
    status = get_status(session)
    return status.elevated and not _attestation_gate_active(request)


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
            if is_elevated_and_attested(info):
                return fn(self, info, *args, **kwargs)
            # Everything below builds the deny response. Re-derive
            # ``request`` / ``attest_required`` here rather than have
            # ``is_elevated_and_attested`` hand them back — that keeps
            # the shared predicate a plain boolean callers elsewhere
            # can rely on without knowing this decorator's internals.
            request = getattr(getattr(info, "context", None), "request", None)
            if request is None or not hasattr(request, "session"):
                # ``is_elevated_and_attested`` already treats this as
                # satisfied, so this branch is unreachable in practice;
                # kept as a defensive fallback.
                log.debug(
                    "step_up: no request/session on info (direct test call?); bypassing %s",
                    fn.__qualname__,
                )
                return fn(self, info, *args, **kwargs)
            session = request.session
            # #496 — when the install requires attestation for
            # sensitive ops, the attestation gate runs in *addition*
            # to the standard step-up freshness gate. A session that
            # is freshly elevated but not attested still gets the
            # deny, with ``requires_attestation: true`` so the FE
            # opens the attest-prompt instead of the password-prompt.
            attest_required = _attestation_gate_active(request)
            supported = _supported_step_up_methods(session)
            if attest_required:
                _emit_deny_audit(
                    fn=fn,
                    action="auth.attestation.required",
                    action_label=action_label,
                    extra={
                        "resolver": fn.__qualname__,
                        "action_label": action_label,
                        "reason": "attestation_required",
                        "supported_methods": supported,
                    },
                )
                return gql_failure(
                    ErrorCode.STEP_UP_REQUIRED.value,
                    _DEFAULT_MESSAGE if not action_label else f"{action_label}: {_DEFAULT_MESSAGE}",
                    requires_attestation=True,
                    supported_methods=supported,
                )

            # Audit the deny so security review can spot patterns
            # (operator hammering a sensitive mutation without ever
            # elevating, scripted callers that don't know about
            # step-up, etc.).
            _emit_deny_audit(
                fn=fn,
                action="auth.step_up.denied",
                action_label=action_label,
                extra={
                    "resolver": fn.__qualname__,
                    "action_label": action_label,
                    "supported_methods": supported,
                },
            )

            return gql_failure(
                ErrorCode.STEP_UP_REQUIRED.value,
                _DEFAULT_MESSAGE if not action_label else f"{action_label}: {_DEFAULT_MESSAGE}",
                supported_methods=supported,
            )

        wrapper.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
        # Mark the resolver so introspection / docs tooling can list
        # every step-up-gated mutation without re-scanning the source.
        wrapper.__astrolift_step_up_required__ = True  # type: ignore[attr-defined]
        wrapper.__astrolift_step_up_label__ = action_label  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _emit_deny_audit(
    *,
    fn: Callable[..., Any],
    action: str,
    action_label: str | None,
    extra: dict[str, Any],
) -> None:
    """Best-effort deny-audit emission shared by step-up + attestation deny paths."""
    tenant = get_current_tenant()
    try:
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action=action,
                decision="DENY",
                target_kind="resolver",
                target_id=fn.__qualname__,
                duration_ms=0,
                permissions=(),
                error_code=ErrorCode.STEP_UP_REQUIRED.value,
                error_message=_DEFAULT_MESSAGE,
                extra=extra,
            )
        )
    except Exception:  # noqa: BLE001 — audit emission must never break a deny
        log.exception("step_up: audit emit failed on deny for %s", fn.__qualname__)


def _supported_step_up_methods(session: Any) -> list[str]:
    """Derive the credential families the FE may use to satisfy step-up.

    Driven by :data:`SESSION_LOGIN_METHOD_KEY` written at login time:

    * ``password`` (or unset, the legacy default) → ``["password"]``
    * ``sso`` → ``["sso"]``
    * ``webauthn`` → ``["webauthn"]``

    SSO users lack a usable password hash on the User row, so a modal
    that only offers password is unfulfillable for them (#526). Returning
    the right method on the deny envelope lets the FE pick the correct
    branch — password form vs. IdP redirect button vs. WebAuthn prompt
    — without a second round-trip to ``astroliftElevationStatus``.

    Falls back to ``["password"]`` for any unrecognized / missing
    value so the worst case is the pre-#526 behaviour (which still
    works for password users) rather than an empty list (which would
    render no controls at all).
    """
    if session is None:
        return [METHOD_PASSWORD]
    try:
        raw = session.get(SESSION_LOGIN_METHOD_KEY) if hasattr(session, "get") else None
    except Exception:  # noqa: BLE001 — never blow up the deny path on a bag read
        raw = None
    if not isinstance(raw, str):
        return [METHOD_PASSWORD]
    lowered = raw.strip().lower()
    if lowered == "sso":
        return [METHOD_SSO]
    if lowered == "webauthn":
        return [METHOD_WEBAUTHN]
    # ``password`` and ``magic_link`` both surface as the password form
    # — magic-link users do have a usable password hash because the
    # link consumption mints one. Future iteration may split magic-link
    # into its own UI branch; for now password is the safe fallback.
    return [METHOD_PASSWORD]


def _attestation_gate_active(request: Any) -> bool:
    """Return True when the current request's session must be device-attested.

    Resolves the mobile session sidecar from the request and asks
    :func:`astrolift_identity.attestation.service.attestation_required`
    to apply the per-install Constance policy.

    Returns False on any error (no session row yet, lookup failed) —
    the gate is opt-in by Constance flag and a transient failure on
    the lookup mustn't lock out non-mobile callers. The
    ``sensitive_op=True`` argument here is hard-coded because
    ``@requires_elevation`` is the marker for "this is a sensitive
    mutation"; if you wrapped the resolver, it's sensitive by
    definition.
    """
    try:
        from astrolift_identity.attestation.service import attestation_required
        from astrolift_identity.models import AstroliftSession

        session = getattr(request, "session", None)
        session_key = getattr(session, "session_key", None) if session else None
        if not session_key:
            return False
        row = AstroliftSession.objects.filter(session_key=session_key).first()
        return attestation_required(session=row, sensitive_op=True)
    except Exception:  # noqa: BLE001 — never block on a lookup failure
        log.exception("step_up: attestation gate lookup failed; allowing through")
        return False


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
    _try("astrolift_operations.schema.mutations", "OperationsMutation")
    return candidates


__all__ = [
    "SESSION_KEY_ELEVATED_UNTIL",  # re-export for callers that want the raw key
    "StepUpProbe",
    "is_elevated_and_attested",
    "list_gated_resolvers",
    "requires_elevation",
]
