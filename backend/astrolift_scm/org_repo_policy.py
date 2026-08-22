"""Whether a source repo is allowed to be an app's source (#1543).

Personal repos ride personal OAuth connections, whose permission
semantics differ from the org GitHub App (#1542): the grant follows a
person rather than the organization, and it leaves with them. Some
installs want that as a hard boundary rather than a case-by-case
failure discovered at deploy time.

`SourceConnection.repo_visibility_scopes` already expresses a per
connection preference, and the four providers already honour it when
listing. It is not a policy boundary, for two reasons: it is empty by
default, and `list_github_repos` skips the filter entirely when the
scopes are empty or the connection is a GitHub App install. So the
default is that everything the token can see is offered.

This is the boundary on top of that. When
`RESTRICT_SOURCE_REPOS_TO_ORG` is on, a repo whose owner is not one of
the org logins this organization has connected is refused — at
registration, and filtered out of the picker so it is never offered in
the first place. Default off, which is the behaviour that exists today.
"""

from __future__ import annotations

from urllib.parse import urlparse

# Kinds whose account_login names a GitHub/GitLab organization rather
# than a person. A personal OAuth row's login is the user, and treating
# it as an allowed owner would defeat the whole setting.
ORG_OWNED_KINDS = frozenset(
    {
        "github_app_install",
        "github_oauth_app",
        "github_pat",
        "gitlab_oauth_app",
        "gitlab_pat",
        "bitbucket_oauth_app",
        "bitbucket_pat",
        "gitea_oauth_app",
        "gitea_pat",
    }
)


def restriction_enabled() -> bool:
    """Is the org-only policy switched on for this install?

    Read through a function rather than at import, because constance
    values are database-backed and an operator flipping the toggle has
    to take effect without a restart.
    """
    try:
        from constance import config

        return bool(config.RESTRICT_SOURCE_REPOS_TO_ORG)
    except Exception:  # noqa: BLE001 — an unreadable setting must not deny work
        return False


def owner_of(source_repo: str) -> str:
    """The owner segment of a repo reference, lower-cased.

    Accepts what the codebase actually stores in `source_repo`, which is
    `owner/name` in some places and a clone URL in others. Returns ""
    when no owner can be read, which callers treat as "cannot judge"
    rather than as a violation.
    """
    raw = (source_repo or "").strip()
    if not raw:
        return ""
    if "://" in raw or raw.startswith("git@"):
        if raw.startswith("git@"):
            raw = raw.partition(":")[2]
        else:
            raw = urlparse(raw).path
    raw = raw.strip("/")
    if raw.endswith(".git"):
        raw = raw[: -len(".git")]
    parts = [p for p in raw.split("/") if p]
    # owner/name, or a longer GitLab path where the first segment is the
    # group that owns everything below it.
    return parts[0].lower() if len(parts) >= 2 else ""


def allowed_owners(organization) -> set[str]:
    """The org logins this organization has connected."""
    from astrolift_scm.models import SourceConnection

    return {
        login
        for login in SourceConnection.objects.filter(
            organization=organization,
            kind__in=ORG_OWNED_KINDS,
            deleted_at__isnull=True,
        ).values_list("account_login", flat=True)
        if (login or "").strip()
        for login in [login.strip().lower()]
    }


def rejection_reason(source_repo: str, organization) -> str | None:
    """Why this repo may not be an app source, or None if it may.

    None when the policy is off, when the reference carries no readable
    owner, or when the owner is one of the connected orgs.
    """
    if not restriction_enabled():
        return None
    owner = owner_of(source_repo)
    if not owner:
        # An unparseable reference is a separate validation concern.
        # Refusing it here would report a policy violation for what is
        # really a malformed input, and name the wrong fix.
        return None
    owners = allowed_owners(organization)
    if owner in owners:
        return None
    if not owners:
        # The policy is on and nothing is connected, so nothing can pass.
        # Saying "not owned by X" with no X reads as a bug.
        return (
            "This install only accepts repositories owned by a connected organization, "
            "and no organization source connection exists yet. Connect one under "
            "Settings > Source connections."
        )
    return (
        f"{source_repo!r} is owned by {owner!r}, which is not a connected organization. "
        f"This install only accepts repositories owned by: {', '.join(sorted(owners))}."
    )


def filter_repos(repos, organization):
    """Drop repos the policy would refuse, so they are never offered.

    A picker that lists a repo registration will reject is worse than
    one that does not list it: the operator gets as far as choosing
    before being told no.
    """
    if not restriction_enabled():
        return list(repos)
    owners = allowed_owners(organization)
    return [r for r in repos if owner_of(getattr(r, "full_name", "")) in owners]
