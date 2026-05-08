"""
GitHub App installation token minter.

Three-step exchange:

  1. Sign a short-lived JWT with the App's private key (RS256,
     ``iss=<app_id>``, ``iat=now``, ``exp=now+9min`` — GitHub allows
     up to 10 minutes; we leave a one-minute safety margin).
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
``SourceConnection.secret_ciphertext``. The connection's
``oauth_client_id`` field repurposed as ``app_id`` on
github_app_install rows — saving a column and keeping the SCM
data model uniform.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import time
import urllib.error
import urllib.request

import jwt

from core.secrets import EncryptedSecret, decrypt


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


def _mint_jwt(app_id: str, private_pem: bytes) -> str:
    now = int(time.time())
    payload = {
        "iat": now - 30,  # GitHub clock-skew tolerance is ±60s
        "exp": now + 9 * 60,
        "iss": str(app_id),
    }
    return jwt.encode(payload, private_pem, algorithm="RS256")


def _exchange_for_installation_token(
    api_base: str, jwt_token: str, installation_id: str
) -> tuple[str, float]:
    url = (
        f"{api_base}/app/installations/{installation_id}/access_tokens"
    )
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
        raise GithubAppError(
            "API_ERROR", f"GitHub returned {exc.code}: {body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise GithubAppError(
            "NETWORK", f"Couldn't reach GitHub: {exc.reason}"
        ) from exc

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

        expires_at = datetime.fromisoformat(
            expires_at_iso.replace("Z", "+00:00")
        ).timestamp()
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
            f"installation_token only works on github_app_install rows, "
            f"got {connection.kind!r}",
        )
    app_id = connection.oauth_client_id or ""
    installation_id = connection.installation_id or ""
    if not app_id or not installation_id:
        raise GithubAppError(
            "INCOMPLETE_CONFIG",
            "github_app_install connection needs both app_id "
            "(stored in oauth_client_id) and installation_id",
            recoverable=True,
        )

    cache_key = (str(connection.guid), installation_id)
    now = time.time()
    with _LOCK:
        cached = _CACHE.get(cache_key)
        if cached and cached.expires_at - _REFRESH_MARGIN_SECONDS > now:
            return cached.token

    private_pem = _decrypt_pem(connection)
    jwt_token = _mint_jwt(app_id, private_pem)
    token, expires_at = _exchange_for_installation_token(
        _api_base(connection), jwt_token, installation_id
    )

    with _LOCK:
        _CACHE[cache_key] = _CachedToken(token=token, expires_at=expires_at)
    return token
