"""Only accept app source repos owned by a connected org (#1543).

Personal repos ride personal OAuth connections, whose grant follows a
person and leaves with them, rather than the org GitHub App (#1542).
`repo_visibility_scopes` expresses a per-connection preference and the
providers honour it when listing, but it is not a boundary: it is empty
by default, and `list_github_repos` skips the filter entirely when the
scopes are empty or the connection is an App install. So the default is
that everything the token can see is offered.

These cover the boundary on top of that, and — more importantly — that
it stays off unless an operator turns it on.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization
from astrolift_scm.models import SourceConnection
from astrolift_scm.org_repo_policy import (
    allowed_owners,
    filter_repos,
    owner_of,
    rejection_reason,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def policy(monkeypatch):
    class _Cfg:
        RESTRICT_SOURCE_REPOS_TO_ORG = True

    cfg = _Cfg()
    monkeypatch.setattr("constance.config", cfg, raising=False)
    return cfg


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-repo-policy")


def _connect(org, *, login: str, kind: str = "github_app_install"):
    return SourceConnection.objects.create(organization=org, kind=kind, account_login=login)


class _Repo:
    def __init__(self, full_name: str) -> None:
        self.full_name = full_name


# ---- reading an owner out of what the codebase actually stores ------------


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("acme/widgets", "acme"),
        ("https://github.com/Acme/Widgets", "acme"),
        ("https://github.com/acme/widgets.git", "acme"),
        ("git@github.com:acme/widgets.git", "acme"),
        # A GitLab nested group: the first segment owns everything below it.
        ("https://gitlab.com/acme/platform/widgets", "acme"),
        ("", ""),
        # No owner segment to read.
        ("widgets", ""),
    ],
)
def test_owner_is_read_from_every_shape_source_repo_takes(reference, expected):
    assert owner_of(reference) == expected


# ---- off by default ------------------------------------------------------


def test_the_policy_is_off_unless_an_operator_turns_it_on(org):
    """The default has to be today's behaviour, or this ships as an outage
    for every install with a personal repo already registered."""
    assert rejection_reason("someone-personal/widgets", org) is None


def test_filtering_is_a_no_op_while_off(org):
    repos = [_Repo("someone/widgets"), _Repo("acme/widgets")]

    assert [r.full_name for r in filter_repos(repos, org)] == [
        "someone/widgets",
        "acme/widgets",
    ]


def test_an_unreadable_setting_does_not_deny_work(org, monkeypatch):
    class _Exploding:
        def __getattr__(self, name):
            raise RuntimeError("constance backend down")

    monkeypatch.setattr("constance.config", _Exploding(), raising=False)

    assert rejection_reason("someone/widgets", org) is None


# ---- on ------------------------------------------------------------------


def test_a_repo_owned_by_a_connected_org_is_accepted(policy, org):
    _connect(org, login="acme")

    assert rejection_reason("acme/widgets", org) is None


def test_owner_matching_ignores_case(policy, org):
    _connect(org, login="Acme")

    assert rejection_reason("https://github.com/ACME/widgets", org) is None


def test_a_personal_repo_is_refused_and_the_message_says_what_to_do(policy, org):
    _connect(org, login="acme")

    reason = rejection_reason("someone-personal/widgets", org)

    assert reason is not None
    # Both halves: what was wrong, and what would be right.
    assert "someone-personal" in reason
    assert "acme" in reason


def test_a_personal_oauth_login_is_not_a_connected_org(policy, org):
    """The one that would quietly defeat the whole setting: a per-user
    OAuth row's account_login is the person, not an organization."""
    _connect(org, login="acme")
    _connect(org, login="someone-personal", kind="github_oauth_user")

    assert allowed_owners(org) == {"acme"}
    assert rejection_reason("someone-personal/widgets", org) is not None


def test_another_orgs_connection_does_not_admit_its_repos(policy, org):
    other = Organization.objects.create(name="Other", slug="other-repo-policy")
    _connect(other, login="rival")
    _connect(org, login="acme")

    assert rejection_reason("rival/widgets", org) is not None


def test_with_nothing_connected_the_message_does_not_name_an_empty_list(policy, org):
    """ "not owned by " with nothing after it reads as a bug, not a policy."""
    reason = rejection_reason("someone/widgets", org)

    assert reason is not None
    assert "no organization source connection" in reason


def test_an_unparseable_reference_is_not_reported_as_a_policy_violation(policy, org):
    """It is a malformed input, and saying "policy" would name the wrong
    fix. Whatever validates shape gets to report it."""
    _connect(org, login="acme")

    assert rejection_reason("widgets", org) is None


def test_a_soft_deleted_connection_stops_admitting_its_repos(policy, org):
    conn = _connect(org, login="acme")
    conn.soft_delete()

    assert allowed_owners(org) == set()


# ---- the picker ----------------------------------------------------------


def test_the_picker_never_offers_a_repo_registration_would_refuse(policy, org):
    """Getting as far as choosing before being told no is worse than not
    seeing it at all."""
    _connect(org, login="acme")
    repos = [_Repo("someone/widgets"), _Repo("acme/widgets"), _Repo("acme/other")]

    assert [r.full_name for r in filter_repos(repos, org)] == ["acme/widgets", "acme/other"]


# ---- the resolver, not just the helper -----------------------------------


def test_the_repo_query_itself_drops_refused_repos(policy, org, monkeypatch, permission_resolver):
    """`filter_repos` passing its own tests proves nothing about whether
    the picker calls it. This is the wiring."""
    from astrolift_scm.models import SourceConnection
    from astrolift_scm.schema.queries import ScmQuery
    from core.permissions import Permission
    from core.tenancy import TenantContext, tenant_context

    permission_resolver.grant(Permission.SCM_READ)
    _connect(org, login="acme")
    conn = SourceConnection.objects.create(
        organization=org,
        kind="github_oauth_user",
        account_login="someone-personal",
        is_active=True,
    )

    monkeypatch.setattr(
        "astrolift_scm.schema.queries.list_repos",
        lambda c, **kw: [_Repo("someone-personal/widgets"), _Repo("acme/widgets")],
    )
    monkeypatch.setattr("astrolift_scm.schema.queries.remote_repo_to_type", lambda r: r)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = ScmQuery().astrolift_available_repos(None, connection_id=str(conn.guid))

    assert [r.full_name for r in result.repos] == ["acme/widgets"]
