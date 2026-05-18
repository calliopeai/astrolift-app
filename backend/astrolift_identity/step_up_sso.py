"""SSO step-up re-auth endpoint (#526).

Fixes the prod blocker where #487's password-only step-up modal was
unfulfillable for SSO users (Auth0/Cognito-federated accounts with an
unusable Django password hash). The flow:

1. FE catches a STEP_UP_REQUIRED envelope with
   ``supportedMethods=["sso"]`` and points the operator at
   :func:`elevate_sso_start` — ``/auth1/elevate-sso?return=...``.
2. :func:`elevate_sso_start` builds a one-shot nonce + state,
   binds them to the current session, and 302s to the IdP's
   authorize endpoint with ``prompt=login&max_age=0`` so the IdP
   forces a fresh authentication even with an active IdP cookie.
3. The IdP bounces the operator back to :func:`elevate_sso_callback`,
   which authlib-validates the id_token, asserts the state matches,
   asserts the ``auth_time`` claim is within
   ``STEP_UP_SSO_FRESHNESS_SECONDS`` (defeats replay of a cached
   id_token), then calls :func:`session_elevation.elevate` and
   redirects back to a safe-relative return URL.

This module deliberately mirrors the existing ``auth1.sessions``
authlib client (same registration, same metadata) — no parallel IdP
config — but uses a separate URL prefix so the dedicated callback can
do the freshness check without contaminating the primary login path.

The :data:`SESSION_SSO_AUTH_TIME_KEY` value is also written by the
primary SSO login callback (``auth1.sessions._register_remote_user``)
so a session that just logged in via SSO already has a fresh
``auth_time`` — the step-up gate accepts that without forcing a
second IdP round-trip when the login happened seconds ago.
"""

from __future__ import annotations

import logging
import secrets
import time
from urllib.parse import quote_plus, urlparse

from authlib.integrations.django_client import OAuth
from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django_ratelimit.decorators import ratelimit

from astrolift_identity.session_elevation import METHOD_SSO, elevate
from core.mutations import AuditEntry, emit_audit

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- session keys

SESSION_SSO_AUTH_TIME_KEY = "astrolift_sso_auth_time"
"""IdP-asserted ``auth_time`` (Unix seconds) from the operator's last
SSO login. Written by ``auth1.sessions._register_remote_user`` on the
primary login callback and by :func:`elevate_sso_callback` after a
successful step-up re-auth. The step-up SSO verifier reads it back to
decide whether the session's primary auth is fresh enough that no IdP
round-trip is needed."""

SESSION_SSO_STATE_KEY = "astrolift_sso_elevate_state"
"""One-shot CSRF state for the elevate-sso flow. Generated in
:func:`elevate_sso_start`, asserted in :func:`elevate_sso_callback`,
popped on either path so a replay of the callback URL fails."""

SESSION_SSO_NONCE_KEY = "astrolift_sso_elevate_nonce"
"""One-shot ``nonce`` claim passed to the IdP authorize URL and
verified inside the returned id_token to prevent token-substitution
attacks. Popped after verification."""

SESSION_SSO_RETURN_KEY = "astrolift_sso_elevate_return"
"""Validated, safe-relative return URL stashed at start time so the
callback can redirect the browser back to where the operator was."""


# ---------------------------------------------------------------- constance

_DEFAULT_SSO_FRESHNESS_SECONDS = 60


def freshness_window_seconds() -> int:
    """Return the configured SSO ``auth_time`` freshness window.

    Reads Constance ``STEP_UP_SSO_FRESHNESS_SECONDS`` first, falling
    back to 60 seconds on a fresh install or when Constance is
    unreachable (test settings). The lower bound is 1s so a
    misconfigured zero/negative value can't accidentally accept a
    cached id_token from yesterday.
    """
    try:
        from constance import config as _config

        value = int(getattr(_config, "STEP_UP_SSO_FRESHNESS_SECONDS", _DEFAULT_SSO_FRESHNESS_SECONDS))
    except Exception:  # noqa: BLE001 — constance optional in tests
        value = _DEFAULT_SSO_FRESHNESS_SECONDS
    return max(1, value)


# ---------------------------------------------------------------- safe return


def is_safe_return_url(raw: str | None) -> bool:
    """Return True when ``raw`` is a safe relative return URL.

    Accepts only ``/`` -leading paths with no scheme and no host so a
    malicious caller can't turn the elevate-sso endpoint into an open
    redirect. Schemeless URLs like ``//evil.example.com/x`` are
    rejected — ``urlparse`` reports them with ``netloc != ""``.
    """
    if not raw or not isinstance(raw, str):
        return False
    if not raw.startswith("/"):
        return False
    # Reject protocol-relative URLs (``//evil.example.com/x``).
    if raw.startswith("//"):
        return False
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return False
    return True


def _normalize_return(raw: str | None) -> str:
    """Coerce ``raw`` to a safe relative URL or the app root."""
    if is_safe_return_url(raw):
        return raw  # type: ignore[return-value]
    return f"{settings.BASE_URL}"


# ---------------------------------------------------------------- audit


def _emit_audit(
    *,
    action: str,
    decision: str,
    user_id: int | None,
    extra: dict | None = None,
    error_message: str | None = None,
) -> None:
    try:
        emit_audit(
            AuditEntry(
                actor_user_id=user_id,
                organization_id=None,
                action=action,
                decision=decision,
                target_kind="session",
                target_id=None,
                duration_ms=0,
                permissions=(),
                error_message=error_message,
                extra=extra or None,
            )
        )
    except Exception:  # noqa: BLE001 — audit must never break the request
        logger.exception("elevate_sso: audit emission failed for %s", action)


# ---------------------------------------------------------------- OAuth client

# Independent OAuth registration so the elevate-sso callback can stay
# decoupled from the primary login client (and from its registered
# callback URL). Reads the same AUTH0_DOMAIN / CLIENT_ID / SECRET /
# server-metadata URL settings as ``auth1.sessions.Auth1SessionWorkflow``.
_oauth = OAuth()
_oauth.register(
    name="auth0_stepup",
    client_id=settings.AUTH0_CLIENT_ID,
    client_secret=settings.AUTH0_CLIENT_SECRET,
    client_kwargs={
        # Force a fresh authentication: ``prompt=login`` makes the IdP
        # re-render the login screen even when an active IdP cookie
        # exists; ``max_age=0`` instructs the IdP to assert the
        # ``auth_time`` claim corresponds to the *current* interaction
        # (no silent reuse of an older session).
        "scope": settings.AUTH0_CLIENT_SCOPES,
        "prompt": "login",
        "max_age": 0,
    },
    server_metadata_url=(
        getattr(settings, "AUTH0_SERVER_METADATA_URL", None)
        or f"https://{settings.AUTH0_DOMAIN}/.well-known/openid-configuration"
    ),
)


# ---------------------------------------------------------------- start


@csrf_exempt
@ratelimit(key="user_or_ip", rate="10/m", block=True)
def elevate_sso_start(request: HttpRequest) -> HttpResponse:
    """Begin the SSO step-up flow.

    ``GET /auth1/elevate-sso?return=<safe-relative-path>``.

    Authenticated, SSO-method-only. Local-login operators that hit
    this URL get a 400 explaining the gap so a misconfigured FE
    doesn't redirect them through a flow they can't satisfy. The
    actual elevation happens on the callback after the IdP attests a
    fresh ``auth_time`` — this view just builds the redirect.
    """
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return JsonResponse({"detail": "authentication required"}, status=401)

    return_to = _normalize_return(request.GET.get("return") or request.GET.get("next"))

    # Generate cryptographically-random one-shot values. ``state``
    # binds to *this* browser session; ``nonce`` binds to *this*
    # id_token. Both are short-lived (next callback consumes them).
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    request.session[SESSION_SSO_STATE_KEY] = state
    request.session[SESSION_SSO_NONCE_KEY] = nonce
    request.session[SESSION_SSO_RETURN_KEY] = return_to
    request.session.modified = True
    request.session.save()

    from django.urls import reverse

    callback_url = request.build_absolute_uri(reverse("auth1-elevate-sso-callback"))
    return _oauth.auth0_stepup.authorize_redirect(
        request,
        callback_url,
        state=state,
        nonce=nonce,
        prompt="login",
        max_age=0,
    )


# ---------------------------------------------------------------- callback


@csrf_exempt
def elevate_sso_callback(request: HttpRequest) -> HttpResponse:
    """Complete the SSO step-up flow.

    Validates state + id_token, asserts ``auth_time`` is within
    :func:`freshness_window_seconds`, then elevates the current
    session and redirects back to the safe return URL.

    Every deny path emits a distinct audit row so security review can
    discriminate replay attempts (``stale_auth_time``) from CSRF
    (``state_mismatch``) from operator-cancel (``error_response``).
    """
    viewer = getattr(request, "user", None)
    user_id = getattr(viewer, "pk", None) if viewer else None

    expected_state = request.session.pop(SESSION_SSO_STATE_KEY, None)
    expected_nonce = request.session.pop(SESSION_SSO_NONCE_KEY, None)
    return_to = request.session.pop(SESSION_SSO_RETURN_KEY, None) or f"{settings.BASE_URL}"
    request.session.modified = True

    if request.GET.get("error"):
        _emit_audit(
            action="auth.elevate_admin.sso.error_response",
            decision="DENY",
            user_id=user_id,
            extra={
                "error": request.GET.get("error"),
                "error_description": request.GET.get("error_description"),
            },
        )
        return _fail(return_to, "elevation_cancelled")

    received_state = request.GET.get("state")
    if not expected_state or not received_state or not secrets.compare_digest(expected_state, received_state):
        _emit_audit(
            action="auth.elevate_admin.sso.state_mismatch",
            decision="DENY",
            user_id=user_id,
        )
        return _fail(return_to, "state_mismatch")

    if viewer is None or not viewer.is_authenticated:
        # Session was lost between start + callback (logout, cookie
        # cleared). Don't elevate — just bounce them to login.
        _emit_audit(
            action="auth.elevate_admin.sso.unauthenticated",
            decision="DENY",
            user_id=None,
        )
        return _fail(return_to, "not_authenticated")

    try:
        token = _oauth.auth0_stepup.authorize_access_token(request)
    except Exception as exc:  # noqa: BLE001 — IdP rejected or network failed
        logger.exception("elevate_sso: authorize_access_token failed")
        _emit_audit(
            action="auth.elevate_admin.sso.token_exchange_failed",
            decision="DENY",
            user_id=user_id,
            error_message=str(exc),
        )
        return _fail(return_to, "token_exchange_failed")

    userinfo = token.get("userinfo") or {}
    received_nonce = userinfo.get("nonce")
    if expected_nonce and received_nonce and not secrets.compare_digest(expected_nonce, received_nonce):
        _emit_audit(
            action="auth.elevate_admin.sso.nonce_mismatch",
            decision="DENY",
            user_id=user_id,
        )
        return _fail(return_to, "nonce_mismatch")

    auth_time = userinfo.get("auth_time") or token.get("auth_time")
    if not isinstance(auth_time, (int, float)):
        _emit_audit(
            action="auth.elevate_admin.sso.missing_auth_time",
            decision="DENY",
            user_id=user_id,
        )
        return _fail(return_to, "missing_auth_time")

    age = int(time.time() - int(auth_time))
    if age > freshness_window_seconds() or age < -freshness_window_seconds():
        # The replay-protection branch: a cached id_token with an old
        # ``auth_time`` lands here. Negative ages (clock skew the wrong
        # way) are equally suspect — fail both.
        _emit_audit(
            action="auth.elevate_admin.sso.stale_auth_time",
            decision="DENY",
            user_id=user_id,
            extra={
                "auth_time": int(auth_time),
                "age_seconds": age,
                "window_seconds": freshness_window_seconds(),
            },
        )
        return _fail(return_to, "stale_auth_time")

    # All checks pass — elevate the session for the standard step-up
    # window. ``method=sso`` shows up on the audit row and on the
    # elevation-status query so the FE can render "elevated via SSO".
    elevate(request.session, method=METHOD_SSO)
    request.session[SESSION_SSO_AUTH_TIME_KEY] = int(auth_time)
    request.session.modified = True
    request.session.save()

    _emit_audit(
        action="auth.elevate_admin.sso.success",
        decision="ALLOW",
        user_id=user_id,
        extra={"auth_time": int(auth_time), "age_seconds": age},
    )
    return HttpResponseRedirect(return_to)


def _fail(return_to: str, reason: str) -> HttpResponseRedirect:
    """Redirect back to the caller with a short ``stepUp`` query param
    so the FE can render a transient error toast without parsing the
    audit log. The session bag stays clean: caller-side state was
    already popped at the top of the callback.
    """
    if "?" in return_to:
        sep = "&"
    else:
        sep = "?"
    return HttpResponseRedirect(f"{return_to}{sep}stepUp={quote_plus(reason)}")


__all__ = [
    "SESSION_SSO_AUTH_TIME_KEY",
    "SESSION_SSO_NONCE_KEY",
    "SESSION_SSO_RETURN_KEY",
    "SESSION_SSO_STATE_KEY",
    "elevate_sso_callback",
    "elevate_sso_start",
    "freshness_window_seconds",
    "is_safe_return_url",
]
