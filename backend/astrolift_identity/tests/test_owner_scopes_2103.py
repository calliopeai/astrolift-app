"""Identity owners and credential ceilings, exercised through real grants (#2103)."""

import inspect
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.conf import settings
from django.test import Client

from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member, Organization, Project, Team
from astrolift_identity.schema.mutations import (
    CreateProjectInput,
    IdentityMutation,
    SoftDeleteByGuidInput,
    UpdateProjectInput,
    UpdateTeamInput,
)
from astrolift_identity.schema.queries import IdentityQuery
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db

# Organization administration never borrows a selected subordinate grant.
ORG_ROUTES = (
    ("astrolift_organization", "query"),
    ("astrolift_members", "query"),
    ("astrolift_members_page", "query"),
    ("astrolift_org_members_for_approval_picker", "query"),
    ("astrolift_invitations", "query"),
    ("astrolift_invitations_page", "query"),
    ("astrolift_roles", "query"),
    ("astrolift_roles_page", "query"),
    ("astrolift_searchable_users", "query"),
    ("astrolift_roles_i_can_grant", "query"),
    ("astrolift_group_role_mappings_page", "query"),
    ("astrolift_access_on", "query"),
    ("astrolift_principal_search", "query"),
    ("astrolift_grant_preview", "query"),
    ("astrolift_policy_simulation", "query"),
    ("astrolift_policy_condition_catalog", "query"),
    ("astrolift_role", "query"),
    ("astrolift_policy", "query"),
    ("astrolift_api_tokens", "query"),
    ("astrolift_api_token_scope_catalog", "query"),
    ("astrolift_api_tokens_page", "query"),
    ("astrolift_policies", "query"),
    ("astrolift_policies_page", "query"),
    ("astrolift_organization_allowlist_domains", "query"),
    ("astrolift_identity_providers", "query"),
    ("add_organization_allowlist_domain", "mutation"),
    ("remove_organization_allowlist_domain", "mutation"),
    ("create_identity_provider", "mutation"),
    ("update_identity_provider", "mutation"),
    ("set_active_identity_provider", "mutation"),
    ("soft_delete_identity_provider", "mutation"),
    ("create_invitation", "mutation"),
    ("revoke_invitation", "mutation"),
    ("delete_invitation", "mutation"),
    ("resend_invitation", "mutation"),
    ("set_organization_module", "mutation"),
    ("update_organization", "mutation"),
    ("soft_delete_organization", "mutation"),
    ("create_policy", "mutation"),
    ("update_policy", "mutation"),
    ("soft_delete_policy", "mutation"),
    ("mark_onboarding_complete", "mutation"),
    ("create_group_role_mapping", "mutation"),
    ("delete_group_role_mapping", "mutation"),
    ("create_role", "mutation"),
    ("update_role", "mutation"),
    ("soft_delete_role", "mutation"),
    ("create_team", "mutation"),
    ("create_api_token", "mutation"),
    ("revoke_api_token", "mutation"),
)


@pytest.fixture(name="world")
def _world(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    world = ScopeWorld("identity-2103")
    world.user = make_user("identity-2103")
    world.other_org = Organization.objects.create(name="Other", slug="other-identity-2103")
    return world


def _grant(world, kind="ORG", *, foreign=False):
    return bind_role(
        world.user,
        permissions=list(Permission),
        kind=kind,
        scope_id=world.other_org.pk
        if foreign
        else {
            "ORG": world.org.pk,
            "TEAM": world.medops.pk,
            "PROJECT": world.medops_project.pk,
            "APP": world.medops_app.pk,
        }[kind],
        slug=f"identity-{uuid4().hex}",
    )


def _tenant(world):
    return tenant_context(
        TenantContext(
            organization_id=world.org.pk,
            actor_user_id=world.user.pk,
            team_id=world.medops.pk,
            project_id=world.medops_project.pk,
        )
    )


@contextmanager
def _token(world, ceiling="org"):
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.other_org if ceiling == "foreign" else world.org,
        team=world.medops if ceiling == "team" else None,
        name="identity 2103",
        token_hash=uuid4().hex,
        scopes=["read:apps"] if ceiling == "read" else ["admin"],
    )
    handle = set_current_api_token(token)
    try:
        yield token
    finally:
        reset_current_api_token(handle)


def _org_invoke(world, route):
    """Deny cases still go through the public resolver and its outer audit gate."""
    name, kind = route
    method = getattr(IdentityQuery() if kind == "query" else IdentityMutation(), name)
    kwargs = {}
    for key, parameter in inspect.signature(method).parameters.items():
        if key != "info" and parameter.default is inspect.Parameter.empty:
            kwargs[key] = SimpleNamespace(id=world.org.guid) if key == "input" else str(world.org.guid)
    return method(make_info(world.user), **kwargs)


def _assert_org_denied(world, route):
    if route[1] == "query":
        with pytest.raises(PermissionDenied):
            _org_invoke(world, route)
    else:
        result = _org_invoke(world, route)
        assert not result.ok and result.errors[0].code == "PERMISSION_DENIED", result


@pytest.mark.parametrize("route", ORG_ROUTES, ids=lambda row: row[0])
@pytest.mark.parametrize("kind", ["TEAM", "PROJECT", "APP", "foreign-ORG", "none"])
def test_org_administration_never_uses_selected_or_foreign_grant(world, route, kind):
    if kind != "none":
        _grant(world, "ORG" if kind == "foreign-ORG" else kind, foreign=kind == "foreign-ORG")
    with _tenant(world):
        _assert_org_denied(world, route)


@pytest.mark.parametrize("route", ORG_ROUTES, ids=lambda row: row[0])
@pytest.mark.parametrize("ceiling", ["team", "foreign"])
def test_org_administration_bearer_cannot_borrow_actor_org_grant(world, route, ceiling):
    _grant(world)
    # Even the platform operator keeps the credential owner ceiling.
    world.user.is_superuser = True
    world.user.save(update_fields=["is_superuser"])
    with _tenant(world), _token(world, ceiling):
        _assert_org_denied(world, route)


@pytest.mark.parametrize(
    "name",
    [
        "astrolift_members",
        "astrolift_members_page",
        "astrolift_invitations",
        "astrolift_invitations_page",
        "astrolift_roles",
        "astrolift_roles_page",
        "astrolift_roles_i_can_grant",
        "astrolift_role_bindings",
        "astrolift_role_bindings_page",
        "astrolift_group_role_mappings_page",
        "astrolift_api_tokens",
        "astrolift_api_tokens_page",
        "astrolift_api_token_scope_catalog",
        "astrolift_policies",
        "astrolift_policies_page",
        "astrolift_policy_condition_catalog",
        "astrolift_organization_allowlist_domains",
        "astrolift_identity_providers",
    ],
)
@pytest.mark.parametrize("credential", ["session", "org-token"])
def test_org_administration_preserves_real_org_grant_reads(world, name, credential):
    _grant(world)
    with _tenant(world):
        if credential == "org-token":
            with _token(world):
                assert getattr(IdentityQuery(), name)(make_info(world.user)) is not None
        else:
            assert getattr(IdentityQuery(), name)(make_info(world.user)) is not None


OWNER_ROUTES = (
    "update_team",
    "soft_delete_team",
    "update_project",
    "soft_delete_project",
    "create_project",
    "astrolift_team_members",
    "astrolift_team_slug_available",
    "astrolift_project_slug_available",
)


def _owner_invoke(world, name, *, sibling=False, missing=False):
    team = world.platform if sibling else world.medops
    project = world.platform_project if sibling else world.medops_project
    team_guid, project_guid = (uuid4(), uuid4()) if missing else (team.guid, project.guid)
    kwargs = {
        "update_team": {"input": UpdateTeamInput(id=team_guid, name="Changed")},
        "soft_delete_team": {"input": SoftDeleteByGuidInput(id=team_guid)},
        "update_project": {"input": UpdateProjectInput(id=project_guid, name="Changed")},
        "soft_delete_project": {"input": SoftDeleteByGuidInput(id=project_guid)},
        "create_project": {
            "input": CreateProjectInput(team_id=team_guid, name="Created", slug="created-2103")
        },
        "astrolift_team_members": {"team_id": team_guid},
        "astrolift_team_slug_available": {"slug": team.slug, "exclude_id": team_guid},
        "astrolift_project_slug_available": {
            "team_id": team_guid,
            "slug": project.slug,
            "exclude_id": project_guid,
        },
    }[name]
    cls = IdentityQuery if name.startswith("astrolift_") else IdentityMutation
    return getattr(cls(), name)(make_info(world.user), **kwargs)


def _owner_denied(world, name, **kwargs):
    if name.startswith("astrolift_"):
        with pytest.raises(PermissionDenied):
            _owner_invoke(world, name, **kwargs)
    else:
        result = _owner_invoke(world, name, **kwargs)
        assert not result.ok and result.errors[0].code == "PERMISSION_DENIED", result


@pytest.mark.parametrize("name", OWNER_ROUTES)
def test_team_binding_reaches_only_its_live_owner(world, name):
    _grant(world, "TEAM")
    with _tenant(world):
        _owner_denied(world, name, sibling=True)
        _owner_denied(world, name, missing=True)
        result = _owner_invoke(world, name)
    if name.startswith("astrolift_"):
        assert result == ([] if name == "astrolift_team_members" else True)
    else:
        assert result.ok, result.errors


@pytest.mark.parametrize("name", OWNER_ROUTES)
@pytest.mark.parametrize("operator", [False, True])
def test_team_bearer_with_org_actor_stays_inside_live_team(world, name, operator):
    _grant(world)
    world.user.is_superuser = operator
    world.user.save(update_fields=["is_superuser"])
    with _tenant(world), _token(world, "team"):
        _owner_denied(world, name, sibling=True)
        _owner_denied(world, name, missing=True)
        result = _owner_invoke(world, name)
    assert (
        result == ([] if name == "astrolift_team_members" else True)
        if name.startswith("astrolift_")
        else result.ok
    )


@pytest.mark.parametrize(
    "name", ["update_project", "soft_delete_project", "astrolift_project_slug_available"]
)
def test_project_grant_covers_its_edit_and_slug_check(world, name):
    _grant(world, "PROJECT")
    with _tenant(world):
        _owner_denied(world, name, sibling=True)
        result = _owner_invoke(world, name)
    assert result is True if name.startswith("astrolift_") else result.ok


def test_project_slug_check_cannot_name_a_sibling_parent(world):
    _grant(world, "PROJECT")
    with _tenant(world), pytest.raises(PermissionDenied):
        IdentityQuery().astrolift_project_slug_available(
            make_info(world.user),
            team_id=world.platform.guid,
            slug="probe-2103",
            exclude_id=world.medops_project.guid,
        )


@pytest.mark.parametrize("owner", ["deleted-team", "foreign-team", "foreign-project"])
@pytest.mark.parametrize("name", ["update_project", "soft_delete_project"])
def test_org_actor_cannot_mutate_a_project_with_stale_ancestry(world, owner, name):
    _grant(world)
    if owner == "deleted-team":
        world.medops.soft_delete()
    elif owner == "foreign-team":
        Team.objects.filter(pk=world.medops.pk).update(organization=world.other_org)
    else:
        Project.objects.filter(pk=world.medops_project.pk).update(organization=world.other_org)
    with _tenant(world):
        result = _owner_invoke(world, name)
    assert not result.ok and result.errors[0].code == "NOT_FOUND", result
    project = Project.all_objects.get(pk=world.medops_project.pk)
    assert project.name == "Intake" and project.deleted_at is None


@pytest.mark.parametrize(
    "name", ["astrolift_teams", "astrolift_teams_page", "astrolift_projects", "astrolift_projects_page"]
)
@pytest.mark.parametrize("credential", ["TEAM", "ORG", "team-token", "foreign-token", "deleted-token-team"])
def test_collection_filters_owner_and_credential_before_rows_and_count(world, name, credential):
    _grant(world, "TEAM" if credential == "TEAM" else "ORG")
    query = getattr(IdentityQuery(), name)
    expected = [world.medops.slug] if "teams" in name else [world.medops_project.slug]
    if credential == "ORG":
        expected += [world.platform.slug] if "teams" in name else [world.platform_project.slug]
    if credential in {"foreign-token", "deleted-token-team"}:
        expected = []
    with _tenant(world):
        if credential.endswith("token") or credential == "deleted-token-team":
            with _token(world, "foreign" if credential == "foreign-token" else "team"):
                if credential == "deleted-token-team":
                    world.medops.soft_delete()
                result = query(make_info(world.user))
        else:
            result = query(make_info(world.user))
    rows = result.items if name.endswith("_page") else result
    assert sorted(row.slug for row in rows) == sorted(expected)
    if name.endswith("_page"):
        assert result.total_count == len(expected)


def test_project_collection_excludes_stale_denormalized_organization(world):
    _grant(world)
    Team.objects.filter(pk=world.medops.pk).update(organization=world.other_org)
    with _tenant(world):
        page = IdentityQuery().astrolift_projects_page(make_info(world.user))
    assert [row.slug for row in page.items] == [world.platform_project.slug]
    assert page.total_count == 1


@pytest.mark.parametrize("kind", ["TEAM", "ORG", "team-token", "org-token", "read-token"])
def test_http_identity_org_gate_and_collection_counts(world, kind):
    _grant(world, "TEAM" if kind == "TEAM" else "ORG")
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    client = Client()
    headers = {
        "HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid),
        "HTTP_X_ASTROLIFT_TEAM": str(world.medops.pk),
    }
    if kind.endswith("token"):
        issued = mint_token()
        with _token(
            world, "team" if kind == "team-token" else "read" if kind == "read-token" else "org"
        ) as token:
            token.token_hash = issued.token_hash
            token.save(update_fields=["token_hash"])
        headers["HTTP_AUTHORIZATION"] = f"Bearer {issued.plaintext}"
    else:
        client.force_login(world.user)
        headers["HTTP_X_PLATFORM"] = "WEB"

    def post(query):
        response = client.post(
            f"/{settings.BASE_URL}gql/config/",
            data={"query": query},
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200, response.content
        return response.json()

    org_result = post("{ astroliftRoles { id } }")
    if kind in {"ORG", "org-token", "read-token"}:
        assert not org_result.get("errors"), org_result
    else:
        assert org_result.get("errors") and org_result.get("data") is None, org_result
    rows = post("{ astroliftTeamsPage { totalCount items { slug } } }")
    assert not rows.get("errors"), rows
    expected = (
        [world.medops.slug] if kind in {"TEAM", "team-token"} else [world.medops.slug, world.platform.slug]
    )
    assert sorted(row["slug"] for row in rows["data"]["astroliftTeamsPage"]["items"]) == sorted(expected)
    assert rows["data"]["astroliftTeamsPage"]["totalCount"] == len(expected)
    mutation = post(
        'mutation { createTeam(input: { organizationId: "'
        + str(world.org.guid)
        + '", name: "New", slug: "new-2103" }) { ok errors { code } } }'
    )
    assert not mutation.get("errors"), mutation
    assert mutation["data"]["createTeam"]["ok"] is (kind in {"ORG", "org-token"}), mutation
    assert Team.objects.filter(organization=world.org, slug="new-2103").exists() is (
        kind in {"ORG", "org-token"}
    )


@pytest.fixture(name="bindings")
def _bindings(world):
    from astrolift_identity.models import Role, RoleBinding

    target = make_user("identity-target-2103")
    Member.objects.create(user=target, scope_kind="ORG", scope_id=world.org.pk)
    role = Role.objects.create(
        organization=world.org,
        name="Viewer",
        slug="viewer-2103",
        scope_level="TEAM",
        permissions=[Permission.APP_READ.value],
    )
    scopes = {
        "org": ("ORG", world.org),
        "own": ("TEAM", world.medops),
        "sibling": ("TEAM", world.platform),
        "project": ("PROJECT", world.medops_project),
        "app": ("APP", world.medops_app),
    }
    rows = {
        key: RoleBinding.objects.create(user=target, role=role, scope_kind=kind, scope_id=owner.pk)
        for key, (kind, owner) in scopes.items()
    }
    return SimpleNamespace(target=target, role=role, scopes=scopes, rows=rows)


def _binding_invoke(world, bindings, name, target):
    from astrolift_identity.schema.mutations import (
        GrantRoleInput,
        RevokeRoleBindingInput,
        UpdateRoleBindingInput,
    )

    row = bindings.rows[target]
    if name == "grant_role":
        kind, owner = bindings.scopes[target]
        # A distinct group principal avoids the fixture's existing user grant.
        payload = GrantRoleInput(
            role_id=bindings.role.guid,
            scope_kind=kind,
            scope_guid=owner.guid,
            group_external_id="grant-group-2103",
        )
    elif name == "update_role_binding":
        payload = UpdateRoleBindingInput(id=row.guid, expires_at=None)
    else:
        payload = RevokeRoleBindingInput(id=row.guid)
    return getattr(IdentityMutation(), name)(make_info(world.user), input=payload)


@pytest.mark.parametrize("name", ["grant_role", "update_role_binding", "revoke_role_binding"])
@pytest.mark.parametrize("credential", ["team-grant", "team-token", "org"])
def test_binding_changes_use_actual_target_and_retain_grant_ceiling(world, bindings, name, credential):
    _grant(world, "TEAM" if credential == "team-grant" else "ORG")

    def run():
        for target in ["sibling", "org"]:
            result = _binding_invoke(world, bindings, name, target)
            if credential == "org":
                assert result.ok, result.errors
            else:
                assert not result.ok and result.errors[0].code == "PERMISSION_DENIED", result
                bindings.rows[target].refresh_from_db()
                assert bindings.rows[target].deleted_at is None
        for target in ["own", "project", "app"]:
            result = _binding_invoke(world, bindings, name, target)
            assert result.ok, result.errors

    with _tenant(world):
        if credential == "team-token":
            with _token(world, "team"):
                run()
        else:
            run()


@pytest.mark.parametrize("name", ["astrolift_role_bindings", "astrolift_role_bindings_page"])
@pytest.mark.parametrize("credential", ["team-grant", "team-token", "org", "foreign-token"])
def test_binding_collections_filter_polymorphic_owners_before_count(world, bindings, name, credential):
    actor_binding = _grant(world, "TEAM" if credential == "team-grant" else "ORG")

    def run():
        return getattr(IdentityQuery(), name)(make_info(world.user))

    with _tenant(world):
        if credential.endswith("token"):
            with _token(world, "foreign" if credential == "foreign-token" else "team"):
                result = run()
        else:
            result = run()
    rows = result.items if name.endswith("_page") else result
    keys = ["org", "own", "sibling", "project", "app"] if credential == "org" else ["own", "project", "app"]
    expected = {str(bindings.rows[key].guid) for key in keys}
    if credential in {"team-grant", "org"}:
        expected.add(str(actor_binding.guid))
    if credential == "foreign-token":
        expected = set()
    assert {str(row.id) for row in rows} == expected
    if name.endswith("_page"):
        assert result.total_count == len(expected)


@pytest.mark.parametrize("credential", ["team-grant", "team-token", "org"])
def test_bulk_revoke_keeps_sibling_and_org_rows_outside_actual_reach(world, bindings, credential):
    from astrolift_identity.schema.mutations import BulkRevokeRoleBindingsInput

    _grant(world, "TEAM" if credential == "team-grant" else "ORG")

    def run():
        return IdentityMutation().bulk_revoke_astrolift_role_bindings(
            make_info(world.user),
            input=BulkRevokeRoleBindingsInput(binding_ids=[row.guid for row in bindings.rows.values()]),
        )

    with _tenant(world):
        if credential == "team-token":
            with _token(world, "team"):
                result = run()
        else:
            result = run()
    assert result.ok, result.errors
    expected = {key: credential == "org" or key in {"own", "project", "app"} for key in bindings.rows}
    assert {str(row.id): row.ok for row in result.data.results} == {
        str(row.guid): expected[key] for key, row in bindings.rows.items()
    }
    for key, row in bindings.rows.items():
        row.refresh_from_db()
        assert (row.deleted_at is not None) is expected[key]


@pytest.mark.parametrize("name", ["grant_role", "update_role_binding", "revoke_role_binding"])
def test_binding_mutation_missing_target_requires_explicit_org_even_with_team_selected(world, bindings, name):
    _grant(world, "TEAM")
    bindings.rows["own"].guid = uuid4()
    if name == "grant_role":
        bindings.scopes["own"][1].guid = uuid4()
    with _tenant(world):
        result = _binding_invoke(world, bindings, name, "own")
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED", result


def test_binding_grant_rejects_incoherent_project_owner_before_write(world, bindings):
    from astrolift_identity.models import RoleBinding

    _grant(world)
    Team.objects.filter(pk=world.medops.pk).update(organization=world.other_org)
    before = RoleBinding.objects.count()
    with _tenant(world):
        result = _binding_invoke(world, bindings, "grant_role", "project")
    assert not result.ok and result.errors[0].code == "NOT_FOUND", result
    assert RoleBinding.objects.count() == before


@pytest.mark.parametrize("credential", ["TEAM", "team-token", "ORG"])
def test_bulk_team_role_assignment_checks_destination_and_real_writes(world, bindings, credential):
    from astrolift_identity.models import RoleBinding
    from astrolift_identity.schema.mutations import BulkAssignTeamMemberRolesInput

    _grant(world, "TEAM" if credential == "TEAM" else "ORG")
    new_user = make_user("bulk-target-2103")
    own_member = Member.objects.create(user=new_user, scope_kind="TEAM", scope_id=world.medops.pk)
    sibling_member = Member.objects.create(user=new_user, scope_kind="TEAM", scope_id=world.platform.pk)

    def run():
        for team, member, expected in [
            (world.platform, sibling_member, credential == "ORG"),
            (world.medops, own_member, True),
        ]:
            result = IdentityMutation().bulk_assign_astrolift_team_member_roles(
                make_info(world.user),
                input=BulkAssignTeamMemberRolesInput(
                    team_id=team.guid,
                    role_id=bindings.role.guid,
                    member_ids=[member.guid],
                ),
            )
            assert result.ok is expected, result
            assert (
                RoleBinding.objects.filter(
                    user=new_user, role=bindings.role, scope_kind="TEAM", scope_id=team.pk
                ).exists()
                is expected
            )
            if expected:
                assert result.data.assigned_count == 1
            else:
                assert result.errors[0].code == "PERMISSION_DENIED"

    with _tenant(world):
        if credential == "team-token":
            with _token(world, "team"):
                run()
        else:
            run()


@pytest.mark.parametrize("credential", ["TEAM", "team-token", "ORG", "read-token"])
def test_http_binding_collection_and_revoke_use_actual_owner(world, bindings, credential):
    _grant(world, "TEAM" if credential == "TEAM" else "ORG")
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    client = Client()
    headers = {
        "HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid),
        "HTTP_X_ASTROLIFT_TEAM": str(world.platform.pk),
    }
    if credential.endswith("token"):
        issued = mint_token()
        with _token(world, "team" if credential == "team-token" else "read") as token:
            token.token_hash = issued.token_hash
            token.save(update_fields=["token_hash"])
        headers["HTTP_AUTHORIZATION"] = f"Bearer {issued.plaintext}"
    else:
        client.force_login(world.user)
        headers["HTTP_X_PLATFORM"] = "WEB"

    def post(query):
        response = client.post(
            f"/{settings.BASE_URL}gql/config/",
            data={"query": query},
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200, response.content
        return response.json()

    page = post('{ astroliftRoleBindingsPage(search: "identity-target-2103") { totalCount items { id } } }')
    expected = {
        str(row.guid)
        for key, row in bindings.rows.items()
        if credential == "ORG" or key in {"own", "project", "app"}
    }
    if credential == "read-token":
        assert page.get("errors") and page.get("data") is None, page
    else:
        assert not page.get("errors"), page
        assert {row["id"] for row in page["data"]["astroliftRoleBindingsPage"]["items"]} == expected
        assert page["data"]["astroliftRoleBindingsPage"]["totalCount"] == len(expected)
    for target in ["sibling", "own"]:
        result = post(
            'mutation { revokeRoleBinding(input: { id: "'
            + str(bindings.rows[target].guid)
            + '" }) { ok errors { code } } }'
        )
        assert not result.get("errors"), result
        allowed = credential == "ORG" or (credential != "read-token" and target == "own")
        assert result["data"]["revokeRoleBinding"]["ok"] is allowed, result
        bindings.rows[target].refresh_from_db()
        assert (bindings.rows[target].deleted_at is not None) is allowed


def test_binding_collection_keeps_explicit_org_rows_under_target_policy_denies(world, bindings):
    from astrolift_identity.models import Policy

    _grant(world)
    Policy.objects.create(
        organization=world.org,
        name="Deny selected team",
        slug="binding-deny-2103",
        effect="DENY",
        action_pattern=Permission.ORG_MANAGE_MEMBERS.value,
        scope_level="TEAM",
        scope_id=world.medops.pk,
    )
    with _tenant(world):
        page = IdentityQuery().astrolift_role_bindings_page(
            make_info(world.user), search="identity-target-2103"
        )
    ids = {str(row.id) for row in page.items}
    assert str(bindings.rows["org"].guid) in ids
    assert str(bindings.rows["own"].guid) not in ids
    assert page.total_count == len(ids)


def test_stale_app_binding_cleanup_uses_org_ceiling_and_cannot_borrow_old_team_grants(world, bindings):
    from astrolift_identity.schema.mutations import RevokeRoleBindingInput

    bind_role(
        world.user,
        permissions=[Permission.ORG_MANAGE_MEMBERS],
        kind="ORG",
        scope_id=world.org.pk,
        slug="cleanup-manager-2103",
    )
    bind_role(
        world.user,
        permissions=[Permission.APP_READ],
        kind="TEAM",
        scope_id=world.medops.pk,
        slug="cleanup-team-2103",
    )
    Project.objects.filter(pk=world.medops_project.pk).update(team=world.platform)
    row = bindings.rows["app"]
    with _tenant(world):
        result = IdentityMutation().revoke_role_binding(
            make_info(world.user), input=RevokeRoleBindingInput(id=row.guid)
        )
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED", result
    row.refresh_from_db()
    assert row.deleted_at is None
