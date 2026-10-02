"""Real database resource pages, exact targets and independent consumer gates."""

from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from graphql import GraphQLError

from astrolift_graphql import GUID
from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from astrolift_services.schema.resource_reads import ManagedResourceReadsQuery
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db
query = ManagedResourceReadsQuery()


@pytest.fixture(autouse=True)
def no_search(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))


@pytest.fixture
def world():
    w = ScopeWorld("resources2207")
    w.user = make_user("resources2207")
    w.cluster = make_cluster(w, "resources2207")
    w.service = ManagedService.objects.create(
        project=w.medops_project,
        tenant_cluster=w.cluster,
        name="cache",
        kind="redis",
        config={"password": "RESOURCE_PRIVATE_MARKER"},
        applied_config={"token": "RESOURCE_PRIVATE_MARKER"},
        status_error="RESOURCE_PRIVATE_MARKER",
        backend_ref="RESOURCE_PRIVATE_MARKER",
    )
    return w


def grant(w, *permissions, kind="PROJECT", target=None, slug="reader2207"):
    return bind_role(
        w.user, permissions=permissions, kind=kind, scope_id=target or w.medops_project.pk, slug=slug
    )


def subject(w):
    return tenant_context(TenantContext(organization_id=w.org.pk, actor_user_id=w.user.pk))


def page(w, **kwargs):
    return query.astrolift_project_managed_services_page(
        make_info(w.user), project_id=GUID(str(w.medops_project.guid)), **kwargs
    )


def detail(w, service=None, **kwargs):
    return query.astrolift_project_managed_service(
        make_info(w.user),
        project_id=GUID(str(w.medops_project.guid)),
        id=GUID(str((service or w.service).guid)),
        **kwargs,
    )


def attachments(w, **kwargs):
    return query.astrolift_project_managed_service_attachments_page(
        make_info(w.user),
        project_id=GUID(str(w.medops_project.guid)),
        managed_service_id=GUID(str(w.service.guid)),
        **kwargs,
    )


def test_every_resource_reachable_above_200_with_timestamp_ties(world):
    grant(world, Permission.PROJECT_READ)
    ManagedService.objects.bulk_create(
        [
            ManagedService(
                project=world.medops_project,
                tenant_cluster=world.cluster,
                name=f"resource-{index:03}",
                kind="redis",
            )
            for index in range(250)
        ]
    )
    ManagedService.objects.filter(project=world.medops_project).update(created_at=timezone.now())
    seen, cursor = set(), None
    with subject(world):
        while True:
            current = page(world, limit=50, after=cursor)
            assert current.total_count == 251
            assert len(current.items) <= 50
            assert not seen.intersection(str(row.id) for row in current.items)
            seen.update(str(row.id) for row in current.items)
            cursor = current.next_cursor
            if cursor is None:
                break
    assert len(seen) == 251


def test_basic_projection_never_selects_sensitive_columns_or_nested_grants(world):
    grant(world, Permission.PROJECT_READ, Permission.APP_READ)
    with subject(world), CaptureQueriesContext(connection) as captured:
        assert detail(world).id == str(world.service.guid)
        assert query.astrolift_managed_service(make_info(world.user), id=GUID(str(world.service.guid)))
        assert page(world).total_count == 1
    service_selects = [
        row["sql"] for row in captured if 'FROM "astrolift_services_managedservice"' in row["sql"]
    ]
    assert service_selects
    for sql in service_selects:
        for field in (
            '"config"',
            '"applied_config"',
            '"status_error"',
            '"connection_secret_ref"',
            '"backend_ref"',
        ):
            assert field not in sql
    assert not any("workloadidentitygrant" in row["sql"].lower() for row in captured)


def test_project_permission_does_not_implicitly_grant_app_or_cost_reads(world):
    grant(world, Permission.PROJECT_READ)
    with subject(world):
        assert detail(world)
        assert attachments(world).total_count == 0
        with pytest.raises(PermissionDenied):
            query.astrolift_managed_service(make_info(world.user), id=GUID(str(world.service.guid)))
        from astrolift_services.schema.queries import ServicesQuery

        with pytest.raises(PermissionDenied):
            ServicesQuery().astrolift_managed_service_cost_preview(
                make_info(world.user), managed_service_id=GUID(str(world.service.guid))
            )


def test_same_name_deleted_target_never_resolves_replacement(world):
    grant(world, Permission.PROJECT_READ)
    original = world.service
    original.delete()
    ManagedService.objects.create(
        project=world.medops_project, tenant_cluster=world.cluster, name="cache", kind="redis"
    )
    with subject(world):
        assert detail(world, original) is None
        assert page(world, name="cache").total_count == 1
        assert (
            query.astrolift_project_managed_service(
                make_info(world.user), project_id=GUID(str(world.medops_project.guid)), id=GUID(str(uuid4()))
            )
            is None
        )


def test_changed_exact_context_refuses_attachments_and_detail(world):
    grant(world, Permission.PROJECT_READ)
    with subject(world):
        revision = detail(world).context_revision
        world.cluster.region = "new-region"
        world.cluster.save()
        for read in (detail, attachments):
            with pytest.raises(GraphQLError) as error:
                read(world, expected_context_revision=revision)
            assert error.value.extensions["code"] == "STALE_TARGET"


@pytest.mark.parametrize("change", ["malformed", "filter", "deleted"])
def test_cursor_refuses_malformed_foreign_or_deleted_anchor(world, change):
    grant(world, Permission.PROJECT_READ)
    ManagedService.objects.create(
        project=world.medops_project, tenant_cluster=world.cluster, name="second", kind="redis"
    )
    with subject(world):
        first = page(world, limit=1)
        cursor = first.next_cursor
        kwargs = {}
        if change == "malformed":
            cursor = "bad-cursor"
        elif change == "filter":
            kwargs["search"] = "cache"
        else:
            ManagedService.objects.get(guid=first.items[-1].id).delete()
        with pytest.raises(GraphQLError) as error:
            page(world, limit=1, after=cursor, **kwargs)
        assert error.value.extensions["code"] == ("STALE_CURSOR" if change == "deleted" else "INVALID_CURSOR")


def test_attachments_above_200_are_bounded_and_independently_visible(world):
    grant(world, Permission.PROJECT_READ)
    grant(world, Permission.APP_READ, slug="apps2207")
    apps = RegisteredApp.objects.bulk_create(
        [
            RegisteredApp(
                organization=world.org,
                team=world.medops,
                project=world.medops_project,
                name=f"app-{index}",
                slug=f"app-{index}",
                k8s_namespace=f"app-{index}",
            )
            for index in range(251)
        ]
    )
    environments = AppEnvironment.objects.bulk_create(
        [AppEnvironment(registered_app=app, name="production", tenant_cluster=world.cluster) for app in apps]
    )
    ManagedServiceAttachment.objects.bulk_create(
        [
            ManagedServiceAttachment(
                managed_service=world.service,
                app_environment=env,
                credential_ref="ATTACHMENT_PRIVATE_MARKER",
                slice_handle="ATTACHMENT_PRIVATE_MARKER",
            )
            for env in environments
        ]
    )
    ManagedServiceAttachment.objects.filter(managed_service=world.service).update(created_at=timezone.now())
    seen, cursor = set(), None
    with subject(world):
        while True:
            with CaptureQueriesContext(connection) as captured:
                current = attachments(world, limit=50, after=cursor)
            assert current.total_count == 251
            assert len(current.items) <= 50
            assert not seen.intersection(str(row.id) for row in current.items)
            seen.update(str(row.id) for row in current.items)
            for row in captured:
                if 'FROM "astrolift_services_managedserviceattachment"' in row["sql"]:
                    assert '"credential_ref"' not in row["sql"]
                    assert '"slice_handle"' not in row["sql"]
            cursor = current.next_cursor
            if cursor is None:
                break
        assert len(seen) == 251
        apps[-1].project = world.platform_project
        apps[-1].save()
        assert attachments(world).total_count == 250


def test_real_scope_revocation_and_team_token_ceiling(world):
    binding = grant(world, Permission.PROJECT_READ, kind="ORG", target=world.org.pk)
    foreign = ManagedService.objects.create(
        project=world.platform_project, tenant_cluster=world.cluster, name="cache", kind="redis"
    )
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=world.medops,
        name="reader",
        token_hash="hash",
        scopes=["read:apps"],
    )
    with subject(world):
        marker = set_current_api_token(token)
        try:
            assert page(world).total_count == 1
            assert detail(world, foreign) is None
            with pytest.raises(PermissionDenied):
                query.astrolift_project_managed_services_page(
                    make_info(world.user), project_id=GUID(str(world.platform_project.guid))
                )
        finally:
            reset_current_api_token(marker)
        binding.delete()
        with pytest.raises(PermissionDenied):
            page(world)


def test_graphql_contract_serializes_only_exact_metadata(world):
    from config.schema import schema

    grant(world, Permission.PROJECT_READ)
    document = """query($project: GUID!, $service: GUID!) {
      astroliftProjectManagedServicesPage(projectId:$project, limit:1) {
        items { id contextRevision organizationId projectId registeredAppId clusterId environmentId status }
        totalCount nextCursor
      }
      astroliftProjectManagedService(projectId:$project, id:$service) { id ownerScope contextRevision }
      astroliftProjectManagedServiceAttachmentsPage(projectId:$project, managedServiceId:$service) {
        items { id consumerId registeredAppId environmentId clusterId } totalCount nextCursor
      }
    }"""
    with subject(world):
        result = schema.execute_sync(
            document,
            variable_values={"project": str(world.medops_project.guid), "service": str(world.service.guid)},
            context_value=make_info(world.user).context,
        )
    assert result.errors is None
    assert result.data["astroliftProjectManagedServicesPage"]["items"][0]["id"] == str(world.service.guid)
    assert "RESOURCE_PRIVATE_MARKER" not in str(result.data)


def test_project_and_organization_mismatch_never_resolves_same_name(world):
    from astrolift_identity.models import Organization, Project, Team

    grant(world, Permission.PROJECT_READ)
    foreign_org = Organization.objects.create(name="Other", slug="other-resources2207")
    foreign_team = Team.objects.create(organization=foreign_org, name="Other", slug="other")
    foreign_project = Project.objects.create(
        organization=foreign_org, team=foreign_team, name="Other", slug="other"
    )
    foreign = ManagedService.objects.create(
        project=foreign_project, tenant_cluster=world.cluster, name="cache", kind="redis"
    )
    with subject(world):
        assert detail(world, foreign) is None
        with pytest.raises(PermissionDenied):
            query.astrolift_project_managed_services_page(
                make_info(world.user), project_id=GUID(str(foreign_project.guid))
            )
        assert page(world).total_count == 1


@pytest.mark.parametrize("revoked", ["app", "environment", "grant", "team"])
def test_attachment_counts_exclude_currently_deleted_or_refused_consumers(world, revoked):
    grant(world, Permission.PROJECT_READ)
    binding = grant(world, Permission.APP_READ, slug="consumer-reader2207")
    environment = AppEnvironment.objects.create(
        registered_app=world.medops_app, name="production", tenant_cluster=world.cluster
    )
    ManagedServiceAttachment.objects.create(managed_service=world.service, app_environment=environment)
    with subject(world):
        assert attachments(world).total_count == 1
        target = {
            "app": world.medops_app,
            "environment": environment,
            "grant": binding,
            "team": world.medops,
        }[revoked]
        target.delete() if revoked == "grant" else target.soft_delete()
        if revoked == "team":
            with pytest.raises(PermissionDenied):
                attachments(world)
        else:
            assert attachments(world).total_count == 0


def test_missing_attachment_target_is_refused_not_an_empty_consumer_page(world):
    grant(world, Permission.PROJECT_READ, Permission.APP_READ, kind="ORG", target=world.org.pk)
    world.service.delete()
    with subject(world):
        for read in (
            lambda: attachments(world),
            lambda: query.astrolift_managed_service_attachments_page(
                make_info(world.user), managed_service_id=GUID(str(world.service.guid))
            ),
        ):
            with pytest.raises(GraphQLError) as error:
                read()
            assert error.value.extensions["code"] == "TARGET_UNAVAILABLE"
            assert "RESOURCE_PRIVATE_MARKER" not in str(error.value)
