"""
SCM provider drivers — call the host's API with a stored credential.

Each driver implements the same shape:

    list_repos(connection: SourceConnection, *, search: str | None) -> list[RemoteRepo]
    fetch_file(connection, *, repo_full_name, path, ref) -> str | None

The dispatcher in this package picks a driver by ``connection.kind``.
Failures (auth, rate-limit, network) become structured errors the
GraphQL resolver can translate to a clean MutationResult so the UI
shows a friendly "reconnect" affordance instead of a 500.

Today: GitHub via PAT, OAuth user token, or App-installation token,
and GitLab via PAT or OAuth user token. Bitbucket and Gitea slot in
alongside as the same shape.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable

from astrolift_scm.models import SourceConnection
from astrolift_scm.providers.github import (
    GithubProviderError,
    delete_github_webhook,
    fetch_github_file,
    install_github_webhook,
    list_github_repos,
    put_github_file,
)
from astrolift_scm.providers.gitlab import (
    GitlabProviderError,
    fetch_gitlab_file,
    install_gitlab_webhook,
    list_gitlab_projects,
    put_gitlab_file,
)


@dataclasses.dataclass(frozen=True, slots=True)
class RemoteRepo:
    """One row of a SCM-host's repo list, normalized across hosts."""

    full_name: str  # owner/name
    name: str
    description: str
    default_branch: str
    visibility: str  # "public" | "private" | "internal" | "unknown"
    clone_url_https: str
    clone_url_ssh: str
    web_url: str
    is_archived: bool
    is_fork: bool
    pushed_at: str | None  # ISO 8601


class ProviderError(Exception):
    """Translates to a clean GraphQL error envelope at the resolver."""

    def __init__(self, code: str, message: str, *, recoverable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.recoverable = recoverable


_GITHUB_KINDS = {
    "github_pat",
    "github_oauth_user",
    "github_app_install",
    "github_oauth_app",
}
_GITLAB_KINDS = {
    "gitlab_pat",
    "gitlab_oauth_user",
    "gitlab_oauth_app",
}


def list_repos(
    connection: SourceConnection,
    *,
    search: str | None = None,
    limit: int = 100,
) -> Iterable[RemoteRepo]:
    """Dispatch a list_repos call to the right driver for ``connection.kind``."""
    if connection.kind in _GITHUB_KINDS:
        try:
            return list_github_repos(connection, search=search, limit=limit)
        except GithubProviderError as exc:
            raise ProviderError(exc.code, exc.message, recoverable=exc.recoverable) from exc

    if connection.kind in _GITLAB_KINDS:
        try:
            return list_gitlab_projects(connection, search=search, limit=limit)
        except GitlabProviderError as exc:
            raise ProviderError(exc.code, exc.message, recoverable=exc.recoverable) from exc

    raise ProviderError(
        "UNSUPPORTED",
        f"repo listing for {connection.kind!r} not implemented yet",
    )


def fetch_file(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    path: str,
    ref: str,
) -> str | None:
    """Fetch a file from a remote repo at ``ref`` through a stored
    connection. Returns None when the file doesn't exist; raises
    ProviderError on auth/network failures."""
    if connection.kind in _GITHUB_KINDS:
        try:
            return fetch_github_file(
                connection,
                repo_full_name=repo_full_name,
                path=path,
                ref=ref,
            )
        except GithubProviderError as exc:
            raise ProviderError(exc.code, exc.message, recoverable=exc.recoverable) from exc

    if connection.kind in _GITLAB_KINDS:
        try:
            return fetch_gitlab_file(
                connection,
                repo_full_name=repo_full_name,
                path=path,
                ref=ref,
            )
        except GitlabProviderError as exc:
            raise ProviderError(exc.code, exc.message, recoverable=exc.recoverable) from exc

    raise ProviderError(
        "UNSUPPORTED",
        f"file fetch for {connection.kind!r} not implemented yet",
    )


@dataclasses.dataclass(frozen=True, slots=True)
class PutFileResult:
    """Outcome of writing a file to a remote repo through a connection.

    ``commit_sha`` is the SHA of the commit that landed the change;
    ``web_url`` is the host-side URL the operator can open to see the
    file. ``file_path`` echoes the path the host actually wrote (which
    may differ from the requested one if the host normalizes it)."""

    commit_sha: str
    file_path: str
    web_url: str


def put_file(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    path: str,
    branch: str,
    content: str,
    commit_message: str,
) -> PutFileResult:
    """Create or update ``path`` on ``branch`` of ``repo_full_name``.

    Auth follows the connection's stored credential — user-bearer
    tokens (PAT / OAuth-user) commit-attribute to the underlying user
    on the SCM host; App-installation tokens attribute to the App's
    bot identity. Existing files are updated in-place (the GitHub
    driver looks up the blob SHA first, GitLab picks PUT vs POST)."""
    if connection.kind in _GITHUB_KINDS:
        try:
            result = put_github_file(
                connection,
                repo_full_name=repo_full_name,
                path=path,
                branch=branch,
                content=content,
                commit_message=commit_message,
            )
            return PutFileResult(
                commit_sha=result.commit_sha,
                file_path=result.file_path,
                web_url=result.web_url,
            )
        except GithubProviderError as exc:
            raise ProviderError(exc.code, exc.message, recoverable=exc.recoverable) from exc

    if connection.kind in _GITLAB_KINDS:
        try:
            result = put_gitlab_file(
                connection,
                repo_full_name=repo_full_name,
                path=path,
                branch=branch,
                content=content,
                commit_message=commit_message,
            )
            return PutFileResult(
                commit_sha=result.commit_sha,
                file_path=result.file_path,
                web_url=result.web_url,
            )
        except GitlabProviderError as exc:
            raise ProviderError(exc.code, exc.message, recoverable=exc.recoverable) from exc

    raise ProviderError(
        "UNSUPPORTED",
        f"file write for {connection.kind!r} not implemented yet",
    )


@dataclasses.dataclass(frozen=True, slots=True)
class InstallWebhookResult:
    """Outcome of installing a webhook on a SCM host. ``hook_id`` is
    the host-side identifier so we can later rotate or delete it."""

    hook_id: str
    webhook_url: str


def install_webhook(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    target_url: str,
    secret: str,
) -> InstallWebhookResult:
    """Install a webhook on the host repo through ``connection``.

    GitHub-App-install connections raise ProviderError("APP_INSTALLED")
    so the resolver can short-circuit (those connections already
    receive deliveries via the App's own webhook). The caller should
    surface that case to the UI as "already installed via App" — no
    actual hook is created.
    """
    if connection.kind == "github_app_install":
        raise ProviderError(
            "APP_INSTALLED",
            "github_app_install connections already deliver webhooks via the GitHub App; "
            "no per-repo install is required",
            recoverable=True,
        )

    if connection.kind in _GITHUB_KINDS:
        try:
            result = install_github_webhook(
                connection,
                repo_full_name=repo_full_name,
                target_url=target_url,
                secret=secret,
            )
            return InstallWebhookResult(hook_id=result.hook_id, webhook_url=result.webhook_url)
        except GithubProviderError as exc:
            raise ProviderError(exc.code, exc.message, recoverable=exc.recoverable) from exc

    if connection.kind in _GITLAB_KINDS:
        try:
            result = install_gitlab_webhook(
                connection,
                repo_full_name=repo_full_name,
                target_url=target_url,
                secret=secret,
            )
            return InstallWebhookResult(hook_id=result.hook_id, webhook_url=result.webhook_url)
        except GitlabProviderError as exc:
            raise ProviderError(exc.code, exc.message, recoverable=exc.recoverable) from exc

    raise ProviderError(
        "UNSUPPORTED",
        f"webhook install for {connection.kind!r} not implemented yet",
    )


def delete_webhook(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    hook_id: str,
) -> None:
    """Delete a webhook on the host repo through ``connection``.

    Idempotent: a missing hook (404) is treated as the desired end
    state. GitHub-App-install connections raise UNSUPPORTED — those
    never install a per-repo hook in the first place, so there is
    nothing to delete; the caller treats the case as a clean no-op.
    """
    if connection.kind in _GITHUB_KINDS:
        try:
            delete_github_webhook(
                connection,
                repo_full_name=repo_full_name,
                hook_id=hook_id,
            )
            return
        except GithubProviderError as exc:
            raise ProviderError(exc.code, exc.message, recoverable=exc.recoverable) from exc

    raise ProviderError(
        "UNSUPPORTED",
        f"webhook delete for {connection.kind!r} not implemented yet",
    )
