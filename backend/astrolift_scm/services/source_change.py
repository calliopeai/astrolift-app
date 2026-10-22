"""Checks for pointing an app at another GitHub repository (#2478).

A renamed repository keeps its webhook, workflow file and Actions secrets,
but everything that names it stops matching: the App webhook receiver finds
apps by ``source_repo``, ``pr_webhook`` refuses a delivery from any other
repository, and the CI push role trusts ``repo:<owner>/<name>:*``. Moving an
app to a different repository breaks the same things. So the change is
accepted only for a repository the organization's GitHub App installation
includes, the one autowire will then wire, under the name GitHub lists.
"""

from __future__ import annotations

import re

from astrolift_scm.providers.github import GithubProviderError, github_installation_repo_full_name
from astrolift_scm.services.connection_resolver import (
    PLATFORM_REPO_WRITE,
    ConnectionResolutionError,
    resolve_connection,
)

# One owner segment and one name segment of the characters GitHub allows.
_REPO_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")
_MAX_LENGTH = 255  # RegisteredApp.source_repo


class SourceChangeRefused(Exception):
    """The repository cannot become the app's source. ``code`` is the
    ``ErrorCode`` value the mutation answers with."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def parse_github_repo(value: str) -> str:
    """``value`` as ``owner/name``, or ``SourceChangeRefused`` (VALIDATION)."""
    repo = (value or "").strip()
    segments = repo.split("/")
    if (
        len(repo) > _MAX_LENGTH
        or len(segments) != 2
        or not all(_REPO_SEGMENT.fullmatch(part) and part not in {".", ".."} for part in segments)
    ):
        raise SourceChangeRefused(
            "VALIDATION",
            f"sourceRepo must be a GitHub repository written as owner/name, not {repo!r}",
        )
    return repo


def installed_repository(organization_id: int, repo: str) -> str:
    """GitHub's spelling of ``repo`` when the GitHub App installation that
    serves it includes it, or ``SourceChangeRefused`` (PRECONDITION).

    The installation is picked the way the CI secrets push picks it, so a
    repository accepted here is one the wiring can write to.
    """
    try:
        connection = resolve_connection(
            organization_id, purpose=PLATFORM_REPO_WRITE, source_kind="github", repo=repo
        )
    except ConnectionResolutionError as exc:
        raise SourceChangeRefused("PRECONDITION", exc.message) from exc
    try:
        listed = github_installation_repo_full_name(connection, repo_full_name=repo)
    except GithubProviderError as exc:
        raise SourceChangeRefused(
            "PRECONDITION",
            f"couldn't read the repositories of the GitHub App installation on "
            f"{connection.account_login or 'this organization'}: {exc.message}",
        ) from exc
    if listed is None:
        raise SourceChangeRefused(
            "PRECONDITION",
            f"{repo} is not in the GitHub App installation on "
            f"{connection.account_login or 'this organization'}. Give the App access to the "
            "repository on GitHub, then retry.",
        )
    return listed
