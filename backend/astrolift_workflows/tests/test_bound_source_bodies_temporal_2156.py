"""Actual import, storage, native nested execution and source feedback contracts."""

import asyncio
import json
import uuid

import pytest
from asgiref.sync import sync_to_async

from astrolift_identity.models import Organization
from astrolift_operations.models import WorkflowRun
from astrolift_workflows.tests.test_serial_collections_temporal_2156 import (
    REGISTERED,
    replay_family,
    rows,
    start,
)
from astrolift_workflows.workflows.workflow_definition_run import WorkflowDefinitionRunWorkflow
from core.testing.temporal import temporal_worker
from workflows.importers.langflow import LangflowImporter
from workflows.manifest import (
    create_definition_from_manifest,
    definition_to_manifest,
    emit_workflow_manifest,
    parse_workflow_manifest,
)
from workflows.models import WorkflowDefinition, WorkflowStage
from workflows.tests.test_langflow_bound_bodies_2156 import bound_source

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


def native_bound_plan(*, second_parser=True, missing_output=False):
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
