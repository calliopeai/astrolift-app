"""Exact project destinations and retained ownership across native imports."""

from uuid import uuid4

import pytest
from django.utils import timezone

from astrolift_identity.models import Project
from astrolift_workflows.schema.manifest import WorkflowManifestMutation
from astrolift_workflows.tests.test_import_scopes_2114 import (
    grant,
    owned,
    tenant,
    token,
)
from astrolift_workflows.tests.test_import_scopes_2114 import (
    no_index as no_index,
)
from astrolift_workflows.tests.test_import_scopes_2114 import (
    world as world_fixture,
)
from astrolift_workflows.tests.test_manifest_schema import VALID_TOML
from core.permissions import Permission, PermissionDenied
from core.tests.utils.scope_world import ScopeWorld
from workflows.manifest import create_definition_from_manifest, parse_workflow_manifest
from workflows.models import Workflow, WorkflowDefinition, WorkflowStage
from workflows.tests.test_manifest import ONE_GATE_TOML, ONE_STAGE_TOML, TWO_GATE_TOML

world = world_fixture
pytestmark = pytest.mark.django_db


def invoke(w, **kwargs):
    return WorkflowManifestMutation().import_workflow_manifest(
        w.info, **{"toml": VALID_TOML, "preview": False, "project_id": str(w.medops_project.guid), **kwargs}
    )


@pytest.mark.parametrize("kind", ["PROJECT", "TEAM", "ORG", "APP"])
@pytest.mark.parametrize("preview", [True, False])
def test_exact_project_destination_uses_actual_grant_and_returns_persisted_identity(world, kind, preview):
    grant(world, Permission.WORKFLOW_CREATE, kind=kind)
    with tenant(world):
        if kind == "APP":
            with pytest.raises(PermissionDenied):
                invoke(world, preview=preview)
            return
        result = invoke(world, preview=preview)
    assert result.ok
    rows = WorkflowDefinition.objects.filter(organization=world.org)
    if preview:
        assert result.definition_id is None and result.created_slug is None
        assert not rows.exists()
    else:
        row = rows.get(guid=result.definition_id)
        assert row.project == world.medops_project
        assert row.slug == result.created_slug == result.manifest.definition.slug
        assert not row.is_enabled
        assert row.stages.count() == 1


@pytest.mark.parametrize("owner", ["own", "org", "sibling", "foreign"])
@pytest.mark.parametrize("operator", [False, True])
def test_project_import_enforces_token_ceiling_even_for_org_roles(world, owner, operator):
    grant(world, Permission.WORKFLOW_CREATE, kind="ORG")
    with tenant(world), token(world, owner=owner, scope="workflow:write", operator=operator):
        if owner in {"own", "org"}:
            assert invoke(world).ok
        else:
            with pytest.raises(PermissionDenied):
                invoke(world)
    assert WorkflowDefinition.objects.filter(organization=world.org).count() == int(owner in {"own", "org"})


@pytest.mark.parametrize("case", ["foreign", "missing", "deleted_project", "deleted_team", "incoherent"])
def test_invalid_destination_never_falls_back_to_org(world, case):
    grant(world, Permission.WORKFLOW_CREATE, kind="ORG")
    project_id = str(world.medops_project.guid)
    if case == "foreign":
        project_id = str(ScopeWorld("project-import-foreign").medops_project.guid)
    elif case == "missing":
        project_id = str(uuid4())
    elif case == "deleted_project":
        Project.objects.filter(pk=world.medops_project.pk).update(deleted_at=timezone.now())
    elif case == "deleted_team":
        world.medops.deleted_at = timezone.now()
        world.medops.save()
    else:
        Project.objects.filter(pk=world.medops_project.pk).update(team=ScopeWorld("incoherent").medops)
    with tenant(world), pytest.raises(PermissionDenied):
        invoke(world, project_id=project_id)
    assert not WorkflowDefinition.objects.filter(organization=world.org).exists()


@pytest.mark.parametrize("shape_changed", [False, True])
def test_replacement_refuses_explicit_ownership_transfer(world, shape_changed):
    definition = owned(world)
    grant(world, Permission.WORKFLOW_CREATE, Permission.WORKFLOW_UPDATE, kind="ORG")
    with tenant(world), pytest.raises(PermissionDenied):
        invoke(
            world,
            replace=True,
            project_id=str(world.platform_project.guid),
            toml=VALID_TOML + ('\n[[stage]]\nkind="human_gate"\n' if shape_changed else ""),
        )
    definition.refresh_from_db()
    assert definition.project == world.medops_project
    assert WorkflowDefinition.objects.filter(organization=world.org).count() == 1
    assert definition.stages.count() == 1


@pytest.mark.parametrize("explicit", [True, False])
@pytest.mark.parametrize("blocked", [True, False])
def test_project_version_preserves_owner_enablement_and_binding_rollback(world, explicit, blocked):
    old = create_definition_from_manifest(
        parse_workflow_manifest(TWO_GATE_TOML),
        organization=world.org,
        project=world.medops_project,
        is_enabled=True,
    )
    configured = Workflow.objects.create(
        organization=world.org,
        definition=old,
        name="Project workflow",
        slug="project-workflow",
        stage_bindings={},
        is_enabled=False,
    )
    before = set(WorkflowStage.objects.values_list("pk", flat=True))
    grant(world, Permission.WORKFLOW_CREATE, Permission.WORKFLOW_UPDATE)
    with tenant(world):
        result = invoke(
            world,
            toml=ONE_STAGE_TOML if blocked else ONE_GATE_TOML,
            replace=True,
            project_id=str(world.medops_project.guid) if explicit else None,
        )
    configured.refresh_from_db()
    if blocked:
        assert not result.ok
        assert result.definition_id is None
        assert result.errors[0].field == "workflow.project-workflow"
        assert configured.definition_id == old.pk
        assert WorkflowDefinition.objects.filter(organization=world.org).count() == 1
        assert set(WorkflowStage.objects.values_list("pk", flat=True)) == before
    else:
        assert result.ok and result.mode == "versioned"
        assert configured.definition_id != old.pk
        assert str(configured.definition.guid) == str(result.definition_id)
        assert configured.definition.project == world.medops_project
        assert configured.definition.is_enabled
        assert result.repointed_slugs == [configured.slug]
    assert old.stages.count() == 2


def test_duplicate_new_import_reports_unique_persisted_identity(world):
    grant(world, Permission.WORKFLOW_CREATE)
    with tenant(world):
        first, second = invoke(world), invoke(world)
    assert first.definition_id != second.definition_id
    assert first.created_slug != second.created_slug
    assert WorkflowDefinition.objects.filter(project=world.medops_project, is_enabled=False).count() == 2


def test_replace_creates_then_updates_exact_project_definition(world):
    grant(world, Permission.WORKFLOW_CREATE, Permission.WORKFLOW_UPDATE)
    with tenant(world):
        first = invoke(world, replace=True)
        assert first.ok and first.mode == "created"
        definition = WorkflowDefinition.objects.get(guid=first.definition_id)
        stages = list(definition.stages.values_list("pk", flat=True))
        second = invoke(
            world, replace=True, toml=VALID_TOML.replace('name = "Feature Dev"', 'name = "Edited"')
        )
    definition.refresh_from_db()
    assert second.ok and second.mode == "updated_in_place"
    assert first.definition_id == second.definition_id
    assert definition.name == "Edited"
    assert definition.project == world.medops_project
    assert list(definition.stages.values_list("pk", flat=True)) == stages


@pytest.mark.parametrize("retired", ["project", "team"])
def test_versioning_a_retired_project_refuses_instead_of_promoting_to_org(world, retired):
    old = owned(world)
    grant(world, Permission.WORKFLOW_CREATE, Permission.WORKFLOW_UPDATE, kind="ORG")
    row = world.medops_project if retired == "project" else world.medops
    row.deleted_at = timezone.now()
    row.save()
    with tenant(world):
        result = invoke(
            world, project_id=None, replace=True, toml=VALID_TOML + '\n[[stage]]\nkind="human_gate"\n'
        )
    assert not result.ok and result.definition_id is None
    assert result.errors[0].field == "project"
    assert WorkflowDefinition.objects.filter(organization=world.org).count() == 1
    assert old.stages.count() == 1


def test_new_import_rechecks_project_after_admission(world, monkeypatch):
    import astrolift_workflows.schema.mutations as mutations

    grant(world, Permission.WORKFLOW_CREATE)
    original = mutations._resolve_caller_org

    def retired(org_id):
        Project.objects.filter(pk=world.medops_project.pk).update(deleted_at=timezone.now())
        return original(org_id)

    monkeypatch.setattr(mutations, "_resolve_caller_org", retired)
    with tenant(world), pytest.raises(PermissionDenied):
        invoke(world)
    assert not WorkflowDefinition.objects.filter(organization=world.org).exists()


def test_graphql_accepts_project_guid_and_returns_exact_persisted_id(world):
    from django.conf import settings
    from django.test import Client

    from astrolift_identity.models import Member

    grant(world, Permission.WORKFLOW_CREATE)
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    client = Client()
    client.force_login(world.user)
    response = client.post(
        f"/{settings.BASE_URL}gql/config/",
        data={
            "query": "mutation($toml: String!, $project: GUID!) { importWorkflowManifest(toml: $toml, projectId: $project, preview: false) { ok definitionId createdSlug } }",
            "variables": {"toml": VALID_TOML, "project": str(world.medops_project.guid)},
        },
        content_type="application/json",
        HTTP_X_PLATFORM="WEB",
        HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
        HTTP_X_ASTROLIFT_TEAM=str(world.platform.pk),
    )
    assert response.status_code == 200
    payload = response.json()
    assert not payload.get("errors"), payload
    result = payload["data"]["importWorkflowManifest"]
    assert result["ok"]
    assert WorkflowDefinition.objects.get(guid=result["definitionId"]).project == world.medops_project
