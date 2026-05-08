"""
GitHub repo listing through a stored connection.

Auth modes covered:
- ``github_pat``: Authorization: token <pat>
- ``github_app_install``: not yet (phase 2b — needs JWT minting from
  the App private key + per-installation token exchange)

Visibility scopes from ``SourceConnection.repo_visibility_scopes``
are applied client-side to the API response. We could push some
filtering into the API call itself (the ``type`` parameter on
``/user/repos``), but the API doesn't have a single mode that
matches our four-axis scope model exactly, so a clean post-filter
is more honest than juggling combinations.
"""

from __future__ import annotations

import dataclasses
import json
import urllib.parse
import urllib.request
from typing import Iterable

from core.secrets import EncryptedSecret, decrypt


GITHUB_API_DEFAULT = "https://api.github.com"


@dataclasses.dataclass(frozen=True, slots=True)
class GithubRepo:
    full_name: str
    name: str
    description: str
    default_branch: str
    visibility: str
    clone_url_https: str
    clone_url_ssh: str
    web_url: str
    is_archived: bool
    is_fork: bool
    pushed_at: str | None
    owner_login: str
    owner_type: str  # "User" or "Organization"


class GithubProviderError(Exception):
    def __init__(self, code: str, message: str, *, recoverable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.recoverable = recoverable


def _api_base(connection) -> str:
    return (connection.api_base_url or GITHUB_API_DEFAULT).rstrip("/")


def _token(connection) -> str:
    if connection.kind in {"github_pat", "github_oauth_user"}:
        plaintext = decrypt(
            EncryptedSecret(
                backend_kind=connection.secret_backend_kind,
                backend_ref=bytes(connection.secret_ciphertext),
            )
        )
        return plaintext.decode("utf-8")
    if connection.kind == "github_app_install":
        raise GithubProviderError(
            "UNSUPPORTED_AUTH",
            "github_app_install token minting lands in phase 2b",
        )
    if connection.kind == "github_oauth_app":
        raise GithubProviderError(
            "OAUTH_NOT_CONNECTED",
            "this is the OAuth app config row; complete the OAuth dance "
            "to create a token connection first",
            recoverable=True,
        )
    raise GithubProviderError(
        "UNSUPPORTED_AUTH",
        f"github driver doesn't know how to auth {connection.kind!r}",
    )


def list_github_repos(
    connection,
    *,
    search: str | None = None,
    limit: int = 100,
) -> list:
    """Call GitHub's repo listing API and apply the connection's
    visibility-scope filter to the result."""
    from astrolift_scm.providers import RemoteRepo

    token = _token(connection)
    base = _api_base(connection)

    # /user/repos returns repos the token can see (private + public,
    # owned + collaborator + org). We do per_page=100, single page;
    # paginating the long tail lands in a follow-up when we need it.
    qs = urllib.parse.urlencode(
        {
            "per_page": str(min(max(limit, 1), 100)),
            "sort": "pushed",
            "direction": "desc",
        }
    )
    url = f"{base}/user/repos?{qs}"

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"token {token}",
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
        if exc.code in (401, 403):
            raise GithubProviderError(
                "AUTH_FAILED",
                f"GitHub rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        raise GithubProviderError(
            "API_ERROR",
            f"GitHub returned {exc.code}: {body}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GithubProviderError(
            "NETWORK", f"Couldn't reach GitHub: {exc.reason}"
        ) from exc

    if not isinstance(payload, list):
        raise GithubProviderError(
            "UNEXPECTED_SHAPE", "GitHub returned a non-list payload"
        )

    org_login = (connection.account_login or "").lower()
    scopes = set(connection.repo_visibility_scopes or [])

    out: list[RemoteRepo] = []
    for raw in payload:
        owner = raw.get("owner") or {}
        owner_login = (owner.get("login") or "").lower()
        owner_type = owner.get("type") or "User"
        is_private = bool(raw.get("private"))
        full_name = raw.get("full_name", "")

        # Apply visibility-scope filter when scopes is non-empty.
        if scopes:
            in_org = bool(org_login) and owner_login == org_login
            keep = (
                ("private_org" in scopes and in_org and is_private)
                or ("public_org" in scopes and in_org and not is_private)
                or ("user_repos" in scopes and owner_type == "User")
                or (
                    "public_non_org" in scopes
                    and not in_org
                    and not is_private
                )
            )
            if not keep:
                continue

        # Search filter is a substring match on the full_name. Cheap
        # and correct for the picker UX; phase 2b can swap to GitHub's
        # /search/repositories endpoint when we want server-side hits.
        if search and search.lower() not in full_name.lower():
            continue

        out.append(
            RemoteRepo(
                full_name=full_name,
                name=raw.get("name") or "",
                description=raw.get("description") or "",
                default_branch=raw.get("default_branch") or "main",
                visibility="private" if is_private else "public",
                clone_url_https=raw.get("clone_url") or "",
                clone_url_ssh=raw.get("ssh_url") or "",
                web_url=raw.get("html_url") or "",
                is_archived=bool(raw.get("archived")),
                is_fork=bool(raw.get("fork")),
                pushed_at=raw.get("pushed_at"),
            )
        )

    return out
