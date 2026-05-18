"""
GitHub App installation token minter.

Three-step exchange:

  1. Sign a short-lived JWT with the App's private key (RS256,
     ``iss=<client_id>``, ``iat=now``, ``exp=now+9min`` — GitHub
     allows up to 10 minutes; we leave a one-minute safety margin).
  2. POST the JWT to
     ``/app/installations/<installation_id>/access_tokens``.
  3. GitHub returns an installation access token that lasts ~1 hour
     and the same scopes the App was registered with.

We cache the installation token in-process keyed by
``(connection.guid, installation_id)`` until ~5 minutes before
expiry. A multi-process deployment that needs cross-process sharing
swaps this for Redis at the cache layer (the rest of the call shape
stays identical).

The App's private-key PEM lives encrypted in
``SourceConnection.secret_ciphertext``. The connection carries two
GitHub-side identifiers:

  - ``app_client_id`` (e.g. ``Iv23lic8662KXwe4XKEI``) — the OAuth
    Client ID. GitHub now recommends this for the JWT ``iss`` claim
    too; see the GitHub docs on "Generating a JSON web token (JWT) for
    a GitHub App". Required for the user-to-server OAuth dance.
  - ``oauth_client_id`` — historically dual-purposed; on
    ``github_app_install`` rows it carries the numeric **App ID**
    (e.g. ``3705068``) which is what GitHub embeds in webhook payloads
    and what the pre-Client-ID JWT format required. We keep using it
    as a fallback for the JWT ``iss`` when ``app_client_id`` is empty
    (legacy connections that pre-date #525).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
import time
import urllib.error
import urllib.request

import jwt

from core.secrets import EncryptedSecret, decrypt

logger = logging.getLogger(__name__)

GITHUB_API_DEFAULT = "https://api.github.com"


class GithubAppError(Exception):
    def __init__(self, code: str, message: str, *, recoverable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.recoverable = recoverable


@dataclasses.dataclass(slots=True)
class _CachedToken:
    token: str
    expires_at: float


_CACHE: dict[tuple[str, str], _CachedToken] = {}
_LOCK = threading.Lock()
_REFRESH_MARGIN_SECONDS = 300  # refresh 5 minutes before GitHub-stated expiry.


def _api_base(connection) -> str:
    return (connection.api_base_url or GITHUB_API_DEFAULT).rstrip("/")


def _decrypt_pem(connection) -> bytes:
    return decrypt(
        EncryptedSecret(
            backend_kind=connection.secret_backend_kind,
            backend_ref=bytes(connection.secret_ciphertext),
        )
    )


def _mint_jwt(issuer: str, private_pem: bytes) -> str:
    """Sign a short-lived RS256 JWT for the GitHub App.

    ``issuer`` is the value to use as the JWT ``iss`` claim. GitHub
    now recommends the App **Client ID** (string, e.g.
    ``Iv23lic8662KXwe4XKEI``); the legacy numeric **App ID** still
    works for Apps registered before the Client-ID rollout. The
    caller is responsible for picking the right value — this helper
    just signs whatever it's handed."""
    now = int(time.time())
    payload = {
        "iat": now - 30,  # GitHub clock-skew tolerance is ±60s
        "exp": now + 9 * 60,
        "iss": str(issuer),
    }
    return jwt.encode(payload, private_pem, algorithm="RS256")


def _exchange_for_installation_token(
    api_base: str, jwt_token: str, installation_id: str
) -> tuple[str, float]:
    url = f"{api_base}/app/installations/{installation_id}/access_tokens"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        if exc.code in (401, 403, 404):
            raise GithubAppError(
                "AUTH_FAILED",
                f"GitHub rejected the App credentials ({exc.code}): {body}",
                recoverable=True,
            ) from exc
        raise GithubAppError("API_ERROR", f"GitHub returned {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise GithubAppError("NETWORK", f"Couldn't reach GitHub: {exc.reason}") from exc

    token = payload.get("token") or ""
    expires_at_iso = payload.get("expires_at") or ""
    if not token:
        raise GithubAppError(
            "UNEXPECTED_SHAPE",
            "GitHub didn't return an installation access token",
        )

    # GitHub returns ISO 8601; convert to a unix epoch we can compare.
    # We don't need millisecond precision — the cache-margin already
    # buffers us against race conditions.
    try:
        from datetime import datetime

        expires_at = datetime.fromisoformat(expires_at_iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        expires_at = time.time() + 50 * 60  # conservative ~50min

    return token, expires_at


def installation_token(connection) -> str:
    """Return a fresh-ish installation access token for the given
    github_app_install connection.

    Cached in-process; refreshed automatically when within
    ``_REFRESH_MARGIN_SECONDS`` of expiry."""
    if connection.kind != "github_app_install":
        raise GithubAppError(
            "WRONG_KIND",
            f"installation_token only works on github_app_install rows, got {connection.kind!r}",
        )
    # Prefer the App Client ID for the JWT ``iss`` claim (post-#525,
    # post-GitHub-recommendation). Fall back to the numeric App ID
    # stored in ``oauth_client_id`` for connections that pre-date the
    # split — GitHub still accepts it but we log a one-line deprecation
    # warning so operators can be nudged via observability.
    issuer = connection.app_client_id or ""
    if not issuer:
        legacy_app_id = connection.oauth_client_id or ""
        if legacy_app_id:
            logger.warning(
                "github_app_install connection %s is using numeric App ID "
                "for the JWT iss claim (deprecated); set app_client_id to "
                "the GitHub App Client ID to silence this warning",
                connection.guid,
            )
            issuer = legacy_app_id
    installation_id = connection.installation_id or ""
    if not issuer or not installation_id:
        raise GithubAppError(
            "INCOMPLETE_CONFIG",
            "github_app_install connection needs both app_client_id "
            "(or legacy oauth_client_id App ID) and installation_id",
            recoverable=True,
        )

    cache_key = (str(connection.guid), installation_id)
    now = time.time()
    with _LOCK:
        cached = _CACHE.get(cache_key)
        if cached and cached.expires_at - _REFRESH_MARGIN_SECONDS > now:
            return cached.token

    private_pem = _decrypt_pem(connection)
    jwt_token = _mint_jwt(issuer, private_pem)
    token, expires_at = _exchange_for_installation_token(_api_base(connection), jwt_token, installation_id)

    with _LOCK:
        _CACHE[cache_key] = _CachedToken(token=token, expires_at=expires_at)
    return token
