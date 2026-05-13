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
    fetch_github_file,
    list_github_repos,
)
from astrolift_scm.providers.gitlab import (
    GitlabProviderError,
    fetch_gitlab_file,
    list_gitlab_projects,
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
