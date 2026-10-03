"""Native authoring and frozen plans retain explicitly selected target GUIDs."""

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_identity.models import Member, Organization, Project, Role, RoleBinding, Team
from astrolift_manifest.parser import ManifestError
from astrolift_operations.models import WorkflowRun
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_workflows.activities.workflow_stage_activities import _get_workflow_stages_sync
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from workflows.composition import resolve_child_definition, workflow_parent_references
from workflows.manifest import (
    create_definition_from_manifest,
    definition_to_manifest,
    emit_workflow_manifest,
    parse_workflow_manifest,
    replace_definition_from_manifest,
)
from workflows.models import WorkflowDefinition, WorkflowStage
from workflows.repo_sync import discover_repository_workflows, reconcile_repository_workflows
from workflows.reviewed_starts import _definition_graph, _freeze_plans
from workflows.target_references import reference_guid, reference_matches, references_filter

pytestmark = pytest.mark.django_db


@pytest.fixture
def targets():
    org = Organization.objects.create(name="Exact imported targets", slug="guid-targets")
    team = Team.objects.create(organization=org, name="Team", slug="guid-team")
    project = Project.objects.create(organization=org, team=team, name="Project", slug="guid-project")
    apps = [
        RegisteredApp.objects.create(
            organization=org, team=team, project=project, name=f"App {i}", slug=f"guid-app-{i}"
        )
        for i in range(2)
    ]
    agents = [
        Workload.objects.create(registered_app=app, name="Worker", slug="worker", kind="agent")
        for app in apps
    ]
    child = WorkflowDefinition.objects.create(
        organization=org, name="Child", slug="guid-child", is_enabled=True, model_label=""
    )
    WorkflowStage.objects.create(definition=child, order=0, kind="checkpoint", output_key="record")
    return SimpleNamespace(org=org, project=project, agents=agents, child=child)


def source(targets):
    return f"""[workflow]
slug="guid-parent"
name="Exact parent"
pattern="chained"
[[stage]]
kind="agent_dispatch"
agent="guid:{targets.agents[1].guid}"
output_key="agent_result"
[[stage]]
kind="workflow"
workflow="guid:{targets.child.guid}"
output_key="child_result"
"""


def test_explicit_targets_survive_storage_export_replace_and_repository_sync(targets):
    parsed = parse_workflow_manifest(source(targets))
    parent = create_definition_from_manifest(parsed, organization=targets.org, is_enabled=True)
    assert parent.stages.get(order=0).agent_definition_id == targets.agents[1].pk
    exported = definition_to_manifest(parent)
    assert exported.stages[0].agent == f"guid:{targets.agents[1].guid}"
    assert exported.stages[1].workflow == f"guid:{targets.child.guid}"
    assert parse_workflow_manifest(emit_workflow_manifest(exported)) == exported
    replaced = replace_definition_from_manifest(parsed, organization=targets.org)
    assert replaced.definition.pk == parent.pk
    assert replaced.definition.stages.get(order=0).agent_definition_id == targets.agents[1].pk
    manifests = discover_repository_workflows(
        {"workflows/exact.toml": source(targets).replace('slug="guid-parent"', 'slug="guid-repository"')}
    )
    reconcile_repository_workflows(
        organization=targets.org,
        project=targets.project,
        source_repo="example/agents",
        source_ref="reviewed-source",
        manifests=manifests,
    )
    repo = WorkflowDefinition.objects.get(organization=targets.org, slug="guid-repository")
    assert repo.stages.get(order=0).agent_definition_id == targets.agents[1].pk


def test_runtime_and_review_graph_select_exact_guid_instead_of_shared_slug(targets):
    parent = create_definition_from_manifest(
        parse_workflow_manifest(source(targets)), organization=targets.org, is_enabled=True
    )
    WorkflowDefinition.objects.create(
        organization=None, name="Global shadow", slug=targets.child.slug, is_enabled=True
    )
    graph = _definition_graph(parent)
    assert set(graph["definitions"]) == {parent.pk, targets.child.pk}
    assert graph["edges"][parent.stages.get(order=1).pk].pk == targets.child.pk
    plan = _get_workflow_stages_sync(parent.slug, review_organization_id=targets.org.pk)
    assert plan["stages"][0]["agent_definition_id"] == targets.agents[1].pk
    assert plan["stages"][1]["nested_definition_id"] == str(targets.child.pk)
    assert workflow_parent_references(targets.child) == [parent.stages.get(order=1)]


def test_late_bound_exact_agent_never_selects_same_slug_replacement(targets):
    parent = create_definition_from_manifest(
        parse_workflow_manifest(source(targets)), organization=targets.org, is_enabled=True
    )
    stage = parent.stages.get(order=0)
    stage.agent_definition = None
    stage.save()
    plan = _get_workflow_stages_sync(parent.slug, review_organization_id=targets.org.pk)
    assert plan["stages"][0]["agent_definition_id"] == targets.agents[1].pk
    targets.agents[1].deleted_at = timezone.now()
    targets.agents[1].save()
    with pytest.raises(RuntimeError, match="no resolvable agent"):
        _get_workflow_stages_sync(parent.slug, review_organization_id=targets.org.pk)


def test_conflicting_stored_agent_mapping_does_not_rebind_explicit_guid(targets):
    parent = create_definition_from_manifest(
        parse_workflow_manifest(source(targets)), organization=targets.org, is_enabled=True
    )
    stage = parent.stages.get(order=0)
    stage.agent_definition = targets.agents[0]
    stage.save()
    with pytest.raises(RuntimeError, match="differs from its explicit GUID"):
        _get_workflow_stages_sync(parent.slug, review_organization_id=targets.org.pk)


def test_real_role_bindings_freeze_only_the_explicit_agent_app_and_nested_definition(targets):
    parent = create_definition_from_manifest(
        parse_workflow_manifest(source(targets)), organization=targets.org, is_enabled=True
    )
    stage = parent.stages.get(order=0)
    stage.agent_definition = None
    stage.save()
    role = Role.objects.create(
        organization=targets.org,
        name="Trigger definitions",
        slug="guid-trigger-role",
        scope_level="ORG",
        permissions=[Permission.WORKFLOW_TRIGGER.value],
    )
    agent_role = Role.objects.create(
        organization=targets.org,
        name="Dispatch selected app",
        slug="guid-dispatch-role",
        scope_level="APP",
        permissions=[Permission.AGENT_DISPATCH.value],
    )
    run = WorkflowRun.objects.create(
        organization=targets.org,
        workflow_definition=parent,
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id="guid-freeze",
        status="running",
    )
    for index in (0, 1):
        user = get_user_model().objects.create_user(username=f"guid-review-actor-{index}")
        Member.objects.create(user=user, scope_kind="ORG", scope_id=targets.org.pk)
        RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=targets.org.pk)
        RoleBinding.objects.create(
            user=user,
            role=agent_role,
            scope_kind="APP",
            scope_id=targets.agents[index].registered_app_id,
        )
        with tenant_context(TenantContext(organization_id=targets.org.pk, actor_user_id=user.pk)):
            if index == 0:
                with pytest.raises(PermissionDenied):
                    _freeze_plans(run, parent)
            else:
                plans = _freeze_plans(run, parent)
                assert set(plans) == {str(parent.pk), str(targets.child.pk)}
                assert plans[str(parent.pk)]["stages"][0]["agent_definition_id"] == targets.agents[1].pk


def test_guid_child_deletion_does_not_fall_back_to_global_same_slug(targets):
    parent = create_definition_from_manifest(
        parse_workflow_manifest(source(targets)), organization=targets.org, is_enabled=True
    )
    WorkflowDefinition.objects.create(
        organization=None, name="Global shadow", slug=targets.child.slug, is_enabled=True
    )
    targets.child.deleted_at = timezone.now()
    targets.child.save()
    assert resolve_child_definition(parent, f"guid:{targets.child.guid}") is None
    assert _definition_graph(parent)["edges"][parent.stages.get(order=1).pk] is None


def test_exact_foreign_guid_is_unavailable_under_current_organization(targets):
    parent = create_definition_from_manifest(
        parse_workflow_manifest(source(targets)), organization=targets.org, is_enabled=True
    )
    foreign = Organization.objects.create(name="Foreign", slug="guid-foreign")
    targets.child.organization = foreign
    targets.child.save()
    assert resolve_child_definition(parent, f"guid:{targets.child.guid}") is None
    assert _definition_graph(parent)["edges"][parent.stages.get(order=1).pk] is None
    targets.agents[1].registered_app.organization = foreign
    targets.agents[1].registered_app.save()
    with pytest.raises(RuntimeError, match="outside the run organization"):
        _get_workflow_stages_sync(parent.slug, review_organization_id=targets.org.pk)


@pytest.mark.parametrize("ref", ["guid:", "guid:worker", "guid:" + uuid.uuid4().hex])
def test_malformed_explicit_guid_refuses_without_slug_query_fallback(targets, ref):
    with pytest.raises(ManifestError, match="canonical UUID"):
        parse_workflow_manifest(source(targets).replace(f"guid:{targets.agents[1].guid}", ref))
    with pytest.raises(ValueError, match="canonical UUID"):
        reference_guid(ref)
    assert not reference_matches(targets.agents[0], ref)
    assert not Workload.objects.filter(references_filter([ref])).exists()
