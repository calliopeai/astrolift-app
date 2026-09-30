"""Managed resource owners, explicit misses and bearer ceilings (#2106)."""

from contextlib import contextmanager

import pytest
from django.utils import timezone

from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import AppTeamAccess
from astrolift_services.models import (
    AppSecretBundleRef,
    ManagedService,
    ManagedServiceAttachment,
    SecretBundle,
)
from astrolift_services.scopes import (
    bundle_attachment_app_scope,
    managed_service_attachment_scope,
    managed_service_scope_by_guid,
    secret_bundle_project_scope,
    services_project_scope_by_guid,
)
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind, check_permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_search(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))


@pytest.fixture
def world():
    world = ScopeWorld("services2106")
    world.user = make_user("services2106")
    world.cluster = make_cluster(world, "services2106")
    for prefix in ("medops", "platform"):
        app = getattr(world, prefix + "_app")
        project = getattr(world, prefix + "_project")
        env = AppEnvironment.objects.create(
            registered_app=app, name="production", tenant_cluster=world.cluster
        )
        setattr(world, prefix + "_env", env)
        setattr(
            world,
            prefix + "_private",
            ManagedService.objects.create(registered_app=app, app_environment=env, kind="redis", name=prefix),
        )
        shared = ManagedService.objects.create(
            project=project, tenant_cluster=world.cluster, kind="redis", name=prefix
        )
        setattr(world, prefix + "_shared", shared)
        bundle = SecretBundle.objects.create(
            organization=world.org,
            project=project,
            team=project.team,
            tenant_cluster=world.cluster,
            name=prefix,
            slug=prefix,
            backend_ref="unused",
        )
        setattr(world, prefix + "_bundle", bundle)
    world.bundle_attachment = AppSecretBundleRef.objects.create(
        registered_app=world.medops_app, app_environment=world.medops_env, secret_bundle=world.medops_bundle
    )
    world.service_attachment = ManagedServiceAttachment.objects.create(
        managed_service=world.medops_shared, app_environment=world.medops_env
    )
    return world


@contextmanager
def subject(world, *, token_team=None, token_org=None, scopes=("admin",)):
    with tenant_context(
        TenantContext(organization_id=world.org.pk, team_id=world.medops.pk, actor_user_id=world.user.pk)
    ):
        marker = None
        if token_team is not None or token_org is not None:
            marker = set_current_api_token(
                ApiToken.objects.create(
                    user=world.user,
                    token_hash="hash",
                    name="service test",
                    organization_id=token_org or world.org.pk,
                    team_id=token_team,
                    scopes=list(scopes),
                )
            )
        try:
            yield
        finally:
            if marker is not None:
                reset_current_api_token(marker)


FACTORIES = [
    managed_service_scope_by_guid,
    secret_bundle_project_scope,
    services_project_scope_by_guid,
    bundle_attachment_app_scope,
    managed_service_attachment_scope,
]


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize("value", [None, "", "missing", "01920000-0000-7000-8000-000000000000"])
def test_missing_resource_never_borrows_selected_team(world, factory, value):
    bind_role(
        world.user, permissions=[Permission.APP_READ], kind="TEAM", scope_id=world.medops.pk, slug="reader"
    )
    with subject(world):
        resolved = factory("id")({"id": value})
        assert resolved == PermissionScope(ScopeKind.ORG, world.org.pk)
        with pytest.raises(PermissionDenied):
            check_permission(Permission.APP_READ, scope=resolved)


@pytest.mark.parametrize("owner", ["private", "shared", "bundle"])
@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_live_resource_requires_its_actual_owner_grant(world, owner, kind):
    scope_id = {
        "APP": world.medops_app.pk,
        "PROJECT": world.medops_project.pk,
        "TEAM": world.medops.pk,
        "ORG": world.org.pk,
    }[kind]
    bind_role(world.user, permissions=[Permission.APP_READ], kind=kind, scope_id=scope_id, slug="reader")
    factory = secret_bundle_project_scope if owner == "bundle" else managed_service_scope_by_guid
    with subject(world):
        own = factory("id")({"id": str(getattr(world, "medops_" + owner).guid)})
        sibling = factory("id")({"id": str(getattr(world, "platform_" + owner).guid)})
        if owner != "private" and kind == "APP":
            with pytest.raises(PermissionDenied):
                check_permission(Permission.APP_READ, scope=own)
        else:
            check_permission(Permission.APP_READ, scope=own)
        if kind == "ORG":
            check_permission(Permission.APP_READ, scope=sibling)
        else:
            with pytest.raises(PermissionDenied):
                check_permission(Permission.APP_READ, scope=sibling)


@pytest.mark.parametrize("owner", ["private", "shared", "bundle"])
def test_team_token_retains_ceiling_under_an_org_operator(world, owner):
    bind_role(
        world.user, permissions=[Permission.APP_READ], kind="ORG", scope_id=world.org.pk, slug="operator"
    )
    factory = secret_bundle_project_scope if owner == "bundle" else managed_service_scope_by_guid
    with subject(world, token_team=world.medops.pk):
        check_permission(
            Permission.APP_READ,
            scope=factory("id", permissions=(Permission.APP_READ,))(
                {"id": str(getattr(world, "medops_" + owner).guid)}
            ),
        )
        for value in (str(getattr(world, "platform_" + owner).guid), None):
            with pytest.raises(PermissionDenied):
                factory("id", permissions=(Permission.APP_READ,))({"id": value})


@pytest.mark.parametrize("owner", ["private", "shared", "bundle"])
@pytest.mark.parametrize("ancestor", ["project", "team"])
def test_stale_owners_require_explicit_org_authority(world, owner, ancestor):
    bind_role(
        world.user, permissions=[Permission.APP_READ], kind="TEAM", scope_id=world.medops.pk, slug="reader"
    )
    target = world.medops_project if ancestor == "project" else world.medops
    target.deleted_at = timezone.now()
    target.save()
    factory = secret_bundle_project_scope if owner == "bundle" else managed_service_scope_by_guid
    with subject(world):
        resolved = factory("id")({"id": str(getattr(world, "medops_" + owner).guid)})
        assert resolved.kind == ScopeKind.ORG
        with pytest.raises(PermissionDenied):
            check_permission(Permission.APP_READ, scope=resolved)


@pytest.mark.parametrize(
    "permissions,allowed",
    [
        ((Permission.APP_READ,), True),
        ((Permission.APP_READ, Permission.MANAGED_SERVICE_UPDATE), False),
        ((Permission.APP_UPDATE,), False),
    ],
)
def test_shared_private_service_ceiling_checks_every_actual_action(world, permissions, allowed):
    bind_role(world.user, permissions=list(Permission), kind="ORG", scope_id=world.org.pk, slug="operator")
    AppTeamAccess.objects.create(registered_app=world.platform_app, team=world.medops, access_level="viewer")
    with subject(world, token_team=world.medops.pk):
        factory = managed_service_scope_by_guid("id", permissions=permissions)
        if allowed:
            check_permission(permissions[0], scope=factory({"id": str(world.platform_private.guid)}))
        else:
            with pytest.raises(PermissionDenied):
                factory({"id": str(world.platform_private.guid)})


@pytest.mark.parametrize("team_token", [False, True])
@pytest.mark.parametrize(
    "method",
    [
        "astrolift_project_resource_clusters",
        "astrolift_project_managed_service_catalog",
        "astrolift_project_managed_services",
        "astrolift_project_secret_bundles",
        "astrolift_managed_service_cost_preview",
        "astrolift_managed_service_objects",
        "astrolift_managed_service_queue_depth",
        "astrolift_email_service_detail",
        "astrolift_email_templates",
        "astrolift_email_template",
        "astrolift_email_template_stats",
        "astrolift_email_messages",
        "astrolift_email_engagement_metrics",
    ],
)
def test_project_and_service_reads_refuse_sibling_before_provider_fetch(world, method, team_token):
    import inspect

    from astrolift_services.schema.queries import ServicesQuery
    from core.tests.utils.scope_world import make_info

    bind_role(
        world.user,
        permissions=list(Permission),
        kind="ORG" if team_token else "TEAM",
        scope_id=world.org.pk if team_token else world.medops.pk,
        slug="operator",
    )
    resolver = getattr(ServicesQuery(), method)
    values = {
        "project_id": str(world.platform_project.guid),
        "managed_service_id": str(world.platform_shared.guid),
    }
    for name, parameter in inspect.signature(resolver).parameters.items():
        if name not in ("self", "info") and parameter.default is inspect.Parameter.empty:
            values.setdefault(name, "unused")
    with subject(world, token_team=world.medops.pk if team_token else None):
        with pytest.raises(PermissionDenied):
            resolver(
                make_info(world.user),
                **{
                    name: value
                    for name, value in values.items()
                    if name in inspect.signature(resolver).parameters
                },
            )


@pytest.mark.parametrize("team_token", [False, True])
@pytest.mark.parametrize(
    "method",
    [
        "create_project_secret_bundle",
        "update_project_secret_bundle",
        "delete_project_secret_bundle",
        "set_project_bundle_secret_value",
        "delete_project_bundle_secret_value",
        "reveal_project_bundle_secret_value",
        "rotate_secret_bundle",
        "provision_project_managed_service",
        "attach_project_managed_service",
        "detach_project_managed_service",
        "update_project_managed_service",
        "reprovision_project_managed_service",
        "deprovision_project_managed_service",
        "update_managed_service",
        "reprovision_managed_service",
        "deprovision_managed_service",
        "adopt_managed_resource",
        "reveal_managed_service_connection",
        "test_model_endpoint",
        "send_managed_service_test_email",
        "add_email_suppression_entry",
        "remove_email_suppression_entry",
        "create_email_template",
        "update_email_template",
        "delete_email_template",
    ],
)
def test_resource_mutations_refuse_sibling_without_writes(world, method, team_token):
    from types import SimpleNamespace

    from astrolift_services.schema.mutations import ServicesMutation
    from core.tests.utils.scope_world import make_info

    bind_role(
        world.user,
        permissions=list(Permission),
        kind="ORG" if team_token else "TEAM",
        scope_id=world.org.pk if team_token else world.medops.pk,
        slug="operator",
    )
    attachment = ManagedServiceAttachment.objects.create(
        managed_service=world.platform_shared, app_environment=world.platform_env
    )
    bundle_method = "bundle" in method
    data = SimpleNamespace(
        id=str(world.platform_bundle.guid if bundle_method else world.platform_shared.guid),
        managed_service_id=str(world.platform_shared.guid),
        bundle_id=str(world.platform_bundle.guid),
        project_id=str(world.platform_project.guid),
        attachment_id=str(attachment.guid),
        app_slug=world.platform_app.slug,
        environment_name="production",
        cluster_id=str(world.cluster.guid),
    )
    before = (
        ManagedService.objects.count(),
        SecretBundle.objects.count(),
        ManagedServiceAttachment.objects.count(),
    )
    with subject(world, token_team=world.medops.pk if team_token else None):
        arguments = (
            {"bundle_id": data.bundle_id} if method == "delete_project_secret_bundle" else {"input": data}
        )
        result = getattr(ServicesMutation(), method)(make_info(world.user), **arguments)
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert before == (
        ManagedService.objects.count(),
        SecretBundle.objects.count(),
        ManagedServiceAttachment.objects.count(),
    )
    world.platform_shared.refresh_from_db()
    assert world.platform_shared.status == "pending"
    assert world.platform_shared.deleted_at is None
    attachment.refresh_from_db()
    assert attachment.deleted_at is None


@pytest.mark.parametrize("kind", ["APP", "PROJECT", "TEAM", "ORG"])
def test_bundle_list_filters_actual_owners_before_limit(world, kind):
    from astrolift_services.schema.queries import ServicesQuery
    from core.tests.utils.scope_world import make_info

    scope_id = {
        "APP": world.medops_app.pk,
        "PROJECT": world.medops_project.pk,
        "TEAM": world.medops.pk,
        "ORG": world.org.pk,
    }[kind]
    bind_role(world.user, permissions=[Permission.APP_READ], kind=kind, scope_id=scope_id, slug="reader")
    SecretBundle.objects.bulk_create(
        [
            SecretBundle(
                organization=world.org,
                project=world.platform_project,
                team=world.platform,
                tenant_cluster=world.cluster,
                name=f"sibling {n}",
                slug=f"sibling-{n}",
                backend_ref="unused",
            )
            for n in range(210)
        ]
    )
    with subject(world):
        rows = ServicesQuery().astrolift_secret_bundles(make_info(world.user))
    if kind == "ORG":
        assert len(rows) == 200
    elif kind == "APP":
        assert rows == []
    else:
        assert [str(row.id) for row in rows] == [str(world.medops_bundle.guid)]


def test_model_page_count_does_not_borrow_org_user_grant_through_team_token(world):
    from astrolift_services.schema.queries import ServicesQuery
    from core.tests.utils.scope_world import make_info

    bind_role(
        world.user,
        permissions=[Permission.APP_READ, Permission.PROJECT_READ],
        kind="ORG",
        scope_id=world.org.pk,
        slug="reader",
    )
    ManagedService.objects.all().update(kind="model_endpoint")
    with subject(world, token_team=world.medops.pk):
        page = ServicesQuery().astrolift_model_endpoints_page(make_info(world.user), page=1, page_size=10)
    assert page.total_count == 2
    assert {str(row.id) for row in page.items} == {
        str(world.medops_private.guid),
        str(world.medops_shared.guid),
    }


MCP_RESOURCE_TOOLS = [
    "astrolift_list_project_resource_clusters",
    "astrolift_list_project_resource_catalog",
    "astrolift_list_project_resources",
    "astrolift_preview_project_resource_cost",
    "astrolift_provision_project_resource",
    "astrolift_attach_project_resource",
    "astrolift_detach_project_resource",
    "astrolift_update_project_resource",
    "astrolift_reprovision_project_resource",
    "astrolift_deprovision_project_resource",
]


def _http_token(world):
    import hashlib

    from astrolift_identity.models import Member

    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    plaintext = "alft_at_services-2106-http-test"
    ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=world.medops,
        name="http regression",
        scopes=["admin"],
        token_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
    )
    return {"HTTP_AUTHORIZATION": f"Bearer {plaintext}", "HTTP_ACCEPT": "application/json, text/event-stream"}


@pytest.mark.parametrize("tool_name", MCP_RESOURCE_TOOLS)
def test_mcp_http_resource_tools_refuse_sibling_with_normal_permission_envelope(world, client, tool_name):
    import json

    from astrolift_agents.mcp_contract import MCP_TOOL_META
    from astrolift_operations.models import AuditEvent

    bind_role(world.user, permissions=list(Permission), kind="ORG", scope_id=world.org.pk, slug="operator")
    common = _http_token(world)
    initialized = client.post(
        "/api/mcp/v1/",
        data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}
        ),
        content_type="application/json",
        **common,
    )
    assert initialized.status_code == 200
    common["HTTP_MCP_SESSION_ID"] = initialized["Mcp-Session-Id"]
    common["HTTP_MCP_PROTOCOL_VERSION"] = "2025-11-25"
    attachment = ManagedServiceAttachment.objects.create(
        managed_service=world.platform_shared, app_environment=world.platform_env
    )
    values = {
        "project_id": str(world.platform_project.guid),
        "managed_service_id": str(world.platform_shared.guid),
        "attachment_id": str(attachment.guid),
        "cluster_id": str(world.cluster.guid),
        "kind": "redis",
        "confirm_managed_service_id": str(world.platform_shared.guid),
    }
    arguments = {name: values[name] for name in MCP_TOOL_META[tool_name]["inputSchema"]["required"]}
    response = client.post(
        "/api/mcp/v1/",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }
        ),
        content_type="application/json",
        **common,
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"]
    assert result["structuredContent"]["code"] == "permission_denied"
    assert AuditEvent.objects.filter(action=f"mcp.tool.{tool_name}", decision="DENY").exists()
    attachment.refresh_from_db()
    assert attachment.deleted_at is None
    world.platform_shared.refresh_from_db()
    assert world.platform_shared.status == "pending"


def test_graphql_http_project_service_gate_allows_owner_and_refuses_sibling(world, client):
    import json

    bind_role(
        world.user, permissions=[Permission.PROJECT_READ], kind="ORG", scope_id=world.org.pk, slug="reader"
    )
    headers = _http_token(world)
    query = "query($id: GUID!) { astroliftProjectManagedServices(projectId: $id) { name } }"
    owned = client.post(
        "/app/gql/config/",
        data=json.dumps({"query": query, "variables": {"id": str(world.medops_project.guid)}}),
        content_type="application/json",
        **headers,
    )
    assert owned.status_code == 200
    assert not owned.json().get("errors")
    assert owned.json()["data"]["astroliftProjectManagedServices"] == [{"name": "medops"}]
    sibling = client.post(
        "/app/gql/config/",
        data=json.dumps({"query": query, "variables": {"id": str(world.platform_project.guid)}}),
        content_type="application/json",
        **headers,
    )
    assert sibling.status_code == 200
    assert sibling.json()["data"] is None
    assert sibling.json()["errors"][0]["path"] == ["astroliftProjectManagedServices"]
    assert "outside the credential" in sibling.json()["errors"][0]["message"]


def test_project_updater_can_attach_and_detach_its_own_app_consumer(world):
    from astrolift_services.schema.mutations import (
        AttachProjectManagedServiceInput,
        DetachProjectManagedServiceInput,
        ServicesMutation,
    )
    from core.tests.utils.scope_world import make_info

    bind_role(
        world.user,
        permissions=[Permission.PROJECT_UPDATE],
        kind="PROJECT",
        scope_id=world.medops_project.pk,
        slug="updater",
    )
    with subject(world, token_team=world.medops.pk):
        attached = ServicesMutation().attach_project_managed_service(
            make_info(world.user),
            input=AttachProjectManagedServiceInput(
                managed_service_id=str(world.medops_shared.guid),
                app_environment_id=str(world.medops_env.guid),
            ),
        )
        assert attached.ok, attached.errors
        detached = ServicesMutation().detach_project_managed_service(
            make_info(world.user), input=DetachProjectManagedServiceInput(attachment_id=str(attached.data.id))
        )
    assert detached.ok, detached.errors
    world.service_attachment.refresh_from_db()
    assert world.service_attachment.deleted_at is not None
    assert world.platform_shared.deleted_at is None


def test_attachment_checks_destination_environment_facts_and_restores_source_context(world):
    from astrolift_identity.abac import RequestAttributes, current_attributes, request_attributes
    from astrolift_identity.models import Policy
    from astrolift_services.schema.mutations import AttachProjectManagedServiceInput, ServicesMutation
    from core.tests.utils.scope_world import make_info

    bind_role(
        world.user,
        permissions=[Permission.PROJECT_UPDATE],
        kind="PROJECT",
        scope_id=world.medops_project.pk,
        slug="updater",
    )
    world.medops_shared.environment_name = "production"
    world.medops_shared.save(update_fields=["environment_name", "updated_at", "version"])
    staging = AppEnvironment.objects.create(
        registered_app=world.medops_app, name="staging", tenant_cluster=world.cluster
    )
    Policy.objects.create(
        organization=world.org,
        name="Deny staging",
        slug="deny-staging",
        scope_level="ORG",
        effect="DENY",
        action_pattern="project.update",
        resource_pattern={"env": ["staging"]},
        actor_pattern={},
        conditions=[],
    )
    attrs = RequestAttributes(actor_user_id=world.user.pk)
    before = ManagedServiceAttachment.objects.count()
    with subject(world), request_attributes(attrs):
        denied = ServicesMutation().attach_project_managed_service(
            make_info(world.user),
            input=AttachProjectManagedServiceInput(
                managed_service_id=str(world.medops_shared.guid), app_environment_id=str(staging.guid)
            ),
        )
        assert not denied.ok
        assert denied.errors[0].code == "PERMISSION_DENIED"
        assert current_attributes() is attrs
        assert attrs.environment is None
        allowed = ServicesMutation().attach_project_managed_service(
            make_info(world.user),
            input=AttachProjectManagedServiceInput(
                managed_service_id=str(world.medops_shared.guid),
                app_environment_id=str(world.medops_env.guid),
            ),
        )
        assert allowed.ok, allowed.errors
        assert current_attributes() is attrs
        assert attrs.environment is None
    assert ManagedServiceAttachment.objects.count() == before


@pytest.mark.parametrize("owner", ["TEAM", "ORG"])
def test_legacy_bundle_uses_its_recorded_owner_scope(world, owner):
    bundle = SecretBundle.objects.create(
        organization=world.org,
        team=world.medops if owner == "TEAM" else None,
        name="Legacy",
        slug="legacy",
        backend_ref="unused",
    )
    scope_id = world.medops.pk if owner == "TEAM" else world.org.pk
    bind_role(world.user, permissions=[Permission.APP_UPDATE], kind=owner, scope_id=scope_id, slug="updater")
    with subject(world):
        resolved = secret_bundle_project_scope("id", permissions=(Permission.APP_UPDATE,))(
            {"id": str(bundle.guid)}
        )
        assert resolved == PermissionScope(getattr(ScopeKind, owner), scope_id)
        check_permission(Permission.APP_UPDATE, scope=resolved)
    if owner == "ORG":
        with subject(world, token_team=world.medops.pk):
            with pytest.raises(PermissionDenied):
                secret_bundle_project_scope("id", permissions=(Permission.APP_UPDATE,))(
                    {"id": str(bundle.guid)}
                )


@pytest.mark.parametrize(
    "provided,expected", [(None, "production"), ("", "production"), (" staging ", "staging")]
)
def test_mcp_creation_policy_facts_match_the_persisted_environment_default(world, provided, expected):
    from astrolift_agents.views.mcp import _operation_for_tool

    arguments = {
        "project_id": str(world.medops_project.guid),
        "cluster_id": str(world.cluster.guid),
        "kind": "redis",
    }
    if provided is not None:
        arguments["environment_name"] = provided
    with subject(world):
        facts = _operation_for_tool("astrolift_provision_project_resource", arguments)
    assert facts.environment == expected
