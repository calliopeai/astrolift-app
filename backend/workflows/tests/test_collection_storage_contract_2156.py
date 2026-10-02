"""Real tenant authorization, immutable reviewed plans and item-routing records."""

from types import SimpleNamespace

import pytest
from astrolift_identity.models import Organization
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.activities.workflow_stage_activities import (
    _create_stage_execution_sync,
    _get_workflow_stages_sync,
    _update_stage_execution_sync,
)
from astrolift_workflows.schema.mutations import _execution_signal_target
from config.schema import schema
from core.run_input_contract import digest
from core.tenancy import TenantContext, tenant_context
from django.contrib.auth import get_user_model

from workflows.importers.langflow import LangflowImporter
from workflows.manifest import (
    create_definition_from_manifest,
    definition_to_manifest,
    emit_workflow_manifest,
    parse_workflow_manifest,
)
from workflows.models import WorkflowStage, WorkflowStageExecution
from workflows.reviewed_starts import definition_revision, reserve_start
from workflows.tests.test_langflow_collections_2156 import source_collection

pytestmark = pytest.mark.django_db(transaction=True)


def world():
    org = Organization.objects.create(
        name="Collection storage", slug="collection-storage"
    )
    user = get_user_model().objects.create_superuser(
        username="collection-reviewer", email="collection@example.test", password="test"
    )
    manifest = LangflowImporter().import_flow(source_collection()).manifest
    definition = create_definition_from_manifest(
        manifest, organization=org, is_enabled=True
    )
    return org, user, definition


def test_review_freezes_collection_body_and_records_without_live_fallback():
    org, user, definition = world()
    owner = definition.stages.get(kind="collection")
    before = definition_revision(definition)
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        reviewed = reserve_start(
            definition_id=definition.guid,
            expected_revision=before,
            expected_input_schema_digest=digest(definition.input_schema),
            request_id="collection-freeze",
            inputs={},
            user=user,
        )
        owner.iteration = {
            **owner.iteration,
            "max_items": 20,
            "items": [{"text": "changed"}],
        }
        owner.save()
        plan = _get_workflow_stages_sync(
            definition.slug,
            str(reviewed.execution.pk),
            workflow_definition_id=str(definition.pk),
            review_bounded_loops=True,
        )
    assert plan["stages"][0]["iteration"]["max_items"] == 3
    assert plan["stages"][0]["iteration"]["items"] == [
        {"text": "first"},
        {"text": "second"},
    ]
    assert definition_revision(definition) != before
    assert (
        parse_workflow_manifest(
            emit_workflow_manifest(definition_to_manifest(definition))
        )
        .stages[0]
        .iteration
        == owner.iteration
    )


def test_actual_graphql_import_and_edit_preserve_iteration_contract():
    org, user, _ = world()
    context = SimpleNamespace(user=user, request=None)
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        result = schema.execute_sync(
            'mutation($payload:JSON!){importWorkflowFlow(format:"langflow",payload:$payload,preview:false){ok createdSlug stages{kind iteration}}}',
            variable_values={"payload": source_collection()},
            context_value=context,
        )
        assert not result.errors and result.data["importWorkflowFlow"]["ok"]
        from workflows.models import WorkflowDefinition

        imported = WorkflowDefinition.objects.get(
            organization=org, slug=result.data["importWorkflowFlow"]["createdSlug"]
        )
        owner = imported.stages.get(kind="collection")
        iteration = {**owner.iteration, "max_items": 4}
        updated = schema.execute_sync(
            "mutation($id:ID!,$iteration:JSON!){updateWorkflowStage(stageGuid:$id,iteration:$iteration){ok errors{field messages}}}",
            variable_values={"id": str(owner.guid), "iteration": iteration},
            context_value=context,
        )
        assert not updated.errors and updated.data["updateWorkflowStage"]["ok"]
    owner.refresh_from_db()
    assert owner.iteration == iteration


def test_parent_item_binding_rejects_spoofed_parent_bounds_and_exact_signal_target():
    org, _, definition = world()
    owner, body = definition.stages.order_by("order")
    run = WorkflowRun.objects.create(
        organization=org,
        workflow_definition=definition,
        workflow_id="collection-routing-target",
        workflow_kind="WorkflowDefinitionRunWorkflow",
        status="running",
    )
    parent_id = _create_stage_execution_sync(str(run.pk), str(owner.pk), 1, {})
    binding = {
        "owner_order": 0,
        "body_stage_ids": [str(body.pk)],
        "item_count": 2,
        "max_items": 3,
    }
    _update_stage_execution_sync(parent_id, "running", {"collection": binding}, None)
    target = f"WorkflowDefinitionRunWorkflow-{run.pk}:collection:0:0-parent-{parent_id}"
    context = {
        "collection_parent_execution_id": parent_id,
        "collection_index": 0,
        "collection_workflow_id": target,
    }
    item_id = _create_stage_execution_sync(str(run.pk), str(body.pk), 1, context)
    assert (
        _create_stage_execution_sync(str(run.pk), str(body.pk), 1, context) == item_id
    )
    assert (
        _execution_signal_target(item_id, run.workflow_id, organization_id=org.pk)
        == target
    )
    assert (
        _execution_signal_target(item_id, run.workflow_id, organization_id=org.pk + 1)
        is None
    )
    unrelated = WorkflowStage.objects.create(
        definition=definition, order=2, kind="checkpoint", output_key="not-in-body"
    )
    for patch in [
        {"collection_index": -1},
        {"collection_index": True},
        {"collection_index": 2},
        {"collection_workflow_id": "other-run"},
        {"collection_parent_execution_id": item_id},
    ]:
        with pytest.raises((ValueError, WorkflowStageExecution.DoesNotExist)):
            _create_stage_execution_sync(
                str(run.pk), str(body.pk), 1, {**context, **patch}
            )
    with pytest.raises(ValueError, match="does not bind"):
        _create_stage_execution_sync(str(run.pk), str(unrelated.pk), 1, context)
    item = WorkflowStageExecution.objects.get(pk=item_id)
    item.collection_workflow_id = "attacker-target"
    item.save()
    assert (
        _execution_signal_target(item_id, run.workflow_id, organization_id=org.pk)
        is None
    )
    _update_stage_execution_sync(parent_id, "failed", {"collection": binding}, "closed")
    with pytest.raises(ValueError, match="does not bind"):
        _create_stage_execution_sync(str(run.pk), str(body.pk), 2, context)


def test_actual_graphql_exposes_only_owned_recorded_collection_identity():
    org, user, definition = world()
    other = Organization.objects.create(
        name="Other collection identity", slug="other-collection-identity"
    )
    owner, body = definition.stages.order_by("order")
    run = WorkflowRun.objects.create(
        organization=org,
        workflow_definition=definition,
        workflow_id="collection-metadata-proof",
        run_id="run-metadata-proof",
        workflow_kind="WorkflowDefinitionRunWorkflow",
        status="running",
    )
    parent = WorkflowStageExecution.objects.create(
        workflow_run=run,
        stage=owner,
        status="running",
        slug="collection-metadata-parent",
    )
    item = WorkflowStageExecution.objects.create(
        workflow_run=run,
        stage=body,
        collection_parent_execution=parent,
        collection_index=1,
        status="running",
        slug="collection-metadata-item",
    )
    query = 'query{workflowStageExecutions(workflowId:"collection-metadata-proof",runId:"run-metadata-proof"){guid collectionIndex collectionStageId collectionParentExecutionGuid fanoutIndex}}'
    context = SimpleNamespace(user=user, request=None)
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        own = schema.execute_sync(query, context_value=context)
    assert not own.errors
    row = next(
        row
        for row in own.data["workflowStageExecutions"]
        if row["guid"] == str(item.guid)
    )
    assert row == {
        "guid": str(item.guid),
        "collectionIndex": 1,
        "collectionStageId": str(owner.guid),
        "collectionParentExecutionGuid": str(parent.guid),
        "fanoutIndex": None,
    }
    with tenant_context(TenantContext(organization_id=other.pk, actor_user_id=user.pk)):
        foreign = schema.execute_sync(query, context_value=context)
    assert not foreign.errors and foreign.data["workflowStageExecutions"] == []
