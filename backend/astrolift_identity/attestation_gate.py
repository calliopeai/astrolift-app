"""``@requires_attestation`` resolver decorator (#496).

Companion to :func:`astrolift_identity.step_up.requires_elevation`.
Where ``requires_elevation`` gates on session freshness (recent
password / MFA re-prompt), this gates on device attestation: the
mobile session must have produced a passing iOS App Attest or
Android Play Integrity blob within its lifetime.

Usage::

    @strawberry.field
    @mutation_audit(action="app.deploy")
    @requires_attestation()
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def deploy_app(self, info, input): ...

On a non-mobile session this decorator is a no-op — browser and CLI
clients can't attest, and gating them would lock the operator out
of their own platform. The per-install policy
(``REQUIRE_ATTESTATION_FOR_MOBILE``) decides whether attestation is
even attempted; this decorator enforces it once enabled.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any

from astrolift_graphql import failure as gql_failure
from core.mutations import ErrorCode

log = logging.getLogger(__name__)

_DEFAULT_MESSAGE = "Device attestation required."


def requires_attestation(
    *,
    action_label: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Gate a Strawberry mutation on a verified device attestation.

    ``action_label`` is echoed on the deny envelope so the FE can
    render an action-specific attest prompt
    ("To deploy this app your phone needs to attest …").
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        import inspect

        @functools.wraps(fn)
        def wrapper(self, info, *args, **kwargs):
            from astrolift_identity.attestation.service import attestation_required
            from astrolift_identity.models import AstroliftSession
            from astrolift_identity.step_up import _emit_deny_audit

            request = getattr(getattr(info, "context", None), "request", None)
            # API-token-authenticated calls bypass — same rationale
            # as ``@requires_elevation``: token-authed callers can't
            # have a mobile-device attestation, so the gate is
            # meaningless for them.
            if request is not None and getattr(request, "_api_token", None) is not None:
                return fn(self, info, *args, **kwargs)
            if request is None or not hasattr(request, "session"):
                log.debug(
                    "attestation: no request/session on info (direct test call?); bypassing %s",
                    fn.__qualname__,
                )
                return fn(self, info, *args, **kwargs)

            session_key = getattr(request.session, "session_key", None)
            session_row = None
            if session_key:
                session_row = AstroliftSession.objects.filter(session_key=session_key).first()

            if not attestation_required(session=session_row, sensitive_op=True):
                return fn(self, info, *args, **kwargs)

            _emit_deny_audit(
                fn=fn,
                action="auth.attestation.required",
                action_label=action_label,
                extra={
                    "resolver": fn.__qualname__,
                    "action_label": action_label,
                    "reason": "attestation_required",
                },
            )
            return gql_failure(
                ErrorCode.STEP_UP_REQUIRED.value,
                _DEFAULT_MESSAGE if not action_label else f"{action_label}: {_DEFAULT_MESSAGE}",
                requires_attestation=True,
            )

        wrapper.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
        wrapper.__astrolift_attestation_required__ = True  # type: ignore[attr-defined]
        wrapper.__astrolift_attestation_label__ = action_label  # type: ignore[attr-defined]
        return wrapper

    return decorator


__all__ = ["requires_attestation"]
