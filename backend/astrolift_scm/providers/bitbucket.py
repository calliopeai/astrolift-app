"""Bitbucket Cloud SCM driver.

Bitbucket Cloud API v2 only — Bitbucket Server / Data Center have a
different API shape and are not in scope. Auth:

- ``bitbucket_pat`` — app password; Basic auth using
  ``{connection.account_login}:{app_password}``
- ``bitbucket_oauth_user`` — OAuth user access token; Bearer auth
- ``bitbucket_oauth_app`` — config row only; raises UNSUPPORTED_AUTH
  so the UI can prompt the user to complete the OAuth dance

The file-write endpoint (POST ``/2.0/repositories/{ws}/{repo}/src``) uses
multipart/form-data instead of JSON — the field key is the file path,
the value is the raw file content. A ``_encode_multipart`` helper builds
the body without pulling in an extra dependency.

Bitbucket uses ``workspace/repo_slug`` as the full_name convention,
matching the ``owner/repo`` shape used by GitHub/GitLab/Gitea.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from core.secrets import EncryptedSecret, decrypt

_BASE = "https://api.bitbucket.org"


class BitbucketProviderError(Exception):
    def __init__(self, code: str, message: str, *, recoverable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.recoverable = recoverable


def _split_workspace_repo(repo_full_name: str) -> tuple[str, str]:
    parts = repo_full_name.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise BitbucketProviderError(
            "VALIDATION",
            f"expected 'workspace/repo_slug', got {repo_full_name!r}",
        )
    return parts[0], parts[1]


def _auth_header(connection) -> str:
    if connection.kind == "bitbucket_oauth_user":
        token = decrypt(
            EncryptedSecret(
                backend_kind=connection.secret_backend_kind,
                backend_ref=bytes(connection.secret_ciphertext),
            )
        ).decode("utf-8")
        return f"Bearer {token}"

    if connection.kind == "bitbucket_pat":
        password = decrypt(
            EncryptedSecret(
                backend_kind=connection.secret_backend_kind,
                backend_ref=bytes(connection.secret_ciphertext),
            )
        ).decode("utf-8")
        username = connection.account_login or ""
        credentials = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        return f"Basic {credentials}"

    if connection.kind == "bitbucket_oauth_app":
        raise BitbucketProviderError(
            "UNSUPPORTED_AUTH",
            "this is the OAuth app config row; complete the OAuth dance to create a token connection first",
            recoverable=True,
        )
    raise BitbucketProviderError(
        "UNSUPPORTED_AUTH",
        f"bitbucket driver doesn't know how to auth {connection.kind!r}",
    )


def _headers(connection, *, content_type: str | None = None) -> dict[str, str]:
    h = {
        "Authorization": _auth_header(connection),
        "Accept": "application/json",
        "User-Agent": "astrolift",
    }
    if content_type:
        h["Content-Type"] = content_type
    return h


def _encode_multipart(fields: dict[str, str], boundary: str) -> bytes:
    """Build a multipart/form-data body from string fields."""
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            "\r\n"
            f"{value}\r\n".encode()
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)


def _handle_http_error(exc: urllib.error.HTTPError, *, context: str) -> None:
    body_text = ""
    try:
        body_text = exc.read().decode("utf-8", "replace")[:300]
    except Exception:
        pass
    if exc.code in (401, 403):
        raise BitbucketProviderError(
            "AUTH_FAILED",
            f"Bitbucket rejected the token ({exc.code}) {context}. Reconnect or rotate.",
            recoverable=True,
        ) from exc
    raise BitbucketProviderError(
        "API_ERROR",
        f"Bitbucket returned {exc.code} {context}: {body_text}",
    ) from exc


# ---------------------------------------------------------------------------
# list_bitbucket_repos
# ---------------------------------------------------------------------------


def list_bitbucket_repos(
    connection,
    *,
    search: str | None = None,
    limit: int = 100,
) -> list:
    """GET /2.0/repositories/{workspace}?pagelen={limit}&q=...

    Returns a normalized list of RemoteRepo objects. Visibility scopes
    are applied client-side using the same filter contract as the
    GitHub/GitLab drivers.
    """
    from astrolift_scm.providers import RemoteRepo

    workspace = connection.account_login or ""
    if not workspace:
        raise BitbucketProviderError(
            "VALIDATION",
            "SourceConnection.account_login is required as the Bitbucket workspace slug",
            recoverable=True,
        )

    pagelen = min(max(limit, 1), 100)
    params: dict[str, str] = {"pagelen": str(pagelen), "role": "member"}
    if search:
        params["q"] = f'full_name ~ "{search}"'

    url = (
        f"{_BASE}/2.0/repositories/{urllib.parse.quote(workspace, safe='')}?{urllib.parse.urlencode(params)}"
    )
    req = urllib.request.Request(url, headers=_headers(connection))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        _handle_http_error(exc, context="listing repos")
    except urllib.error.URLError as exc:
        raise BitbucketProviderError("NETWORK", f"Couldn't reach Bitbucket: {exc.reason}") from exc

    rows = payload.get("values") or []
    scopes = set(connection.repo_visibility_scopes or [])
    org_login = workspace.lower()

    out: list[RemoteRepo] = []
    for raw in rows:
        is_private = raw.get("is_private", False)
        owner = raw.get("workspace") or raw.get("owner") or {}
        owner_login = (owner.get("slug") or owner.get("nickname") or "").lower()
        full_name = (raw.get("full_name") or "").strip()

        if scopes:
            in_org = bool(org_login) and owner_login == org_login
            keep = (
                ("private_org" in scopes and in_org and is_private)
                or ("public_org" in scopes and in_org and not is_private)
                or ("user_repos" in scopes and not in_org)
                or ("public_non_org" in scopes and not in_org and not is_private)
            )
            if not keep:
                continue

        links = raw.get("links") or {}
        html_url = (links.get("html") or {}).get("href") or ""
        clone_https = ""
        clone_ssh = ""
        for clone in raw.get("links", {}).get("clone") or []:
            href = clone.get("href") or ""
            if clone.get("name") == "https":
                clone_https = href
            elif clone.get("name") == "ssh":
                clone_ssh = href

        out.append(
            RemoteRepo(
                full_name=full_name,
                name=raw.get("name") or raw.get("slug") or "",
                description=raw.get("description") or "",
                default_branch=raw.get("mainbranch", {}).get("name") or "main",
                visibility="private" if is_private else "public",
                clone_url_https=clone_https,
                clone_url_ssh=clone_ssh,
                web_url=html_url,
                is_archived=False,  # Bitbucket has no archived concept in v2
                is_fork=bool(raw.get("parent")),
                pushed_at=raw.get("updated_on"),
            )
        )

    return out


# ---------------------------------------------------------------------------
# fetch_bitbucket_file
# ---------------------------------------------------------------------------


def fetch_bitbucket_file(
    connection,
    *,
    repo_full_name: str,
    path: str,
    ref: str,
) -> str | None:
    """GET /2.0/repositories/{ws}/{repo}/src/{ref}/{path}

    Returns the raw file content or None on 404.
    """
    workspace, repo_slug = _split_workspace_repo(repo_full_name)
    safe_ws = urllib.parse.quote(workspace, safe="")
    safe_repo = urllib.parse.quote(repo_slug, safe="")
    safe_ref = urllib.parse.quote(ref, safe="")
    safe_path = "/".join(urllib.parse.quote(p, safe="") for p in path.split("/"))
    url = f"{_BASE}/2.0/repositories/{safe_ws}/{safe_repo}/src/{safe_ref}/{safe_path}"

    hdrs = _headers(connection)
    hdrs["Accept"] = "text/plain"
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        _handle_http_error(exc, context=f"fetching {path!r}")
    except urllib.error.URLError as exc:
        raise BitbucketProviderError("NETWORK", f"Couldn't reach Bitbucket: {exc.reason}") from exc


# ---------------------------------------------------------------------------
# put_bitbucket_file
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class BitbucketPutFileResult:
    commit_sha: str
    file_path: str
    web_url: str


def put_bitbucket_file(
    connection,
    *,
    repo_full_name: str,
    path: str,
    branch: str,
    content: str,
    commit_message: str,
) -> BitbucketPutFileResult:
    """POST /2.0/repositories/{ws}/{repo}/src (multipart/form-data).

    Bitbucket's /src endpoint creates or updates a file in one call —
    no separate GET-for-SHA is required. The response is a 201 Redirect
    pointing to the new commit; we extract the commit SHA from the URL.
    """
    workspace, repo_slug = _split_workspace_repo(repo_full_name)
    safe_ws = urllib.parse.quote(workspace, safe="")
    safe_repo = urllib.parse.quote(repo_slug, safe="")
    url = f"{_BASE}/2.0/repositories/{safe_ws}/{safe_repo}/src"

    boundary = os.urandom(12).hex()
    fields = {
        path: content,
        "message": commit_message,
        "branch": branch,
    }
    body = _encode_multipart(fields, boundary)
    hdrs = _headers(connection, content_type=f"multipart/form-data; boundary={boundary}")
    req = urllib.request.Request(url, data=body, method="POST", headers=hdrs)

    opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
    try:
        with opener.open(req, timeout=15) as resp:
            commit_url = resp.url or ""
            commit_sha = commit_url.rstrip("/").split("/")[-1] if commit_url else ""
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise BitbucketProviderError(
                "NOT_FOUND",
                f"Bitbucket couldn't find {repo_full_name}@{branch}. Check the connection's repo access.",
                recoverable=True,
            ) from exc
        _handle_http_error(exc, context=f"writing {path!r} to {repo_full_name}")
    except urllib.error.URLError as exc:
        raise BitbucketProviderError("NETWORK", f"Couldn't reach Bitbucket: {exc.reason}") from exc

    web_url = f"https://bitbucket.org/{repo_full_name}/src/{branch}/{path}"
    return BitbucketPutFileResult(
        commit_sha=commit_sha,
        file_path=path,
        web_url=web_url,
    )


# ---------------------------------------------------------------------------
# install_bitbucket_webhook
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class BitbucketInstallWebhookResult:
    hook_id: str
    webhook_url: str


def install_bitbucket_webhook(
    connection,
    *,
    repo_full_name: str,
    target_url: str,
    secret: str,
    events: tuple[str, ...] = ("repo:push",),
) -> BitbucketInstallWebhookResult:
    """POST /2.0/repositories/{ws}/{repo}/hooks.

    Bitbucket's webhook payload doesn't carry a secret for HMAC
    verification the same way GitHub does — the ``secret`` parameter is
    stored locally but not forwarded (Bitbucket uses IP allowlisting or
    JWT-signed payloads in newer plans). ``events`` default to ``repo:push``.
    """
    workspace, repo_slug = _split_workspace_repo(repo_full_name)
    safe_ws = urllib.parse.quote(workspace, safe="")
    safe_repo = urllib.parse.quote(repo_slug, safe="")
    url = f"{_BASE}/2.0/repositories/{safe_ws}/{safe_repo}/hooks"

    body = {
        "description": "Astrolift",
        "url": target_url,
        "active": True,
        "events": list(events),
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers=_headers(connection, content_type="application/json"),
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
        text = body_text.lower()
        if exc.code in (401, 403):
            raise BitbucketProviderError(
                "AUTH_FAILED",
                f"Bitbucket rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        if exc.code == 404:
            raise BitbucketProviderError(
                "NOT_FOUND",
                f"Bitbucket couldn't find {repo_full_name}. Check repo access.",
                recoverable=True,
            ) from exc
        if exc.code == 409 or "already exists" in text:
            raise BitbucketProviderError(
                "ALREADY_EXISTS",
                "a webhook with this URL is already installed on the repo",
                recoverable=True,
            ) from exc
        raise BitbucketProviderError(
            "API_ERROR",
            f"Bitbucket returned {exc.code}: {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise BitbucketProviderError("NETWORK", f"Couldn't reach Bitbucket: {exc.reason}") from exc

    hook_uuid = (payload or {}).get("uuid") or ""
    if not hook_uuid:
        raise BitbucketProviderError(
            "UNEXPECTED_SHAPE",
            "Bitbucket POST hooks response didn't include 'uuid'",
        )
    return BitbucketInstallWebhookResult(hook_id=hook_uuid, webhook_url=target_url)


# ---------------------------------------------------------------------------
# delete_bitbucket_webhook
# ---------------------------------------------------------------------------


def delete_bitbucket_webhook(
    connection,
    *,
    repo_full_name: str,
    hook_id: str,
) -> None:
    """DELETE /2.0/repositories/{ws}/{repo}/hooks/{uid}. Idempotent: a
    404 is treated as already-deleted."""
    workspace, repo_slug = _split_workspace_repo(repo_full_name)
    safe_ws = urllib.parse.quote(workspace, safe="")
    safe_repo = urllib.parse.quote(repo_slug, safe="")
    safe_uid = urllib.parse.quote(hook_id, safe="")
    url = f"{_BASE}/2.0/repositories/{safe_ws}/{safe_repo}/hooks/{safe_uid}"

    req = urllib.request.Request(url, method="DELETE", headers=_headers(connection))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            _ = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return
        _handle_http_error(exc, context=f"deleting hook {hook_id!r}")
    except urllib.error.URLError as exc:
        raise BitbucketProviderError(
            "NETWORK",
            f"Couldn't reach Bitbucket deleting hook {hook_id!r}: {exc.reason}",
        ) from exc


# ---------------------------------------------------------------------------
# open_bitbucket_pull_request
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class BitbucketOpenPRResult:
    url: str
    number: str


def open_bitbucket_pull_request(
    connection,
    *,
    repo_full_name: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
) -> BitbucketOpenPRResult:
    """POST /2.0/repositories/{ws}/{repo}/pullrequests.

    Bitbucket calls the target branch ``destination`` and the source
    branch ``source``. The PR ``id`` is the project-scoped integer
    identifier; ``links.html.href`` is the browser URL.
    """
    workspace, repo_slug = _split_workspace_repo(repo_full_name)
    safe_ws = urllib.parse.quote(workspace, safe="")
    safe_repo = urllib.parse.quote(repo_slug, safe="")
    url = f"{_BASE}/2.0/repositories/{safe_ws}/{safe_repo}/pullrequests"

    payload = {
        "title": title,
        "description": body or "",
        "source": {"branch": {"name": head_branch}},
        "destination": {"branch": {"name": base_branch}},
        "close_source_branch": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=_headers(connection, content_type="application/json"),
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        text = body_text.lower()
        if exc.code in (401, 403):
            raise BitbucketProviderError(
                "AUTH_FAILED",
                f"Bitbucket rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        if exc.code == 404:
            raise BitbucketProviderError(
                "NOT_FOUND",
                f"Bitbucket couldn't find {repo_full_name}. Check the connection's repo access.",
                recoverable=True,
            ) from exc
        if exc.code == 400 and (
            "no commits" in text or "source and destination" in text or "already exists" in text
        ):
            raise BitbucketProviderError(
                "NOTHING_TO_MERGE",
                f"branch {head_branch!r} has no commits ahead of {base_branch!r}",
                recoverable=True,
            ) from exc
        if exc.code == 409 or "already exists" in text:
            raise BitbucketProviderError(
                "ALREADY_EXISTS",
                f"a pull request from {head_branch!r} into {base_branch!r} is already open",
                recoverable=True,
            ) from exc
        raise BitbucketProviderError(
            "API_ERROR",
            f"Bitbucket returned {exc.code}: {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise BitbucketProviderError("NETWORK", f"Couldn't reach Bitbucket: {exc.reason}") from exc

    pr_id = (data or {}).get("id")
    links = (data or {}).get("links") or {}
    pr_url = (links.get("html") or {}).get("href") or ""
    if pr_id is None or not pr_url:
        raise BitbucketProviderError(
            "UNEXPECTED_SHAPE",
            "Bitbucket POST pullrequests response missing id or links.html.href",
        )
    return BitbucketOpenPRResult(url=pr_url, number=str(pr_id))


# ---------------------------------------------------------------------------
# put_bitbucket_pipeline_variable
# ---------------------------------------------------------------------------


def put_bitbucket_pipeline_variable(
    connection,
    *,
    repo_full_name: str,
    key: str,
    value: str,
    secured: bool = False,
) -> None:
    """Upsert a pipeline variable on the repo.

    Bitbucket doesn't have a single-call upsert: we list variables,
    look up the existing UUID by key, then PATCH if found or POST if not.
    Secured variables mask the value on subsequent GET calls; the write
    always sends the plaintext.
    """
    if not key:
        raise BitbucketProviderError("VALIDATION", "pipeline variable key is required")

    workspace, repo_slug = _split_workspace_repo(repo_full_name)
    safe_ws = urllib.parse.quote(workspace, safe="")
    safe_repo = urllib.parse.quote(repo_slug, safe="")
    base_url = f"{_BASE}/2.0/repositories/{safe_ws}/{safe_repo}/pipelines_config/variables/"

    existing_uuid: str | None = None
    list_req = urllib.request.Request(base_url, headers=_headers(connection))
    try:
        with urllib.request.urlopen(list_req, timeout=10) as resp:
            list_payload = json.loads(resp.read().decode("utf-8"))
        for var in list_payload.get("values") or []:
            if var.get("key") == key:
                existing_uuid = var.get("uuid")
                break
    except urllib.error.HTTPError as exc:
        _handle_http_error(exc, context=f"listing pipeline variables for {repo_full_name}")
    except urllib.error.URLError as exc:
        raise BitbucketProviderError("NETWORK", f"Couldn't reach Bitbucket: {exc.reason}") from exc

    var_body = {"key": key, "value": value, "secured": secured}
    body_bytes = json.dumps(var_body).encode("utf-8")

    if existing_uuid:
        safe_uuid = urllib.parse.quote(existing_uuid, safe="")
        patch_url = f"{base_url}{safe_uuid}"
        req = urllib.request.Request(
            patch_url,
            data=body_bytes,
            method="PUT",
            headers=_headers(connection, content_type="application/json"),
        )
    else:
        req = urllib.request.Request(
            base_url,
            data=body_bytes,
            method="POST",
            headers=_headers(connection, content_type="application/json"),
        )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            _ = resp.read()
    except urllib.error.HTTPError as exc:
        _handle_http_error(exc, context=f"writing pipeline variable {key!r}")
    except urllib.error.URLError as exc:
        raise BitbucketProviderError("NETWORK", f"Couldn't reach Bitbucket: {exc.reason}") from exc
