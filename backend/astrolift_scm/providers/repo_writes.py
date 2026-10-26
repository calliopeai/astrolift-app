"""Repository creation and whole-tree commits through a stored connection (#2424).

Creating a semantic layer (``createBrain``) needs three host calls the rest of
the SCM layer never had: whether a repository exists and is empty, creating
one, and landing a whole file tree as **one** commit (``put_file`` writes one
file per commit). Each call dispatches on ``connection.kind`` like
``astrolift_scm.providers`` does and raises its ``ProviderError``:

* ``repository_state`` - missing, empty, or its commits; "empty" also covers a
  repository whose only commit is a host-generated README.
* ``create_repository`` - under the owner (a user or an organization) with a
  visibility. A GitHub App installation token cannot create repositories
  (the Astrolift AI App has no ``administration: write``), so it refuses
  with ``PRECONDITION`` before any call.
* ``commit_tree`` - GitHub's git data API (blobs, trees, commit, ref), GitLab's
  commits API ``actions``, Gitea's multi-file contents API and Bitbucket's
  ``/src`` form. GitHub's git data API refuses an empty repository, so an
  empty GitHub repository is first initialized with one file through the
  contents API, and the tree lands on top of it.
* ``branch_head_detail`` and ``latest_workflow_run`` read a branch head with
  its time and the newest run of one workflow file (GitHub only today; other
  hosts raise ``UNSUPPORTED`` and callers report "unknown").
"""

from __future__ import annotations

import base64
import dataclasses
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

#: Visibilities a create accepts. ``internal`` exists on GitHub organizations and GitLab.
VISIBILITIES = ("private", "internal", "public")
#: Entries per GitHub tree request; trees chain through ``base_tree``.
_TREE_CHUNK = 100


@dataclasses.dataclass(frozen=True, slots=True)
class TreeFile:
    path: str
    content: bytes
    executable: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class RepoState:
    """``exists`` False: nothing at ``owner/name``. ``empty``: no commits, or
    one commit holding only a README. ``head_sha`` is the default branch head
    (empty for a repository with no commits); ``files`` are that commit's
    top-level paths when it is the only one."""

    exists: bool
    empty: bool = True
    default_branch: str = "main"
    head_sha: str = ""
    commit_count: int = 0
    files: tuple[str, ...] = ()
    file_shas: dict[str, str] = dataclasses.field(default_factory=dict)


def _error(code: str, message: str, *, recoverable: bool = False):
    from astrolift_scm.providers import ProviderError

    return ProviderError(code, message, recoverable=recoverable)


def _readme_only(files) -> bool:
    names = [f for f in files if f]
    return len(names) == 1 and names[0].lower().startswith("readme")


def _split(repo_full_name: str) -> tuple[str, str]:
    parts = (repo_full_name or "").strip().split("/")
    if len(parts) != 2 or not all(parts):
        raise _error("VALIDATION", f"expected owner/name, got {repo_full_name!r}")
    return parts[0], parts[1]


def _quote_repo(repo_full_name: str) -> str:
    owner, name = _split(repo_full_name)
    return f"{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}"


class _HttpError(Exception):
    def __init__(self, status: int, body: str, headers=None):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body
        self.headers = headers or {}


def _http(
    method: str,
    url: str,
    headers: dict[str, str],
    *,
    body: Any = None,
    raw: bytes | None = None,
    timeout: int = 20,
    host: str = "the source host",
) -> tuple[int, Any, Any]:
    """One request; ``(status, parsed JSON or None, response)``. Raises
    ``_HttpError`` for a non-2xx answer and ``ProviderError`` NETWORK when the
    host cannot be reached."""
    data = raw
    hdrs = {"User-Agent": "astrolift", "Accept": "application/json", **headers}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
            parsed = json.loads(text) if text.strip() else None
            return resp.status, parsed, resp
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except Exception:  # noqa: BLE001 - the body is diagnostic only
            detail = ""
        raise _HttpError(exc.code, detail, exc.headers) from exc
    except urllib.error.URLError as exc:
        raise _error("NETWORK", f"couldn't reach {host}: {exc.reason}", recoverable=True) from exc


def _translate(exc: _HttpError, context: str):
    if exc.status in (401, 403):
        return _error(
            "AUTH_FAILED",
            f"the source host refused the connection's credential ({exc.status}) {context}",
            recoverable=True,
        )
    if exc.status == 404:
        return _error("NOT_FOUND", f"not found {context}", recoverable=True)
    if exc.status in (409, 422):
        return _error("CONFLICT", f"the source host refused {context}: {exc.body[:200]}", recoverable=True)
    return _error("API_ERROR", f"the source host returned {exc.status} {context}: {exc.body[:200]}")


def _host(connection) -> str:
    kind = connection.kind or ""
    for prefix in ("github", "gitlab", "gitea", "bitbucket"):
        if kind.startswith(prefix):
            return prefix
    raise _error("UNSUPPORTED", f"repository writes for {kind!r} are not implemented")


def _wrap(call, *args, **kwargs):
    """Run a host driver, mapping its own provider error onto ``ProviderError``."""
    from astrolift_scm.providers import ProviderError

    try:
        return call(*args, **kwargs)
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - only the drivers' typed errors are translated
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", None)
        if code and message:
            raise ProviderError(code, message, recoverable=getattr(exc, "recoverable", False)) from exc
        raise


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def _gh(connection):
    from astrolift_scm.providers import github

    token = github._token(connection)
    scheme = "Bearer" if connection.kind == "github_app_install" else "token"
    headers = {
        "Authorization": f"{scheme} {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    return github._api_base(connection), headers


def _github_state(connection, repo_full_name: str) -> RepoState:
    base, headers = _gh(connection)
    repo = _quote_repo(repo_full_name)
    try:
        _, meta, _ = _http("GET", f"{base}/repos/{repo}", headers, host="GitHub")
    except _HttpError as exc:
        if exc.status == 404:
            return RepoState(exists=False)
        raise _translate(exc, f"reading {repo_full_name}") from exc
    branch = (meta or {}).get("default_branch") or "main"
    try:
        _, commits, _ = _http(
            "GET",
            f"{base}/repos/{repo}/commits?per_page=2&sha={urllib.parse.quote(branch, safe='')}",
            headers,
            host="GitHub",
        )
    except _HttpError as exc:
        # 409 "Git Repository is empty"; 404 when the default branch is unborn.
        if exc.status in (404, 409):
            return RepoState(exists=True, empty=True, default_branch=branch)
        raise _translate(exc, f"listing commits of {repo_full_name}") from exc
    commits = commits or []
    if not commits:
        return RepoState(exists=True, empty=True, default_branch=branch)
    head = commits[0]["sha"]
    if len(commits) > 1:
        return RepoState(exists=True, empty=False, default_branch=branch, head_sha=head, commit_count=2)
    try:
        _, tree, _ = _http("GET", f"{base}/repos/{repo}/git/trees/{head}", headers, host="GitHub")
    except _HttpError as exc:
        raise _translate(exc, f"reading the tree of {repo_full_name}") from exc
    entries = (tree or {}).get("tree") or []
    files = tuple(e.get("path", "") for e in entries)
    return RepoState(
        exists=True,
        empty=_readme_only(files),
        default_branch=branch,
        head_sha=head,
        commit_count=1,
        files=files,
        file_shas={e.get("path", ""): e.get("sha", "") for e in entries},
    )


def _github_create(connection, owner: str, name: str, visibility: str) -> RepoState:
    if connection.kind == "github_app_install":
        raise _error(
            "PRECONDITION",
            "the GitHub App installation cannot create repositories; create the empty repository "
            "first, or use a user OAuth or PAT connection",
            recoverable=True,
        )
    base, headers = _gh(connection)
    try:
        _, me, _ = _http("GET", f"{base}/user", headers, host="GitHub")
    except _HttpError as exc:
        raise _translate(exc, "reading the connection's account") from exc
    personal = str((me or {}).get("login") or "").lower() == owner.lower()
    if personal and visibility == "internal":
        raise _error("VALIDATION", "internal visibility needs an organization owner")
    body = {"name": name, "private": visibility != "public", "auto_init": True}
    if not personal:
        body["visibility"] = visibility
    url = f"{base}/user/repos" if personal else f"{base}/orgs/{urllib.parse.quote(owner, safe='')}/repos"
    try:
        _, created, _ = _http("POST", url, headers, body=body, host="GitHub")
    except _HttpError as exc:
        raise _translate(exc, f"creating {owner}/{name}") from exc
    return _github_state(connection, (created or {}).get("full_name") or f"{owner}/{name}")


def _github_commit(
    connection,
    repo_full_name: str,
    branch: str,
    files: list[TreeFile],
    message: str,
    parent: str,
    author: dict[str, str] | None,
) -> str:
    base, headers = _gh(connection)
    repo = _quote_repo(repo_full_name)
    if not parent:
        # The git data API answers 409 on a repository with no commits; one
        # contents write gives it a root to build on.
        seed = next((f for f in files if f.path.lower() == "readme.md"), files[0])
        try:
            _, put, _ = _http(
                "PUT",
                f"{base}/repos/{repo}/contents/{urllib.parse.quote(seed.path)}",
                headers,
                body={
                    "message": "Initialize the repository",
                    "content": base64.b64encode(seed.content).decode("ascii"),
                    "branch": branch,
                },
                host="GitHub",
            )
        except _HttpError as exc:
            raise _translate(exc, f"initializing {repo_full_name}") from exc
        parent = ((put or {}).get("commit") or {}).get("sha") or ""
    tree_sha = ""
    for start in range(0, len(files), _TREE_CHUNK):
        entries = []
        for item in files[start : start + _TREE_CHUNK]:
            entry = {"path": item.path, "mode": "100755" if item.executable else "100644", "type": "blob"}
            text = _as_text(item.content)
            if text is not None:
                entry["content"] = text
            else:
                try:
                    _, blob, _ = _http(
                        "POST",
                        f"{base}/repos/{repo}/git/blobs",
                        headers,
                        body={
                            "content": base64.b64encode(item.content).decode("ascii"),
                            "encoding": "base64",
                        },
                        host="GitHub",
                    )
                except _HttpError as exc:
                    raise _translate(exc, f"writing {item.path!r} to {repo_full_name}") from exc
                entry["sha"] = (blob or {})["sha"]
            entries.append(entry)
        # The first request has no base tree, so the commit holds exactly the
        # given files (a host-generated README is replaced, not merged).
        payload: dict[str, Any] = {"tree": entries}
        if tree_sha:
            payload["base_tree"] = tree_sha
        try:
            _, tree, _ = _http("POST", f"{base}/repos/{repo}/git/trees", headers, body=payload, host="GitHub")
        except _HttpError as exc:
            raise _translate(exc, f"writing the tree of {repo_full_name}") from exc
        tree_sha = (tree or {})["sha"]
    commit_body: dict[str, Any] = {"message": message, "tree": tree_sha, "parents": [parent]}
    if author:
        commit_body["author"] = author
    try:
        _, commit, _ = _http(
            "POST", f"{base}/repos/{repo}/git/commits", headers, body=commit_body, host="GitHub"
        )
        sha = (commit or {})["sha"]
        _http(
            "PATCH",
            f"{base}/repos/{repo}/git/refs/heads/{urllib.parse.quote(branch, safe='')}",
            headers,
            body={"sha": sha, "force": False},
            host="GitHub",
        )
    except _HttpError as exc:
        raise _translate(exc, f"committing to {repo_full_name}@{branch}") from exc
    return sha


def _github_head(connection, repo_full_name: str, branch: str) -> tuple[str, str]:
    base, headers = _gh(connection)
    try:
        _, data, _ = _http(
            "GET",
            f"{base}/repos/{_quote_repo(repo_full_name)}/branches/{urllib.parse.quote(branch, safe='')}",
            headers,
            host="GitHub",
        )
    except _HttpError as exc:
        raise _translate(exc, f"reading {repo_full_name}@{branch}") from exc
    commit = (data or {}).get("commit") or {}
    when = ((commit.get("commit") or {}).get("committer") or {}).get("date") or ""
    return commit.get("sha") or "", when


def _github_latest_run(connection, repo_full_name: str, workflow_file: str) -> dict[str, str] | None:
    base, headers = _gh(connection)
    url = (
        f"{base}/repos/{_quote_repo(repo_full_name)}/actions/workflows/"
        f"{urllib.parse.quote(workflow_file, safe='')}/runs?per_page=1"
    )
    try:
        _, data, _ = _http("GET", url, headers, host="GitHub")
    except _HttpError as exc:
        if exc.status == 404:
            return None
        raise _translate(exc, f"reading {workflow_file} runs of {repo_full_name}") from exc
    runs = (data or {}).get("workflow_runs") or []
    if not runs:
        return None
    run = runs[0]
    return {
        "status": str(run.get("status") or ""),
        "conclusion": str(run.get("conclusion") or ""),
        "url": str(run.get("html_url") or ""),
        "created_at": str(run.get("created_at") or ""),
    }


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------


def _gl(connection):
    from astrolift_scm.providers import gitlab

    return gitlab._api_base(connection), {"Authorization": f"Bearer {gitlab._token(connection)}"}


def _gitlab_state(connection, repo_full_name: str) -> RepoState:
    base, headers = _gl(connection)
    project = urllib.parse.quote(repo_full_name, safe="")
    try:
        _, meta, _ = _http("GET", f"{base}/api/v4/projects/{project}", headers, host="GitLab")
    except _HttpError as exc:
        if exc.status == 404:
            return RepoState(exists=False)
        raise _translate(exc, f"reading {repo_full_name}") from exc
    branch = (meta or {}).get("default_branch") or "main"
    if (meta or {}).get("empty_repo"):
        return RepoState(exists=True, empty=True, default_branch=branch)
    ref = urllib.parse.quote(branch, safe="")
    try:
        _, commits, _ = _http(
            "GET", f"{base}/api/v4/projects/{project}/repository/commits?per_page=2&ref_name={ref}", headers
        )
        if len(commits or []) != 1:
            return RepoState(
                exists=True,
                empty=not commits,
                default_branch=branch,
                head_sha=(commits or [{}])[0].get("id", "") if commits else "",
                commit_count=len(commits or []),
            )
        _, tree, _ = _http("GET", f"{base}/api/v4/projects/{project}/repository/tree?ref={ref}", headers)
    except _HttpError as exc:
        raise _translate(exc, f"reading the history of {repo_full_name}") from exc
    files = tuple(e.get("path", "") for e in tree or [])
    return RepoState(
        exists=True,
        empty=_readme_only(files),
        default_branch=branch,
        head_sha=commits[0].get("id", ""),
        commit_count=1,
        files=files,
    )


def _gitlab_create(connection, owner: str, name: str, visibility: str) -> RepoState:
    base, headers = _gl(connection)
    try:
        _, namespace, _ = _http(
            "GET", f"{base}/api/v4/namespaces/{urllib.parse.quote(owner, safe='')}", headers, host="GitLab"
        )
        _http(
            "POST",
            f"{base}/api/v4/projects",
            headers,
            body={
                "name": name,
                "path": name,
                "namespace_id": (namespace or {}).get("id"),
                "visibility": visibility,
                "initialize_with_readme": False,
            },
            host="GitLab",
        )
    except _HttpError as exc:
        raise _translate(exc, f"creating {owner}/{name}") from exc
    return _gitlab_state(connection, f"{owner}/{name}")


def _gitlab_commit(connection, repo_full_name, branch, files, message, parent, author, existing) -> str:
    base, headers = _gl(connection)
    actions = [
        {
            "action": "update" if item.path in existing else "create",
            "file_path": item.path,
            "content": base64.b64encode(item.content).decode("ascii"),
            "encoding": "base64",
            "execute_filemode": item.executable,
        }
        for item in files
    ]
    # The README a host generated goes with the skeleton, which brings its own.
    actions += [
        {"action": "delete", "file_path": path} for path in existing if path not in {f.path for f in files}
    ]
    body: dict[str, Any] = {"branch": branch, "commit_message": message, "actions": actions}
    if author:
        body.update(author_name=author["name"], author_email=author["email"])
    try:
        _, commit, _ = _http(
            "POST",
            f"{base}/api/v4/projects/{urllib.parse.quote(repo_full_name, safe='')}/repository/commits",
            headers,
            body=body,
            timeout=60,
            host="GitLab",
        )
    except _HttpError as exc:
        raise _translate(exc, f"committing to {repo_full_name}@{branch}") from exc
    return (commit or {}).get("id") or ""


# ---------------------------------------------------------------------------
# Gitea
# ---------------------------------------------------------------------------


def _gt(connection):
    from astrolift_scm.providers import gitea

    return gitea._api_base(connection), {"Authorization": f"token {gitea._token(connection)}"}


def _gitea_state(connection, repo_full_name: str) -> RepoState:
    base, headers = _gt(connection)
    repo = _quote_repo(repo_full_name)
    try:
        _, meta, _ = _http("GET", f"{base}/api/v1/repos/{repo}", headers, host="Gitea")
    except _HttpError as exc:
        if exc.status == 404:
            return RepoState(exists=False)
        raise _translate(exc, f"reading {repo_full_name}") from exc
    branch = (meta or {}).get("default_branch") or "main"
    if (meta or {}).get("empty"):
        return RepoState(exists=True, empty=True, default_branch=branch)
    try:
        _, commits, _ = _http(
            "GET",
            f"{base}/api/v1/repos/{repo}/commits?limit=2&sha={urllib.parse.quote(branch, safe='')}",
            headers,
            host="Gitea",
        )
        if len(commits or []) != 1:
            return RepoState(
                exists=True,
                empty=not commits,
                default_branch=branch,
                head_sha=commits[0].get("sha", "") if commits else "",
                commit_count=len(commits or []),
            )
        head = commits[0].get("sha", "")
        _, tree, _ = _http("GET", f"{base}/api/v1/repos/{repo}/git/trees/{head}", headers, host="Gitea")
    except _HttpError as exc:
        raise _translate(exc, f"reading the history of {repo_full_name}") from exc
    entries = (tree or {}).get("tree") or []
    files = tuple(e.get("path", "") for e in entries)
    return RepoState(
        exists=True,
        empty=_readme_only(files),
        default_branch=branch,
        head_sha=head,
        commit_count=1,
        files=files,
        file_shas={e.get("path", ""): e.get("sha", "") for e in entries},
    )


def _gitea_create(connection, owner: str, name: str, visibility: str) -> RepoState:
    if visibility == "internal":
        raise _error("VALIDATION", "Gitea repositories are private or public")
    base, headers = _gt(connection)
    try:
        _, me, _ = _http("GET", f"{base}/api/v1/user", headers, host="Gitea")
        personal = str((me or {}).get("login") or "").lower() == owner.lower()
        url = (
            f"{base}/api/v1/user/repos"
            if personal
            else f"{base}/api/v1/orgs/{urllib.parse.quote(owner, safe='')}/repos"
        )
        _http(
            "POST",
            url,
            headers,
            body={
                "name": name,
                "private": visibility != "public",
                "auto_init": False,
                "default_branch": "main",
            },
            host="Gitea",
        )
    except _HttpError as exc:
        raise _translate(exc, f"creating {owner}/{name}") from exc
    return _gitea_state(connection, f"{owner}/{name}")


def _gitea_commit(connection, repo_full_name, branch, files, message, parent, author, existing) -> str:
    base, headers = _gt(connection)
    changes = []
    for item in files:
        change = {
            "operation": "update" if item.path in existing else "create",
            "path": item.path,
            "content": base64.b64encode(item.content).decode("ascii"),
        }
        if item.path in existing:
            change["sha"] = existing[item.path]
        changes.append(change)
    paths = {f.path for f in files}
    changes += [{"operation": "delete", "path": p, "sha": s} for p, s in existing.items() if p not in paths]
    body: dict[str, Any] = {"branch": branch, "message": message, "files": changes}
    if author:
        body["author"] = author
    try:
        _, result, _ = _http(
            "POST",
            f"{base}/api/v1/repos/{_quote_repo(repo_full_name)}/contents",
            headers,
            body=body,
            timeout=60,
        )
    except _HttpError as exc:
        raise _translate(exc, f"committing to {repo_full_name}@{branch}") from exc
    return ((result or {}).get("commit") or {}).get("sha") or ""


# ---------------------------------------------------------------------------
# Bitbucket
# ---------------------------------------------------------------------------


def _bb(connection):
    from astrolift_scm.providers import bitbucket

    return bitbucket._BASE, {"Authorization": bitbucket._auth_header(connection)}


def _bitbucket_state(connection, repo_full_name: str) -> RepoState:
    base, headers = _bb(connection)
    repo = _quote_repo(repo_full_name)
    try:
        _, meta, _ = _http("GET", f"{base}/2.0/repositories/{repo}", headers, host="Bitbucket")
    except _HttpError as exc:
        if exc.status == 404:
            return RepoState(exists=False)
        raise _translate(exc, f"reading {repo_full_name}") from exc
    branch = ((meta or {}).get("mainbranch") or {}).get("name") or "main"
    try:
        _, page, _ = _http(
            "GET", f"{base}/2.0/repositories/{repo}/commits?pagelen=2", headers, host="Bitbucket"
        )
    except _HttpError as exc:
        if exc.status == 404:
            return RepoState(exists=True, empty=True, default_branch=branch)
        raise _translate(exc, f"listing commits of {repo_full_name}") from exc
    commits = (page or {}).get("values") or []
    if len(commits) != 1:
        return RepoState(
            exists=True,
            empty=not commits,
            default_branch=branch,
            head_sha=commits[0].get("hash", "") if commits else "",
            commit_count=min(len(commits), 2),
        )
    head = commits[0].get("hash", "")
    try:
        _, listing, _ = _http("GET", f"{base}/2.0/repositories/{repo}/src/{head}/", headers, host="Bitbucket")
    except _HttpError as exc:
        raise _translate(exc, f"reading the tree of {repo_full_name}") from exc
    files = tuple(v.get("path", "") for v in (listing or {}).get("values") or [])
    return RepoState(
        exists=True,
        empty=_readme_only(files),
        default_branch=branch,
        head_sha=head,
        commit_count=1,
        files=files,
    )


def _bitbucket_create(connection, owner: str, name: str, visibility: str) -> RepoState:
    if visibility == "internal":
        raise _error("VALIDATION", "Bitbucket repositories are private or public")
    base, headers = _bb(connection)
    try:
        _http(
            "POST",
            f"{base}/2.0/repositories/{_quote_repo(f'{owner}/{name}')}",
            headers,
            body={"scm": "git", "is_private": visibility != "public"},
            host="Bitbucket",
        )
    except _HttpError as exc:
        raise _translate(exc, f"creating {owner}/{name}") from exc
    return _bitbucket_state(connection, f"{owner}/{name}")


def _multipart(fields: list[tuple[str, bytes, bool]], boundary: str) -> bytes:
    """``(name, value, is_file)`` parts; a file part carries its path as the filename."""
    out: list[bytes] = []
    for name, value, is_file in fields:
        disposition = f'form-data; name="{name}"' + (f'; filename="{name}"' if is_file else "")
        out.append(f"--{boundary}\r\nContent-Disposition: {disposition}\r\n\r\n".encode() + value + b"\r\n")
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out)


def _bitbucket_commit(connection, repo_full_name, branch, files, message, parent, author, existing) -> str:
    base, headers = _bb(connection)
    paths = {f.path for f in files}
    fields: list[tuple[str, bytes, bool]] = [(f.path, f.content, True) for f in files]
    fields += [("message", message.encode(), False), ("branch", branch.encode(), False)]
    fields += [("files", p.encode(), False) for p in existing if p not in paths]
    if parent:
        fields.append(("parents", parent.encode(), False))
    if author:
        fields.append(("author", f"{author['name']} <{author['email']}>".encode(), False))
    boundary = os.urandom(12).hex()
    try:
        _, _, resp = _http(
            "POST",
            f"{base}/2.0/repositories/{_quote_repo(repo_full_name)}/src",
            {**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"},
            raw=_multipart(fields, boundary),
            timeout=60,
            host="Bitbucket",
        )
    except _HttpError as exc:
        raise _translate(exc, f"committing to {repo_full_name}@{branch}") from exc
    location = (resp.headers.get("Location") if resp is not None else "") or getattr(resp, "url", "") or ""
    return location.rstrip("/").split("/")[-1]


def _as_text(content: bytes) -> str | None:
    if b"\0" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_STATE = {
    "github": _github_state,
    "gitlab": _gitlab_state,
    "gitea": _gitea_state,
    "bitbucket": _bitbucket_state,
}
_CREATE = {
    "github": _github_create,
    "gitlab": _gitlab_create,
    "gitea": _gitea_create,
    "bitbucket": _bitbucket_create,
}


def repository_state(connection, repo_full_name: str) -> RepoState:
    _split(repo_full_name)
    return _wrap(_STATE[_host(connection)], connection, repo_full_name)


def create_repository(connection, owner: str, name: str, visibility: str) -> RepoState:
    if visibility not in VISIBILITIES:
        raise _error("VALIDATION", f"visibility must be one of {', '.join(VISIBILITIES)}")
    _split(f"{owner}/{name}")
    return _wrap(_CREATE[_host(connection)], connection, owner, name, visibility)


def commit_tree(
    connection,
    repo_full_name: str,
    branch: str,
    files: list[TreeFile],
    message: str,
    *,
    state: RepoState,
    author: dict[str, str] | None = None,
) -> str:
    """Land ``files`` as one commit on ``branch`` of a repository ``state``
    says is empty, replacing a host-generated README. Returns the commit SHA."""
    if not files:
        raise _error("VALIDATION", "nothing to commit")
    if not state.empty:
        raise _error("CONFLICT", f"{repo_full_name} is not empty")
    host = _host(connection)
    if host == "github":
        return _wrap(
            _github_commit, connection, repo_full_name, branch, files, message, state.head_sha, author
        )
    existing = dict(state.file_shas) or {path: "" for path in state.files}
    call = {"gitlab": _gitlab_commit, "gitea": _gitea_commit, "bitbucket": _bitbucket_commit}[host]
    return _wrap(call, connection, repo_full_name, branch, files, message, state.head_sha, author, existing)


def branch_head_detail(connection, repo_full_name: str, branch: str) -> tuple[str, str]:
    """``(sha, committed_at ISO)`` of ``branch``."""
    if _host(connection) != "github":
        raise _error("UNSUPPORTED", "branch detail is read from GitHub only")
    return _wrap(_github_head, connection, repo_full_name, branch)


def latest_workflow_run(connection, repo_full_name: str, workflow_file: str) -> dict[str, str] | None:
    """The newest run of ``workflow_file`` (a file name under
    ``.github/workflows``), or ``None`` when it has none or does not exist."""
    if _host(connection) != "github":
        raise _error("UNSUPPORTED", "workflow runs are read from GitHub only")
    return _wrap(_github_latest_run, connection, repo_full_name, workflow_file)


def _github_update(connection, repo_full_name, branch, files, message, author) -> str:
    base, headers = _gh(connection)
    repo = _quote_repo(repo_full_name)
    try:
        _, ref, _ = _http(
            "GET",
            f"{base}/repos/{repo}/git/ref/heads/{urllib.parse.quote(branch, safe='')}",
            headers,
            host="GitHub",
        )
        parent = (ref or {})["object"]["sha"]
        _, head, _ = _http("GET", f"{base}/repos/{repo}/git/commits/{parent}", headers, host="GitHub")
        entries = [
            {
                "path": f.path,
                "mode": "100755" if f.executable else "100644",
                "type": "blob",
                "content": f.content.decode("utf-8"),
            }
            for f in files
        ]
        _, tree, _ = _http(
            "POST",
            f"{base}/repos/{repo}/git/trees",
            headers,
            body={"base_tree": (head or {})["tree"]["sha"], "tree": entries},
            host="GitHub",
        )
        body: dict[str, Any] = {"message": message, "tree": (tree or {})["sha"], "parents": [parent]}
        if author:
            body["author"] = author
        _, commit, _ = _http("POST", f"{base}/repos/{repo}/git/commits", headers, body=body, host="GitHub")
        sha = (commit or {})["sha"]
        _http(
            "PATCH",
            f"{base}/repos/{repo}/git/refs/heads/{urllib.parse.quote(branch, safe='')}",
            headers,
            body={"sha": sha, "force": False},
            host="GitHub",
        )
    except _HttpError as exc:
        raise _translate(exc, f"committing to {repo_full_name}@{branch}") from exc
    return sha


def _github_update_if_head(connection, repo_full_name, branch, files, message, expected_head) -> str:
    """GitHub's expectedHeadOid is checked atomically with the branch update.

    A REST ref PATCH with force=False alone permits building stale files on
    a newly fetched parent. This mutation also records the credential owner
    as commit author; the original Astrolift requester remains in our audit.
    """
    base, headers = _gh(connection)
    endpoint = (
        base.removesuffix("/api/v3") + "/api/graphql" if base.endswith("/api/v3") else base + "/graphql"
    )
    query = """mutation UpdateInspectedBranch($input: CreateCommitOnBranchInput!) {
      createCommitOnBranch(input: $input) { commit { oid } }
    }"""
    try:
        _, reply, _ = _http(
            "POST",
            endpoint,
            headers,
            body={
                "query": query,
                "variables": {
                    "input": {
                        "branch": {"repositoryNameWithOwner": repo_full_name, "refName": branch},
                        "expectedHeadOid": expected_head,
                        "message": {"headline": message},
                        "fileChanges": {
                            "additions": [
                                {
                                    "path": item.path,
                                    "contents": base64.b64encode(item.content).decode("ascii"),
                                }
                                for item in files
                            ]
                        },
                    }
                },
            },
            host="GitHub",
        )
    except _HttpError as exc:
        code = "AUTH_FAILED" if exc.status in (401, 403) else "PRECONDITION"
        raise _error(
            code, f"The source host did not confirm the inspected branch update ({exc.status})"
        ) from exc
    if not isinstance(reply, dict) or reply.get("errors"):
        # Never expose source-host errors, which may include submitted content.
        raise _error(
            "PRECONDITION", "The source host did not confirm the branch update; inspect it before retrying"
        )
    data = reply.get("data")
    result = data.get("createCommitOnBranch") if isinstance(data, dict) else None
    commit = result.get("commit") if isinstance(result, dict) else None
    sha = commit.get("oid") if isinstance(commit, dict) else None
    if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise _error("PRECONDITION", "The source host did not confirm the inspected branch update")
    return sha


def update_files(
    connection,
    repo_full_name: str,
    branch: str,
    files: list[TreeFile],
    message: str,
    *,
    author: dict[str, str] | None = None,
    expected_head: str | None = None,
) -> str:
    """Change text ``files`` on the head of ``branch`` in one commit (GitHub:
    git data API on the head's tree; GitLab: commits API). Other hosts write
    one file per commit through ``put_file``. The ref update is never forced,
    but does not bind a prior inspection. ``expected_head`` instead requires
    an atomic host-side condition; unsupported hosts refuse before writing.
    GitHub authors conditional commits as the stored credential's owner."""
    if not files:
        raise _error("VALIDATION", "nothing to commit")
    host = _host(connection)
    if expected_head is not None:
        if not isinstance(expected_head, str) or re.fullmatch(r"[0-9a-f]{40}", expected_head) is None:
            raise _error("VALIDATION", "an inspected full commit SHA is required")
        if host != "github":
            raise _error("UNSUPPORTED", "conditional multi-file branch updates are supported on GitHub only")
        if any(item.executable for item in files):
            raise _error("UNSUPPORTED", "conditional updates cannot change executable file modes")
        return _wrap(
            _github_update_if_head, connection, repo_full_name, branch, files, message, expected_head
        )
    if host == "github":
        return _wrap(_github_update, connection, repo_full_name, branch, files, message, author)
    if host == "gitlab":
        existing = {f.path: "" for f in files}  # every file updated is one the brain already has
        return _wrap(_gitlab_commit, connection, repo_full_name, branch, files, message, "", author, existing)
    from astrolift_scm.providers import put_file

    sha = ""
    for item in files:
        sha = put_file(
            connection,
            repo_full_name=repo_full_name,
            path=item.path,
            branch=branch,
            content=item.content.decode("utf-8"),
            commit_message=message,
        ).commit_sha
    return sha
