"""Anonymizing someone else stays inside the caller's org (#1979).

``org.manage_members`` was the only gate, held in the caller's own org,
while the target was looked up by pk across the install. A custom role
holding just that one permission erased another org's owner and the
platform operator. Everything here runs through the real resolver and
real bindings; no permission stub is installed.
"""

from __future__ import annotations

import importlib
import uuid
from types import SimpleNamespace

import pytest
from constance.test import override_config
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model

from astrolift_identity.anonymize import (
    AstroliftAnonymizeUserInput,
    IdentityAnonymizeUserMutation,
)
from astrolift_identity.models import Member, Organization, Role, RoleBinding, Team
from astrolift_identity.system_roles import SYSTEM_ROLES
from core.mutations import AuditEntry, ErrorCode, register_audit_writer
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()

RESYNC = importlib.import_module("astrolift_identity.migrations.0034_resync_system_roles_stock_catalogue")


@pytest.fixture(autouse=True)
def _no_external_side_effects(monkeypatch):
    """``Profile.anonymize_user`` saves the profile, which reaches for
    Auth0 and OpenSearch; neither runs in the test container."""
    monkeypatch.setattr(
        "core.models.user.Profile.register_in_auth0",
        lambda self, reset_password=True: None,
    )
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture
def stock():
    RESYNC.upsert_system_roles(django_apps, None)
    slugs = [slug for slug, *_rest in SYSTEM_ROLES]
    return {slug: Role.objects.get(slug=slug, is_system=True, organization=None) for slug in slugs}


@pytest.fixture
def audit_capture():
    captured: list[AuditEntry] = []
    from core.mutations import _audit_writer as original

    register_audit_writer(captured.append)
    yield captured
    register_audit_writer(original)


def _org(tag: str) -> Organization:
    return Organization.objects.create(name=tag, slug=f"{tag}-{uuid.uuid4().hex[:6]}")


def _user(tag: str, **extra) -> User:
    email = f"{tag}-{uuid.uuid4().hex[:8]}@anon1979.test"
    return User.objects.create(username=email.split("@")[0], email=email, **extra)


def _member(org: Organization, user: User, *, active: bool = True) -> User:
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=active,
        lifecycle=Member.Lifecycle.ACTIVE if active else Member.Lifecycle.DEACTIVATED,
    )
    return user


def _bind(user: User, role: Role, kind: str, scope_id: int) -> RoleBinding:
    return RoleBinding.objects.create(user=user, role=role, scope_kind=kind, scope_id=scope_id)


def _custom_role(org: Organization, *permissions: Permission) -> Role:
    return Role.objects.create(
        organization=org,
        slug=f"custom-{uuid.uuid4().hex[:6]}",
        name="Custom",
        scope_level="ORG",
        permissions=[p.value for p in permissions],
    )


def _member_manager(org: Organization, *extra: Permission) -> User:
    """A member whose only role is a custom one with org.manage_members (plus ``extra``)."""
    user = _member(org, _user("mm"))
    _bind(user, _custom_role(org, Permission.ORG_MANAGE_MEMBERS, *extra), "ORG", org.id)
    return user


def _anonymize(org: Organization, actor: User, target: User, *, session=None, **selected):
    request = SimpleNamespace(user=actor) if session is None else SimpleNamespace(user=actor, session=session)
    info = SimpleNamespace(context=SimpleNamespace(user=actor, request=request))
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id, **selected)):
        return IdentityAnonymizeUserMutation().astrolift_anonymize_user(
            info, input=AstroliftAnonymizeUserInput(user_gid=str(target.pk))
        )


def _denied(result) -> bool:
    return result.ok is False and result.errors[0].code == ErrorCode.PERMISSION_DENIED.value


def _untouched(user: User) -> bool:
    email = user.email
    user.refresh_from_db()
    return user.is_active and user.email == email


def _erased(user: User) -> bool:
    user.refresh_from_db()
    return not user.is_active and user.email.endswith("@anon-astrolift.net")


# ---------------------------------------------------------------------------
# The probe from the issue
# ---------------------------------------------------------------------------


def test_manage_members_cannot_erase_another_orgs_owner_or_the_operator(stock, audit_capture):
    a, b = _org("a"), _org("b")
    actor = _member_manager(a)
    owner_b = _member(b, _user("owner-b"))
    _bind(owner_b, stock["org_owner"], "ORG", b.id)
    operator = _user("root", is_superuser=True)

    results = [_anonymize(a, actor, owner_b), _anonymize(a, actor, operator)]

    assert all(_denied(r) for r in results), results
    assert _untouched(owner_b)
    assert _untouched(operator)
    assert Member.objects.get(user=owner_b).is_active is True
    # Audited as refusals naming who was targeted, not as allowed calls.
    rows = [e for e in audit_capture if e.action == "identity.user.anonymized"]
    assert [(e.decision, e.target_kind, e.target_id) for e in rows] == [
        ("DENY", "user", str(owner_b.pk)),
        ("DENY", "user", str(operator.pk)),
    ]


# ---------------------------------------------------------------------------
# Inside the caller's own org
# ---------------------------------------------------------------------------


def test_manage_members_erases_its_own_member_within_its_reach(stock):
    a = _org("a")
    actor = _member_manager(a, Permission.APP_READ)
    target = _member(a, _user("t"))
    _bind(target, _custom_role(a, Permission.APP_READ), "ORG", a.id)

    result = _anonymize(a, actor, target)

    assert result.ok, result.errors
    assert _erased(target)


def test_a_former_member_can_still_be_erased(stock):
    """Right-to-delete usually follows offboarding."""
    a = _org("a")
    actor = _member_manager(a)
    target = _member(a, _user("t"), active=False)

    assert _anonymize(a, actor, target).ok
    assert _erased(target)


@pytest.mark.parametrize("slug,kind", [("org_owner", "ORG"), ("org_admin", "ORG"), ("team_admin", "TEAM")])
def test_a_member_holding_more_than_the_caller_is_refused(stock, slug, kind):
    a = _org("a")
    team = Team.objects.create(organization=a, name="Eng", slug=f"eng-{uuid.uuid4().hex[:6]}")
    actor = _member_manager(a)
    target = _member(a, _user("t"))
    _bind(target, stock[slug], kind, a.id if kind == "ORG" else team.id)

    assert _denied(_anonymize(a, actor, target))
    assert _untouched(target)


def test_member_management_held_on_one_team_does_not_reach_the_org(stock):
    """A team binding passes a targetless check while its team is
    selected; erasing someone reaches the whole org."""
    a = _org("a")
    team = Team.objects.create(organization=a, name="Eng", slug=f"eng-{uuid.uuid4().hex[:6]}")
    actor = _member(a, _user("team-mm"))
    _bind(actor, _custom_role(a, Permission.ORG_MANAGE_MEMBERS), "TEAM", team.id)
    target = _member(a, _user("t"))

    assert _denied(_anonymize(a, actor, target, team_id=team.id))
    assert _untouched(target)


def test_an_owner_erases_an_admin(stock):
    a = _org("a")
    owner = _member(a, _user("owner"))
    _bind(owner, stock["org_owner"], "ORG", a.id)
    admin = _member(a, _user("admin"))
    _bind(admin, stock["org_admin"], "ORG", a.id)

    assert _anonymize(a, owner, admin).ok
    assert _erased(admin)


def test_a_superuser_member_here_is_still_only_the_operators_to_erase(stock):
    a = _org("a")
    owner = _member(a, _user("owner"))
    _bind(owner, stock["org_owner"], "ORG", a.id)
    root = _member(a, _user("root", is_superuser=True))

    assert _denied(_anonymize(a, owner, root))
    assert _untouched(root)


def test_someone_still_active_in_another_org_is_not_this_orgs_to_erase(stock):
    a, b = _org("a"), _org("b")
    actor = _member_manager(a)
    shared = _member(b, _member(a, _user("shared")))

    assert _denied(_anonymize(a, actor, shared))
    assert _untouched(shared)

    # Once they have left the other org, nobody else relies on the account.
    Member.objects.filter(user=shared, scope_id=b.id).update(is_active=False)
    assert _anonymize(a, actor, shared).ok
    assert _erased(shared)


# ---------------------------------------------------------------------------
# The platform operator, and the step-up gate
# ---------------------------------------------------------------------------


def test_the_platform_operator_erases_a_superuser_or_a_shared_member(stock):
    a, b = _org("a"), _org("b")
    operator = _user("root", is_superuser=True)
    other_root = _member(a, _user("root2", is_superuser=True))
    shared = _member(b, _member(a, _user("shared")))

    assert _anonymize(a, operator, other_root).ok
    assert _anonymize(a, operator, shared).ok
    assert _erased(other_root) and _erased(shared)


def test_even_the_operator_names_a_member_of_the_active_org_here(stock):
    """The members page acts on the active org's people; erasing anyone
    else on the install is ``profileRequestDeleteUser``."""
    a = _org("a")
    operator = _user("root", is_superuser=True)
    stranger = _user("stranger")

    assert _denied(_anonymize(a, operator, stranger))
    assert _untouched(stranger)


class _UnelevatedSession(dict):
    modified = False


@override_config(REQUIRE_STEP_UP_AUTH=True)
def test_anonymizing_requires_a_fresh_elevation(stock):
    a = _org("a")
    actor = _member_manager(a)
    target = _member(a, _user("t"))

    result = _anonymize(a, actor, target, session=_UnelevatedSession())

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.STEP_UP_REQUIRED.value
    assert _untouched(target)
