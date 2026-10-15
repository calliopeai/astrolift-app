"""The grant preview evaluates the policies that match the draft (#2272).

Approval and deny policies that already match the drafted role and scope
show as approval required, denied by policy, or may deny; an app's own
deploy-approval setting shows as approval required for deploys there.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_graphql import GUID
from astrolift_identity.models import Member, Policy, Role, RoleBinding
from astrolift_identity.schema.access_ux import GrantPreviewInput, PrincipalRefInput
from astrolift_identity.schema.queries import IdentityQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld

pytestmark = pytest.mark.django_db
User = get_user_model()

DEPLOY = Permission.APP_DEPLOY
READ = Permission.APP_READ


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, g: None))


def _tag() -> str:
    return uuid.uuid4().hex[:6]


@pytest.fixture
def world():
    return ScopeWorld(f"x2272-{_tag()}")


@pytest.fixture
def other():
    return ScopeWorld(f"x2272o-{_tag()}")


def _member(world, prefix="u", groups=(), **extra):
    email = f"{prefix}-{_tag()}@acme.test"
    user = User.objects.create(username=email.split("@")[0], email=email, **extra)
    Member.objects.create(
        user=user, scope_kind="ORG", scope_id=world.org.pk, is_active=True, idp_groups=list(groups)
    )
    return user


def _role(*perms, slug=None):
    return Role.objects.create(
        name=slug or "r",
        slug=slug or f"r-{_tag()}",
        scope_level="ORG",
        permissions=[p.value for p in perms],
        is_system=False,
    )


def _policy(world, **fields):
    fields.setdefault("scope_level", "ORG")
    slug = fields.pop("slug", f"p-{_tag()}")
    return Policy.objects.create(organization=world.org, name=slug, slug=slug, **fields)


@pytest.fixture
def manager(world):
    user = _member(world, "mgr")
    RoleBinding.objects.create(
        user=user,
        role=_role(Permission.ORG_MANAGE_MEMBERS, Permission.ORG_READ, DEPLOY, READ),
        scope_kind="ORG",
        scope_id=world.org.pk,
    )
    return user


def _preview(world, actor, *, role, principal, scope_kind="APP", scope=None):
    info = SimpleNamespace(context=SimpleNamespace(user=actor, request=SimpleNamespace(user=actor)))
    with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=actor.pk)):
        return IdentityQuery().astrolift_grant_preview(
            info,
            input=GrantPreviewInput(
                action="GRANT",
                principals=[PrincipalRefInput(kind="USER", id=str(principal.pk))],
                role_id=GUID(str(role.guid)),
                scope_kind=scope_kind,
                scope_id=str((scope or world.medops_app).guid),
            ),
        )


def _by_slug(result):
    return {p.policy_slug: p for p in result.policies if p.source == "POLICY"}


def test_preview_shows_the_approval_and_deny_policies_that_match(world, other, manager):
    person = _member(world, "dep")
    role = _role(DEPLOY, READ, slug=f"deployer-{_tag()}")
    approval = _policy(
        world,
        effect="ALLOW",
        action_pattern="app.deploy",
        conditions=[{"kind": "approval_required", "min_approvers": 2}],
    )
    deny = _policy(
        world,
        scope_level="APP",
        scope_id=world.medops_app.pk,
        action_pattern="app.*",
        actor_pattern={"user_role_at_scope": [role.slug]},
    )
    office = _policy(
        world,
        effect="ALLOW",
        action_pattern="app.deploy",
        conditions=[{"kind": "ip_allowlist", "cidrs": ["10.0.0.0/8"]}],
    )
    elsewhere = _policy(world, scope_level="APP", scope_id=world.platform_app.pk, action_pattern="app.*")
    unrelated = _policy(world, action_pattern="org.update")
    broken = _policy(
        world,
        effect="ALLOW",
        action_pattern="app.deploy",
        conditions=[{"kind": "approval_required", "min_approvers": 0}],
    )
    Policy.objects.create(
        organization=other.org, name="F", slug=f"f-{_tag()}", scope_level="ORG", action_pattern="*"
    )

    result = _preview(world, manager, role=role, principal=person)
    assert result.ok, result.errors
    found = _by_slug(result)
    assert set(found) == {approval.slug, deny.slug, office.slug, broken.slug}
    assert elsewhere.slug not in found and unrelated.slug not in found
    assert found[approval.slug].outcome == "APPROVAL_REQUIRED"
    assert found[approval.slug].permissions == [DEPLOY.value]
    assert found[approval.slug].people_count == 1
    assert found[deny.slug].outcome == "DENIED"
    assert found[deny.slug].permissions == sorted([DEPLOY.value, READ.value])
    assert found[deny.slug].policy_id == str(deny.guid)
    assert found[office.slug].outcome == "MAY_DENY"
    # A malformed approval condition denies for real; it is not a wait for approvals.
    assert found[broken.slug].outcome == "DENIED"
    assert [p.outcome for p in result.policies][:2] == ["DENIED", "DENIED"]
    assert not any("not applied" in n for n in result.notes)


def test_a_role_scoped_policy_matches_only_the_drafted_role(world, manager):
    person = _member(world, "dep")
    drafted = _role(DEPLOY)
    _policy(world, action_pattern="app.deploy", actor_pattern={"user_role_at_scope": ["someone-else"]})
    result = _preview(world, manager, role=drafted, principal=person)
    assert result.ok and result.policies == []


def test_preview_reads_the_apps_deploy_approval_setting(world, manager):
    person = _member(world, "dep")
    world.medops_app.requires_approval = True
    world.medops_app.minimum_approvals = 2
    world.medops_app.save()
    deployer = _role(DEPLOY)

    on_app = _preview(world, manager, role=deployer, principal=person)
    setting = [p for p in on_app.policies if p.source == "APP_SETTING"]
    assert len(setting) == 1
    assert setting[0].outcome == "APPROVAL_REQUIRED" and setting[0].permissions == [DEPLOY.value]
    assert "2 approvals" in setting[0].detail

    reader = _preview(world, manager, role=_role(READ), principal=person)
    assert reader.policies == []
    on_project = _preview(
        world, manager, role=deployer, principal=person, scope_kind="PROJECT", scope=world.medops_project
    )
    assert on_project.policies == []


def test_platform_operators_are_left_out(world, manager):
    operator = _member(world, "op", is_superuser=True)
    _policy(world, action_pattern="app.deploy")
    result = _preview(world, manager, role=_role(DEPLOY), principal=operator)
    assert result.ok and result.policies == []


def test_removal_previews_no_policies(world, manager):
    person = _member(world, "rm")
    binding = RoleBinding.objects.create(
        user=person, role=_role(DEPLOY), scope_kind="APP", scope_id=world.medops_app.pk
    )
    _policy(world, action_pattern="app.deploy")
    info = SimpleNamespace(context=SimpleNamespace(user=manager, request=SimpleNamespace(user=manager)))
    with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=manager.pk)):
        result = IdentityQuery().astrolift_grant_preview(
            info, input=GrantPreviewInput(action="REMOVE", binding_id=GUID(str(binding.guid)))
        )
    assert result.ok and result.policies == []
