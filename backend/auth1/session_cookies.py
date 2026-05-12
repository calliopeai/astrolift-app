r"""
Session cookie policy (#272, spec 12 §2.2 + §2.4).

Pure-Python policy. The auth views and IdP logout handler consult
this module for:

* **Cookie attribute construction** — Secure, HttpOnly, SameSite,
  Domain scoping. Single-source-of-truth so the access-cookie,
  refresh-cookie, and logout-clear all stay consistent.
* **Domain scoping** — picks up the install's parent domain from
  the well-known JSON; refuses to set cookies broader than the
  install (so a cookie minted on \`acme.platform.example\` doesn't
  leak to \`globex.platform.example\`).
* **Sign-out cascade rules** — local-only vs RP-initiated OIDC
  logout, what to revoke when, and which audit reason to record.

Pairs with #147's session_tokens.py (the issuance/rotation policy).
"""

from __future__ import annotations

import dataclasses
import re
from enum import Enum


class CookieError(ValueError):
    pass


# ---- cookie attribute construction ---------------------------------


class CookieKind(str, Enum):
    """Two cookies per session — one short-lived (access), one
    long-lived (refresh). Different paths so the refresh cookie
    only ships to the refresh endpoint, narrowing exposure."""

    ACCESS = "access"
    """Short-lived. Path=/, lives for access_ttl."""

    REFRESH = "refresh"
    """Long-lived. Path=/api/v1/auth/refresh so it only ships
    to that endpoint, not every API request."""


# Per-cookie path scoping. Refresh cookie is path-scoped so it
# isn't exposed on every API call.
_COOKIE_PATHS = {
    CookieKind.ACCESS: "/",
    CookieKind.REFRESH: "/api/v1/auth/refresh",
}


@dataclasses.dataclass(frozen=True, slots=True)
class CookieAttributes:
    """The set of attributes the view layer applies to a Set-Cookie
    response header."""

    name: str
    value: str
    """The token plaintext (access JWT or refresh secret).
    Caller never logs this — same hash-at-rest invariant as
    deploy tokens (#147)."""

    domain: str
    path: str
    secure: bool
    """Always True in production. Tests set False explicitly."""

    http_only: bool
    """Always True. JS shouldn't see session cookies."""

    same_site: str
    """\"Lax\" by default. Strict breaks legitimate top-level
    cross-site flows (e.g. clicking a magic link from email)."""

    max_age_seconds: int


def access_cookie_name(*, install_slug: str = "") -> str:
    """Spec §2.2: cookies named per install so multiple installs
    on overlapping zones don't shadow each other."""
    if install_slug:
        return f"alft_{install_slug}_access"
    return "alft_access"


def refresh_cookie_name(*, install_slug: str = "") -> str:
    if install_slug:
        return f"alft_{install_slug}_refresh"
    return "alft_refresh"


_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def parent_domain(*, install_zone: str) -> str:
    """Spec §2.2: cookie Domain= is the install's parent domain
    so subdomain apps on the install share the cookie. For
    install zone ``acme.platform.example``, parent is
    ``acme.platform.example`` itself (cookies set there are
    sent to ``app.acme.platform.example`` etc).

    REFUSES to widen beyond a 2-label public suffix — setting
    Domain=.com would break universally + cookies wouldn't
    actually be accepted by browsers, but we want the failure
    to be loud rather than silent."""
    zone = install_zone.strip(".").lower()
    if not zone:
        raise CookieError("install_zone is required")

    parts = zone.split(".")
    if len(parts) < 2:
        raise CookieError(
            f"install_zone {install_zone!r} too short for cookie "
            "domain — at least 2 labels required"
        )
    for part in parts:
        if not _DOMAIN_LABEL_RE.match(part):
            raise CookieError(
                f"install_zone {install_zone!r} has invalid label "
                f"{part!r}"
            )
    return zone


def build_cookie_attributes(
    *,
    kind: CookieKind,
    token_value: str,
    install_zone: str,
    install_slug: str = "",
    ttl_seconds: int,
    secure: bool = True,
) -> CookieAttributes:
    """Build the cookie attributes for one of the two session
    cookies. Caller passes the resulting struct into Django's
    response.set_cookie / Starlette equivalent."""
    if not token_value:
        raise CookieError(
            f"token_value required for {kind.value} cookie"
        )
    if ttl_seconds <= 0:
        raise CookieError(
            f"{kind.value} cookie ttl must be positive, "
            f"got {ttl_seconds}"
        )

    name = (
        access_cookie_name(install_slug=install_slug)
        if kind == CookieKind.ACCESS
        else refresh_cookie_name(install_slug=install_slug)
    )

    return CookieAttributes(
        name=name,
        value=token_value,
        domain=parent_domain(install_zone=install_zone),
        path=_COOKIE_PATHS[kind],
        secure=secure,
        http_only=True,
        same_site="Lax",
        max_age_seconds=ttl_seconds,
    )


def build_clear_cookie(
    *,
    kind: CookieKind,
    install_zone: str,
    install_slug: str = "",
) -> CookieAttributes:
    """Logout: zero-value, max_age=0 cookie that browsers will
    drop. Same Domain + Path as the original; differing values
    cause some browsers to keep the original."""
    name = (
        access_cookie_name(install_slug=install_slug)
        if kind == CookieKind.ACCESS
        else refresh_cookie_name(install_slug=install_slug)
    )
    return CookieAttributes(
        name=name,
        value="",
        domain=parent_domain(install_zone=install_zone),
        path=_COOKIE_PATHS[kind],
        secure=True,
        http_only=True,
        same_site="Lax",
        max_age_seconds=0,
    )


# ---- sign-out cascade ----------------------------------------------


class SignoutTrigger(str, Enum):
    """What kicked off the sign-out. Different triggers need
    different cleanup."""

    USER_INITIATED = "user_initiated"
    """User clicked Sign Out. Local revoke + clear cookies +
    optional RP-initiated OIDC redirect."""

    LOGOUT_ALL = "logout_all"
    """User clicked 'Sign out all devices' — revoke all
    sessions for the user."""

    BACK_CHANNEL_OIDC = "back_channel_oidc"
    """IdP notified us via back-channel logout. We revoke
    session(s) bound to that IdP sid."""

    REPLAY_DETECTED = "replay_detected"
    """Refresh token replay → revoke the entire chain (#147's
    revoke_chain)."""

    SESSION_EXPIRED = "session_expired"
    """Token rotation hit max_age; session naturally expires."""


@dataclasses.dataclass(frozen=True, slots=True)
class SignoutPlan:
    """What the view layer should do for one sign-out."""

    revoke_local_session: bool
    revoke_all_user_sessions: bool
    """For LOGOUT_ALL trigger only."""

    revoke_chain: bool
    """For REPLAY_DETECTED — revoke parent + descendants per
    #147."""

    redirect_to_idp_logout: bool
    """For OIDC-backed sessions when user-initiated; redirects
    to IdP's logout endpoint with post_logout_redirect_uri."""

    audit_reason: str
    """Recorded on the session row's revoke_reason field +
    emitted as audit event."""


def plan_signout(
    *,
    trigger: SignoutTrigger,
    is_oidc_session: bool,
) -> SignoutPlan:
    """Decide cleanup actions for the sign-out trigger."""
    if trigger == SignoutTrigger.USER_INITIATED:
        return SignoutPlan(
            revoke_local_session=True,
            revoke_all_user_sessions=False,
            revoke_chain=False,
            redirect_to_idp_logout=is_oidc_session,
            audit_reason="user signed out",
        )
    if trigger == SignoutTrigger.LOGOUT_ALL:
        return SignoutPlan(
            revoke_local_session=True,
            revoke_all_user_sessions=True,
            revoke_chain=False,
            redirect_to_idp_logout=False,
            audit_reason="user signed out all devices",
        )
    if trigger == SignoutTrigger.BACK_CHANNEL_OIDC:
        return SignoutPlan(
            revoke_local_session=True,
            revoke_all_user_sessions=False,
            revoke_chain=False,
            redirect_to_idp_logout=False,
            audit_reason="IdP back-channel logout",
        )
    if trigger == SignoutTrigger.REPLAY_DETECTED:
        return SignoutPlan(
            revoke_local_session=True,
            revoke_all_user_sessions=False,
            revoke_chain=True,
            redirect_to_idp_logout=False,
            audit_reason="refresh token replay",
        )
    if trigger == SignoutTrigger.SESSION_EXPIRED:
        return SignoutPlan(
            revoke_local_session=True,
            revoke_all_user_sessions=False,
            revoke_chain=False,
            redirect_to_idp_logout=False,
            audit_reason="session expired",
        )
    raise CookieError(f"unknown signout trigger {trigger!r}")
