"""Tests for app discovery + claiming policy (#133)."""

from __future__ import annotations

import pytest

from astrolift_registry.app_claiming import (
    AppDiscoveryRow,
    ClaimableState,
    ClaimError,
    UserContext,
    assert_can_claim,
    can_claim,
    claimable_state_of,
    discoverable_apps,
)

REPO_URL = "https://github.com/acme/api"


def _app(**kw) -> AppDiscoveryRow:
    base = {
        "app_id": 1,
        "app_slug": "api",
        "org_id": 10,
        "repo_url": REPO_URL,
        "bound_team_id": None,
        "bound_user_id": None,
    }
    base.update(kw)
    return AppDiscoveryRow(**base)


def _user(**kw) -> UserContext:
    base = {
        "user_id": 42,
        "accessible_repo_urls": frozenset({REPO_URL}),
        "org_memberships": frozenset({10}),
        "team_memberships": frozenset(),
    }
    base.update(kw)
    return UserContext(**base)


# ---- claimable_state_of --------------------------------------------


def test_unowned_app_in_user_org_with_repo_access_is_unowned():
    """The base case: app registered by CI; user has SCM access
    + is in the org → can claim."""
    state = claimable_state_of(app=_app(), user=_user())
    assert state == ClaimableState.UNOWNED


def test_team_bound_app_not_claimable():
    """Bound apps require a transfer flow, not claim."""
    state = claimable_state_of(
        app=_app(bound_team_id=99),
        user=_user(),
    )
    assert state == ClaimableState.NOT_CLAIMABLE


def test_no_repo_access_not_claimable():
    """SCM access is the gating signal — without it the user
    couldn't deploy anyway."""
    state = claimable_state_of(
        app=_app(),
        user=_user(accessible_repo_urls=frozenset()),
    )
    assert state == ClaimableState.NOT_CLAIMABLE


def test_user_not_in_org_for_unowned_app_not_claimable():
    """Truly unowned but user is in a different org → would need
    invitation first."""
    state = claimable_state_of(
        app=_app(),
        user=_user(org_memberships=frozenset()),
    )
    assert state == ClaimableState.NOT_CLAIMABLE


def test_already_owned_by_user_not_claimable():
    """Self-owned apps shouldn't appear in discovery."""
    state = claimable_state_of(
        app=_app(bound_user_id=42),
        user=_user(),
    )
    assert state == ClaimableState.NOT_CLAIMABLE


def test_owned_by_other_user_in_org_org_only():
    """Another user in the org owns it; the requesting user has
    SCM access. Treat as ORG_ONLY: claim takes ownership."""
    state = claimable_state_of(
        app=_app(bound_user_id=99),
        user=_user(),
    )
    assert state == ClaimableState.ORG_ONLY


# ---- discoverable_apps ---------------------------------------------


def test_discoverable_filters_to_claimable():
    other_repo = "https://github.com/acme/other"
    apps = [
        _app(app_id=1),  # claimable
        _app(app_id=2, bound_team_id=5),  # not claimable
        _app(app_id=3, repo_url=other_repo),  # no SCM access
    ]
    user = _user(accessible_repo_urls=frozenset({REPO_URL}))
    out = discoverable_apps(apps=apps, user=user)
    assert {a.app_id for a in out} == {1}


def test_discoverable_includes_org_only():
    apps = [
        _app(app_id=1, bound_user_id=99),  # owned by other user
    ]
    out = discoverable_apps(apps=apps, user=_user())
    assert len(out) == 1


# ---- can_claim -----------------------------------------------------


def test_can_claim_returns_owner_id():
    decision = can_claim(app=_app(), user=_user())
    assert decision.accepted is True
    assert decision.new_owner_user_id == 42
    assert "unowned" in decision.reason


def test_can_claim_rejects_team_bound_with_specific_reason():
    decision = can_claim(
        app=_app(bound_team_id=5),
        user=_user(),
    )
    assert decision.accepted is False
    assert "transfer flow" in decision.reason


def test_can_claim_rejects_no_scm_access_with_specific_reason():
    decision = can_claim(
        app=_app(),
        user=_user(accessible_repo_urls=frozenset()),
    )
    assert decision.accepted is False
    assert "SCM access" in decision.reason


def test_can_claim_rejects_self_owned():
    decision = can_claim(
        app=_app(bound_user_id=42),
        user=_user(),
    )
    assert decision.accepted is False
    assert "already owns" in decision.reason


def test_can_claim_rejects_org_outsider():
    decision = can_claim(
        app=_app(),
        user=_user(org_memberships=frozenset()),
    )
    assert decision.accepted is False
    assert "not a member" in decision.reason


# ---- assert_can_claim ----------------------------------------------


def test_assert_returns_owner_id_on_success():
    new_owner = assert_can_claim(app=_app(), user=_user())
    assert new_owner == 42


def test_assert_raises_on_rejection():
    with pytest.raises(ClaimError, match="transfer flow"):
        assert_can_claim(
            app=_app(bound_team_id=5),
            user=_user(),
        )


# ---- race condition guard ------------------------------------------


def test_can_claim_re_checks_at_claim_time():
    """User saw an unowned app in discovery, then someone bound
    it to a team between snapshot + claim. The claim must reject
    rather than overwrite."""
    # Initial discovery: claimable
    user = _user()
    initial_app = _app()
    assert claimable_state_of(app=initial_app, user=user) == ClaimableState.UNOWNED

    # State changed between snapshots (race)
    later_app = _app(bound_team_id=5)
    decision = can_claim(app=later_app, user=user)
    assert decision.accepted is False
