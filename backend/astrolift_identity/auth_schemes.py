"""
Control API authentication scheme classifier (#89, spec 27 §9).

Pure-Python module. The auth middleware reads request headers
(or the session cookie + CSRF check) and asks this module 'which
scheme is this and is the credential well-formed?'. The actual
verification (DB lookup, OIDC exchange, mTLS cert chain) lives
in scheme-specific verifiers; this module is the dispatcher.

Six schemes per spec 27 §9:

* Session cookie — UI / browser. ``astrolift_session`` HttpOnly +
  CSRF token in form/header.
* Bearer (API token) — CLI / scripts.
* Bearer (Deploy token) — CI runners; app-scoped.
* Bearer (OIDC token) — federated CI; verified via
  ``auth.exchange_oidc``.
* mTLS — internal services; client cert pinned to service identity.
* Bearer (SCIM token) — only on /api/scim/v2/* endpoints.

The classifier picks one scheme per request based on headers +
the request path. Multi-scheme requests (cookie + Bearer) are
rejected — pick one.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from enum import Enum


class AuthScheme(str, Enum):
    SESSION_COOKIE = "session_cookie"
    API_TOKEN = "api_token"
    DEPLOY_TOKEN = "deploy_token"
    OIDC = "oidc"
    MTLS = "mtls"
    SCIM = "scim"


# Token prefix conventions. Pairs with #143 deploy_token (alft_dt_)
# and the broader API token scheme. Lets the classifier disambiguate
# bearer types without a DB lookup.
_TOKEN_PREFIXES: dict[str, AuthScheme] = {
    "alft_at_": AuthScheme.API_TOKEN,
    "alft_dt_": AuthScheme.DEPLOY_TOKEN,
    "alft_st_": AuthScheme.SCIM,
}


# JWT structural detection: three base64url segments separated by
# dots. OIDC tokens are JWTs; our local API/deploy/SCIM tokens are
# opaque-string-with-prefix and never JWT-shaped.
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


# SCIM endpoints have a fixed prefix; SCIM tokens are only valid
# there.
_SCIM_PATH_PREFIX = "/api/scim/v2/"


class AuthSchemeError(ValueError):
    pass


@dataclasses.dataclass(frozen=True, slots=True)
class ClassifiedAuth:
    """Output of the classifier."""

    scheme: AuthScheme
    """Which scheme the request used."""

    raw_credential: str
    """Bearer token / cookie value / cert fingerprint. Caller
    hands this to the scheme-specific verifier. NEVER logged."""

    is_browser_request: bool = False
    """True for cookie-auth; the middleware then enforces CSRF."""


def classify(
    *,
    path: str,
    headers: Mapping[str, str],
    cookies: Mapping[str, str] | None = None,
    client_cert_fingerprint: str = "",
) -> ClassifiedAuth:
    """Pick the auth scheme. Single-scheme rule: a request must
    present exactly one credential type. Cookie + Bearer is
    ambiguous (CSRF risk if we tolerate it) and rejects.

    Order of disambiguation:
      1. mTLS cert (presented by the TLS terminator out-of-band
         from headers).
      2. Bearer in Authorization header — JWT-shape → OIDC; else
         prefix-classified token.
      3. Session cookie.
      4. None → unauthenticated.
    """
    has_bearer = bool(_get_bearer_token(headers))
    has_cookie = bool(cookies and cookies.get("astrolift_session"))
    has_mtls = bool(client_cert_fingerprint)

    presented = sum([has_bearer, has_cookie, has_mtls])
    if presented > 1:
        raise AuthSchemeError("multiple credentials presented; submit exactly one")

    if has_mtls:
        return ClassifiedAuth(
            scheme=AuthScheme.MTLS,
            raw_credential=client_cert_fingerprint,
        )

    if has_bearer:
        token = _get_bearer_token(headers)
        scheme = _classify_bearer(token=token, path=path)
        return ClassifiedAuth(scheme=scheme, raw_credential=token)

    if has_cookie:
        return ClassifiedAuth(
            scheme=AuthScheme.SESSION_COOKIE,
            raw_credential=cookies["astrolift_session"],
            is_browser_request=True,
        )

    raise AuthSchemeError("no credential presented")


def _get_bearer_token(headers: Mapping[str, str]) -> str:
    """Case-insensitive Authorization lookup. Returns the token
    bytes after 'Bearer '. Empty string when the header isn't
    present or doesn't start with 'Bearer '."""
    for k, v in headers.items():
        if k.lower() == "authorization":
            if v.lower().startswith("bearer "):
                return v[7:].strip()
    return ""


def _classify_bearer(*, token: str, path: str) -> AuthScheme:
    """Disambiguate bearer types.

    JWT-shape (three b64url segments) → OIDC. Astrolift-issued
    tokens use opaque prefixes and never look like JWTs.

    Path scoping: SCIM tokens only valid at /api/scim/v2/*; an
    SCIM token presented at a non-SCIM endpoint rejects.
    """
    if _JWT_RE.match(token):
        return AuthScheme.OIDC

    for prefix, scheme in _TOKEN_PREFIXES.items():
        if token.startswith(prefix):
            if scheme == AuthScheme.SCIM and not path.startswith(_SCIM_PATH_PREFIX):
                raise AuthSchemeError("SCIM token presented at non-SCIM endpoint")
            if scheme != AuthScheme.SCIM and path.startswith(_SCIM_PATH_PREFIX):
                raise AuthSchemeError("non-SCIM token presented at SCIM endpoint")
            return scheme

    raise AuthSchemeError("token format not recognized")


# ---- token introspection contract ----------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class TokenIntrospection:
    """Output of ``auth.introspect`` per spec 27 §4.1.

    Returned to operators / admins (elevated) when investigating
    a token's origin. Never includes the raw token value or any
    derivable secret material.
    """

    active: bool
    scheme: str
    principal: str  # 'user:42' / 'app:7' / 'service:workers'
    scopes: tuple[str, ...]
    """e.g. ('app.deploy', 'app.read'). Empty for OIDC tokens
    where scope comes from the upstream IdP claim."""

    constraints: Mapping[str, str] = dataclasses.field(default_factory=dict)
    """Optional bindings: 'app_id': '42', 'env': 'prod',
    'ip_allowlist': '203.0.113.0/24'. Operator UI surfaces these."""

    expires_at: str = ""  # ISO-8601 UTC; empty for non-expiring tokens
    issued_at: str = ""


def is_introspection_authorized(
    *,
    requester_amr: tuple[str, ...],
    requester_is_admin: bool,
) -> bool:
    """Per spec 27 §4.1: introspection requires admin elevation
    OR strong MFA on the requester's session. We check both
    conditions; either suffices.

    The point: introspection reveals which app/user a token
    belongs to and what it can do — sensitive enough to require
    elevated context.
    """
    if requester_is_admin:
        return True
    return any(amr in {"otp", "webauthn", "hwk"} for amr in requester_amr)
