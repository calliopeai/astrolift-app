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


@dataclasses.dataclass(frozen=True, slots=True)
class GitlabPutFileResult:
    commit_sha: str
    file_path: str
    web_url: str


def _gitlab_files_url(base: str, repo_full_name: str, path: str) -> str:
    project_path = urllib.parse.quote(repo_full_name, safe="")
    file_path_q = urllib.parse.quote(path, safe="")
    return f"{base}/api/v4/projects/{project_path}/repository/files/{file_path_q}"


def _gitlab_file_exists(
    *,
    token: str,
    base: str,
    repo_full_name: str,
    path: str,
    branch: str,
) -> bool:
    """HEAD-equivalent: GitLab's metadata endpoint returns 200 when the
    file exists on ``branch`` and 404 when it doesn't. We use this to
    pick POST (create) vs PUT (update); GitLab's API rejects POST on an
    existing path with 400, so guessing is not optional."""
    url = f"{_gitlab_files_url(base, repo_full_name, path)}?ref={urllib.parse.quote(branch)}"
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        if exc.code in (401, 403):
            raise GitlabProviderError(
                "AUTH_FAILED",
                f"GitLab rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        raise GitlabProviderError("API_ERROR", f"GitLab returned {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise GitlabProviderError("NETWORK", f"Couldn't reach GitLab: {exc.reason}") from exc


def put_gitlab_file(
    connection,
    *,
    repo_full_name: str,
    path: str,
    branch: str,
    content: str,
    commit_message: str,
) -> GitlabPutFileResult:
    """Create or update a single file at ``path`` on ``branch`` via
    GitLab's repository-files API.

    POST creates, PUT updates; trying POST against an existing path
    400s. We HEAD first to pick the method, then issue the write.

    GitLab's response carries ``file_path`` and ``branch`` but *not*
    a commit SHA, so we follow up with a single GET against the
    branch's commit head to surface the SHA we just produced. That
    matches what an operator would see in the GitLab UI right after
    the commit."""
    token = _token(connection)
    base = _api_base(connection)

    exists = _gitlab_file_exists(
        token=token,
        base=base,
        repo_full_name=repo_full_name,
        path=path,
        branch=branch,
    )

    body = json.dumps(
        {
            "branch": branch,
            "content": content,
            "commit_message": commit_message,
            "encoding": "text",
        }
    ).encode("utf-8")
    method = "PUT" if exists else "POST"

    url = _gitlab_files_url(base, repo_full_name, path)
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
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
            raise GitlabProviderError(
                "AUTH_FAILED",
                f"GitLab rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        if exc.code == 404:
            raise GitlabProviderError(
                "NOT_FOUND",
                f"GitLab couldn't find {repo_full_name}@{branch}. Check the connection's project access.",
                recoverable=True,
            ) from exc
        raise GitlabProviderError(
            "API_ERROR",
            f"GitLab returned {exc.code}: {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GitlabProviderError("NETWORK", f"Couldn't reach GitLab: {exc.reason}") from exc

    file_path = (payload or {}).get("file_path") or path
    commit_sha = _gitlab_branch_head_sha(
        token=token,
        base=base,
        repo_full_name=repo_full_name,
        branch=branch,
    )
    web_url = _gitlab_blob_url(
        base=base,
        repo_full_name=repo_full_name,
        branch=branch,
        file_path=file_path,
    )
    return GitlabPutFileResult(commit_sha=commit_sha, file_path=file_path, web_url=web_url)


def _gitlab_branch_head_sha(
    *,
    token: str,
    base: str,
    repo_full_name: str,
    branch: str,
) -> str:
    project_path = urllib.parse.quote(repo_full_name, safe="")
    branch_q = urllib.parse.quote(branch, safe="")
    url = f"{base}/api/v4/projects/{project_path}/repository/branches/{branch_q}"
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
        if exc.code in (401, 403):
            raise GitlabProviderError(
                "AUTH_FAILED",
                f"GitLab rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        raise GitlabProviderError("API_ERROR", f"GitLab returned {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise GitlabProviderError("NETWORK", f"Couldn't reach GitLab: {exc.reason}") from exc
    commit = (payload or {}).get("commit") or {}
    sha = commit.get("id") or ""
    if not sha:
        raise GitlabProviderError(
            "UNEXPECTED_SHAPE",
            "GitLab branch response didn't include commit.id",
        )
    return sha


@dataclasses.dataclass(frozen=True, slots=True)
class GitlabInstallWebhookResult:
    hook_id: str
    webhook_url: str


def install_gitlab_webhook(
    connection,
    *,
    repo_full_name: str,
    target_url: str,
    secret: str,
    events: tuple[str, ...] = ("push_events", "merge_requests_events"),
) -> GitlabInstallWebhookResult:
    """POST /api/v4/projects/{id}/hooks via the connection's token.

    ``repo_full_name`` is a URL-encoded path-with-namespace; GitLab's
    hooks API accepts either that or the numeric project id, but the
    path is what we already carry on the RegisteredApp.
    """
    token = _token(connection)
    base = _api_base(connection)
    project_path = urllib.parse.quote(repo_full_name, safe="")
    url = f"{base}/api/v4/projects/{project_path}/hooks"

    body: dict[str, object] = {
        "url": target_url,
        "token": secret,
        "enable_ssl_verification": True,
    }
    for evt in events:
        # GitLab accepts a boolean per event class — push_events,
        # merge_requests_events, tag_push_events, etc. Anything unknown
        # gets ignored by the host, so this is forward-compatible.
        body[evt] = True

    payload_bytes = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload_bytes,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
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
            raise GitlabProviderError(
                "AUTH_FAILED",
                f"GitLab rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        if exc.code == 404:
            raise GitlabProviderError(
                "NOT_FOUND",
                f"GitLab couldn't find project {repo_full_name}",
                recoverable=True,
            ) from exc
        # GitLab returns 422 with "Hook already exists" or similar when
        # a webhook with the same URL is registered.
        if exc.code == 422 and "already exists" in body_text.lower():
            raise GitlabProviderError(
                "ALREADY_EXISTS",
                "a webhook with this URL is already installed on the project",
                recoverable=True,
            ) from exc
        raise GitlabProviderError("API_ERROR", f"GitLab returned {exc.code}: {body_text}") from exc
    except urllib.error.URLError as exc:
        raise GitlabProviderError("NETWORK", f"Couldn't reach GitLab: {exc.reason}") from exc

    hook_id = (payload or {}).get("id")
    if hook_id is None:
        raise GitlabProviderError(
            "UNEXPECTED_SHAPE",
            "GitLab POST hooks response didn't include 'id'",
        )
    return GitlabInstallWebhookResult(hook_id=str(hook_id), webhook_url=target_url)


def _gitlab_blob_url(*, base: str, repo_full_name: str, branch: str, file_path: str) -> str:
    """Best-effort blob URL for ``file_path`` on ``branch``. GitLab's
    file endpoint doesn't return a web URL; we synthesize one from the
    API base. For SaaS gitlab.com the API host doubles as the web host;
    self-hosted installs that split the two will need an override on
    the connection (out of scope for this issue)."""
    project = "/".join(urllib.parse.quote(p, safe="") for p in repo_full_name.split("/"))
    safe_path = "/".join(urllib.parse.quote(p, safe="") for p in file_path.split("/"))
    return f"{base}/{project}/-/blob/{urllib.parse.quote(branch)}/{safe_path}"


@dataclasses.dataclass(frozen=True, slots=True)
class OpenMergeRequestResult:
    """Outcome of ``POST /projects/{id}/merge_requests``. ``url`` is
    the operator-clickable MR page (``web_url`` on GitLab); ``number``
    is the MR's ``iid`` (project-scoped) stringified for parity with
    the GitHub PR number."""

    url: str
    number: str


def open_gitlab_merge_request(
    connection,
    *,
    repo_full_name: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
) -> OpenMergeRequestResult:
    """Open a merge request via
    ``POST /projects/{url-encoded full path}/merge_requests``.

    Auth follows :func:`put_gitlab_file`: ``Authorization: Bearer
    <token>`` regardless of whether the underlying credential is a
    PAT or an OAuth user token.

    Returns the MR's ``web_url`` + ``iid``. Errors map to recoverable
    :class:`GitlabProviderError`:

    - 401 / 403 → ``AUTH_FAILED``
    - 404      → ``NOT_FOUND``
    - 409 or 4xx body containing ``already exists`` → ``ALREADY_EXISTS``
      (a sibling push already opened the MR; the mutation layer
      surfaces the existing one rather than failing)
    """
    token = _token(connection)
    base = _api_base(connection)
    project = urllib.parse.quote(repo_full_name, safe="")
    url = f"{base}/api/v4/projects/{project}/merge_requests"

    body_payload = {
        "source_branch": head_branch,
        "target_branch": base_branch,
        "title": title,
        "description": body or "",
        # Common-sense defaults for automation-opened MRs. The opener
        # is the token bearer; squash-on-merge is left to the project's
        # default policy so we don't fight the operator's CI config.
        "remove_source_branch": False,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(body_payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
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
            raise GitlabProviderError(
                "AUTH_FAILED",
                f"GitLab rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        if exc.code == 404:
            raise GitlabProviderError(
                "NOT_FOUND",
                f"GitLab couldn't find project {repo_full_name}. Check the connection's project access.",
                recoverable=True,
            ) from exc
        text = body_text.lower()
        if exc.code == 409 or "already exists" in text:
            raise GitlabProviderError(
                "ALREADY_EXISTS",
                f"a merge request from {head_branch!r} into {base_branch!r} is already open",
                recoverable=True,
            ) from exc
        raise GitlabProviderError(
            "API_ERROR",
            f"GitLab returned {exc.code}: {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GitlabProviderError("NETWORK", f"Couldn't reach GitLab: {exc.reason}") from exc

    web_url = (payload or {}).get("web_url") or ""
    iid = (payload or {}).get("iid")
    if not web_url or iid is None:
        raise GitlabProviderError(
            "UNEXPECTED_SHAPE",
            "GitLab POST merge_requests response missing web_url or iid",
        )
    return OpenMergeRequestResult(url=web_url, number=str(iid))
