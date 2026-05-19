"""Gitea SCM driver.

Gitea is always self-hosted: ``connection.api_base_url`` is required
(no SaaS default). Auth header is ``Authorization: token <token>``
for both ``gitea_pat`` and ``gitea_oauth_user``; ``gitea_oauth_app``
is a config-only row and rejected here.

Gitea's REST surface deliberately mirrors GitHub's, so the
``contents``, ``hooks``, and ``pulls`` shapes used here read like the
GitHub driver — but the path structure splits ``{owner}`` and
``{repo}`` into separate parameters rather than carrying a single
``full_name`` segment.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import urllib.error
import urllib.parse
import urllib.request

from core.secrets import EncryptedSecret, decrypt


class GiteaProviderError(Exception):
    def __init__(self, code: str, message: str, *, recoverable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.recoverable = recoverable


def _api_base(connection) -> str:
    base = (connection.api_base_url or "").rstrip("/")
    if not base:
        raise GiteaProviderError(
            "NO_BASE_URL",
            "gitea is self-hosted; SourceConnection.api_base_url is required",
            recoverable=True,
        )
    return base


def _token(connection) -> str:
    if connection.kind in {"gitea_pat", "gitea_oauth_user"}:
        plaintext = decrypt(
            EncryptedSecret(
                backend_kind=connection.secret_backend_kind,
                backend_ref=bytes(connection.secret_ciphertext),
            )
        )
        return plaintext.decode("utf-8")
    if connection.kind == "gitea_oauth_app":
        raise GiteaProviderError(
            "UNSUPPORTED_AUTH",
            "this is the OAuth app config row; complete the OAuth dance to create a token connection first",
            recoverable=True,
        )
    raise GiteaProviderError(
        "UNSUPPORTED_AUTH",
        f"gitea driver doesn't know how to auth {connection.kind!r}",
    )


def _split_owner_repo(repo_full_name: str) -> tuple[str, str]:
    parts = repo_full_name.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise GiteaProviderError(
            "VALIDATION",
            f"expected 'owner/repo', got {repo_full_name!r}",
        )
    return parts[0], parts[1]


def _repo_path(repo_full_name: str) -> str:
    owner, repo = _split_owner_repo(repo_full_name)
    return f"{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(repo, safe='')}"


def _headers(token: str, *, content_type: str | None = None) -> dict[str, str]:
    h = {
        "Authorization": f"token {token}",
        "Accept": "application/json",
        "User-Agent": "astrolift",
    }
    if content_type:
        h["Content-Type"] = content_type
    return h


def list_gitea_repos(
    connection,
    *,
    search: str | None = None,
    limit: int = 100,
) -> list:
    """GET /api/v1/repos/search?q=&limit= and apply the connection's
    visibility scopes client-side, same contract as the GitHub /
    GitLab drivers."""
    from astrolift_scm.providers import RemoteRepo

    token = _token(connection)
    base = _api_base(connection)

    qs_pairs = {
        "limit": str(min(max(limit, 1), 100)),
        "sort": "updated",
        "order": "desc",
    }
    if search:
        qs_pairs["q"] = search
    qs = urllib.parse.urlencode(qs_pairs)
    url = f"{base}/api/v1/repos/search?{qs}"

    req = urllib.request.Request(url, headers=_headers(token))
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
            raise GiteaProviderError(
                "AUTH_FAILED",
                f"Gitea rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        raise GiteaProviderError(
            "API_ERROR",
            f"Gitea returned {exc.code}: {body}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GiteaProviderError("NETWORK", f"Couldn't reach Gitea: {exc.reason}") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise GiteaProviderError(
            "UNEXPECTED_SHAPE",
            "Gitea returned an unexpected payload for /repos/search",
        )
    rows = payload["data"]

    org_login = (connection.account_login or "").lower()
    scopes = set(connection.repo_visibility_scopes or [])

    out: list[RemoteRepo] = []
    for raw in rows:
        owner = raw.get("owner") or {}
        owner_login = (owner.get("login") or owner.get("username") or "").lower()
        # Gitea reports owner type as "User" or "Organization" via `type`.
        owner_type = owner.get("type") or "User"
        is_private = bool(raw.get("private"))
        full_name = (raw.get("full_name") or "").strip()

        if scopes:
            in_org = bool(org_login) and owner_login == org_login
            keep = (
                ("private_org" in scopes and in_org and is_private)
                or ("public_org" in scopes and in_org and not is_private)
                or ("user_repos" in scopes and owner_type == "User")
                or ("public_non_org" in scopes and not in_org and not is_private)
            )
            if not keep:
                continue

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
                pushed_at=raw.get("updated_at") or raw.get("pushed_at"),
            )
        )

    return out


def fetch_gitea_file(
    connection,
    *,
    repo_full_name: str,
    path: str,
    ref: str,
) -> str | None:
    """GET /api/v1/repos/{owner}/{repo}/raw/{path}?ref={ref} — returns
    raw bytes (no base64 round-trip) or None on 404."""
    token = _token(connection)
    base = _api_base(connection)
    repo_path = _repo_path(repo_full_name)
    safe_path = "/".join(urllib.parse.quote(p, safe="") for p in path.split("/"))
    url = f"{base}/api/v1/repos/{repo_path}/raw/{safe_path}" f"?ref={urllib.parse.quote(ref)}"

    headers = _headers(token)
    headers["Accept"] = "text/plain"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code in (401, 403):
            raise GiteaProviderError(
                "AUTH_FAILED",
                f"Gitea rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        raise GiteaProviderError("API_ERROR", f"Gitea returned {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise GiteaProviderError("NETWORK", f"Couldn't reach Gitea: {exc.reason}") from exc


@dataclasses.dataclass(frozen=True, slots=True)
class GiteaPutFileResult:
    commit_sha: str
    file_path: str
    web_url: str


def _gitea_contents_url(base: str, repo_full_name: str, path: str) -> str:
    repo_path = _repo_path(repo_full_name)
    safe_path = "/".join(urllib.parse.quote(p, safe="") for p in path.split("/"))
    return f"{base}/api/v1/repos/{repo_path}/contents/{safe_path}"


def _gitea_existing_sha(
    *,
    token: str,
    base: str,
    repo_full_name: str,
    path: str,
    branch: str,
) -> str | None:
    """Probe for an existing blob's SHA on ``branch``. None on 404 (file
    doesn't exist yet — the create path). Auth failures bubble as
    GiteaProviderError so the caller sees one envelope."""
    url = f"{_gitea_contents_url(base, repo_full_name, path)}?ref={urllib.parse.quote(branch)}"
    req = urllib.request.Request(url, headers=_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code in (401, 403):
            raise GiteaProviderError(
                "AUTH_FAILED",
                f"Gitea rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        raise GiteaProviderError("API_ERROR", f"Gitea returned {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise GiteaProviderError("NETWORK", f"Couldn't reach Gitea: {exc.reason}") from exc

    if isinstance(payload, dict):
        sha = payload.get("sha")
        if isinstance(sha, str):
            return sha
    return None


def put_gitea_file(
    connection,
    *,
    repo_full_name: str,
    path: str,
    branch: str,
    content: str,
    commit_message: str,
) -> GiteaPutFileResult:
    """Create or update a file at ``path`` on ``branch`` via Gitea's
    Contents API. PUT is the create-or-update verb on Gitea (same as
    GitHub); the difference is whether the body carries an existing
    blob ``sha``. We GET first to discover it."""
    token = _token(connection)
    base = _api_base(connection)

    existing_sha = _gitea_existing_sha(
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

    url = _gitea_contents_url(base, repo_full_name, path)
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="PUT",
        headers=_headers(token, content_type="application/json"),
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
            raise GiteaProviderError(
                "AUTH_FAILED",
                f"Gitea rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        if exc.code == 404:
            raise GiteaProviderError(
                "NOT_FOUND",
                f"Gitea couldn't find {repo_full_name}@{branch}. Check the connection's repo access.",
                recoverable=True,
            ) from exc
        raise GiteaProviderError(
            "API_ERROR",
            f"Gitea returned {exc.code}: {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GiteaProviderError("NETWORK", f"Couldn't reach Gitea: {exc.reason}") from exc

    commit = (payload or {}).get("commit") or {}
    content_meta = (payload or {}).get("content") or {}
    commit_sha = commit.get("sha") or ""
    web_url = content_meta.get("html_url") or commit.get("html_url") or ""
    file_path = content_meta.get("path") or content_meta.get("name") or path
    if not commit_sha:
        raise GiteaProviderError(
            "UNEXPECTED_SHAPE",
            "Gitea PUT contents response didn't include commit.sha",
        )
    return GiteaPutFileResult(commit_sha=commit_sha, file_path=file_path, web_url=web_url)


@dataclasses.dataclass(frozen=True, slots=True)
class GiteaInstallWebhookResult:
    hook_id: str
    webhook_url: str


def install_gitea_webhook(
    connection,
    *,
    repo_full_name: str,
    target_url: str,
    secret: str,
    events: tuple[str, ...] = ("push",),
) -> GiteaInstallWebhookResult:
    """POST /api/v1/repos/{owner}/{repo}/hooks. Gitea distinguishes
    hook ``type`` ("gitea", "slack", "discord", …); we always set
    "gitea" since the receiver is Astrolift's own endpoint."""
    token = _token(connection)
    base = _api_base(connection)
    repo_path = _repo_path(repo_full_name)
    url = f"{base}/api/v1/repos/{repo_path}/hooks"

    body = {
        "type": "gitea",
        "active": True,
        "events": list(events),
        "config": {
            "url": target_url,
            "content_type": "json",
            "secret": secret,
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers=_headers(token, content_type="application/json"),
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
            raise GiteaProviderError(
                "AUTH_FAILED",
                f"Gitea rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        if exc.code == 404:
            raise GiteaProviderError(
                "NOT_FOUND",
                f"Gitea couldn't find {repo_full_name}. Check repo access.",
                recoverable=True,
            ) from exc
        if exc.code == 422 and "already exists" in body_text.lower():
            raise GiteaProviderError(
                "ALREADY_EXISTS",
                "a webhook with this URL is already installed on the repo",
                recoverable=True,
            ) from exc
        raise GiteaProviderError(
            "API_ERROR",
            f"Gitea returned {exc.code}: {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GiteaProviderError("NETWORK", f"Couldn't reach Gitea: {exc.reason}") from exc

    hook_id = (payload or {}).get("id")
    if hook_id is None:
        raise GiteaProviderError(
            "UNEXPECTED_SHAPE",
            "Gitea POST hooks response didn't include 'id'",
        )
    return GiteaInstallWebhookResult(hook_id=str(hook_id), webhook_url=target_url)


@dataclasses.dataclass(frozen=True, slots=True)
class GiteaOpenPRResult:
    url: str
    number: str


def open_gitea_pull_request(
    connection,
    *,
    repo_full_name: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
) -> GiteaOpenPRResult:
    """POST /api/v1/repos/{owner}/{repo}/pulls. Gitea mirrors GitHub's
    payload shape: ``{title, head, base, body}``; the response carries
    ``html_url`` and a project-scoped ``number``."""
    token = _token(connection)
    base = _api_base(connection)
    repo_path = _repo_path(repo_full_name)
    url = f"{base}/api/v1/repos/{repo_path}/pulls"

    body_payload = {
        "title": title,
        "head": head_branch,
        "base": base_branch,
        "body": body or "",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body_payload).encode("utf-8"),
        method="POST",
        headers=_headers(token, content_type="application/json"),
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
            raise GiteaProviderError(
                "AUTH_FAILED",
                f"Gitea rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        if exc.code == 404:
            raise GiteaProviderError(
                "NOT_FOUND",
                f"Gitea couldn't find {repo_full_name}. Check the connection's repo access.",
                recoverable=True,
            ) from exc
        text = body_text.lower()
        if exc.code == 409 or "already exists" in text or "pull request already exists" in text:
            raise GiteaProviderError(
                "ALREADY_EXISTS",
                f"a pull request from {head_branch!r} into {base_branch!r} is already open",
                recoverable=True,
            ) from exc
        if exc.code == 422 and ("no commits between" in text or "head branch is empty" in text):
            raise GiteaProviderError(
                "NOTHING_TO_MERGE",
                f"branch {head_branch!r} has no commits ahead of {base_branch!r}",
                recoverable=True,
            ) from exc
        raise GiteaProviderError(
            "API_ERROR",
            f"Gitea returned {exc.code}: {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GiteaProviderError("NETWORK", f"Couldn't reach Gitea: {exc.reason}") from exc

    pr_url = (payload or {}).get("html_url") or ""
    number = (payload or {}).get("number")
    if not pr_url or number is None:
        raise GiteaProviderError(
            "UNEXPECTED_SHAPE",
            "Gitea POST pulls response missing html_url or number",
        )
    return GiteaOpenPRResult(url=pr_url, number=str(number))


def delete_gitea_webhook(
    connection,
    *,
    repo_full_name: str,
    hook_id: str,
) -> None:
    """DELETE /api/v1/repos/{owner}/{repo}/hooks/{id}. Idempotent: a
    404 is treated as already-deleted and returned successfully."""
    token = _token(connection)
    base = _api_base(connection)
    repo_path = _repo_path(repo_full_name)
    safe_id = urllib.parse.quote(hook_id, safe="")
    url = f"{base}/api/v1/repos/{repo_path}/hooks/{safe_id}"

    req = urllib.request.Request(url, method="DELETE", headers=_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            _ = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return
        if exc.code in (401, 403):
            raise GiteaProviderError(
                "AUTH_FAILED",
                f"Gitea rejected the token ({exc.code}) deleting hook {hook_id!r}. Reconnect or rotate.",
                recoverable=True,
            ) from exc
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise GiteaProviderError(
            "API_ERROR",
            f"Gitea returned {exc.code} deleting hook {hook_id!r}: {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GiteaProviderError(
            "NETWORK",
            f"Couldn't reach Gitea deleting hook {hook_id!r}: {exc.reason}",
        ) from exc


def put_gitea_actions_secret(
    connection,
    *,
    repo_full_name: str,
    key: str,
    value: str,
) -> None:
    """PUT /api/v1/repos/{owner}/{repo}/actions/secrets/{key} — Gitea
    Actions secret create-or-update. Body is ``{data: <plaintext>}``;
    Gitea handles encryption server-side (unlike GitHub Actions, which
    requires a libsodium-sealed box)."""
    if not key:
        raise GiteaProviderError("VALIDATION", "secret key is required")

    token = _token(connection)
    base = _api_base(connection)
    repo_path = _repo_path(repo_full_name)
    safe_key = urllib.parse.quote(key, safe="")
    url = f"{base}/api/v1/repos/{repo_path}/actions/secrets/{safe_key}"

    payload_bytes = json.dumps({"data": value}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload_bytes,
        method="PUT",
        headers=_headers(token, content_type="application/json"),
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            _ = resp.read()
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if exc.code in (401, 403):
            raise GiteaProviderError(
                "AUTH_FAILED",
                f"Gitea rejected the token ({exc.code}) writing secret {key!r}. Reconnect or rotate.",
                recoverable=True,
            ) from exc
        if exc.code == 404:
            raise GiteaProviderError(
                "NOT_FOUND",
                f"Gitea couldn't find {repo_full_name} (writing secret {key!r}). Check repo access.",
                recoverable=True,
            ) from exc
        raise GiteaProviderError(
            "API_ERROR",
            f"Gitea returned {exc.code} writing secret {key!r}: {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GiteaProviderError(
            "NETWORK",
            f"Couldn't reach Gitea writing secret {key!r}: {exc.reason}",
        ) from exc
