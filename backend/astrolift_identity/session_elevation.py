"""Session elevation (step-up auth) for sensitive mutations (#487).

Spec 27 §4.1 lists ``auth.elevate_admin`` / ``auth.deelevate_admin``
as identity operations. Without them every authenticated session
can perform a destructive operation (secret writes, role binding
changes, force-redeploys, app deregistration) regardless of how old
the session is — a phone left unlocked for thirty seconds is enough
to leak a prod secret. SOC2 / SOX commonly require fresh-auth for
privileged operations.

This module is the server-side gate. Elevation state is stored as
two values on the Django session:

* ``elevated_until`` — ISO 8601 UTC datetime string; null means the
  session has never been elevated (or the prior elevation expired).
* ``elevation_method`` — ``"password"``, ``"webauthn"``, ``"otp"``,
  ``"magic_link"`` — the credential the operator presented. Recorded
  on the audit row so security review can spot installs where
  step-up always falls back to password (no MFA enrollment yet).

Why session-state instead of a new model: ``request.session`` is
already the per-session bag the platform writes to (the existing
``_auth_user_backend`` / ``auth_next`` keys). A new ``SessionRow``
model would duplicate ``django_session``'s expire-cleanup machinery
without buying anything — elevation is intrinsically a session-
scoped property, not a user-scoped one (other sessions stay
un-elevated even when one is elevated).

The TTL is sourced from the ``STEP_UP_AUTH_TTL_SECONDS`` Constance
flag (default 900s / 15 min). Per-call overrides are clamped at
``STEP_UP_AUTH_MAX_TTL_SECONDS`` so an operator can't elevate for a
week and defeat the purpose of step-up.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from collections.abc import Callable
from typing import Any

from django.utils import timezone

log = logging.getLogger(__name__)

# Session-bag keys. Stable strings — changing them invalidates every
# in-flight elevation (acceptable on migration since elevation is
# short-lived by design, but we don't want a code refactor to wipe
# every operator's admin status mid-session).
SESSION_KEY_ELEVATED_UNTIL = "astrolift_elevated_until"
SESSION_KEY_ELEVATION_METHOD = "astrolift_elevation_method"

# Allowed credential methods, mirrored from spec 27 §4.1. New methods
# get added here first so the elevateAdminSession mutation rejects
# unknown values with a VALIDATION error envelope rather than a 500.
METHOD_PASSWORD = "password"
METHOD_OTP = "otp"
METHOD_WEBAUTHN = "webauthn"
METHOD_MAGIC_LINK = "magic_link"
KNOWN_METHODS: frozenset[str] = frozenset({METHOD_PASSWORD, METHOD_OTP, METHOD_WEBAUTHN, METHOD_MAGIC_LINK})

# Default fallback when Constance is unreachable (test settings that
# disable constance, fresh installs before migrate runs, etc.). 15 min
# matches spec 27 §4.1 and the issue body. The hard cap matches too.
_DEFAULT_TTL_SECONDS = 900
_DEFAULT_MAX_TTL_SECONDS = 900


@dataclasses.dataclass(frozen=True, slots=True)
class ElevationStatus:
    """Snapshot of a session's elevation state at one point in time.

    Returned by :func:`get_status` and surfaced on the GraphQL
    ``astroliftElevationStatus`` query so the FE can render the
    'Admin elevated for N more seconds' indicator.
    """

    elevated: bool
    elevated_until: dt.datetime | None
    method: str | None
    seconds_remaining: int


def _ttl_seconds_default() -> int:
    """Read the Constance default; fall back to 15 min if unavailable."""
    try:
        from constance import config

        value = int(getattr(config, "STEP_UP_AUTH_TTL_SECONDS", _DEFAULT_TTL_SECONDS))
    except Exception:  # noqa: BLE001 — constance optional in unit tests
        value = _DEFAULT_TTL_SECONDS
    return max(1, value)


def _ttl_seconds_max() -> int:
    """Hard cap on per-call ttlSeconds; clamps the elevateAdminSession arg."""
    try:
        from constance import config

        value = int(getattr(config, "STEP_UP_AUTH_MAX_TTL_SECONDS", _DEFAULT_MAX_TTL_SECONDS))
    except Exception:  # noqa: BLE001
        value = _DEFAULT_MAX_TTL_SECONDS
    return max(1, value)


def clamp_ttl_seconds(requested: int | None) -> int:
    """Resolve the effective TTL for an elevateAdminSession call.

    ``None`` -> Constance default. Below 1s gets clamped to 1s (a
    zero-TTL elevation is just a noisy no-op). Above the configured
    max gets silently clamped — the FE doesn't surface it as an
    error because the cap is policy, not user input.
    """
    if requested is None:
        return _ttl_seconds_default()
    if requested < 1:
        return 1
    cap = _ttl_seconds_max()
    if requested > cap:
        return cap
    return int(requested)


def get_status(session: Any, *, now: dt.datetime | None = None) -> ElevationStatus:
    """Read the elevation snapshot from a Django session object.

    ``session`` is duck-typed — anything with ``.get(key)`` works,
    which covers both ``HttpRequest.session`` and the in-memory dict
    tests pass. Returns a fresh :class:`ElevationStatus` every call;
    the snapshot is read-only.
    """
    if session is None:
        return ElevationStatus(elevated=False, elevated_until=None, method=None, seconds_remaining=0)

    now = now or timezone.now()
    raw = session.get(SESSION_KEY_ELEVATED_UNTIL)
    method = session.get(SESSION_KEY_ELEVATION_METHOD)
    if not raw:
        return ElevationStatus(elevated=False, elevated_until=None, method=None, seconds_remaining=0)

    parsed = _parse_iso(raw)
    if parsed is None:
        # Corrupt value (someone hand-edited the session row, or a
        # cross-version migration left a bad string). Treat as
        # un-elevated rather than 500ing the request.
        log.warning("malformed elevated_until in session; treating as un-elevated: %r", raw)
        return ElevationStatus(elevated=False, elevated_until=None, method=None, seconds_remaining=0)

    remaining = int((parsed - now).total_seconds())
    if remaining <= 0:
        return ElevationStatus(elevated=False, elevated_until=parsed, method=method, seconds_remaining=0)
    return ElevationStatus(
        elevated=True,
        elevated_until=parsed,
        method=method if method in KNOWN_METHODS else None,
        seconds_remaining=remaining,
    )


def is_elevated(session: Any, *, now: dt.datetime | None = None) -> bool:
    """Fast yes/no for the ``@requires_elevation`` decorator."""
    return get_status(session, now=now).elevated


def elevate(
    session: Any,
    *,
    method: str,
    ttl_seconds: int | None = None,
    now: dt.datetime | None = None,
) -> ElevationStatus:
    """Set the session's elevation expiry to ``now + ttl``.

    Caller is responsible for validating the credential — this
    module only flips the session bit once the verifier returned ok.
    Returns the resulting :class:`ElevationStatus` so the resolver
    can echo ``elevatedUntil`` to the FE without re-reading.
    """
    if method not in KNOWN_METHODS:
        raise ValueError(f"unknown elevation method {method!r}; allowed: {sorted(KNOWN_METHODS)}")
    now = now or timezone.now()
    seconds = clamp_ttl_seconds(ttl_seconds)
    expires_at = now + dt.timedelta(seconds=seconds)
    session[SESSION_KEY_ELEVATED_UNTIL] = expires_at.isoformat()
    session[SESSION_KEY_ELEVATION_METHOD] = method
    # Django's SessionStore only persists when ``modified`` is True;
    # explicit assignment flips it but defensive-set in case the
    # in-memory dict tests use doesn't watch attribute access.
    _mark_modified(session)
    return ElevationStatus(
        elevated=True,
        elevated_until=expires_at,
        method=method,
        seconds_remaining=seconds,
    )


def deelevate(session: Any) -> ElevationStatus:
    """Clear the session's elevation immediately.

    Returns a fresh un-elevated :class:`ElevationStatus` so the
    resolver can echo it back without a round-trip. Idempotent —
    calling on an already-un-elevated session is a no-op.
    """
    if session is None:
        return ElevationStatus(elevated=False, elevated_until=None, method=None, seconds_remaining=0)
    session.pop(SESSION_KEY_ELEVATED_UNTIL, None)
    session.pop(SESSION_KEY_ELEVATION_METHOD, None)
    _mark_modified(session)
    return ElevationStatus(elevated=False, elevated_until=None, method=None, seconds_remaining=0)


def _mark_modified(session: Any) -> None:
    try:
        session.modified = True
    except (AttributeError, TypeError):
        # Plain dicts (used by unit tests) don't have ``modified`` —
        # the real SessionStore does. Either case the value has been
        # mutated above; nothing more to do.
        pass


def _parse_iso(raw: Any) -> dt.datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Defensive: legacy sessions might store a naive datetime.
        # Treat as UTC rather than crashing on comparison.
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


# ---- credential verifier hook ---------------------------------------
#
# The elevateAdminSession mutation needs to validate the credential
# before flipping the session bit. The real verifier wires into the
# active IdP (password against Auth0 / Cognito / local-login;
# webauthn against the device public key registry). To keep this
# module testable and dep-free, we ship a pluggable verifier hook:
# tests register their own ``verify(user, method, credential)``
# callable; production registers the real one at startup.
#
# Default verifier: deny everything (deny-by-default). This means a
# fresh install with no real verifier registered cannot elevate at
# all — sensitive mutations stay locked until the install wires the
# real auth backend. That's safer than a permissive default.

CredentialVerifier = Callable[[Any, str, Any], bool]


def _deny_all_verifier(_user: Any, _method: str, _credential: Any) -> bool:
    return False


_verifier: CredentialVerifier = _deny_all_verifier


def register_credential_verifier(verifier: CredentialVerifier) -> None:
    """Register the real credential verifier at startup.

    The verifier signature is ``(user, method, credential) -> bool``.
    ``user`` is the Django ``User`` row attached to the request;
    ``method`` is one of the ``METHOD_*`` constants; ``credential``
    is the raw value the operator submitted (password string,
    WebAuthn assertion dict, OTP code, magic-link token).
    """
    global _verifier
    _verifier = verifier


def verify_credential(user: Any, method: str, credential: Any) -> bool:
    """Dispatch to the registered verifier. Falls back to deny."""
    if user is None or method not in KNOWN_METHODS:
        return False
    try:
        return bool(_verifier(user, method, credential))
    except Exception:  # noqa: BLE001 — never blow up a mutation on a verifier bug
        log.exception(
            "credential verifier raised for method=%r user=%r; denying",
            method,
            getattr(user, "pk", None),
        )
        return False


def reset_credential_verifier_for_tests() -> None:
    """Test-only: restore the deny-all default between tests."""
    global _verifier
    _verifier = _deny_all_verifier


# ---- default password verifier --------------------------------------
#
# Local-login installs (the ``auth1.local_login`` flow) store a
# Django-hashed password on the User row. ``check_password`` is the
# standard verifier. We register this at app-ready time so installs
# get a working password elevation out of the box; SSO-only installs
# replace it with their IdP-specific verifier.


def default_password_verifier(user: Any, method: str, credential: Any) -> bool:
    """Validate ``method == "password"`` against the User row's hashed password.

    Other methods fall through to deny — the install's app config
    layers webauthn / otp / magic-link verifiers on top of this one
    (see :func:`compose_verifiers`). A user with an unusable
    password (SSO-only account) cannot elevate via password; the FE
    should hide the password tab for those accounts.
    """
    if method != METHOD_PASSWORD:
        return False
    if user is None or not getattr(user, "is_active", False):
        return False
    if not isinstance(credential, str) or not credential:
        return False
    if not user.has_usable_password():
        return False
    return bool(user.check_password(credential))


def compose_verifiers(*verifiers: CredentialVerifier) -> CredentialVerifier:
    """Combine multiple per-method verifiers with first-true wins.

    Useful for installs that bolt a WebAuthn verifier alongside the
    default password one — register ``compose_verifiers(webauthn,
    default_password_verifier)`` and the verifier dispatches by
    method internally.
    """

    def _combined(user: Any, method: str, credential: Any) -> bool:
        for v in verifiers:
            try:
                if v(user, method, credential):
                    return True
            except Exception:  # noqa: BLE001
                log.exception("verifier %r raised; trying next", v)
        return False

    return _combined
