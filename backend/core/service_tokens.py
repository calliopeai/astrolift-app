"""
Platform service tokens: HMAC-signed bearer JWT for
service-to-service auth (#149, spec 12 §3.4).

Used by platform components (workers, build agents, the
status-page reader) to call each other's APIs. Not user-associated;
each token carries:

  - ``sub``: the service identity (e.g. "workers", "build-agent")
  - ``aud``: the intended audience (e.g. "graphql", "events")
    — recipients reject tokens with the wrong audience so a
    leaked workers→graphql token can't talk to events
  - ``exp``: hard expiry (default 1 hour; rotation by re-issuing
    well before this)
  - ``jti``: random token id for revocation lookup

We sign with HMAC-SHA256 because every component already shares
``settings.SECRET_KEY``; switching to asymmetric (RS256/Ed25519)
would mean a key-distribution story we don't have a use-case for
yet. The HMAC envelope is base64url-encoded so it's a drop-in
``Authorization: Bearer …`` value — no JWS library dependency.

Pure module. The Django integration (issue endpoint, middleware
that consumes incoming tokens, revocation table) lives separately;
this module is the crypto + claims policy layer that those wire up.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone


# Spec 12 §3.4: short-lived. 1-hour default works for sync workloads
# and keeps the blast radius of a leaked token small.
DEFAULT_TTL_SECONDS = 60 * 60


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _now_unix(now: datetime) -> int:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return int(now.timestamp())


@dataclasses.dataclass(frozen=True, slots=True)
class ServiceTokenClaims:
    """Decoded claims after verification. The dataclass mirrors the
    JWT payload but with parsed Python types."""

    sub: str   # service identity
    aud: str   # audience this token is intended for
    iss: str   # issuer (the install hostname)
    exp: int   # unix timestamp, seconds
    iat: int
    jti: str

    def is_expired(self, *, now: datetime) -> bool:
        return _now_unix(now) >= self.exp


class ServiceTokenInvalid(Exception):
    """Raised when verification fails. The single-message-per-failure
    shape (vs. multiple exception types) is intentional — recipients
    should always respond identically (401, no diagnostic) regardless
    of *which* check failed, so an attacker can't probe for
    'signature wrong' vs. 'audience wrong' timing differences."""


def issue(
    *,
    sub: str,
    aud: str,
    issuer: str,
    secret: bytes,
    now: datetime,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    """Issue a fresh service token. Returns the bearer string."""
    if not sub:
        raise ValueError("sub (service identity) is required")
    if not aud:
        raise ValueError("aud (audience) is required")
    if not issuer:
        raise ValueError("issuer is required")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")

    iat = _now_unix(now)
    payload = {
        "sub": sub,
        "aud": aud,
        "iss": issuer,
        "iat": iat,
        "exp": iat + ttl_seconds,
        "jti": secrets.token_urlsafe(16),
    }
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url(_canonical_json(header))
    payload_b64 = _b64url(_canonical_json(payload))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(secret, signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url(sig)}"


def verify(
    token: str,
    *,
    expected_audience: str,
    expected_issuer: str,
    secret: bytes,
    now: datetime,
) -> ServiceTokenClaims:
    """Validate a presented bearer token; return its claims.

    Failures uniformly raise :class:`ServiceTokenInvalid`. Order of
    checks is structured first (parseable + signed) before semantic
    (audience/issuer/exp) so a malformed token can't time-leak which
    audience we expect.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise ServiceTokenInvalid("token shape")

    header_b64, payload_b64, sig_b64 = parts

    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
        sig = _b64url_decode(sig_b64)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ServiceTokenInvalid("token decode") from exc

    if header.get("alg") != "HS256":
        # 'none' / 'RS256' confusion attacks: refuse anything but our
        # one supported algorithm.
        raise ServiceTokenInvalid("alg")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(secret, signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig):
        raise ServiceTokenInvalid("sig")

    try:
        claims = ServiceTokenClaims(
            sub=str(payload["sub"]),
            aud=str(payload["aud"]),
            iss=str(payload["iss"]),
            exp=int(payload["exp"]),
            iat=int(payload["iat"]),
            jti=str(payload["jti"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceTokenInvalid("claims") from exc

    if claims.aud != expected_audience:
        raise ServiceTokenInvalid("aud")
    if claims.iss != expected_issuer:
        raise ServiceTokenInvalid("iss")
    if claims.is_expired(now=now):
        raise ServiceTokenInvalid("exp")

    return claims


def _canonical_json(obj: dict) -> bytes:
    """Sorted-keys, no-whitespace JSON. Required so re-encoding for
    debugging or logging produces the same bytes the signature was
    computed over."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def needs_rotation(claims: ServiceTokenClaims, *, now: datetime) -> bool:
    """Heuristic for the issuing service: rotate when ≤25% of the
    token's lifetime remains. Caller wires this into a background
    refresh — e.g. each worker checks on every heartbeat and re-issues
    via the issuance endpoint when needed."""
    now_u = _now_unix(now)
    total = claims.exp - claims.iat
    if total <= 0:
        return True
    remaining = claims.exp - now_u
    return remaining <= total // 4
