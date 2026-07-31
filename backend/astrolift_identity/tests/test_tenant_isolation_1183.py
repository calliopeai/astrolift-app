"""Cross-tenant isolation regression tests for astrolift_identity (#1183).

The audit behind #1183 found that ``@tenant_scoped()`` only *asserts* a
tenant context exists — it does not filter rows — and that
``TenantScopedManager`` is wired on zero models. Every by-guid/slug
resolver that fetched without an explicit org clause therefore crossed
tenants (guids are globally unique).

These tests pin the fail-closed org scoping on the CRITICAL identity
surface:

* ``grant_role`` — the privilege-escalation path (grant any role, on any
  scope, to any user, in any org → cross-tenant account takeover);
* ``update_identity_provider`` / ``set_active_identity_provider`` — the
  SSO-hijack path (overwrite/flip a victim org's login provider);
* the org-scoped read/mutate resolvers (members PII, team members, api
  tokens, policies, teams).

Each test builds two independent orgs and proves a caller scoped to
org-A cannot read or mutate org-B's rows (NOT_FOUND / empty / None),
while the same operation inside org-A still works.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_graphql import GUID
from astrolift_identity.models import (
    ApiToken,
    IdentityProvider,
    Member,
    Organization,
    Policy,
    Role,
    RoleBinding,
    Team,
)
from astrolift_identity.schema.mutations import (
    GrantRoleInput,
    IdentityMutation,
    RevokeApiTokenInput,
    SetActiveIdentityProviderInput,
    SoftDeleteByGuidInput,
    UpdateIdentityProviderInput,
    UpdatePolicyInput,
)
from astrolift_identity.schema.queries import IdentityQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    """Skip OpenSearch side-effects when User / Organization creates fire
    profile-indexing signal handlers (same pattern as the members /
    identity-provider suites)."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


# ---- helpers ---------------------------------------------------------


def _user(email: str | None = None) -> User:
    if email is None:
        email = f"u-{uuid.uuid4().hex[:8]}@astrolift.dev"
    return User.objects.create(username=email.split("@")[0], email=email)


def _info(user):
    request = SimpleNamespace(user=user, session={})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _org(slug: str) -> Organization:
    return Organization.objects.create(name=slug.title(), slug=f"{slug}-{uuid.uuid4().hex[:6]}")


def _org_member(org: Organization, user) -> Member:
    return Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )


def _null_org_role(slug: str) -> Role:
    """A system/null-org role — grantable from any org."""
    return Role.objects.create(
        slug=slug,
        name=slug.title(),
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.read"],
    )


# =====================================================================
# grant_role — privilege escalation (the flagged CRITICAL path)
# =====================================================================


def test_grant_role_cannot_grant_on_another_orgs_org_scope(permission_resolver):
    """A caller in org-A cannot grant a role on org-B's ORG scope.

    The target is a member of BOTH orgs and the role is a null-org
    (system) role, so membership + role checks pass — the ONLY thing
    that can stop the grant is the scope-belongs-to-another-org guard.
    Without the fix the scope guid resolved globally and the binding was
    created against org-B.
    """
    caller = _org("attacker-a")
    victim = _org("victim-b")
    admin = _user()
    target = _user()
    _org_member(caller, target)  # target IS a member of the caller org
    _org_member(victim, target)  # ...and of the victim org
    role = _null_org_role("dev-1183-orgscope")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with tenant_context(TenantContext(organization_id=caller.id, actor_user_id=admin.id)):
        result = IdentityMutation().grant_role(
            _info(admin),
            input=GrantRoleInput(
                user_id=str(target.id),
                role_id=GUID(str(role.guid)),
                scope_kind="ORG",
                scope_guid=GUID(str(victim.guid)),  # org-B's scope
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "scopeGuid"
    # No binding leaked into org-B's scope.
    assert not RoleBinding.objects.filter(scope_kind="ORG", scope_id=victim.id).exists()


def test_grant_role_cannot_grant_on_another_orgs_team_scope(permission_resolver):
    """A caller in org-A cannot grant on a team that belongs to org-B."""
    caller = _org("attacker-a-team")
    victim = _org("victim-b-team")
    victim_team = Team.objects.create(organization=victim, slug="vic-team", name="Vic")
    admin = _user()
    target = _user()
    _org_member(caller, target)
    role = _null_org_role("dev-1183-teamscope")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with tenant_context(TenantContext(organization_id=caller.id, actor_user_id=admin.id)):
        result = IdentityMutation().grant_role(
            _info(admin),
            input=GrantRoleInput(
                user_id=str(target.id),
                role_id=GUID(str(role.guid)),
                scope_kind="TEAM",
                scope_guid=GUID(str(victim_team.guid)),
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "scopeGuid"
    assert not RoleBinding.objects.filter(scope_kind="TEAM", scope_id=victim_team.id).exists()


def test_grant_role_cannot_grant_to_non_member_of_caller_org(permission_resolver):
    """The target must already belong to the caller's org. A user who is
    only a member of org-B cannot be granted a role in org-A even on
    org-A's own scope."""
    caller = _org("attacker-a-nonmember")
    victim = _org("victim-b-nonmember")
    admin = _user()
    outsider = _user()
    _org_member(victim, outsider)  # outsider belongs to victim, NOT caller
    role = _null_org_role("dev-1183-nonmember")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with tenant_context(TenantContext(organization_id=caller.id, actor_user_id=admin.id)):
        result = IdentityMutation().grant_role(
            _info(admin),
            input=GrantRoleInput(
                user_id=str(outsider.id),
                role_id=GUID(str(role.guid)),
                scope_kind="ORG",
                scope_guid=GUID(str(caller.guid)),  # caller's OWN scope
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "userId"
    assert not RoleBinding.objects.filter(user=outsider).exists()


def test_grant_role_cannot_grant_another_orgs_custom_role(permission_resolver):
    """A caller in org-A cannot grant org-B's custom (org-bound) role."""
    caller = _org("attacker-a-role")
    victim = _org("victim-b-role")
    admin = _user()
    target = _user()
    _org_member(caller, target)
    victim_role = Role.objects.create(
        organization=victim,
        slug="victim-custom",
        name="Victim Custom",
        scope_level=Role.ScopeLevel.ORG,
        permissions=["app.read"],
    )
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with tenant_context(TenantContext(organization_id=caller.id, actor_user_id=admin.id)):
        result = IdentityMutation().grant_role(
            _info(admin),
            input=GrantRoleInput(
                user_id=str(target.id),
                role_id=GUID(str(victim_role.guid)),
                scope_kind="ORG",
                scope_guid=GUID(str(caller.guid)),
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "roleId"
    assert not RoleBinding.objects.filter(role=victim_role).exists()


def test_grant_role_same_org_succeeds(permission_resolver):
    """Baseline: the legitimate in-org grant still works."""
    org = _org("legit")
    admin = _user()
    target = _user()
    _org_member(org, target)
    role = _null_org_role("dev-1183-legit")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=admin.id)):
        result = IdentityMutation().grant_role(
            _info(admin),
            input=GrantRoleInput(
                user_id=str(target.id),
                role_id=GUID(str(role.guid)),
                scope_kind="ORG",
                scope_guid=GUID(str(org.guid)),
            ),
        )

    assert result.ok is True, result.errors
    assert RoleBinding.objects.filter(user=target, role=role, scope_kind="ORG", scope_id=org.id).exists()


def _app(org: Organization):
    from astrolift_identity.models import Project, Team
    from astrolift_registry.models import RegisteredApp

    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{uuid.uuid4().hex[:6]}")
    project = Project.objects.create(organization=org, team=team, name="P", slug=f"p-{uuid.uuid4().hex[:6]}")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug=f"app-{uuid.uuid4().hex[:6]}",
    )


def test_grant_role_app_scope_succeeds_in_org(permission_resolver):
    """App-scope grants resolve the org's own RegisteredApp (#1227) — the
    permission resolver honors APP bindings, so denying the grant made the
    app_* roles unassignable."""
    org = _org("app-grant")
    admin = _user()
    target = _user()
    _org_member(org, target)
    app = _app(org)
    role = _null_org_role("app-dev-1227")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=admin.id)):
        result = IdentityMutation().grant_role(
            _info(admin),
            input=GrantRoleInput(
                user_id=str(target.id),
                role_id=GUID(str(role.guid)),
                scope_kind="app",  # lowercase from a client normalizes fine
                scope_guid=GUID(str(app.guid)),
            ),
        )

    assert result.ok is True, result.errors
    assert RoleBinding.objects.filter(user=target, role=role, scope_kind="APP", scope_id=app.id).exists()
    # The auto-added Member row at APP scope lets middleware resolve tenant.
    assert Member.objects.filter(user=target, scope_kind="APP", scope_id=app.id).exists()


def test_grant_role_cannot_grant_on_another_orgs_app_scope(permission_resolver):
    """Cross-tenant guard extends to APP scopes: a foreign org's app guid
    reads as scope-not-found."""
    victim = _org("victim-app")
    victim_app = _app(victim)
    caller = _org("attacker-app")
    admin = _user()
    target = _user()
    _org_member(caller, target)
    role = _null_org_role("app-dev-1227-x")
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with tenant_context(TenantContext(organization_id=caller.id, actor_user_id=admin.id)):
        result = IdentityMutation().grant_role(
            _info(admin),
            input=GrantRoleInput(
                user_id=str(target.id),
                role_id=GUID(str(role.guid)),
                scope_kind="APP",
                scope_guid=GUID(str(victim_app.guid)),
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    assert not RoleBinding.objects.filter(user=target, scope_kind="APP").exists()


# =====================================================================
# Identity provider — SSO hijack (flagged CRITICAL)
# =====================================================================


def _idp(org: Organization, *, display_name: str, client_id: str = "cid") -> IdentityProvider:
    return IdentityProvider.objects.create(
        organization=org,
        kind=IdentityProvider.Kind.OIDC,
        display_name=display_name,
        client_id=client_id,
        client_secret_ref="secret-ref",
        oidc_discovery_url="https://idp.example/.well-known",
    )


def test_update_identity_provider_cannot_tamper_another_orgs_sso(permission_resolver):
    """A caller in org-A cannot overwrite org-B's IdP client_id /
    client_secret_ref / discovery URL (SSO hijack)."""
    victim = _org("victim-idp-update")
    victim_idp = _idp(victim, display_name="victim-oidc", client_id="victim-client")
    caller = _org("attacker-idp-update")
    admin = _user()
    permission_resolver.grant(Permission.ORG_UPDATE)

    with tenant_context(TenantContext(organization_id=caller.id, actor_user_id=admin.id)):
        result = IdentityMutation().update_identity_provider(
            _info(admin),
            input=UpdateIdentityProviderInput(
                id=GUID(str(victim_idp.guid)),
                client_id="attacker-client",
                client_secret_ref="attacker-secret",
                oidc_discovery_url="https://attacker.example/.well-known",
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    victim_idp.refresh_from_db()
    assert victim_idp.client_id == "victim-client"
    assert victim_idp.client_secret_ref == "secret-ref"
    assert victim_idp.oidc_discovery_url == "https://idp.example/.well-known"


def test_update_identity_provider_same_org_succeeds(permission_resolver):
    org = _org("legit-idp-update")
    idp = _idp(org, display_name="oidc", client_id="orig-client")
    admin = _user()
    permission_resolver.grant(Permission.ORG_UPDATE)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=admin.id)):
        result = IdentityMutation().update_identity_provider(
            _info(admin),
            input=UpdateIdentityProviderInput(id=GUID(str(idp.guid)), client_id="new-client"),
        )

    assert result.ok is True, result.errors
    idp.refresh_from_db()
    assert idp.client_id == "new-client"


def test_set_active_identity_provider_cannot_flip_another_orgs_idp(permission_resolver):
    """A caller in org-A cannot flip org-B's active login provider."""
    victim = _org("victim-idp-active")
    victim_active = _idp(victim, display_name="victim-active")
    victim_other = _idp(victim, display_name="victim-other")
    victim.identity_provider_id = victim_active.pk
    victim.save(update_fields=["identity_provider", "updated_at", "version"])

    caller = _org("attacker-idp-active")
    admin = _user()
    permission_resolver.grant(Permission.ORG_UPDATE)

    with tenant_context(TenantContext(organization_id=caller.id, actor_user_id=admin.id)):
        result = IdentityMutation().set_active_identity_provider(
            _info(admin),
            input=SetActiveIdentityProviderInput(id=GUID(str(victim_other.guid))),
        )

    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    victim.refresh_from_db()
    # The victim org's active IdP is untouched.
    assert victim.identity_provider_id == victim_active.pk


def test_set_active_identity_provider_same_org_succeeds(permission_resolver):
    org = _org("legit-idp-active")
    idp_a = _idp(org, display_name="a")
    idp_b = _idp(org, display_name="b")
    org.identity_provider_id = idp_a.pk
    org.save(update_fields=["identity_provider", "updated_at", "version"])
    admin = _user()
    permission_resolver.grant(Permission.ORG_UPDATE)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=admin.id)):
        result = IdentityMutation().set_active_identity_provider(
            _info(admin),
            input=SetActiveIdentityProviderInput(id=GUID(str(idp_b.guid))),
        )

    assert result.ok is True, result.errors
    org.refresh_from_db()
    assert org.identity_provider_id == idp_b.pk


# =====================================================================
# API tokens
# =====================================================================


def test_revoke_api_token_another_org_not_found(permission_resolver):
    victim = _org("victim-token")
    holder = _user()
    token = ApiToken.objects.create(
        user=holder,
        organization=victim,
        name="victim-token",
        token_hash="hash-" + uuid.uuid4().hex,
    )
    caller = _org("attacker-token")
    admin = _user()
    permission_resolver.grant(Permission.API_TOKEN_REVOKE)

    with tenant_context(TenantContext(organization_id=caller.id, actor_user_id=admin.id)):
        result = IdentityMutation().revoke_api_token(
            _info(admin),
            input=RevokeApiTokenInput(id=GUID(str(token.guid))),
        )

    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    token.refresh_from_db()
    assert token.is_revoked is False


def test_revoke_api_token_same_org_succeeds(permission_resolver):
    org = _org("legit-token")
    holder = _user()
    token = ApiToken.objects.create(
        user=holder,
        organization=org,
        name="my-token",
        token_hash="hash-" + uuid.uuid4().hex,
    )
    admin = _user()
    permission_resolver.grant(Permission.API_TOKEN_REVOKE)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=admin.id)):
        result = IdentityMutation().revoke_api_token(
            _info(admin),
            input=RevokeApiTokenInput(id=GUID(str(token.guid))),
        )

    assert result.ok is True, result.errors
    token.refresh_from_db()
    assert token.is_revoked is True


# =====================================================================
# Policies
# =====================================================================


def _policy(org: Organization, slug: str, *, action_pattern: str = "*") -> Policy:
    return Policy.objects.create(
        organization=org,
        name=slug,
        slug=slug,
        scope_level="ORG",
        effect="DENY",
        action_pattern=action_pattern,
    )


def test_update_policy_another_org_not_found(permission_resolver):
    victim = _org("victim-policy")
    policy = _policy(victim, "victim-deny", action_pattern="app.deploy")
    caller = _org("attacker-policy")
    admin = _user()
    permission_resolver.grant(Permission.ORG_UPDATE)

    with tenant_context(TenantContext(organization_id=caller.id, actor_user_id=admin.id)):
        result = IdentityMutation().update_policy(
            _info(admin),
            input=UpdatePolicyInput(id=GUID(str(policy.guid)), action_pattern="*"),
        )

    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    policy.refresh_from_db()
    assert policy.action_pattern == "app.deploy"


def test_update_policy_same_org_succeeds(permission_resolver):
    org = _org("legit-policy")
    policy = _policy(org, "legit-deny", action_pattern="app.deploy")
    admin = _user()
    permission_resolver.grant(Permission.ORG_UPDATE)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=admin.id)):
        result = IdentityMutation().update_policy(
            _info(admin),
            input=UpdatePolicyInput(id=GUID(str(policy.guid)), action_pattern="app.*"),
        )

    assert result.ok is True, result.errors
    policy.refresh_from_db()
    assert policy.action_pattern == "app.*"


# =====================================================================
# Teams (soft delete)
# =====================================================================


def test_soft_delete_team_another_org_not_found(permission_resolver):
    victim = _org("victim-team-del")
    victim_team = Team.objects.create(organization=victim, slug="keepme", name="Keep Me")
    caller = _org("attacker-team-del")
    admin = _user()
    permission_resolver.grant(Permission.TEAM_DELETE)

    with tenant_context(TenantContext(organization_id=caller.id, actor_user_id=admin.id)):
        result = IdentityMutation().soft_delete_team(
            _info(admin),
            input=SoftDeleteByGuidInput(id=GUID(str(victim_team.guid))),
        )

    assert result.ok is False
    assert result.errors[0].code == "NOT_FOUND"
    victim_team.refresh_from_db()
    assert victim_team.deleted_at is None


def test_soft_delete_team_same_org_succeeds(permission_resolver):
    org = _org("legit-team-del")
    team = Team.objects.create(organization=org, slug="byebye", name="Bye Bye")
    admin = _user()
    permission_resolver.grant(Permission.TEAM_DELETE)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=admin.id)):
        result = IdentityMutation().soft_delete_team(
            _info(admin),
            input=SoftDeleteByGuidInput(id=GUID(str(team.guid))),
        )

    assert result.ok is True, result.errors
    team.refresh_from_db()
    assert team.deleted_at is not None


# =====================================================================
# Members / team members — PII reads
# =====================================================================


def test_members_returns_only_caller_org_members(permission_resolver):
    """astroliftMembers must not leak another org's member roster (PII)."""
    caller = _org("caller-members")
    other = _org("other-members")
    alice = _user("alice-1183@example.test")
    bob = _user("bob-1183@example.test")
    _org_member(caller, alice)
    _org_member(other, bob)
    admin = _user()
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with tenant_context(TenantContext(organization_id=caller.id, actor_user_id=admin.id)):
        rows = IdentityQuery().astrolift_members(_info(admin))

    returned = {int(r.user.id) for r in rows}
    assert alice.pk in returned
    assert bob.pk not in returned


def test_team_members_another_org_team_returns_empty(permission_resolver):
    """A team guid from another org yields an empty roster, not that
    org's members (PII)."""
    victim = _org("victim-teammembers")
    victim_team = Team.objects.create(organization=victim, slug="vt", name="VT")
    member_user = _user()
    Member.objects.create(
        user=member_user,
        scope_kind=Member.ScopeKind.TEAM,
        scope_id=victim_team.pk,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    caller = _org("attacker-teammembers")
    admin = _user()
    permission_resolver.grant(Permission.TEAM_READ)

    with tenant_context(TenantContext(organization_id=caller.id, actor_user_id=admin.id)):
        rows = IdentityQuery().astrolift_team_members(_info(admin), team_id=GUID(str(victim_team.guid)))

    assert rows == []


def test_team_members_same_org_returns_members(permission_resolver):
    org = _org("legit-teammembers")
    team = Team.objects.create(organization=org, slug="t", name="T")
    member_user = _user()
    m = Member.objects.create(
        user=member_user,
        scope_kind=Member.ScopeKind.TEAM,
        scope_id=team.pk,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    admin = _user()
    permission_resolver.grant(Permission.TEAM_READ)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=admin.id)):
        rows = IdentityQuery().astrolift_team_members(_info(admin), team_id=GUID(str(team.guid)))

    assert {str(r.id) for r in rows} == {str(m.guid)}
