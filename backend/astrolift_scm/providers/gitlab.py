"""
GitLab repo listing through a stored connection.

Auth modes covered:
- ``gitlab_pat``: ``PRIVATE-TOKEN: <pat>`` or
  ``Authorization: Bearer <pat>`` — fine-grained personal/project tokens
  accept either. We send the Bearer form for parity with OAuth.
- ``gitlab_oauth_user``: ``Authorization: Bearer <access_token>``
  produced by the OAuth dance in ``auth1.scm_oauth``.

Self-hosted instances supply ``api_base_url`` on the
SourceConnection (e.g. ``https://gitlab.acme.example``). Empty means
SaaS gitlab.com.

GitLab's project model maps to GitHub's "repo" model 1:1 here:
projects have ``path_with_namespace`` (= ``full_name``), ``name``,
``default_branch``, ``visibility``, ``ssh_url_to_repo``,
``http_url_to_repo``, ``web_url``.

Visibility scopes from ``SourceConnection.repo_visibility_scopes``
are applied client-side to the API response the same way the GitHub
driver does — see that module for the contract.
"""

from __future__ import annotations

import dataclasses
import json
import urllib.error
import urllib.parse
import urllib.request

from core.secrets import EncryptedSecret, decrypt

GITLAB_API_DEFAULT = "https://gitlab.com"


@dataclasses.dataclass(frozen=True, slots=True)
class GitlabProject:
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
    owner_type: str  # "user" or "group"


class GitlabProviderError(Exception):
    def __init__(self, code: str, message: str, *, recoverable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.recoverable = recoverable


def _api_base(connection) -> str:
    return (connection.api_base_url or GITLAB_API_DEFAULT).rstrip("/")


def _token(connection) -> str:
    if connection.kind in {"gitlab_pat", "gitlab_oauth_user"}:
        plaintext = decrypt(
            EncryptedSecret(
                backend_kind=connection.secret_backend_kind,
                backend_ref=bytes(connection.secret_ciphertext),
            )
        )
        return plaintext.decode("utf-8")
    if connection.kind == "gitlab_oauth_app":
        raise GitlabProviderError(
            "OAUTH_NOT_CONNECTED",
            "this is the OAuth app config row; complete the OAuth dance to create a token connection first",
            recoverable=True,
        )
    raise GitlabProviderError(
        "UNSUPPORTED_AUTH",
        f"gitlab driver doesn't know how to auth {connection.kind!r}",
    )


def list_gitlab_projects(
    connection,
    *,
    search: str | None = None,
    limit: int = 100,
) -> list:
    """Call GitLab's projects API and apply the connection's
    visibility-scope filter to the result.

    ``membership=true`` constrains to projects the token-bearer has a
    membership in (owned, maintainer, or invited); without it the
    endpoint returns *all* visible projects on the instance, which
    blows up the page on gitlab.com."""
    from astrolift_scm.providers import RemoteRepo

    token = _token(connection)
    base = _api_base(connection)

    qs_pairs = {
        "membership": "true",
        "order_by": "updated_at",
        "per_page": str(min(max(limit, 1), 100)),
        "simple": "false",
    }
    if search:
        qs_pairs["search"] = search
    qs = urllib.parse.urlencode(qs_pairs)
    url = f"{base}/api/v4/projects?{qs}"

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
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
            raise GitlabProviderError(
                "AUTH_FAILED",
                f"GitLab rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        raise GitlabProviderError(
            "API_ERROR",
            f"GitLab returned {exc.code}: {body}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GitlabProviderError("NETWORK", f"Couldn't reach GitLab: {exc.reason}") from exc

    if not isinstance(payload, list):
        raise GitlabProviderError("UNEXPECTED_SHAPE", "GitLab returned a non-list payload")

    org_login = (connection.account_login or "").lower()
    scopes = set(connection.repo_visibility_scopes or [])

    out: list[RemoteRepo] = []
    for raw in payload:
        namespace = raw.get("namespace") or {}
        namespace_kind = (namespace.get("kind") or "user").lower()  # "user" | "group"
        namespace_path = (namespace.get("full_path") or namespace.get("path") or "").lower()

        full_name = (raw.get("path_with_namespace") or "").strip()
        visibility = (raw.get("visibility") or "private").lower()  # private | internal | public
        is_private = visibility != "public"

        if scopes:
            in_org = bool(org_login) and namespace_path == org_login
            keep = (
                ("private_org" in scopes and in_org and is_private)
                or ("public_org" in scopes and in_org and not is_private)
                or ("user_repos" in scopes and namespace_kind == "user")
                or ("public_non_org" in scopes and not in_org and not is_private)
            )
            if not keep:
                continue

        # GitLab's `search` param already filters server-side; this
        # belt-and-suspenders matches the GitHub driver's contract so
        # tests can drive it without hitting the network.
        if search and search.lower() not in full_name.lower():
            continue

        forked_from = raw.get("forked_from_project")
        out.append(
            RemoteRepo(
                full_name=full_name,
                name=raw.get("name") or "",
                description=raw.get("description") or "",
                default_branch=raw.get("default_branch") or "main",
                visibility="private" if is_private else "public",
                clone_url_https=raw.get("http_url_to_repo") or "",
                clone_url_ssh=raw.get("ssh_url_to_repo") or "",
                web_url=raw.get("web_url") or "",
                is_archived=bool(raw.get("archived")),
                is_fork=bool(forked_from),
                pushed_at=raw.get("last_activity_at"),
            )
        )

    return out


def fetch_gitlab_file(
    connection,
    *,
    repo_full_name: str,
    path: str,
    ref: str,
) -> str | None:
    """Fetch a file from a GitLab project at ``ref``.

    Returns the decoded UTF-8 content, or None when the file
    doesn't exist on that ref. Raises GitlabProviderError on auth /
    network failures (so the caller can render a 'reconnect'
    affordance for genuine auth issues rather than silently saying
    "no manifest")."""
    token = _token(connection)
    base = _api_base(connection)

    project_path = urllib.parse.quote(repo_full_name, safe="")
    file_path = urllib.parse.quote(path, safe="")
    url = (
        f"{base}/api/v4/projects/{project_path}/repository/files/{file_path}/raw"
        f"?ref={urllib.parse.quote(ref)}"
    )
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "text/plain",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code in (401, 403):
            raise GitlabProviderError(
                "AUTH_FAILED",
                f"GitLab rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        raise GitlabProviderError("API_ERROR", f"GitLab returned {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise GitlabProviderError("NETWORK", f"Couldn't reach GitLab: {exc.reason}") from exc
