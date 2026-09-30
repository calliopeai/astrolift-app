"""Legacy declarations preserve account ownership and credential ceilings."""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.test import Client
from django.utils import timezone
from graphql import GraphQLError

from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member, Organization
from config.schema import schema
from core.models import Notification, PinTransaction, Upload
from core.models.upload import FileUpload
from core.permissions import Permission
from core.schema.context import StrawberryContext
from core.schema.mutations.user import UserMutations
from core.schema.types.permission_analysis import PermissionAnalysisQuery
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, guid: None))


@pytest.fixture
def world():
    w = ScopeWorld("legacy-2110")
    w.user = make_user("legacy-2110")
    w.other = make_user("legacy-other-2110")
    for user in (w.user, w.other):
        Member.objects.create(user=user, scope_kind="ORG", scope_id=w.org.pk)
    return w


def grant(w, kind, permission):
    scope = {"ORG": w.org, "TEAM": w.medops, "PROJECT": w.medops_project, "APP": w.medops_app}[kind]
    return bind_role(w.user, permissions=[permission], kind=kind, scope_id=scope.pk, slug=uuid4().hex)


def info(user):
    request = SimpleNamespace(user=user, headers={}, session={}, META={})
    return SimpleNamespace(context=StrawberryContext(request))


@contextmanager
def selected(w):
    with tenant_context(
        TenantContext(
            organization_id=w.org.pk,
            team_id=w.medops.pk,
            project_id=w.medops_project.pk,
            actor_user_id=w.user.pk,
        )
    ):
        yield


@contextmanager
def credential(w, *, scopes=("admin",), team=None, foreign=False):
    issued = mint_token()
    org = Organization.objects.create(name="Foreign", slug="foreign-2110") if foreign else w.org
    Member.objects.get_or_create(user=w.user, scope_kind="ORG", scope_id=org.pk)
    row = ApiToken.objects.create(
        user=w.user,
        organization=org,
        team=team,
        name="legacy-2110",
        scopes=list(scopes),
        token_hash=issued.token_hash,
        token_last_4=issued.last4,
    )
    marker = set_current_api_token(row)
    try:
        yield issued.plaintext
    finally:
        reset_current_api_token(marker)


@pytest.mark.parametrize("field", ["effective", "diagnose", "compare"])
@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_other_user_analysis_needs_explicit_org_management(world, field, kind):
    grant(world, kind, Permission.ORG_MANAGE_MEMBERS)
    query = PermissionAnalysisQuery()
    with selected(world):
        if kind != "ORG":
            with pytest.raises(GraphQLError, match="org.manage_members"):
                analyze(query, world, field)
        else:
            assert analyze(query, world, field) is not None


def analyze(query, w, field):
    if field == "effective":
        return query.effective_permissions(info(w.user), str(w.other.pk))
    if field == "diagnose":
        return query.permission_diagnose(info(w.user), str(w.other.pk), "app.read")
    return query.permission_compare(info(w.user), str(w.user.pk), str(w.other.pk))


@pytest.mark.parametrize("state", ["inactive", "removed", "foreign"])
def test_org_manager_gets_no_foreign_or_stale_target_analysis(world, state):
    grant(world, "ORG", Permission.ORG_MANAGE_MEMBERS)
    membership = Member.objects.get(user=world.other)
    if state == "inactive":
        membership.is_active = False
    elif state == "removed":
        membership.deleted_at = timezone.now()
    else:
        membership.scope_id = ScopeWorld("foreign-member-2110").org.pk
    membership.save()
    with selected(world):
        assert PermissionAnalysisQuery().effective_permissions(info(world.user), str(world.other.pk)) == []
        assert (
            PermissionAnalysisQuery().permission_compare(
                info(world.user), str(world.user.pk), str(world.other.pk)
            )
            is None
        )


@pytest.mark.parametrize("ceiling", ["read", "team", "foreign", "org"])
def test_org_manager_bearer_ceiling_is_checked_before_other_user_analysis(world, ceiling):
    grant(world, "ORG", Permission.ORG_MANAGE_MEMBERS)
    with (
        selected(world),
        credential(
            world,
            scopes=("read:apps",) if ceiling == "read" else ("admin",),
            team=world.medops if ceiling == "team" else None,
            foreign=ceiling == "foreign",
        ),
    ):
        if ceiling == "org":
            assert analyze(PermissionAnalysisQuery(), world, "compare") is not None
        else:
            with pytest.raises(GraphQLError):
                analyze(PermissionAnalysisQuery(), world, "compare")


@pytest.mark.parametrize(
    "query",
    [
        "{ auditLogs { operation } }",
        "{ auditLogsPage { totalCount } }",
        "query($user: ID!) { effectivePermissions(userId: $user) { slug } }",
        'query($user: ID!) { permissionDiagnose(userId: $user, permission: "app.read") { granted } }',
        "query($user: ID!, $caller: ID!) { permissionCompare(userIdA: $caller, userIdB: $user) { shared } }",
    ],
)
@pytest.mark.parametrize("state", ["inactive", "read-token", "admin-token", "session"])
def test_operator_analysis_and_audit_have_active_account_and_admin_bearer_gates(world, query, state):
    world.user.is_superuser = True
    world.user.is_active = state != "inactive"
    world.user.save()
    with selected(world):
        if state.endswith("token"):
            with credential(world, scopes=("admin",) if state == "admin-token" else ("read:apps",)):
                result = schema.execute_sync(
                    query,
                    context_value=info(world.user).context,
                    variable_values={"user": str(world.other.pk), "caller": str(world.user.pk)},
                )
        else:
            result = schema.execute_sync(
                query,
                context_value=info(world.user).context,
                variable_values={"user": str(world.other.pk), "caller": str(world.user.pk)},
            )
    assert bool(result.errors) == (state in {"inactive", "read-token"})


def test_self_analysis_needs_no_role_but_requires_active_account(world):
    with selected(world):
        assert PermissionAnalysisQuery().effective_permissions(info(world.user), str(world.user.pk)) == []
        world.user.is_active = False
        world.user.save()
        with pytest.raises(DjangoPermissionDenied, match="Active account"):
            PermissionAnalysisQuery().effective_permissions(info(world.user), str(world.user.pk))


@pytest.mark.parametrize("action", ["pin", "notification", "rocket"])
@pytest.mark.parametrize("state", ["anonymous", "inactive", "read-token"])
def test_own_account_writes_refuse_bad_credentials_before_side_effects(world, action, state, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "core.utils.api.rocketchat_rest_client.RocketchatRestClient.create_auth_token",
        lambda *args: calls.append(True),
    )
    own = Notification.objects.create(user=world.user, subject="2110", message="before")
    operations = {
        "pin": 'mutation { pinUpdate(pin: "1234") }',
        "notification": f'mutation {{ notificationRead(gid: "{own.pk}") }}',
        "rocket": "mutation { generateRocketChatToken }",
    }
    actor = world.user
    if state == "anonymous":
        actor = AnonymousUser()
    elif state == "inactive":
        actor.is_active = False
        actor.save()
    if state == "read-token":
        with credential(world, scopes=("read:apps",)):
            result = schema.execute_sync(operations[action], context_value=info(actor).context)
    else:
        result = schema.execute_sync(operations[action], context_value=info(actor).context)
    assert result.errors
    own.refresh_from_db()
    assert own.status == "UNREAD"
    assert not PinTransaction.objects.exists()
    assert not calls


def test_bearer_never_switches_the_browser_session_even_with_admin(world):
    request_info = info(world.user)
    with credential(world), pytest.raises(DjangoPermissionDenied, match="browser session"):
        UserMutations().switch_user(request_info, str(world.other.pk))
    assert request_info.context.request.session == {}


@pytest.mark.parametrize(
    "query",
    [
        'mutation { signRequestUser(gid: "999", userToRequest: "999") }',
        'mutation { signRequestSign(gid: "999") }',
        'mutation { signRequestCancel(gid: "999", note: "x") }',
    ],
)
def test_unavailable_sign_actions_refuse_before_resolving_a_supplied_id(world, query):
    result = schema.execute_sync(query, context_value=info(world.user).context)
    assert result.errors[0].message == "Sign requests are unavailable."


@pytest.mark.parametrize("route", ["export", "support"])
@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    "ceiling", ["team-session", "org-session", "read-token", "team-token", "org-token", "foreign-token"]
)
def test_http_legacy_routes_gate_before_external_access(world, route, method, ceiling, settings, monkeypatch):
    monkeypatch.setenv("FEATURE_SUPPORT", "1")
    calls = []

    class Support:
        def __init__(self, *, user, organization):
            calls.append((user.pk, organization.pk))

        def list_tickets(self):
            return {"items": []}

        def create_ticket(self, **kwargs):
            return {"created": True}

    monkeypatch.setattr("core.views_client_cove_support.ClientCoveSupportClient", Support)
    grant(world, "TEAM" if ceiling == "team-session" else "ORG", Permission.ORG_READ)
    client = Client()
    headers = {
        "HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid),
        "HTTP_X_ASTROLIFT_TEAM": str(world.medops.pk),
    }
    path = "/app/export/" if route == "export" else "/api/support/v1/tickets/"
    send = getattr(client, method)
    if ceiling.endswith("session"):
        client.force_login(world.user)
        response = send(path, {"file": "missing"}, content_type="application/json", **headers)
    else:
        with credential(
            world,
            scopes=("read:apps",) if ceiling == "read-token" else ("admin",),
            team=world.medops if ceiling == "team-token" else None,
            foreign=ceiling == "foreign-token",
        ) as plaintext:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {plaintext}"
            response = send(path, {"file": "missing"}, content_type="application/json", **headers)
    allowed = route == "support" and ceiling in {"org-session", "read-token", "org-token"}
    if method == "post" and ceiling == "read-token":
        allowed = False
    assert response.status_code == ((201 if method == "post" else 200) if allowed else 403), response.content
    assert calls == ([(world.user.pk, world.org.pk)] if allowed else [])


def test_legacy_self_delete_requires_elevation_without_anonymizing(world):
    with pytest.raises(DjangoPermissionDenied, match="elevated"):
        UserMutations().profile_request_delete_user(info(world.user))
    world.user.refresh_from_db()
    assert world.user.is_active
    assert Member.objects.filter(user=world.user, is_active=True).exists()


def test_live_file_upload_client_operation_checks_bearer_before_creating_wrapper(two_tenants):
    caller = two_tenants.a.user
    w = SimpleNamespace(
        user=caller,
        org=Organization.objects.create(
            name="Mirrored legacy", slug="mirrored-legacy-2110", guid=two_tenants.a.organization.guid
        ),
    )
    before = FileUpload.objects.count(), Upload.objects.count()
    with credential(w, scopes=("read:apps",)):
        result = schema.execute_sync(
            'mutation { fileUpload(mimetype: "text/plain", name: "private") { id } }',
            context_value=info(caller).context,
        )
    assert result.errors
    assert (FileUpload.objects.count(), Upload.objects.count()) == before


def test_live_file_upload_operation_still_creates_the_callers_private_file(two_tenants, monkeypatch):
    monkeypatch.setattr(
        Upload, "generate_pre_signed_url_for_get", lambda *args, **kwargs: "https://storage.test/get"
    )
    monkeypatch.setattr(
        Upload, "generate_pre_signed_url_for_put", lambda *args, **kwargs: "https://storage.test/put"
    )
    caller = two_tenants.a.user
    client = Client()
    client.force_login(caller)
    response = client.post(
        "/app/gql/config/",
        {
            "query": 'mutation { fileUpload(mimetype: "text/plain", name: "private", isPublic: false) { id preSignedUrl } }'
        },
        content_type="application/json",
        HTTP_X_PLATFORM="WEB",
    )
    assert response.status_code == 200
    assert not response.json().get("errors"), response.json()
    upload = Upload.objects.get(created_by=caller)
    assert upload.organization_id == two_tenants.a.organization.pk
    assert upload.location == Upload.Location.STATIC.value
    assert FileUpload.objects.filter(created_by=caller, upload=upload).exists()


@pytest.mark.parametrize("operator", [False, True])
def test_existing_foreign_upload_wrappers_cannot_be_replaced(two_tenants, operator):
    from core.schema.mutations.upload import UploadMutations

    caller = two_tenants.a.user
    caller.is_superuser = operator
    caller.save()
    upload = Upload.objects.create(
        organization=two_tenants.b.organization,
        created_by=two_tenants.b.user,
        content_type="text/plain",
        target_global_id="foreign",
        metadata={"original": True},
    )
    wrapper = FileUpload.objects.create(created_by=two_tenants.b.user, upload=upload)
    before = FileUpload.objects.count(), Upload.objects.count()
    with pytest.raises(GraphQLError, match="not found"):
        UploadMutations().file_upload(info(caller), "text/plain", file_upload_gid=wrapper.global_id)
    wrapper.refresh_from_db()
    upload.refresh_from_db()
    assert wrapper.upload_id == upload.pk
    assert upload.metadata == {"original": True}
    assert (FileUpload.objects.count(), Upload.objects.count()) == before


@pytest.mark.parametrize("ceiling", ["read", "admin", "foreign"])
def test_legacy_directory_reads_pin_bearer_by_guid_and_omit_removed_members(two_tenants, ceiling):
    from organization.schema.queries import Query

    caller = two_tenants.a.user
    legacy = two_tenants.a.organization
    w = SimpleNamespace(
        user=caller,
        org=Organization.objects.create(
            name="Mirrored directory", slug="mirror-directory-2110", guid=legacy.guid
        ),
    )
    with credential(
        w, scopes=("read:apps",) if ceiling == "read" else ("admin",), foreign=ceiling == "foreign"
    ):
        rows = list(Query().organizations(info(caller)))
        assert [row.pk for row in rows] == ([legacy.pk] if ceiling == "admin" else [])
    two_tenants.a.membership.deleted_at = timezone.now()
    two_tenants.a.membership.save()
    assert list(Query().members(info(caller))) == []


def test_elevated_self_delete_keeps_last_live_owner(world):
    from astrolift_identity.models import Role, RoleBinding
    from astrolift_identity.session_elevation import elevate

    role, _ = Role.objects.get_or_create(
        slug="org_owner",
        is_system=True,
        defaults={"name": "Owner", "scope_level": "ORG", "permissions": [p.value for p in Permission]},
    )
    RoleBinding.objects.create(user=world.user, role=role, scope_kind="ORG", scope_id=world.org.pk)
    request_info = info(world.user)
    elevate(request_info.context.request.session, method="password")
    with pytest.raises(DjangoPermissionDenied, match="last owner"):
        UserMutations().profile_request_delete_user(request_info)
    world.user.refresh_from_db()
    assert world.user.is_active


def test_elevated_http_self_delete_anonymizes_account_and_ends_its_session(world):
    from astrolift_identity.session_elevation import elevate

    client = Client()
    client.force_login(world.user)
    session = client.session
    elevate(session, method="password")
    session.save()
    response = client.post(
        "/app/gql/config/",
        {"query": "mutation { profileRequestDeleteUser }"},
        content_type="application/json",
        HTTP_X_PLATFORM="WEB",
    )
    assert response.status_code == 200
    assert response.json().get("data", {}).get("profileRequestDeleteUser") is True, response.json()
    world.user.refresh_from_db()
    assert not world.user.is_active
    assert world.user.email.endswith("@anon-astrolift.net")
    assert not Member.objects.filter(user=world.user, is_active=True).exists()
    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize("operator", [False, True])
def test_unavailable_legacy_writes_never_resolve_or_modify_a_target(world, operator):
    world.user.is_superuser = operator
    world.user.save()
    for query in [
        'mutation { activate(gid: "invalid") }',
        'mutation { organization(input: {id: "invalid", website: "https://foreign.test"}) { ok } }',
        'mutation { upsertOrganization(input: {id: "invalid", website: "https://foreign.test"}) { ok } }',
    ]:
        result = schema.execute_sync(query, context_value=info(world.user).context)
        assert result.errors
        assert ("unavailable" if operator else "platform operator") in result.errors[0].message


@pytest.mark.parametrize("operator", [False, True])
def test_presigned_target_cannot_confuse_legacy_and_astrolift_org_integer_ids(world, operator):
    from core.schema.mutations.upload import _assert_caller_owns_target
    from organization.models import Organization as LegacyOrganization
    from organization.models import OrganizationMember

    legacy = LegacyOrganization.objects.create(pk=world.org.pk, name="Independent integer ID")
    OrganizationMember.objects.create(organization=legacy, member=world.user, is_active=True)
    world.user.profile.active_organization = legacy
    world.user.profile.save()
    world.user.is_superuser = operator
    world.user.save()
    world.medops_app.created_by = world.user
    world.medops_app.save()
    assert legacy.pk == world.org.pk
    assert legacy.guid != world.org.guid
    with pytest.raises(GraphQLError, match="Not authorized"):
        _assert_caller_owns_target(info(world.user), world.medops_app)
