"""Actual import, storage, native nested execution and source feedback contracts."""

import asyncio
import hashlib
import json
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from temporalio.api.enums.v1 import EventType

from astrolift_identity.models import Member, Organization, Role, RoleBinding
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.tests.test_serial_collections_temporal_2156 import (
    REGISTERED,
    replay_family,
    rows,
    start,
)
from astrolift_workflows.workflows.workflow_definition_run import WorkflowDefinitionRunWorkflow
from core.permissions import Permission
from core.run_input_contract import digest
from core.tenancy import TenantContext, tenant_context
from core.testing.temporal import temporal_worker
from workflows.importers.langflow import LangflowImporter
from workflows.manifest import (
    create_definition_from_manifest,
    definition_to_manifest,
    emit_workflow_manifest,
    parse_workflow_manifest,
)
from workflows.models import WorkflowDefinition, WorkflowStage
from workflows.reviewed_starts import definition_revision, reserve_start
from workflows.tests.test_langflow_bound_bodies_2156 import bound_source

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


def native_bound_plan(*, second_parser=True, missing_output=False, missing_next_input=False):
    org = Organization.objects.create(name="Bound source body", slug="bound-source-native")
    child = WorkflowDefinition.objects.create(
        organization=org,
        name="Reviewed native child",
        slug="bound-native-child",
        model_label="",
        is_enabled=True,
    )
    WorkflowStage.objects.create(
        definition=child,
        order=0,
        kind="checkpoint" if missing_output else "format_record",
        output_key="reply",
        iteration={}
        if missing_output
        else {"source_format": "langflow_parser", "pattern": "Native: {input_value}", "separator": "\n"},
    )
    source = bound_source(child.guid)
    if missing_next_input:
        next_child = WorkflowDefinition.objects.create(
            organization=org,
            name="Reviewed next native child",
            slug="bound-native-next-child",
            model_label="",
            is_enabled=True,
        )
        WorkflowStage.objects.create(
            definition=next_child,
            order=0,
            kind="format_record",
            output_key="reply",
            iteration={
                "source_format": "langflow_parser",
                "pattern": "Next: {input_value}",
                "separator": "\n",
            },
        )
        source["data"]["nodes"][0]["data"]["node"]["template"]["texts"]["value"][0] = ""
        first_node = source["data"]["nodes"][2]
        first_node["data"]["node"]["outputs"] = [{"name": "child-reply~data", "types": ["Data"]}]
        next_source = bound_source(next_child.guid)
        next_node = next_source["data"]["nodes"][2]
        next_node["id"] = "RunFlow-next-native"
        source["data"]["nodes"].append(next_node)
        source["astrolift_bindings"][first_node["id"]].update(output_path="", output_mode="data")
        source["astrolift_bindings"][next_node["id"]] = next_source["astrolift_bindings"]["Parser-body"]
        for node in (first_node, next_node):
            source["astrolift_bindings"][node["id"]]["source_node_digest"] = hashlib.sha256(
                json.dumps(node, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            ).hexdigest()
        return_edge = source["data"]["edges"][2]
        return_edge["sourceHandle"] = json.dumps({"id": first_node["id"], "name": "child-reply~data"})
        return_edge["target"] = next_node["id"]
        return_edge["targetHandle"] = json.dumps(
            {"id": next_node["id"], "fieldName": "child-entry~input_value"}
        )
        source["data"]["edges"].append(
            {
                "source": next_node["id"],
                "target": "Loop-control",
                "sourceHandle": json.dumps({"id": next_node["id"], "name": "child-reply~message"}),
                "targetHandle": json.dumps({"id": "Loop-control", "fieldName": "item"}),
            }
        )
    if second_parser:
        parser = {
            "id": "Parser-after-native",
            "data": {"type": "Parser", "node": {"template": {"pattern": {"value": "Final: {text}"}}}},
        }
        source["data"]["nodes"].append(parser)
        return_edge = source["data"]["edges"][2]
        return_edge["target"] = parser["id"]
        return_edge["targetHandle"] = json.dumps({"id": parser["id"], "fieldName": "input_data"})
        source["data"]["edges"].append(
            {
                "source": parser["id"],
                "target": "Loop-control",
                "sourceHandle": json.dumps({"id": parser["id"], "name": "parsed_text"}),
                "targetHandle": json.dumps({"id": "Loop-control", "fieldName": "item"}),
            }
        )
    mapped = LangflowImporter().import_flow(source).manifest
    imported = parse_workflow_manifest(emit_workflow_manifest(mapped))
    definition = create_definition_from_manifest(imported, organization=org, is_enabled=True)
    exported = definition_to_manifest(definition)
    assert [stage.iteration for stage in exported.stages] == [stage.iteration for stage in imported.stages]
    assert exported.stages[1].workflow == f"guid:{child.guid}"
    if missing_next_input:
        user = get_user_model().objects.create_user(username="bound-source-reviewed-actor")
        Member.objects.create(user=user, scope_kind="ORG", scope_id=org.pk)
        role = Role.objects.create(
            organization=org,
            name="Bound source workflow trigger",
            slug="bound-source-trigger",
            scope_level="ORG",
            permissions=[Permission.WORKFLOW_TRIGGER.value],
        )
        RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.pk)
        with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
            run = reserve_start(
                definition_id=str(definition.guid),
                expected_revision=definition_revision(definition),
                expected_input_schema_digest=digest(definition.input_schema),
                request_id="missing-source-input",
                inputs={},
                user=user,
            ).execution
    else:
        run = WorkflowRun.objects.create(
            organization=org,
            workflow_definition=definition,
            workflow_kind="WorkflowDefinitionRunWorkflow",
            workflow_id=f"bound-source-{uuid.uuid4()}",
            status="running",
        )
    stages = list(definition.stages.order_by("order"))
    return (definition.pk, definition.slug, run.pk, run.workflow_id, stages[0].pk, stages[1].pk), child.pk


async def test_real_source_run_flow_and_parser_body_executes_serially_and_replays(temporal_env):
    plan, child_pk = await sync_to_async(native_bound_plan)()
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        handle = await start(temporal_env, plan)
        result = await asyncio.wait_for(handle.result(), 30)
        history = await handle.fetch_history()
    assert result.ok
    final = result.data["final_output"]
    assert final["complete"] and final["finished_count"] == 2
    assert [record["text"] for record in final["results"]] == [
        "Final: Native: first",
        "Final: Native: second",
    ]
    children = await sync_to_async(list)(WorkflowRun.objects.filter(parent_run_id=plan[2]).order_by("pk"))
    assert len(children) == 2
    assert all(child.workflow_definition_id == child_pk and child.status == "completed" for child in children)
    executions = await rows(plan[2])
    assert [row.collection_index for row in executions[1:]] == [0, 0, 1, 1]
    assert all(row.status == "completed" for row in executions)
    assert executions[2].ended_at <= executions[3].started_at
    await replay_family(temporal_env, history)


async def test_missing_bound_native_output_is_failed_without_later_item(temporal_env):
    plan, child_pk = await sync_to_async(native_bound_plan)(second_parser=False, missing_output=True)
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        handle = await start(temporal_env, plan)
        result = await asyncio.wait_for(handle.result(), 30)
        history = await handle.fetch_history()
    assert not result.ok
    executions = await rows(plan[2])
    assert len(executions) == 2 and all(row.status == "failed" for row in executions)
    assert executions[1].collection_index == 0
    children = await sync_to_async(list)(WorkflowRun.objects.filter(parent_run_id=plan[2]))
    assert len(children) == 1 and children[0].workflow_definition_id == child_pk
    assert children[0].status == "completed"
    await replay_family(temporal_env, history)


async def test_frozen_native_data_without_text_cannot_dispatch_next_native_body_or_item(temporal_env):
    plan, child_pk = await sync_to_async(native_bound_plan)(
        second_parser=False, missing_output=True, missing_next_input=True
    )
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=REGISTERED
    ):
        handle = await start(temporal_env, plan)
        result = await asyncio.wait_for(handle.result(), 30)
        history = await handle.fetch_history()
    assert not result.ok
    executions = await rows(plan[2])
    assert len(executions) == 2
    assert executions[0].status == "failed"
    assert executions[1].status == "completed" and executions[1].collection_index == 0
    assert "text" not in executions[1].output["result"]
    assert executions[1].output["result"]["input_value"] == ""
    children = await sync_to_async(list)(WorkflowRun.objects.filter(parent_run_id=plan[2]))
    assert len(children) == 1 and children[0].workflow_definition_id == child_pk
    assert children[0].status == "completed"
    started = [
        event.child_workflow_execution_started_event_attributes.workflow_execution
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_STARTED
    ]
    assert len(started) == 1
    item = temporal_env.client.get_workflow_handle(started[0].workflow_id, run_id=started[0].run_id)
    item_result = await item.result()
    assert not item_result["ok"] and item_result["data"]["status"] == "unavailable"
    assert item_result["message"] == "imported text input is unavailable"
    item_history = await item.fetch_history()
    assert (
        sum(
            event.event_type == EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_STARTED
            for event in item_history.events
        )
        == 1
    )
    await replay_family(temporal_env, history)
