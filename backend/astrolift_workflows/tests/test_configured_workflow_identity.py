"""Exact configured workflow identities across native authoring and read operations."""

import os
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync
from django.utils import timezone

from astrolift_workflows.schema.mutations import WorkflowsMutation
from astrolift_workflows.schema.queries import WorkflowsQuery
from astrolift_workflows.tests.test_import_scopes_2114 import grant, tenant, token
from astrolift_workflows.tests.test_import_scopes_2114 import no_index as no_index
from astrolift_workflows.tests.test_import_scopes_2114 import world as world_fixture
from core.permissions import Permission, PermissionDenied
from core.tests.utils.scope_world import ScopeWorld
from workflows.manifest import create_definition_from_manifest, parse_workflow_manifest
from workflows.models import Workflow, WorkflowDefinition, WorkflowInstance
from workflows.tests.test_manifest import ONE_GATE_TOML, ONE_STAGE_TOML

world = world_fixture
pytestmark = pytest.mark.django_db
PERMISSIONS = (
    Permission.WORKFLOW_READ,
    Permission.WORKFLOW_CREATE,
    Permission.WORKFLOW_UPDATE,
    Permission.WORKFLOW_DELETE,
)


def recipe(w, *, project=None, slug=None, toml=ONE_GATE_TOML):
    definition = create_definition_from_manifest(
        parse_workflow_manifest(toml),
        organization=w.org,
        project=project or w.medops_project,
    )
    if slug:
        definition.slug = slug
        definition.save()
    return definition


def configured(w, definition=None):
    return Workflow.objects.create(
        organization=w.org,
        definition=definition or recipe(w),
        name="Configured identity",
        slug=f"configured-{uuid4().hex[:8]}",
        is_enabled=False,
    )


def operate(w, operation, **kwargs):
    if operation == "read":
        return WorkflowsQuery().workflow(w.info, **kwargs)
    if operation == "update":
        return WorkflowsMutation().update_workflow(w.info, name="Updated", **kwargs)
    return WorkflowsMutation().delete_workflow(w.info, **kwargs)


@pytest.mark.parametrize("identity", ["guid", "slug", "both"])
def test_configured_crud_uses_one_identity_and_preserves_legacy_calls(world, identity):
    grant(world, *PERMISSIONS)
    definition = recipe(world)
    definition_args = {"definition_id": str(definition.guid)} if identity != "slug" else {}
    if identity != "guid":
        definition_args["definition_slug"] = definition.slug
    with tenant(world):
        created = WorkflowsMutation().create_workflow(
            world.info, name="Prepared", is_enabled=False, **definition_args
        )
        assert created.ok
        row = Workflow.objects.get(guid=created.workflow.guid)
        assert not row.is_enabled and row.definition_id == definition.pk
        assert created.workflow.definition_guid == str(definition.guid)
        args = {"workflow_id": str(row.guid)} if identity != "slug" else {}
        if identity != "guid":
            args["slug"] = row.slug
        assert operate(world, "read", **args).guid == str(row.guid)
        assert operate(world, "update", **args).ok
        row.refresh_from_db()
        assert row.name == "Updated"
        assert operate(world, "delete", **args).ok
    row.refresh_from_db()
    assert row.deleted_at is not None


def test_legacy_create_retains_enabled_default(world):
    grant(world, Permission.WORKFLOW_CREATE)
    definition = recipe(world)
    with tenant(world):
        result = WorkflowsMutation().create_workflow(
            world.info, name="Legacy", definition_slug=definition.slug
        )
    assert result.ok and result.workflow.is_enabled


@pytest.mark.parametrize("owner", ["own", "sibling", "foreign"])
@pytest.mark.parametrize("operator", [False, True])
def test_create_checks_token_organization_and_team_even_with_broad_user_role(world, owner, operator):
    grant(world, Permission.WORKFLOW_CREATE, kind="ORG")
    definition = recipe(world)
    with tenant(world), token(world, owner=owner, scope="workflow:write", operator=operator):
        if owner == "own":
            result = WorkflowsMutation().create_workflow(
                world.info,
                name="Prepared",
                definition_id=str(definition.guid),
                is_enabled=False,
            )
            assert result.ok
        else:
            with pytest.raises(PermissionDenied):
                WorkflowsMutation().create_workflow(
                    world.info,
                    name="Refused",
                    definition_id=str(definition.guid),
                    is_enabled=False,
                )
    assert Workflow.objects.filter(organization=world.org).count() == int(owner == "own")


@pytest.mark.parametrize("operation", ["read", "update", "delete"])
@pytest.mark.parametrize("case", ["missing", "malformed", "retired", "slug_mismatch"])
def test_guid_never_falls_back_to_a_same_slug_workflow(world, operation, case):
    grant(world, *PERMISSIONS, kind="ORG")
    row = configured(world)
    guid = str(row.guid)
    slug = row.slug
    if case == "missing":
        guid = str(uuid4())
    elif case == "malformed":
        guid = "not-a-guid"
    elif case == "slug_mismatch":
        slug = "unrelated"
    else:
        row.deleted_at = timezone.now()
        row.save()
        Workflow.objects.create(
            organization=world.org, definition=row.definition, name="Replacement", slug=slug, is_enabled=False
        )
    before = list(Workflow.objects.order_by("pk").values())
    with tenant(world):
        result = operate(world, operation, workflow_id=guid, slug=slug)
    assert result is None if operation == "read" else not result.ok
    assert list(Workflow.objects.order_by("pk").values()) == before


@pytest.mark.parametrize("case", ["missing", "malformed", "retired", "foreign", "slug_mismatch"])
def test_create_never_substitutes_a_definition_for_a_supplied_guid(world, case):
    grant(world, *PERMISSIONS, kind="ORG")
    definition = recipe(world)
    guid, slug = str(definition.guid), definition.slug
    if case == "missing":
        guid = str(uuid4())
    elif case == "malformed":
        guid = "bad"
    elif case == "foreign":
        guid = str(recipe(ScopeWorld("configured-foreign")).guid)
    elif case == "slug_mismatch":
        slug = "different"
    else:
        definition.deleted_at = timezone.now()
        definition.save()
        recipe(world, slug=slug)
    with tenant(world):
        result = WorkflowsMutation().create_workflow(
            world.info, name="Refused", definition_id=guid, definition_slug=slug, is_enabled=False
        )
    assert not result.ok
    assert not Workflow.objects.filter(organization=world.org).exists()


def test_exact_global_definition_wins_only_when_its_id_was_selected(world):
    grant(world, *PERMISSIONS, kind="ORG")
    slug = f"global-selection-{uuid4().hex[:8]}"
    own = recipe(world, slug=slug)
    global_definition = create_definition_from_manifest(
        parse_workflow_manifest(ONE_GATE_TOML.replace("demand-scout", slug)), organization=None
    )
    assert own.slug == global_definition.slug
    with tenant(world):
        exact = WorkflowsMutation().create_workflow(
            world.info, name="Global", definition_id=str(global_definition.guid), is_enabled=False
        )
        legacy = WorkflowsMutation().create_workflow(
            world.info, name="Own", definition_slug=own.slug, is_enabled=False
        )
    assert exact.ok and legacy.ok
    assert exact.workflow.definition_guid == str(global_definition.guid)
    assert legacy.workflow.definition_guid == str(own.guid)


@pytest.mark.parametrize("operation", ["read", "update", "delete"])
@pytest.mark.parametrize("operator", [False, True])
def test_broad_role_and_operator_cannot_expand_team_token_to_sibling_workflow(world, operation, operator):
    grant(world, *PERMISSIONS, kind="ORG")
    row = configured(world, recipe(world, project=world.platform_project))
    before = list(Workflow.objects.order_by("pk").values())
    with tenant(world), token(world, owner="own", operator=operator), pytest.raises(PermissionDenied):
        operate(world, operation, workflow_id=str(row.guid))
    assert list(Workflow.objects.order_by("pk").values()) == before


@pytest.mark.parametrize("operation", ["read", "update", "delete"])
@pytest.mark.parametrize("retired", ["project", "team"])
def test_retired_configured_owner_cannot_use_selected_context(world, operation, retired):
    grant(world, *PERMISSIONS, kind="ORG")
    row = configured(world)
    target = world.medops_project if retired == "project" else world.medops
    target.deleted_at = timezone.now()
    target.save()
    with tenant(world), pytest.raises(PermissionDenied):
        operate(world, operation, workflow_id=str(row.guid))


def test_repoint_requires_source_update_and_destination_create(world):
    row = configured(world)
    destination = recipe(world, project=world.platform_project)
    grant(world, Permission.WORKFLOW_UPDATE)
    with tenant(world), pytest.raises(PermissionDenied):
        WorkflowsMutation().update_workflow(
            world.info, workflow_id=str(row.guid), definition_id=str(destination.guid)
        )
    row.refresh_from_db()
    assert row.definition_id != destination.pk
    grant(world, Permission.WORKFLOW_CREATE, scope_id=world.platform_project.pk)
    with tenant(world):
        result = WorkflowsMutation().update_workflow(
            world.info, workflow_id=str(row.guid), definition_id=str(destination.guid)
        )
    assert result.ok and result.workflow.definition_guid == str(destination.guid)


def test_failed_repoint_binding_validation_leaves_source_unchanged(world):
    grant(world, *PERMISSIONS)
    row = configured(world)
    destination = recipe(world, toml=ONE_STAGE_TOML)
    before = list(Workflow.objects.filter(pk=row.pk).values())
    with tenant(world):
        result = WorkflowsMutation().update_workflow(
            world.info,
            workflow_id=str(row.guid),
            definition_id=str(destination.guid),
            inputs={"topic": "new"},
        )
    assert not result.ok and result.errors[0].field == "stage_bindings"
    assert list(Workflow.objects.filter(pk=row.pk).values()) == before


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_write_rechecks_actual_owner_after_admission(world, operation, monkeypatch):
    import astrolift_workflows.schema.mutations as mutations

    grant(world, *PERMISSIONS)
    row = configured(world)
    original = mutations._resolve_caller_org

    def changed(org_id):
        WorkflowDefinition.objects.filter(pk=row.definition_id).update(project=world.platform_project)
        return original(org_id)

    monkeypatch.setattr(mutations, "_resolve_caller_org", changed)
    with tenant(world), pytest.raises(PermissionDenied):
        if operation == "create":
            WorkflowsMutation().create_workflow(
                world.info, name="Refused", definition_id=str(row.definition.guid), is_enabled=False
            )
        else:
            operate(world, operation, workflow_id=str(row.guid))
    row.refresh_from_db()
    assert row.name == "Configured identity" and row.deleted_at is None
    assert Workflow.objects.filter(organization=world.org).count() == 1


def test_graphql_create_read_update_delete_uses_exact_workflow_and_definition_ids(world):
    from django.conf import settings
    from django.test import Client

    from astrolift_identity.models import Member
    from astrolift_registry.models import Workload

    grant(world, *PERMISSIONS)
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    agent = Workload.objects.create(
        registered_app=world.medops_app,
        name="Configured agent",
        slug="configured-agent",
        kind="agent",
    )
    definition = recipe(world, toml=ONE_STAGE_TOML)
    client = Client()
    client.force_login(world.user)

    def graphql(query, variables):
        response = client.post(
            f"/{settings.BASE_URL}gql/config/",
            data={"query": query, "variables": variables},
            content_type="application/json",
            HTTP_X_PLATFORM="WEB",
            HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
            HTTP_X_ASTROLIFT_TEAM=str(world.platform.pk),
        )
        assert response.status_code == 200
        payload = response.json()
        assert not payload.get("errors"), payload
        return payload["data"]

    created = graphql(
        'mutation($definition: GUID!, $bindings: JSON!) { createWorkflow(name: "Prepared", definitionId: $definition, stageBindings: $bindings, isEnabled: false) { ok workflow { guid definitionGuid isEnabled } } }',
        {"definition": str(definition.guid), "bindings": {"0": {"agent_workload_id": str(agent.guid)}}},
    )["createWorkflow"]
    assert created["ok"] and not created["workflow"]["isEnabled"]
    assert created["workflow"]["definitionGuid"] == str(definition.guid)
    identity = {"id": created["workflow"]["guid"]}
    updated = graphql(
        'mutation($id: GUID!) { updateWorkflow(workflowId: $id, inputs: {topic: "release notes"}) { ok workflow { guid definitionGuid inputs } } }',
        identity,
    )["updateWorkflow"]
    assert updated["ok"] and updated["workflow"]["inputs"] == {"topic": "release notes"}
    read = graphql(
        "query($id: GUID!) { workflow(workflowId: $id) { guid definitionGuid stageBindings } }", identity
    )["workflow"]
    assert read["guid"] == identity["id"]
    assert read["stageBindings"] == {"0": {"agent_workload_id": str(agent.guid)}}
    assert graphql("mutation($id: GUID!) { deleteWorkflow(workflowId: $id) { ok } }", identity)[
        "deleteWorkflow"
    ]["ok"]
    assert Workflow.objects.get(guid=identity["id"]).deleted_at is not None


@pytest.mark.django_db(transaction=True)
def test_disabled_configuration_does_not_start_real_execution_or_schedule(world, settings):
    from temporalio.client import Client

    from astrolift_workflows import client as native_client

    address = os.environ.get("ASTROLIFT_TEST_TEMPORAL_ADDRESS") or os.environ["TEMPORAL_ADDRESS"]
    settings.TEMPORAL_ADDRESS = address
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    grant(world, *PERMISSIONS)
    definition = recipe(world)

    async def observed():
        client = await Client.connect(address, namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"))
        return (
            {(row.id, row.run_id) async for row in client.list_workflows()},
            {row.id async for row in await client.list_schedules()},
        )

    before = async_to_sync(observed)()
    previous_client = native_client._client
    native_client._client = None
    try:
        assert native_client._temporal_enabled()
        with tenant(world):
            result = WorkflowsMutation().create_workflow(
                world.info,
                name="Prepared only",
                definition_id=str(definition.guid),
                is_enabled=False,
                trigger_kind="schedule",
                schedule_cron="0 13 * * *",
            )
        assert result.ok and not result.workflow.is_enabled
        assert async_to_sync(observed)() == before
        assert not WorkflowInstance.objects.exists()
    finally:
        native_client._client = previous_client
