"""Pointing an app at another GitHub repository (#2478).

A renamed repository keeps its webhook, workflow file and Actions secrets,
but everything that names it stops matching: the App webhook receiver finds
apps by ``source_repo``, ``pr_webhook`` refuses a delivery from any other
repository, and the CI push role trusts ``repo:<owner>/<name>:*``. Moving an
app to a different repository breaks the same things. So the change is
accepted only for a repository the organization's GitHub App installation
includes, the one autowire will then wire, under the name GitHub lists.

``setAppSource`` and the App receiver's ``repository`` ``renamed`` delivery
both make the change through ``change_source``.
"""

from __future__ import annotations

import logging
import re
import urllib.parse

from django.db import IntegrityError, transaction

from astrolift_registry.models import RegisteredApp
from astrolift_scm.org_repo_policy import rejection_reason
from astrolift_scm.providers.github import GithubProviderError, github_installation_repo_full_name
from astrolift_scm.services.autowire import run_autowire
from astrolift_scm.services.connection_resolver import (
    PLATFORM_REPO_WRITE,
    ConnectionResolutionError,
    resolve_connection,
)

logger = logging.getLogger(__name__)

# One owner segment and one name segment of the characters GitHub allows.
_REPO_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")
_MAX_LENGTH = 255  # RegisteredApp.source_repo

# Wiring a repository for an app on its way out would put back the CI files
# teardown removes (#2480).
TORN_DOWN = frozenset(
    {
        RegisteredApp.ProvisioningStatus.TEARING_DOWN.value,
        RegisteredApp.ProvisioningStatus.DEREGISTERED.value,
    }
)
_ALREADY_REGISTERED = "another app in this organization is registered from this repository and manifest path"


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


def _followed_source_url(source_url: str, previous_repo: str, repo: str) -> str:
    """``source_url`` re-pointed at ``repo`` when it named ``previous_repo``;
    any other URL, or none, is left as the operator set it."""
    parts = urllib.parse.urlsplit(source_url or "")
    path = parts.path.strip("/").removesuffix(".git")
    if not parts.netloc or path.casefold() != previous_repo.casefold():
        return source_url
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, f"/{repo}", "", ""))


def change_source(app: RegisteredApp, source_repo: str, *, actor=None) -> bool:
    """Point ``app`` at ``source_repo`` and wire it there. True when the app
    moved, False when it names that repository already. A refused change
    raises ``SourceChangeRefused`` and writes nothing.

    The caller has decided the app may change: a GitHub app, not being torn
    down. An autowire failure does not undo the change; the app's autowire
    status says what is unwired.
    """
    previous = app.source_repo
    repo = parse_github_repo(source_repo)
    if repo == previous:
        return False
    refusal = rejection_reason(repo, app.organization)
    if refusal:
        raise SourceChangeRefused("VALIDATION", refusal)
    # GitHub names ignore case; the other app may belong to a team the
    # caller cannot see, so it is not named.
    if (
        RegisteredApp.objects.filter(
            organization_id=app.organization_id,
            source_repo__iexact=repo,
            manifest_path=app.manifest_path,
            deleted_at__isnull=True,
        )
        .exclude(pk=app.pk)
        .exists()
    ):
        raise SourceChangeRefused("CONFLICT", _ALREADY_REGISTERED)
    repo = installed_repository(app.organization_id, repo)
    if repo == previous:
        return False

    from workflows.repo_sync import move_repository_workflows

    app.source_repo = repo
    app.source_url = _followed_source_url(app.source_url, previous, repo)
    # The verdicts were about the old repository's wiring.
    app.autowire_state = {}
    try:
        with transaction.atomic():
            app.save(update_fields=["source_repo", "source_url", "autowire_state", "updated_at", "version"])
            # The workflows the repository declares are keyed on its name;
            # the next sync under the new one would refuse them.
            move_repository_workflows(organization_id=app.organization_id, from_repo=previous, to_repo=repo)
    except IntegrityError as exc:
        raise SourceChangeRefused("CONFLICT", _ALREADY_REGISTERED) from exc
    try:
        run_autowire(app, actor=actor)
    except Exception:  # noqa: BLE001 - the change stands; the autowire status says what is unwired
        logger.exception("change_source: autowire failed for %s", app.slug)
    return True
