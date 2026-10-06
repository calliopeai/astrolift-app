"""Tests for the shared SourceConnection resolver.

The resolver is the one place that answers "which connection
authenticates this operation", keyed on an explicit ``purpose`` axis.
The regression it guards against: an autonomous platform write (webhook
install, workflow dispatch, manifest read, CI-secrets push) picking a
human's personal OAuth token instead of the org GitHub App — the split
that caused the intermittent 401 on secret pushes.

Real Postgres rows; no network (the resolver never mints a token, it
only selects a row).
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_identity.models import Organization
from astrolift_scm.models import SourceConnection
from astrolift_scm.services.connection_resolver import (
    ORG_REPO_WRITE,
    PLATFORM_REPO_WRITE,
    USER_REPO_DISCOVERY,
    ConnectionResolutionError,
    resolve_connection,
)
from core.secrets import encrypt_at_rest

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-resolver")


@pytest.fixture
def user():
    return get_user_model().objects.create(username="operator-resolver")


def _mk(org, kind, *, user=None, **kw):
    encrypted = encrypt_at_rest(b"secret-bytes")
    return SourceConnection.objects.create(
        organization=org,
        user=user,
        kind=kind,
        account_login=kw.pop("account_login", "acme"),
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=kw.pop("is_active", True),
        **kw,
    )


# ---------------------------------------------------------------------------
# PLATFORM_REPO_WRITE — org GitHub App only
# ---------------------------------------------------------------------------


def test_platform_write_picks_app_install(org):
    app = _mk(org, SourceConnection.Kind.GITHUB_APP_INSTALL, installation_id="1")
    got = resolve_connection(org, purpose=PLATFORM_REPO_WRITE, source_kind="github")
    assert got.pk == app.pk
    assert got.kind == SourceConnection.Kind.GITHUB_APP_INSTALL


def test_platform_write_prefers_app_over_user_oauth(org, user):
    """The core anti-regression: with BOTH an App install and a personal
    OAuth token in the org, a platform write must resolve the App — never
    the human's token."""
    _mk(
        org,
        SourceConnection.Kind.GITHUB_OAUTH_USER,
        user=user,
        account_login="operator",
    )
    app = _mk(org, SourceConnection.Kind.GITHUB_APP_INSTALL, installation_id="1")

    got = resolve_connection(org, purpose=PLATFORM_REPO_WRITE, source_kind="github")
    assert got.pk == app.pk
    assert got.kind == SourceConnection.Kind.GITHUB_APP_INSTALL


def test_platform_write_precondition_when_only_user_oauth(org, user):
    """Only a personal OAuth connection exists → no platform-write
    identity → PRECONDITION (App not installed), NOT a silent fallback
    to the user's token."""
    _mk(
        org,
        SourceConnection.Kind.GITHUB_OAUTH_USER,
        user=user,
        account_login="operator",
    )
    with pytest.raises(ConnectionResolutionError) as exc:
        resolve_connection(org, purpose=PLATFORM_REPO_WRITE, source_kind="github")
    assert exc.value.code == "PRECONDITION"
    assert "github app" in exc.value.message.lower()
    assert "not installed" in exc.value.message.lower()


def test_platform_write_precondition_when_no_connection(org):
    with pytest.raises(ConnectionResolutionError) as exc:
        resolve_connection(org, purpose=PLATFORM_REPO_WRITE, source_kind="github")
    assert exc.value.code == "PRECONDITION"


def test_platform_write_ignores_deleted_orphaned_inactive(org):
    _mk(
        org, SourceConnection.Kind.GITHUB_APP_INSTALL, installation_id="1", is_active=False, account_login="a"
    )
    _mk(
        org,
        SourceConnection.Kind.GITHUB_APP_INSTALL,
        installation_id="2",
        is_orphaned=True,
        account_login="b",
    )
    soft = _mk(org, SourceConnection.Kind.GITHUB_APP_INSTALL, installation_id="3", account_login="c")
    soft.deleted_at = timezone.now()
    soft.save(update_fields=["deleted_at"])

    with pytest.raises(ConnectionResolutionError) as exc:
        resolve_connection(org, purpose=PLATFORM_REPO_WRITE, source_kind="github")
    assert exc.value.code == "PRECONDITION"


def test_platform_write_tie_break_oldest_pk(org):
    first = _mk(org, SourceConnection.Kind.GITHUB_APP_INSTALL, installation_id="1", account_login="a")
    _mk(org, SourceConnection.Kind.GITHUB_APP_INSTALL, installation_id="2", account_login="b")
    got = resolve_connection(org, purpose=PLATFORM_REPO_WRITE, source_kind="github")
    assert got.pk == first.pk


def test_platform_write_gitlab_falls_to_oauth_user(org, user):
    """Documented exception: GitLab has no App-install identity, so a
    platform write there retains the OAuth-user > PAT preference."""
    _mk(org, SourceConnection.Kind.GITLAB_PAT, account_login="pat")
    oauth = _mk(
        org,
        SourceConnection.Kind.GITLAB_OAUTH_USER,
        user=user,
        account_login="operator",
    )
    got = resolve_connection(org, purpose=PLATFORM_REPO_WRITE, source_kind="gitlab")
    assert got.pk == oauth.pk


# ---------------------------------------------------------------------------
# ORG_REPO_WRITE — org-scoped, App > oauth-user > pat (the webhook /
# workflow / manifest ops). The whole point vs PLATFORM_REPO_WRITE: an
# App-less org that onboarded via an org OAuth/PAT connection still works.
# ---------------------------------------------------------------------------


def test_org_write_prefers_app_over_oauth_and_pat(org, user):
    _mk(org, SourceConnection.Kind.GITHUB_PAT, account_login="pat")
    _mk(org, SourceConnection.Kind.GITHUB_OAUTH_USER, user=user, account_login="operator")
    app = _mk(org, SourceConnection.Kind.GITHUB_APP_INSTALL, installation_id="1", account_login="app")
    got = resolve_connection(org, purpose=ORG_REPO_WRITE, source_kind="github")
    assert got.pk == app.pk
    assert got.kind == SourceConnection.Kind.GITHUB_APP_INSTALL


def test_org_write_falls_back_to_oauth_user_when_no_app(org, user):
    """The exact blocker: an App-less org with only an org-level
    ``github_oauth_user`` connection must still resolve for a workflow
    dispatch / file write — ORG_REPO_WRITE falls back to it (unlike
    PLATFORM_REPO_WRITE which is App-only and hard-fails)."""
    _mk(org, SourceConnection.Kind.GITHUB_PAT, account_login="pat")
    oauth = _mk(org, SourceConnection.Kind.GITHUB_OAUTH_USER, user=user, account_login="operator")
    got = resolve_connection(org, purpose=ORG_REPO_WRITE, source_kind="github")
    assert got.pk == oauth.pk
    assert got.kind == SourceConnection.Kind.GITHUB_OAUTH_USER


def test_org_write_falls_back_to_pat_when_no_app_or_oauth(org):
    pat = _mk(org, SourceConnection.Kind.GITHUB_PAT, account_login="pat")
    got = resolve_connection(org, purpose=ORG_REPO_WRITE, source_kind="github")
    assert got.pk == pat.pk
    assert got.kind == SourceConnection.Kind.GITHUB_PAT


def test_org_write_and_platform_write_diverge_on_app_less_org(org, user):
    """The core distinction the fix restores: with ONLY an org-level
    OAuth-user connection, ORG_REPO_WRITE resolves it (workflows/webhooks/
    manifest keep working) while PLATFORM_REPO_WRITE (CI-secrets) still
    HARD-FAILS with PRECONDITION because a secrets write is App-only."""
    oauth = _mk(org, SourceConnection.Kind.GITHUB_OAUTH_USER, user=user, account_login="operator")

    got = resolve_connection(org, purpose=ORG_REPO_WRITE, source_kind="github")
    assert got.pk == oauth.pk

    with pytest.raises(ConnectionResolutionError) as exc:
        resolve_connection(org, purpose=PLATFORM_REPO_WRITE, source_kind="github")
    assert exc.value.code == "PRECONDITION"


def test_org_write_precondition_when_no_connection(org):
    with pytest.raises(ConnectionResolutionError) as exc:
        resolve_connection(org, purpose=ORG_REPO_WRITE, source_kind="github")
    assert exc.value.code == "PRECONDITION"


def test_org_write_tie_break_oldest_pk(org):
    first = _mk(org, SourceConnection.Kind.GITHUB_PAT, account_login="a")
    _mk(org, SourceConnection.Kind.GITHUB_PAT, account_login="b")
    got = resolve_connection(org, purpose=ORG_REPO_WRITE, source_kind="github")
    assert got.pk == first.pk


def test_org_write_ignores_deleted_orphaned_inactive(org):
    _mk(org, SourceConnection.Kind.GITHUB_OAUTH_USER, is_active=False, account_login="a")
    _mk(org, SourceConnection.Kind.GITHUB_OAUTH_USER, is_orphaned=True, account_login="b")
    soft = _mk(org, SourceConnection.Kind.GITHUB_OAUTH_USER, account_login="c")
    soft.deleted_at = timezone.now()
    soft.save(update_fields=["deleted_at"])
    with pytest.raises(ConnectionResolutionError) as exc:
        resolve_connection(org, purpose=ORG_REPO_WRITE, source_kind="github")
    assert exc.value.code == "PRECONDITION"


# ---------------------------------------------------------------------------
# USER_REPO_DISCOVERY — per-user OAuth only
# ---------------------------------------------------------------------------


def test_user_discovery_picks_user_oauth_not_app(org, user):
    _mk(org, SourceConnection.Kind.GITHUB_APP_INSTALL, installation_id="1", account_login="app")
    personal = _mk(
        org,
        SourceConnection.Kind.GITHUB_OAUTH_USER,
        user=user,
        account_login="operator",
    )
    got = resolve_connection(org, purpose=USER_REPO_DISCOVERY, source_kind="github", user_id=user.pk)
    assert got.pk == personal.pk
    assert got.kind == SourceConnection.Kind.GITHUB_OAUTH_USER


def test_user_discovery_precondition_when_only_app_install(org, user):
    """An org App must never satisfy user discovery — that's the human's
    lane, and the App would surface the wrong repo set."""
    _mk(org, SourceConnection.Kind.GITHUB_APP_INSTALL, installation_id="1", account_login="app")
    with pytest.raises(ConnectionResolutionError) as exc:
        resolve_connection(org, purpose=USER_REPO_DISCOVERY, source_kind="github", user_id=user.pk)
    assert exc.value.code == "PRECONDITION"


def test_user_discovery_scoped_to_the_requesting_user(org):
    other = get_user_model().objects.create(username="someone-else")
    _mk(
        org,
        SourceConnection.Kind.GITHUB_OAUTH_USER,
        user=other,
        account_login="other",
    )
    with pytest.raises(ConnectionResolutionError) as exc:
        resolve_connection(
            org,
            purpose=USER_REPO_DISCOVERY,
            source_kind="github",
            user_id=get_user_model().objects.create(username="fresh").pk,
        )
    assert exc.value.code == "PRECONDITION"


# ---------------------------------------------------------------------------
# repo owner — one GitHub App installation per account (#2297)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("purpose", [PLATFORM_REPO_WRITE, ORG_REPO_WRITE])
@pytest.mark.parametrize(
    "repo",
    [
        "ragelink/leo-brain",
        "https://github.com/RageLink/leo-brain.git",
        "git@github.com:ragelink/leo-brain.git",
    ],
)
def test_several_installations_resolve_by_repo_owner(org, purpose, repo):
    """The oldest installation (ConflictHQ) used to win for every repo, so
    a repo on a second account was never reachable."""
    _mk(org, SourceConnection.Kind.GITHUB_APP_INSTALL, installation_id="1", account_login="ConflictHQ")
    second = _mk(org, SourceConnection.Kind.GITHUB_APP_INSTALL, installation_id="2", account_login="ragelink")
    got = resolve_connection(org, purpose=purpose, source_kind="github", repo=repo)
    assert got.pk == second.pk


def test_several_installations_none_on_the_owner_is_a_precondition(org):
    _mk(org, SourceConnection.Kind.GITHUB_APP_INSTALL, installation_id="1", account_login="ConflictHQ")
    _mk(org, SourceConnection.Kind.GITHUB_APP_INSTALL, installation_id="2", account_login="ragelink")
    with pytest.raises(ConnectionResolutionError) as exc:
        resolve_connection(org, purpose=PLATFORM_REPO_WRITE, source_kind="github", repo="someone-else/app")
    assert exc.value.code == "PRECONDITION"
    assert "someone-else" in exc.value.message


def test_org_write_falls_back_past_installations_that_miss_the_owner(org):
    """A PAT or OAuth connection may reach any owner; only installations are
    bound to one account."""
    _mk(org, SourceConnection.Kind.GITHUB_APP_INSTALL, installation_id="1", account_login="ConflictHQ")
    _mk(org, SourceConnection.Kind.GITHUB_APP_INSTALL, installation_id="2", account_login="ragelink")
    pat = _mk(org, SourceConnection.Kind.GITHUB_PAT, account_login="ci-bot")
    got = resolve_connection(org, purpose=ORG_REPO_WRITE, source_kind="github", repo="someone-else/app")
    assert got.pk == pat.pk


def test_a_single_installation_still_resolves_for_any_owner(org):
    """Manifest-flow rows record the App owner, not the installed account,
    so a lone installation keeps the org-wide behaviour."""
    only = _mk(org, SourceConnection.Kind.GITHUB_APP_INSTALL, installation_id="1", account_login="ConflictHQ")
    got = resolve_connection(
        org, purpose=PLATFORM_REPO_WRITE, source_kind="github", repo="ragelink/leo-brain"
    )
    assert got.pk == only.pk
