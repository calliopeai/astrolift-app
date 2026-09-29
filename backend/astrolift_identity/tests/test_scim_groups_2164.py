"""Real PostgreSQL + HTTP SCIM group changes revoke their dynamic grants."""

import json
from uuid import uuid4

import pytest
from django.test import Client

from astrolift_identity.idp_groups import group_member_counts, member_groups, sync_member_groups
from astrolift_identity.models import GroupRoleMapping, Member, ScimGroup
from astrolift_identity.permission_resolver import resolve
from astrolift_identity.scim import SCHEMA_GROUP
from astrolift_identity.scim_views import mint_scim_token
from astrolift_identity.tests.test_access_enforcement_2157 import _no_opensearch  # noqa: F401
from core.permissions import Permission, PermissionScope, ScopeKind
from core.tenancy import TenantContext
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_user

pytestmark = pytest.mark.django_db
GROUPS = "/api/scim/v2/Groups"


@pytest.fixture
def world():
    world = ScopeWorld(f"scim-{uuid4().hex[:8]}")
    token, digest = mint_scim_token()
    world.org.scim_enabled = True
    world.org.scim_token_hash = digest
    world.org.save()
    world.auth = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
    world.members = [
        Member.objects.create(user=make_user(f"scim-{uuid4().hex}"), scope_kind="ORG", scope_id=world.org.pk)
        for _ in range(2)
    ]
    world.client = Client()
    return world


def create(w, *, external="idp-engineering", members=None, **overrides):
    body = {
        "schemas": [SCHEMA_GROUP],
        "displayName": "Engineering",
        "externalId": external,
        "members": [{"value": str(m.guid)} for m in (w.members[:1] if members is None else members)],
    }
    return w.client.post(
        GROUPS, data=json.dumps({**body, **overrides}), content_type="application/scim+json", **w.auth
    )


def patch(w, guid, *operations, **headers):
    return w.client.patch(
        f"{GROUPS}/{guid}",
        data=json.dumps({"Operations": list(operations)}),
        content_type="application/scim+json",
        **w.auth,
        **headers,
    )


def tenant(w, member=None):
    return TenantContext(organization_id=w.org.pk, actor_user_id=(member or w.members[0]).user_id)


def test_group_membership_grants_and_revokes_without_changing_direct_bindings(world):
    w = world
    member = w.members[0]
    mapping_role = bind_role(
        member.user,
        permissions=[Permission.APP_DEPLOY],
        kind="APP",
        scope_id=w.platform_app.pk,
        slug=f"mapped-{uuid4().hex}",
    ).role
    # Use a mapping for medops while preserving an independent direct
    # binding to platform throughout the membership changes.
    GroupRoleMapping.objects.create(
        organization=w.org,
        group_external_id="idp-engineering",
        role=mapping_role,
        scope_kind="APP",
        scope_id=w.medops_app.pk,
    )
    scope = PermissionScope(kind=ScopeKind.APP, id=w.medops_app.pk)
    assert not resolve(tenant(w), Permission.APP_DEPLOY, scope)[0]
    response = create(w)
    assert response.status_code == 201
    guid = response.json()["id"]
    assert response["Location"] == f"{GROUPS}/{guid}"
    assert resolve(tenant(w), Permission.APP_DEPLOY, scope)[0]
    assert group_member_counts(w.org.pk, ["idp-engineering"]) == {"idp-engineering": 1}
    # A later SSO snapshot containing the removed group must not restore
    # SCIM authority, while an unrelated SSO-only group stays intact.
    sync_member_groups(member.user, {"groups": ["idp-engineering", "sso-only"]})
    response = patch(w, guid, {"op": "remove", "path": f'members[value eq "{member.guid}"]'})
    assert response.status_code == 200
    assert not resolve(tenant(w), Permission.APP_DEPLOY, scope)[0]
    assert member_groups(member.user_id, w.org.pk) == {"sso-only"}
    assert resolve(
        tenant(w), Permission.APP_DEPLOY, PermissionScope(kind=ScopeKind.APP, id=w.platform_app.pk)
    )[0]
    assert group_member_counts(w.org.pk, ["idp-engineering"]) == {"idp-engineering": 0}


def test_crud_filters_pagination_replace_and_etag(world):
    w = world
    first = create(w).json()
    second = create(w, external="other", displayName="Operations", members=[]).json()
    response = w.client.get(GROUPS, {"filter": 'displayName co "engin"'}, **w.auth)
    assert response.status_code == 200
    assert response.json()["totalResults"] == 1
    assert response.json()["Resources"][0]["id"] == first["id"]
    page = w.client.get(GROUPS, {"startIndex": 2, "count": 1}, **w.auth).json()
    assert page["totalResults"] == 2 and page["itemsPerPage"] == 1
    assert page["Resources"][0]["id"] == second["id"]
    old = w.client.get(f"{GROUPS}/{first['id']}", **w.auth)
    response = w.client.put(
        f"{GROUPS}/{first['id']}",
        data=json.dumps({"displayName": "Renamed", "members": [{"value": str(w.members[1].guid)}]}),
        content_type="application/scim+json",
        **w.auth,
        HTTP_IF_MATCH=old["ETag"],
    )
    assert response.status_code == 200
    assert response.json()["displayName"] == "Renamed"
    assert response.json()["externalId"] == "idp-engineering"
    assert [m["value"] for m in response.json()["members"]] == [str(w.members[1].guid)]
    assert (
        patch(
            w,
            first["id"],
            {"op": "replace", "path": "displayName", "value": "Stale"},
            HTTP_IF_MATCH=old["ETag"],
        ).status_code
        == 412
    )
    assert w.client.delete(f"{GROUPS}/{first['id']}", **w.auth).status_code == 204
    assert w.client.get(f"{GROUPS}/{first['id']}", **w.auth).status_code == 404
    assert w.client.get(GROUPS, **w.auth).json()["totalResults"] == 1


def test_patch_add_replace_remove_all_and_no_path_are_atomic(world):
    w = world
    guid = create(w, members=[]).json()["id"]
    add = {"op": "add", "path": "members", "value": [{"value": str(m.guid)} for m in w.members]}
    assert len(patch(w, guid, add, add).json()["members"]) == 2
    value = {"displayName": "No path", "members": [{"value": str(w.members[1].guid)}]}
    response = patch(w, guid, {"op": "replace", "value": value})
    assert response.json()["displayName"] == "No path"
    assert len(response.json()["members"]) == 1
    before = ScimGroup.objects.get(guid=guid).version
    response = patch(
        w,
        guid,
        {"op": "replace", "path": "displayName", "value": "Must roll back"},
        {"op": "add", "path": "members", "value": [{"value": str(uuid4())}]},
    )
    assert response.status_code == 400
    group = ScimGroup.objects.get(guid=guid)
    assert group.display_name == "No path" and group.version == before
    assert group.members.count() == 1
    assert patch(w, guid, {"op": "remove", "path": "members"}).json()["members"] == []


def test_token_and_member_references_are_org_confined(world):
    w = world
    foreign = ScopeWorld(f"foreign-{uuid4().hex[:8]}")
    foreign_member = Member.objects.create(
        user=make_user(f"foreign-{uuid4().hex}"), scope_kind="ORG", scope_id=foreign.org.pk
    )
    assert create(w, members=[foreign_member]).status_code == 400
    assert not ScimGroup.objects.filter(organization=w.org).exists()
    group = ScimGroup.objects.create(organization=foreign.org, display_name="Foreign")
    for method in ("get", "patch", "put", "delete"):
        response = getattr(w.client, method)(f"{GROUPS}/{group.guid}", **w.auth)
        assert response.status_code == 404
    assert w.client.get(GROUPS).status_code == 401
    assert w.client.get(GROUPS, HTTP_AUTHORIZATION="Bearer alft_st_bad").status_code == 401
    w.org.scim_enabled = False
    w.org.save()
    assert w.client.get(GROUPS, **w.auth).status_code == 401


def test_group_delete_and_user_deprovision_cannot_resurrect_old_memberships(world):
    w = world
    member = w.members[0]
    guid = create(w).json()["id"]
    sync_member_groups(member.user, {"groups": ["idp-engineering", "unmanaged"]})
    assert w.client.delete(f"{GROUPS}/{guid}", **w.auth).status_code == 204
    assert member_groups(member.user_id, w.org.pk) == {"unmanaged"}
    fresh = create(w).json()["id"]
    assert w.client.delete(f"/api/scim/v2/Users/{member.guid}", **w.auth).status_code == 204
    assert ScimGroup.objects.get(guid=fresh).members.count() == 0
    member.refresh_from_db()
    assert not member.is_active


@pytest.mark.parametrize(
    "payload",
    [
        {"displayName": ""},
        {"displayName": ["Bad"]},
        {"members": ["invalid"]},
        {"members": [{"value": "invalid"}]},
        {"members": {}},
        {"externalId": 7},
    ],
)
def test_invalid_create_writes_nothing(world, payload):
    body = {"schemas": [SCHEMA_GROUP], "displayName": "Invalid", "externalId": "bad", **payload}
    assert (
        world.client.post(
            GROUPS, data=json.dumps(body), content_type="application/scim+json", **world.auth
        ).status_code
        == 400
    )
    assert not ScimGroup.objects.filter(organization=world.org).exists()


def test_group_duplicate_identity_and_rekey_does_not_restore_old_claims(world):
    w = world
    guid = create(w).json()["id"]
    assert create(w).status_code == 409
    sync_member_groups(w.members[0].user, {"groups": ["idp-engineering"]})
    assert patch(w, guid, {"op": "replace", "path": "externalId", "value": "different"}).status_code == 200
    assert ScimGroup.objects.get(guid=guid).external_id == "different"
    assert member_groups(w.members[0].user_id, w.org.pk) == {"different"}
    assert group_member_counts(w.org.pk, ["idp-engineering"]) == {"idp-engineering": 0}
    no_external = create(w, external="").json()
    assert "externalId" not in no_external
    assert ScimGroup.objects.get(guid=no_external["id"]).group_external_id == no_external["id"]


def test_scim_membership_drives_group_bindings_and_actor_policies(world):
    from astrolift_identity.models import Policy, RoleBinding

    w = world
    role = bind_role(
        w.members[1].user,
        permissions=[Permission.APP_DEPLOY],
        kind="APP",
        scope_id=w.medops_app.pk,
        slug=f"group-role-{uuid4().hex}",
    ).role
    RoleBinding.objects.create(
        role=role, group_external_id="idp-engineering", scope_kind="APP", scope_id=w.medops_app.pk
    )
    scope = PermissionScope(kind=ScopeKind.APP, id=w.medops_app.pk)
    assert not resolve(tenant(w), Permission.APP_DEPLOY, scope)[0]
    guid = create(w).json()["id"]
    assert resolve(tenant(w), Permission.APP_DEPLOY, scope)[0]
    # ABAC consumes the exact same group authority as group-derived RBAC.
    Policy.objects.create(
        organization=w.org,
        name="Group denial",
        slug="group-denial",
        action_pattern="app.deploy",
        actor_pattern={"user_in_groups": ["idp-engineering"]},
        conditions=[],
    )
    assert not resolve(tenant(w), Permission.APP_DEPLOY, scope)[0]
    assert resolve(tenant(w, w.members[1]), Permission.APP_DEPLOY, scope)[0]
    assert patch(w, guid, {"op": "remove", "path": "members"}).status_code == 200
    assert not resolve(tenant(w), Permission.APP_DEPLOY, scope)[0]


def test_additive_group_migration_preserves_previous_membership_schema(world):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    w = world
    member = w.members[0]
    sync_member_groups(member.user, {"groups": ["unmanaged"]})
    create(w)
    old = (
        MigrationExecutor(connection)
        .loader.project_state([("astrolift_identity", "0039_role_duplicated_from")])
        .apps
    )
    old_member_model = old.get_model("astrolift_identity", "Member")
    old_member = old_member_model.objects.get(pk=member.pk)
    assert old_member.idp_groups == ["unmanaged"]
    assert old_member.is_active
    with connection.cursor() as cursor:
        columns = {
            column.name
            for column in connection.introspection.get_table_description(
                cursor, old_member_model._meta.db_table
            )
        }
    assert columns == {field.column for field in old_member_model._meta.local_fields}
