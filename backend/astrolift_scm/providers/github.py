"""
GitHub repo listing through a stored connection.

Auth modes covered:
- ``github_pat``: ``Authorization: token <pat>``, lists via ``/user/repos``.
- ``github_oauth_user``: same shape as PAT — a user-bearer token.
- ``github_app_install``: ``Authorization: Bearer <installation-token>``,
  lists via ``/installation/repositories``. Installation tokens are
  scoped to the App's installation (the operator picks the repos at
  install time on GitHub) so the result set is *the* canonical list for
  this connection — no client-side visibility filtering needed.

Visibility scopes from ``SourceConnection.repo_visibility_scopes``
are applied client-side to the user-token API response. We could push
some filtering into the API call itself (the ``type`` parameter on
``/user/repos``), but the API doesn't have a single mode that
matches our four-axis scope model exactly, so a clean post-filter
is more honest than juggling combinations. The App-installation path
ignores visibility scopes because the operator already picked the repo
set at install time.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import urllib.error
import urllib.parse
import urllib.request

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
        from astrolift_scm.providers.github_app import (
            GithubAppError,
            installation_token,
        )

        try:
            return installation_token(connection)
        except GithubAppError as exc:
            raise GithubProviderError(exc.code, exc.message, recoverable=exc.recoverable) from exc
    if connection.kind == "github_oauth_app":
        raise GithubProviderError(
            "OAUTH_NOT_CONNECTED",
            "this is the OAuth app config row; complete the OAuth dance to create a token connection first",
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
    """Call GitHub's repo listing API and (for user-bearer connections)
    apply the connection's visibility-scope filter to the result.

    Branches on auth mode:
      * Installation token → ``/installation/repositories`` (different
        response shape: ``{ total_count, repositories: [...] }``).
        Authorization header is ``Bearer``, not ``token``.
      * PAT / OAuth-user token → ``/user/repos``, ``token`` header,
        client-side visibility-scope filter applied.
    """
    from astrolift_scm.providers import RemoteRepo

    token = _token(connection)
    base = _api_base(connection)
    is_app_install = connection.kind == "github_app_install"

    if is_app_install:
        # The App installation already has its repo set fixed by the
        # operator at install-time on GitHub, so we don't pre-filter by
        # visibility scope. We do honor the operator's per_page hint.
        qs = urllib.parse.urlencode({"per_page": str(min(max(limit, 1), 100))})
        url = f"{base}/installation/repositories?{qs}"
        auth_header = f"Bearer {token}"
    else:
        # /user/repos returns repos the token can see (private + public,
        # owned + collaborator + org). per_page=100, single page;
        # paginating the long tail lands in a follow-up when we need it.
        qs = urllib.parse.urlencode(
            {
                "per_page": str(min(max(limit, 1), 100)),
                "sort": "pushed",
                "direction": "desc",
            }
        )
        url = f"{base}/user/repos?{qs}"
        auth_header = f"token {token}"

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": auth_header,
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
        raise GithubProviderError("NETWORK", f"Couldn't reach GitHub: {exc.reason}") from exc

    if is_app_install:
        if not isinstance(payload, dict) or not isinstance(payload.get("repositories"), list):
            raise GithubProviderError(
                "UNEXPECTED_SHAPE",
                "GitHub returned an unexpected payload for /installation/repositories",
            )
        rows = payload["repositories"]
    else:
        if not isinstance(payload, list):
            raise GithubProviderError("UNEXPECTED_SHAPE", "GitHub returned a non-list payload")
        rows = payload

    org_login = (connection.account_login or "").lower()
    scopes = set(connection.repo_visibility_scopes or [])
    apply_scope_filter = bool(scopes) and not is_app_install

    out: list[RemoteRepo] = []
    for raw in rows:
        owner = raw.get("owner") or {}
        owner_login = (owner.get("login") or "").lower()
        owner_type = owner.get("type") or "User"
        is_private = bool(raw.get("private"))
        full_name = raw.get("full_name", "")

        if apply_scope_filter:
            in_org = bool(org_login) and owner_login == org_login
            keep = (
                ("private_org" in scopes and in_org and is_private)
                or ("public_org" in scopes and in_org and not is_private)
                or ("user_repos" in scopes and owner_type == "User")
                or ("public_non_org" in scopes and not in_org and not is_private)
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


def fetch_github_file(
    connection,
    *,
    repo_full_name: str,
    path: str,
    ref: str,
) -> str | None:
    """Fetch a file from a GitHub repo at ``ref`` through the
    connection's token.

    Returns the file body (UTF-8) or None when the file doesn't
    exist. Raises GithubProviderError on auth / network failures.

    Uses the ``application/vnd.github.raw`` Accept header so the API
    returns the raw bytes directly — no base64 round-trip."""
    token = _token(connection)
    base = _api_base(connection)

    safe_repo = "/".join(urllib.parse.quote(p, safe="") for p in repo_full_name.split("/", 1))
    safe_path = "/".join(urllib.parse.quote(p, safe="") for p in path.split("/"))
    url = f"{base}/repos/{safe_repo}/contents/{safe_path}?ref={urllib.parse.quote(ref)}"

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.raw",
            "X-GitHub-Api-Version": "2022-11-28",
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
            raise GithubProviderError(
                "AUTH_FAILED",
                f"GitHub rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        raise GithubProviderError("API_ERROR", f"GitHub returned {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise GithubProviderError("NETWORK", f"Couldn't reach GitHub: {exc.reason}") from exc


def fetch_github_zipball(
    connection,
    *,
    repo_full_name: str,
    ref: str,
) -> bytes:
    """Download the source archive (zip) of ``repo_full_name`` at ``ref``.

    Returns the raw zipball bytes. The API replies with a 302 to a
    short-lived codeload.github.com URL; ``urllib`` follows it by
    default. Raises GithubProviderError on auth / network / API
    failures so the dispatcher can translate to a clean envelope."""
    token = _token(connection)
    base = _api_base(connection)

    safe_repo = "/".join(urllib.parse.quote(p, safe="") for p in repo_full_name.split("/", 1))
    url = f"{base}/repos/{safe_repo}/zipball/{urllib.parse.quote(ref)}"

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
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise GithubProviderError(
                "AUTH_FAILED",
                f"GitHub rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        raise GithubProviderError("API_ERROR", f"GitHub returned {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise GithubProviderError("NETWORK", f"Couldn't reach GitHub: {exc.reason}") from exc


@dataclasses.dataclass(frozen=True, slots=True)
class PutFileResult:
    commit_sha: str
    file_path: str
    web_url: str


@dataclasses.dataclass(frozen=True, slots=True)
class InstallWebhookResult:
    """Output of installing a webhook on the SCM host. ``hook_id``
    is the host-side identifier we'll need to rotate or delete the
    hook later through the same connection."""

    hook_id: str
    webhook_url: str


def install_github_webhook(
    connection,
    *,
    repo_full_name: str,
    target_url: str,
    secret: str,
    events: tuple[str, ...] = ("push", "pull_request"),
) -> InstallWebhookResult:
    """POST /repos/{owner}/{repo}/hooks via the connection's token.

    GitHub-App-install connections short-circuit (the App's own
    webhook fires) — callers should detect that case upstream rather
    than calling this method. PAT / OAuth-user tokens land the hook
    under the token-owner's name on the repo.
    """
    if connection.kind == "github_app_install":
        raise GithubProviderError(
            "UNSUPPORTED",
            "github_app_install already delivers webhooks via the App; no per-repo hook install is needed",
        )
    token = _token(connection)
    base = _api_base(connection)

    safe_repo = "/".join(urllib.parse.quote(p, safe="") for p in repo_full_name.split("/", 1))
    url = f"{base}/repos/{safe_repo}/hooks"

    body = {
        "name": "web",
        "active": True,
        "events": list(events),
        "config": {
            "url": target_url,
            "content_type": "json",
            "secret": secret,
            "insecure_ssl": "0",
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        # GitHub returns 422 with "Hook already exists" when the same
        # URL is registered. Surface that cleanly as a conflict so the
        # mutation can treat it as idempotent.
        if exc.code == 422 and "already exists" in body_text.lower():
            raise GithubProviderError(
                "ALREADY_EXISTS",
                "a webhook with this URL is already installed on the repo",
                recoverable=True,
            ) from exc
        if exc.code in (401, 403):
            raise GithubProviderError(
                "AUTH_FAILED",
                f"GitHub rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        if exc.code == 404:
            raise GithubProviderError(
                "NOT_FOUND",
                f"GitHub couldn't find {repo_full_name}. Check repo access.",
                recoverable=True,
            ) from exc
        raise GithubProviderError(
            "API_ERROR",
            f"GitHub returned {exc.code}: {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GithubProviderError("NETWORK", f"Couldn't reach GitHub: {exc.reason}") from exc

    hook_id = payload.get("id")
    if hook_id is None:
        raise GithubProviderError(
            "UNEXPECTED_SHAPE",
            "GitHub POST hooks response didn't include 'id'",
        )
    return InstallWebhookResult(hook_id=str(hook_id), webhook_url=target_url)


def delete_github_webhook(
    connection,
    *,
    repo_full_name: str,
    hook_id: str,
) -> None:
    """DELETE /repos/{owner}/{repo}/hooks/{hook_id}.

    Idempotent: 404 (hook already gone) is treated as success — the
    deregister flow's only concern is that the hook is no longer live,
    not that this particular call performed the delete.

    GitHub-App-install connections raise ``UNSUPPORTED`` — those
    connections never install per-repo hooks in the first place, so
    there's nothing to delete. The deregister activity treats that
    case as a clean no-op.
    """
    if connection.kind == "github_app_install":
        raise GithubProviderError(
            "UNSUPPORTED",
            "github_app_install never installs per-repo hooks; nothing to delete",
        )
    if not hook_id:
        raise GithubProviderError(
            "VALIDATION",
            "hook_id is required to delete a webhook",
        )

    token = _token(connection)
    base = _api_base(connection)
    safe_repo = "/".join(urllib.parse.quote(p, safe="") for p in repo_full_name.split("/", 1))
    safe_hook = urllib.parse.quote(str(hook_id), safe="")
    url = f"{base}/repos/{safe_repo}/hooks/{safe_hook}"

    req = urllib.request.Request(
        url,
        method="DELETE",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrolift",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # Hook already gone — the desired end state. Don't fail.
            return
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:300]
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
            f"GitHub returned {exc.code}: {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GithubProviderError("NETWORK", f"Couldn't reach GitHub: {exc.reason}") from exc


def _github_contents_url(base: str, repo_full_name: str, path: str) -> str:
    safe_repo = "/".join(urllib.parse.quote(p, safe="") for p in repo_full_name.split("/", 1))
    safe_path = "/".join(urllib.parse.quote(p, safe="") for p in path.split("/"))
    return f"{base}/repos/{safe_repo}/contents/{safe_path}"


def _github_existing_sha(
    *,
    token: str,
    base: str,
    repo_full_name: str,
    path: str,
    branch: str,
) -> str | None:
    """Discover the blob SHA of an existing file on ``branch`` so we can
    PUT an update instead of failing 422. Returns None when the file
    doesn't exist yet (the create path). Bubbles auth failures as
    GithubProviderError so the caller sees the same envelope shape it
    would on any other API call."""
    url = f"{_github_contents_url(base, repo_full_name, path)}?ref={urllib.parse.quote(branch)}"
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
        if exc.code == 404:
            return None
        if exc.code in (401, 403):
            raise GithubProviderError(
                "AUTH_FAILED",
                f"GitHub rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        raise GithubProviderError("API_ERROR", f"GitHub returned {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise GithubProviderError("NETWORK", f"Couldn't reach GitHub: {exc.reason}") from exc

    if isinstance(payload, dict):
        sha = payload.get("sha")
        if isinstance(sha, str):
            return sha
    # Directory listing or unexpected payload — treat as "no file here".
    return None


def put_github_file(
    connection,
    *,
    repo_full_name: str,
    path: str,
    branch: str,
    content: str,
    commit_message: str,
) -> PutFileResult:
    """Create or update a file at ``path`` on ``branch`` via the
    GitHub Contents API.

    GitHub's PUT /repos/{owner}/{repo}/contents/{path} is the same
    endpoint for create and update; the difference is whether you
    include the existing blob ``sha``. We discover that with a GET
    first so the caller doesn't need to know whether the file is
    already present.

    The auth header follows the connection's mode:
      * ``token <pat-or-oauth-user-token>`` for ``github_pat`` and
        ``github_oauth_user`` (commit attribution = the user who owns
        the token).
      * ``Bearer <installation-token>`` for ``github_app_install``
        (commit attribution = the GitHub App as a bot)."""
    token = _token(connection)
    base = _api_base(connection)
    is_app_install = connection.kind == "github_app_install"
    auth_header = f"Bearer {token}" if is_app_install else f"token {token}"

    existing_sha = _github_existing_sha(
        token=token,
        base=base,
        repo_full_name=repo_full_name,
        path=path,
        branch=branch,
    )

    body: dict[str, str] = {
        "message": commit_message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if existing_sha:
        body["sha"] = existing_sha

    url = _github_contents_url(base, repo_full_name, path)
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="PUT",
        headers={
            "Authorization": auth_header,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if exc.code in (401, 403):
            raise GithubProviderError(
                "AUTH_FAILED",
                f"GitHub rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        if exc.code == 404:
            raise GithubProviderError(
                "NOT_FOUND",
                f"GitHub couldn't find {repo_full_name}@{branch}. Check the connection's repo access.",
                recoverable=True,
            ) from exc
        raise GithubProviderError(
            "API_ERROR",
            f"GitHub returned {exc.code}: {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GithubProviderError("NETWORK", f"Couldn't reach GitHub: {exc.reason}") from exc

    commit = (payload or {}).get("commit") or {}
    content_meta = (payload or {}).get("content") or {}
    commit_sha = commit.get("sha") or ""
    web_url = content_meta.get("html_url") or commit.get("html_url") or ""
    file_path = content_meta.get("path") or path
    if not commit_sha:
        raise GithubProviderError(
            "UNEXPECTED_SHAPE",
            "GitHub PUT contents response didn't include commit.sha",
        )
    return PutFileResult(commit_sha=commit_sha, file_path=file_path, web_url=web_url)


@dataclasses.dataclass(frozen=True, slots=True)
class OpenPullRequestResult:
    """Outcome of ``POST /repos/{owner}/{repo}/pulls``.

    ``url`` is the ``html_url`` of the PR (operator-clickable);
    ``number`` is the host-side PR number stringified so callers can
    compose with the GitLab MR iid (also numeric) without a
    type-dispatch."""

    url: str
    number: str


def open_github_pull_request(
    connection,
    *,
    repo_full_name: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
) -> OpenPullRequestResult:
    """Open a PR via ``POST /repos/{owner}/{repo}/pulls``.

    The auth header follows the connection's mode (same as
    :func:`put_github_file`): ``Bearer <installation-token>`` for
    ``github_app_install``, ``token <pat-or-oauth-user>`` otherwise.

    Common failure modes are mapped to recoverable
    :class:`GithubProviderError`:

    - 401 / 403 → ``AUTH_FAILED``
    - 404      → ``NOT_FOUND`` (most often: the connection's token can't
                  reach the repo)
    - 422 with ``already exists`` in the body → ``ALREADY_EXISTS`` so
      the mutation layer can treat the case as idempotent (a sibling
      push already opened the PR; surface the existing one instead of
      blowing up).
    - 422 with ``No commits between`` → ``NOT_FOUND`` shape
      (``NOTHING_TO_MERGE``) so the mutation can return a
      user-meaningful "nothing to push" rather than the raw 422.
    """
    token = _token(connection)
    base = _api_base(connection)
    is_app_install = connection.kind == "github_app_install"
    auth_header = f"Bearer {token}" if is_app_install else f"token {token}"

    safe_repo = "/".join(urllib.parse.quote(p, safe="") for p in repo_full_name.split("/", 1))
    url = f"{base}/repos/{safe_repo}/pulls"

    body_payload = {
        "title": title,
        "head": head_branch,
        "base": base_branch,
        "body": body or "",
        # ``maintainer_can_modify`` lets the App / token bearer push
        # follow-up commits to the PR's head branch when the operator
        # asks the App to amend a fix. False by default on the API,
        # opting in is the right thing for an automation-opened PR.
        "maintainer_can_modify": True,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(body_payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": auth_header,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if exc.code in (401, 403):
            raise GithubProviderError(
                "AUTH_FAILED",
                f"GitHub rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        if exc.code == 404:
            raise GithubProviderError(
                "NOT_FOUND",
                f"GitHub couldn't find {repo_full_name}. Check the connection's repo access.",
                recoverable=True,
            ) from exc
        if exc.code == 422:
            text = body_text.lower()
            if "already exists" in text or "a pull request already exists" in text:
                raise GithubProviderError(
                    "ALREADY_EXISTS",
                    f"a pull request from {head_branch!r} into {base_branch!r} is already open",
                    recoverable=True,
                ) from exc
            if "no commits between" in text:
                raise GithubProviderError(
                    "NOTHING_TO_MERGE",
                    f"branch {head_branch!r} has no commits ahead of {base_branch!r}",
                    recoverable=True,
                ) from exc
        raise GithubProviderError(
            "API_ERROR",
            f"GitHub returned {exc.code}: {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GithubProviderError("NETWORK", f"Couldn't reach GitHub: {exc.reason}") from exc

    pr_url = (payload or {}).get("html_url") or ""
    number = (payload or {}).get("number")
    if not pr_url or number is None:
        raise GithubProviderError(
            "UNEXPECTED_SHAPE",
            "GitHub POST pulls response missing html_url or number",
        )
    return OpenPullRequestResult(url=pr_url, number=str(number))
