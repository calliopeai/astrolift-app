"""Single source of truth for 'which SourceConnection authenticates
this operation'.

Before this module existed, near-identical pickers each answered that
question their own way, and disagreed on the one axis that matters:
*who* the operation acts as. The defect that forced the split: CI-secrets
push resolved the **requesting viewer's personal OAuth token** and used
it for an autonomous platform write — a human's token that silently
expired and 401'd. That is the one thing that must never happen.

The invariant this module guarantees:

    No VIEWER-scoped personal token is ever used for an autonomous
    platform write.

*Purpose* is the explicit axis, not an accident of which picker a call
site copied. There are three purposes:

  - ``PLATFORM_REPO_WRITE`` — the platform pushing **secrets** into a
    repo (CI-secrets push/validate). On GitHub this is the org **App
    installation** and nothing else: App tokens are org-registered,
    scoped per-installation, and don't carry a human's expiry. A user
    OAuth token or PAT must never authenticate a secrets write; if the
    org hasn't installed the App this HARD-FAILS with PRECONDITION.

  - ``ORG_REPO_WRITE`` — an autonomous platform repo op that runs under
    an **org-level** connection: webhook install, workflow-file write,
    workflow dispatch, manifest read/write-back. Ranked
    ``github_app_install`` > ``github_oauth_user`` > ``github_pat`` — the
    App is preferred, but an App-less org that onboarded with an
    org-level OAuth-user or PAT connection still works (that fallback is
    the whole point). This is NOT the requesting viewer's token: the
    selection is org-scoped and never filters by the caller's user id,
    so an org connection — not the human who clicked — authenticates the
    write. The invariant holds because the picked row is an org
    connection, not a viewer-scoped one.

  - ``USER_REPO_DISCOVERY`` — a signed-in human browsing the repos
    *they* can see during onboarding. This is the per-user
    ``github_oauth_user`` token, filtered to the requesting viewer, and
    only that; it never authenticates a platform write.

The GitHub split between ``PLATFORM_REPO_WRITE`` (App-only) and
``ORG_REPO_WRITE`` (App > oauth-user > pat) is what keeps secrets on the
non-expiring org App while letting the broader repo ops fall back to an
org's existing OAuth/PAT connection. GitLab / Bitbucket / Gitea have no
App-installation analogue, so for both write purposes they retain their
historical OAuth-user > PAT preference — for those hosts the two
purposes resolve identically.
"""

from __future__ import annotations

import enum

from astrolift_scm.models import SourceConnection


class ConnectionPurpose(enum.Enum):
    """Why a connection is being resolved — the identity axis."""

    PLATFORM_REPO_WRITE = "platform_repo_write"
    ORG_REPO_WRITE = "org_repo_write"
    USER_REPO_DISCOVERY = "user_repo_discovery"


# Module-level aliases so call sites read
# ``resolve_connection(..., purpose=PLATFORM_REPO_WRITE)``.
PLATFORM_REPO_WRITE = ConnectionPurpose.PLATFORM_REPO_WRITE
ORG_REPO_WRITE = ConnectionPurpose.ORG_REPO_WRITE
USER_REPO_DISCOVERY = ConnectionPurpose.USER_REPO_DISCOVERY


class ConnectionResolutionError(Exception):
    """No connection satisfies the requested purpose.

    ``code`` is a stable short token the caller maps onto its own
    envelope (every current caller surfaces PRECONDITION):

      - ``PRECONDITION``: no usable connection exists for this purpose
        (e.g. the org hasn't installed the GitHub App, or the viewer
        hasn't connected their personal GitHub account).
      - ``UNSUPPORTED_SOURCE``: the source host has no policy for this
        purpose (e.g. user discovery on a non-GitHub host).
      - ``INVALID_PURPOSE``: programmer error — unknown purpose value.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# For PLATFORM_REPO_WRITE: which connection kinds may authenticate an
# autonomous platform repo operation, in preference order, per host.
# GitHub is App-install ONLY (the whole point of the separation).
# The other hosts have no App-install analogue, so they keep their
# OAuth-user > PAT preference (documented exception).
_PLATFORM_WRITE_KINDS: dict[str, tuple[str, ...]] = {
    "github": ("github_app_install",),
    "gitlab": ("gitlab_oauth_user", "gitlab_pat"),
    "bitbucket": ("bitbucket_oauth_user", "bitbucket_pat"),
    "gitea": ("gitea_oauth_user", "gitea_pat"),
}

# For ORG_REPO_WRITE: which connection kinds may authenticate an
# autonomous *org-level* repo op (webhook install, workflow write/dispatch,
# manifest read), in preference order, per host. Unlike PLATFORM_REPO_WRITE
# (secrets, App-only on GitHub), GitHub here keeps the App > OAuth-user > PAT
# fallback so an App-less org that onboarded via an org OAuth/PAT connection
# still works. The selection is org-scoped, never filtered to the requesting
# viewer, so the picked row is an org connection — not a viewer's personal
# token. On the non-GitHub hosts this is identical to PLATFORM_REPO_WRITE.
_ORG_WRITE_KINDS: dict[str, tuple[str, ...]] = {
    "github": ("github_app_install", "github_oauth_user", "github_pat"),
    "gitlab": ("gitlab_oauth_user", "gitlab_pat"),
    "bitbucket": ("bitbucket_oauth_user", "bitbucket_pat"),
    "gitea": ("gitea_oauth_user", "gitea_pat"),
}

_HOST_LABEL: dict[str, str] = {
    "github": "GitHub",
    "gitlab": "GitLab",
    "bitbucket": "Bitbucket",
    "gitea": "Gitea",
}


def _org_id(organization) -> int:
    """Accept either an Organization instance or a bare id."""
    return getattr(organization, "pk", organization)


def resolve_connection(
    organization,
    *,
    purpose: ConnectionPurpose,
    source_kind: str = "github",
    user_id: int | None = None,
    repo: str | None = None,
) -> SourceConnection:
    """Return the one SourceConnection that authenticates ``purpose`` for
    ``organization``, or raise ``ConnectionResolutionError``.

    ``repo`` (``owner/name`` or a clone URL) picks among several GitHub App
    installations: each covers one GitHub account, so only the one on the
    repo's owner can reach it (#2297). See :func:`covering_repo_owner`.
    """
    if purpose is PLATFORM_REPO_WRITE:
        return _resolve_org_ranked(
            _org_id(organization),
            source_kind=source_kind,
            kinds_map=_PLATFORM_WRITE_KINDS,
            policy="platform-write",
            no_conn_message=_no_platform_conn_message(source_kind),
            repo=repo,
        )
    if purpose is ORG_REPO_WRITE:
        return _resolve_org_ranked(
            _org_id(organization),
            source_kind=source_kind,
            kinds_map=_ORG_WRITE_KINDS,
            policy="org-write",
            no_conn_message=_no_org_conn_message(source_kind),
            repo=repo,
        )
    if purpose is USER_REPO_DISCOVERY:
        return _resolve_user_discovery(_org_id(organization), source_kind=source_kind, user_id=user_id)
    raise ConnectionResolutionError("INVALID_PURPOSE", f"unknown connection purpose {purpose!r}")


def _resolve_org_ranked(
    org_id,
    *,
    source_kind: str,
    kinds_map: dict[str, tuple[str, ...]],
    policy: str,
    no_conn_message: str,
    repo: str | None = None,
) -> SourceConnection:
    """Pick the highest-ranked active org connection for ``source_kind``.

    Org-scoped by construction — the query filters on ``organization_id``
    and connection kind, never on a requesting viewer's ``user_id``, so
    the row returned is an org connection, not a viewer-scoped personal
    token. Shared by ``PLATFORM_REPO_WRITE`` (App-only kinds on GitHub)
    and ``ORG_REPO_WRITE`` (App > oauth-user > pat on GitHub); the
    kinds map is the only difference between them.
    """
    accepted = kinds_map.get(source_kind, ())
    if not accepted:
        raise ConnectionResolutionError(
            "UNSUPPORTED_SOURCE",
            f"no {policy} connection policy for source_kind={source_kind!r}",
        )
    rows = list(
        SourceConnection.objects.filter(
            organization_id=org_id,
            kind__in=accepted,
            is_active=True,
            is_orphaned=False,
            deleted_at__isnull=True,
        )
    )
    if not rows:
        raise ConnectionResolutionError("PRECONDITION", no_conn_message)
    rows = covering_repo_owner(rows, repo)
    if not rows:
        raise ConnectionResolutionError("PRECONDITION", _no_owner_conn_message(repo))
    # Preference order first (App-install wins on GitHub), then oldest
    # pk so repeated resolutions against the same org are stable.
    rank = {k: i for i, k in enumerate(accepted)}
    rows.sort(key=lambda r: (rank.get(r.kind, len(accepted)), r.pk))
    return rows[0]


def _repo_owner(repo: str | None) -> str:
    """The lower-cased owner of ``owner/name``, a clone URL or an SSH remote;
    "" when there is no owner segment."""
    parts = [p for p in (repo or "").strip().replace(":", "/").split("/") if p]
    return parts[-2].lower() if len(parts) >= 2 else ""


def covering_repo_owner(rows: list[SourceConnection], repo: str | None) -> list[SourceConnection]:
    """Drop the GitHub App installations that cannot reach ``repo`` (#2297).

    An installation covers one GitHub account, so when an org holds several
    only the one whose ``account_login`` is the repo's owner can read it.
    Other connection kinds pass through untouched. A lone installation is
    kept whatever its login, as before: rows from the manifest flow record
    the App's owner rather than the account it was installed on, so a
    mismatch there proves nothing.
    """
    owner = _repo_owner(repo)
    install = SourceConnection.Kind.GITHUB_APP_INSTALL
    if not owner or sum(1 for r in rows if r.kind == install) < 2:
        return rows
    return [r for r in rows if r.kind != install or (r.account_login or "").lower() == owner]


def _resolve_user_discovery(org_id, *, source_kind: str, user_id: int | None) -> SourceConnection:
    if source_kind != "github":
        raise ConnectionResolutionError(
            "UNSUPPORTED_SOURCE",
            f"user repo discovery is GitHub-only today; source_kind={source_kind!r}",
        )
    if not user_id:
        raise ConnectionResolutionError("PRECONDITION", "no viewer identity supplied for repo discovery")
    # Newest-updated first (a re-auth refreshes updated_at), then newest
    # pk — unchanged from the historical personal-connection picker.
    conn = (
        SourceConnection.objects.filter(
            organization_id=org_id,
            user_id=user_id,
            kind=SourceConnection.Kind.GITHUB_OAUTH_USER,
            is_active=True,
            is_orphaned=False,
            deleted_at__isnull=True,
        )
        .order_by("-updated_at", "-pk")
        .first()
    )
    if conn is None:
        raise ConnectionResolutionError(
            "PRECONDITION",
            (
                "No personal GitHub OAuth connection found in this org. "
                "Connect GitHub in your account drawer and retry."
            ),
        )
    return conn


def _no_platform_conn_message(source_kind: str) -> str:
    if source_kind == "github":
        return (
            "organization GitHub App is not installed — an org admin must "
            "register GitHub (Settings → Connections) before the platform "
            "can write to repos."
        )
    label = _HOST_LABEL.get(source_kind, source_kind)
    return f"no active {label} connection for this organization; connect {label} and retry."


def _no_owner_conn_message(repo: str | None) -> str:
    owner = _repo_owner(repo)
    return (
        f"no GitHub App installation in this organization covers {owner!r}; "
        f"install the App on {owner!r} under Settings → Connections and retry."
    )


def _no_org_conn_message(source_kind: str) -> str:
    label = _HOST_LABEL.get(source_kind, source_kind)
    return (
        f"no active {label} connection for this organization — connect "
        f"{label} (App, OAuth, or PAT) under Settings → Connections and retry."
    )
