"""
App discovery + claiming policy (#133).

Pure-Python policy. The CLI ``astro app discover`` /
``astro app claim <slug>`` and the matching GraphQL mutations
both consult this module for the eligibility rules.

Two flows:

* **Discovery** — given a user's accessible SCM repos (returned
  by the SCM provider's ``list_repos`` driver), filter the
  platform's registered apps to those whose source repo the
  user has access to and that are unowned or in the org but
  not bound to a team the user belongs to. UI surfaces these
  as 'apps you could claim'.
* **Claim** — verify the user has SCM repo access AND the app
  is in a claimable state, then assign ownership. Already-owned
  apps require a transfer flow (separate ticket); this module
  rejects mid-flow when the state changes underneath.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from enum import StrEnum


class ClaimableState(StrEnum):
    """Why an app appears in the discovery list."""

    UNOWNED = "unowned"
    """App registered by CI or org admin; no team binding yet."""

    ORG_ONLY = "org_only"
    """App bound to the org but no team. User can claim into a
    team they belong to."""

    NOT_CLAIMABLE = "not_claimable"
    """App is bound to a team. User would need a transfer flow."""


@dataclasses.dataclass(frozen=True, slots=True)
class AppDiscoveryRow:
    """Minimum projection per registered app."""

    app_id: int
    app_slug: str
    org_id: int
    repo_url: str
    """The SCM repo URL (e.g. https://github.com/acme/api).
    User-access check happens against this."""

    bound_team_id: int | None
    bound_user_id: int | None


@dataclasses.dataclass(frozen=True, slots=True)
class UserContext:
    """The requesting user's context the policy checks against."""

    user_id: int
    accessible_repo_urls: frozenset[str]
    """Set of repo URLs the user has read+write access to per
    the SCM provider. Driver-supplied; we don't independently
    verify here."""

    org_memberships: frozenset[int]
    """Org IDs the user belongs to."""

    team_memberships: frozenset[int]


def claimable_state_of(
    *,
    app: AppDiscoveryRow,
    user: UserContext,
) -> ClaimableState:
    """Classify an app for one user."""
    if app.bound_team_id is not None:
        # Bound to a team. Not claimable via this flow even if the
        # user is on the team — claiming is for *taking ownership*,
        # not joining membership. (Already-on-team users should
        # see the app via normal listing, not discovery.)
        return ClaimableState.NOT_CLAIMABLE

    if app.repo_url not in user.accessible_repo_urls:
        # Without SCM access the user can't deploy anyway. Not
        # claimable from their perspective.
        return ClaimableState.NOT_CLAIMABLE

    if app.bound_user_id is None and app.org_id not in user.org_memberships:
        # Truly unowned — but the user isn't even in the org.
        # They'd need an invitation first.
        return ClaimableState.NOT_CLAIMABLE

    if app.bound_user_id is None:
        return ClaimableState.UNOWNED

    if app.bound_user_id == user.user_id:
        # Already owned by the user — not 'claimable', already
        # theirs.
        return ClaimableState.NOT_CLAIMABLE

    # Owned by another user in the same org. Treat as ORG_ONLY:
    # claim flow can take ownership when the source-of-truth
    # rule is 'whoever has SCM repo access wins'.
    return ClaimableState.ORG_ONLY


def discoverable_apps(
    *,
    apps: Sequence[AppDiscoveryRow],
    user: UserContext,
) -> tuple[AppDiscoveryRow, ...]:
    """Filter ``apps`` to those the user could claim. UI uses
    this to render the discovery list."""
    return tuple(a for a in apps if claimable_state_of(app=a, user=user) != ClaimableState.NOT_CLAIMABLE)


# ---- claim transition ----------------------------------------------


class ClaimError(ValueError):
    pass


@dataclasses.dataclass(frozen=True, slots=True)
class ClaimDecision:
    """Output of ``can_claim``: either an authorized claim with
    the new owner, or a structured rejection."""

    accepted: bool
    new_owner_user_id: int | None
    reason: str


def can_claim(
    *,
    app: AppDiscoveryRow,
    user: UserContext,
) -> ClaimDecision:
    """Decide whether ``user`` can take ownership of ``app``.

    Re-checks the discoverable_apps rules at claim time — apps
    can transition between snapshots (someone else just claimed,
    user got removed from org, etc.). Catches races at the
    decision boundary.
    """
    state = claimable_state_of(app=app, user=user)
    if state == ClaimableState.NOT_CLAIMABLE:
        # Surface a specific reason the UI/CLI can render.
        if app.bound_team_id is not None:
            reason = "app is bound to a team; use transfer flow"
        elif app.repo_url not in user.accessible_repo_urls:
            reason = "user lacks SCM access to the source repo"
        elif app.bound_user_id == user.user_id:
            reason = "user already owns this app"
        elif app.org_id not in user.org_memberships:
            reason = "user is not a member of the app's org"
        else:
            reason = "app not claimable in current state"
        return ClaimDecision(
            accepted=False,
            new_owner_user_id=None,
            reason=reason,
        )
    return ClaimDecision(
        accepted=True,
        new_owner_user_id=user.user_id,
        reason=f"claimed via {state.value}",
    )


def assert_can_claim(*, app: AppDiscoveryRow, user: UserContext) -> int:
    """Raise on failure; return the new owner_user_id on success.
    Convenience for the GraphQL mutation that wants exception-on-deny
    rather than checking a bool."""
    decision = can_claim(app=app, user=user)
    if not decision.accepted:
        raise ClaimError(decision.reason)
    assert decision.new_owner_user_id is not None
    return decision.new_owner_user_id
