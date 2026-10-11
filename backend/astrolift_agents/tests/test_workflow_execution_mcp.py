"""Native reviewed execution and exact control over authenticated MCP."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone
from temporalio.client import WorkflowFailureError

from astrolift_agents.mcp_contract import MCP_TOOL_META, WORKFLOW_TOOL_NAMES
from astrolift_agents.tests.test_fleet_scopes_1745 import no_opensearch as no_opensearch
from astrolift_agents.tests.test_workflow_mcp import data, rpc, tool
from astrolift_agents.tests.test_workflow_mcp import mcp as mcp_fixture
from astrolift_identity.api_tokens import (
    SCOPE_MCP_DISPATCH,
    SCOPE_MCP_READ,
    SCOPE_WORKFLOW_TRIGGER,
    mint_token,
)
from astrolift_identity.models import ApiToken, Member, Policy
from astrolift_lifecycle.models import AppEnvironment
from astrolift_operations.models import WorkflowRun
from astrolift_workflows import client as workflow_client
from astrolift_workflows.activities import workflow_stage_activities as activities
from astrolift_workflows.workflows.workflow_definition_run import WorkflowDefinitionRunWorkflow
from core.permissions import Permission
from core.testing.temporal import temporal_worker
from core.tests.utils.scope_world import bind_role, make_cluster, make_user
from workflows.models import (
    WorkflowDefinition,
    WorkflowDefinitionStart,
    WorkflowStage,
    WorkflowStageExecution,
)

pytestmark = pytest.mark.django_db(transaction=True)
mcp = mcp_fixture


@pytest.fixture
def execution(mcp, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    mcp.token.scopes = [SCOPE_MCP_READ, SCOPE_MCP_DISPATCH, SCOPE_WORKFLOW_TRIGGER]
    mcp.token.save(update_fields=["scopes"])
    mcp.binding = bind_role(
        mcp.user,
        permissions=[Permission.WORKFLOW_READ, Permission.WORKFLOW_TRIGGER],
        kind="PROJECT",
        scope_id=mcp.world.medops_project.pk,
        slug="mcp-execution-role",
    )
    mcp.definition = WorkflowDefinition.objects.create(
        organization=mcp.world.org,
        project=mcp.world.medops_project,
        slug="mcp-execution",
        name="MCP execution",
        model_label="",
        is_enabled=True,
        input_schema={
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    mcp.stage = WorkflowStage.objects.create(
        definition=mcp.definition,
        slug="mcp-checkpoint",
        order=0,
        kind="checkpoint",
    )
    return mcp


def reviewed_args(execution, **overrides):
    reviewed = data(tool(execution, "get_workflow_definition", definition_id=str(execution.definition.guid)))[
        "definition"
    ]
    return {
        "definition_id": reviewed["guid"],
        "expected_revision": reviewed["revision"],
        "expected_input_schema_digest": reviewed["input_contract"]["digest"],
        "request_id": str(uuid4()),
        "inputs": {"topic": "release notes"},
        "confirmed": True,
        **overrides,
    }


def recorded_run(execution, definition=None, **values):
    definition = definition or execution.definition
    return WorkflowRun.objects.create(
        organization=definition.organization,
        workflow_definition=definition,
        workflow_kind="WorkflowDefinitionRunWorkflow",
        workflow_id=f"mcp-{uuid4()}",
        run_id=str(uuid4()),
        status="running",
        **values,
    )


def test_capabilities_require_both_native_trigger_and_mcp_dispatch(execution):
    writes = {"astrolift_start_workflow_definition", "astrolift_control_workflow_execution"}
    execution_tools = {
        name
        for name in WORKFLOW_TOOL_NAMES
        if MCP_TOOL_META[name]["scope"] in {SCOPE_MCP_READ, SCOPE_MCP_DISPATCH}
    }
    names = {row["name"] for row in rpc(execution, "tools/list").json()["result"]["tools"]}
    assert execution_tools <= names
    assert "astrolift_import_workflow_manifest" not in names
    args = reviewed_args(execution)
    for scopes in ([SCOPE_MCP_READ, SCOPE_MCP_DISPATCH], [SCOPE_MCP_READ, SCOPE_WORKFLOW_TRIGGER]):
        execution.token.scopes = scopes
        execution.token.save(update_fields=["scopes"])
        names = {row["name"] for row in rpc(execution, "tools/list").json()["result"]["tools"]}
        assert not writes & names
        result = tool(execution, "start_workflow_definition", **args)
        assert result["isError"] and result["structuredContent"]["code"] == "permission_denied"
    assert not WorkflowDefinitionStart.objects.exists()


@pytest.mark.parametrize("change", ["unconfirmed", "stale_revision", "stale_schema", "bad_inputs"])
def test_review_failures_preserve_native_result_without_reserving(execution, change):
    args = reviewed_args(execution)
    if change == "unconfirmed":
        args["confirmed"] = False
    elif change == "stale_revision":
        args["expected_revision"] = "0" * 64
    elif change == "stale_schema":
        args["expected_input_schema_digest"] = "0" * 64
    else:
        args["inputs"] = {"unreviewed": "input"}
    result = tool(execution, "start_workflow_definition", **args)
    assert result["isError"]
    assert not result["structuredContent"]["ok"]
    assert result["structuredContent"]["errors"]
    assert result["structuredContent"]["data"] is None
    assert not WorkflowDefinitionStart.objects.exists()


def test_disabled_engine_keeps_recoverable_reserved_identity(execution):
    args = reviewed_args(execution)
    result = tool(execution, "start_workflow_definition", **args)
    assert result["isError"] and not result["structuredContent"]["ok"]
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    reserved = result["structuredContent"]["data"]
    assert reserved["request_id"] == args["request_id"]
    assert reserved["execution_id"] and reserved["temporal_workflow_id"]
    assert reserved["temporal_run_id"] is None
    recovered = data(tool(execution, "get_workflow_start", request_id=args["request_id"]))["start"]
    assert recovered["execution_id"] == reserved["execution_id"]
    repeated = tool(execution, "start_workflow_definition", **args)
    assert repeated["structuredContent"]["data"]["execution_id"] == reserved["execution_id"]
    conflict = tool(execution, "start_workflow_definition", **{**args, "inputs": {"topic": "changed"}})
    assert conflict["isError"] and conflict["structuredContent"]["data"] is None
    assert WorkflowDefinitionStart.objects.count() == 1


def test_recovery_is_scoped_to_the_original_actor(execution):
    args = reviewed_args(execution)
    reserved = tool(execution, "start_workflow_definition", **args)["structuredContent"]["data"]
    assert reserved
    other = make_user("mcp-other-actor")
    Member.objects.create(user=other, scope_kind="ORG", scope_id=execution.world.org.pk)
    bind_role(
        other,
        permissions=[Permission.WORKFLOW_READ],
        kind="ORG",
        scope_id=execution.world.org.pk,
        slug="mcp-other-reader",
    )
    minted = mint_token()
    ApiToken.objects.create(
        user=other,
        organization=execution.world.org,
        name="Other reader",
        token_hash=minted.token_hash,
        token_last_4=minted.last4,
        scopes=[SCOPE_MCP_READ],
    )
    execution.headers["HTTP_AUTHORIZATION"] = f"Bearer {minted.plaintext}"
    assert data(tool(execution, "get_workflow_start", request_id=args["request_id"]))["start"] is None
    assert WorkflowDefinitionStart.objects.count() == 1


def test_run_pagination_and_native_filters(execution):
    first = recorded_run(execution, trigger_actor_user=execution.user)
    second = recorded_run(execution)
    second.status = "failed"
    second.save(update_fields=["status"])
    page = data(tool(execution, "list_workflow_runs", limit=1))
    assert page["items"][0]["guid"] == str(second.guid)
    assert page["total_count"] == 2 and page["next_cursor"]
    tail = data(tool(execution, "list_workflow_runs", limit=1, cursor=page["next_cursor"]))
    assert [row["guid"] for row in tail["items"]] == [str(first.guid)]
    assert tail["next_cursor"] is None
    mine = data(
        tool(
            execution,
            "list_workflow_runs",
            started_by_me=True,
            project_slugs=[execution.world.medops_project.slug],
        )
    )
    assert [row["guid"] for row in mine["items"]] == [str(first.guid)]
    failed = data(
        tool(
            execution, "list_workflow_runs", statuses=["failed"], definition_slugs=[execution.definition.slug]
        )
    )
    assert [row["guid"] for row in failed["items"]] == [str(second.guid)]


def test_unavailable_engine_reports_observation_error_and_refuses_control(execution):
    run = recorded_run(execution)
    observed = data(tool(execution, "get_workflow_execution", execution_id=str(run.guid)))["execution"]
    assert observed["status"] == "running" and not observed["is_terminal"]
    assert observed["observation_error"]
    result = tool(
        execution,
        "control_workflow_execution",
        execution_id=str(run.guid),
        workflow_id=run.workflow_id,
        run_id=run.run_id,
        action="cancel",
    )
    assert result["isError"] and not result["structuredContent"]["requested"]
    run.refresh_from_db()
    assert run.status == "running"


def test_execution_abac_uses_actual_environment_region(execution):
    cluster = make_cluster(execution.world, "mcp-execution-region")
    cluster.region = "us-east-1"
    cluster.save(update_fields=["region"])
    environment = AppEnvironment.objects.create(
        registered_app=execution.world.medops_app, tenant_cluster=cluster, name="production"
    )
    run = recorded_run(execution, registered_app=execution.world.medops_app, app_environment=environment)
    Policy.objects.create(
        organization=execution.world.org,
        name="Deny east execution",
        slug="mcp-deny-east",
        scope_level="ORG",
        effect="DENY",
        action_pattern="workflow.*",
        resource_pattern={"region": ["us-east-1"]},
    )
    execution.headers["HTTP_X_ASTROLIFT_REGION"] = "us-west-2"
    for name in ("get_workflow_execution", "list_workflow_execution_stages", "control_workflow_execution"):
        args = {"execution_id": str(run.guid)}
        if name == "control_workflow_execution":
            args.update(workflow_id=run.workflow_id, run_id=run.run_id, action="cancel")
        result = tool(execution, name, **args)
        assert result["isError"] and result["structuredContent"]["code"] == "permission_denied"
    denied = tool(execution, "list_workflow_runs")
    assert denied["isError"] and denied["structuredContent"]["code"] == "permission_denied"


@pytest.mark.parametrize("target", [2, 3])
@pytest.mark.parametrize(
    "name", ["get_workflow_execution", "list_workflow_execution_stages", "control_workflow_execution"]
)
def test_exact_execution_targets_cannot_escape_project_or_organization(execution, target, name):
    run = recorded_run(execution, execution.definitions[target])
    args = {"execution_id": str(run.guid)}
    if name == "control_workflow_execution":
        args.update(workflow_id=run.workflow_id, run_id=run.run_id, action="cancel")
    result = tool(execution, name, **args)
    assert result["isError"] and result["structuredContent"]["code"] == "permission_denied"


def test_team_credential_caps_org_role_for_runs_and_exact_controls(execution):
    bind_role(
        execution.user,
        permissions=[Permission.WORKFLOW_READ, Permission.WORKFLOW_TRIGGER],
        kind="ORG",
        scope_id=execution.world.org.pk,
        slug="mcp-org-execution-role",
    )
    execution.token.team = execution.world.medops
    execution.token.save(update_fields=["team"])
    own = recorded_run(execution)
    sibling = recorded_run(execution, execution.definitions[2])
    page = data(tool(execution, "list_workflow_runs"))
    assert [row["guid"] for row in page["items"]] == [str(own.guid)]
    for name in ("get_workflow_execution", "list_workflow_execution_stages", "control_workflow_execution"):
        args = {"execution_id": str(sibling.guid)}
        if name == "control_workflow_execution":
            args.update(workflow_id=sibling.workflow_id, run_id=sibling.run_id, action="cancel")
        result = tool(execution, name, **args)
        assert result["isError"] and result["structuredContent"]["code"] == "permission_denied"


def test_org_reader_retains_historical_run_access_after_owner_retirement(execution):
    bind_role(
        execution.user,
        permissions=[Permission.WORKFLOW_READ],
        kind="ORG",
        scope_id=execution.world.org.pk,
        slug="mcp-history-reader",
    )
    run = recorded_run(execution)
    execution.world.medops_project.deleted_at = timezone.now()
    execution.world.medops_project.save(update_fields=["deleted_at"])
    observed = data(tool(execution, "get_workflow_execution", execution_id=str(run.guid)))["execution"]
    assert observed["guid"] == str(run.guid)
    execution.token.team = execution.world.medops
    execution.token.save(update_fields=["team"])
    denied = tool(execution, "get_workflow_execution", execution_id=str(run.guid))
    assert denied["isError"] and denied["structuredContent"]["code"] == "permission_denied"


def test_execution_stage_pages_are_bound_to_exact_run(execution):
    run = recorded_run(execution)
    other = recorded_run(execution)
    for attempt in range(3):
        WorkflowStageExecution.objects.create(
            workflow_run=run,
            stage=execution.stage,
            slug=f"mcp-history-{attempt}",
            status="completed",
            attempt_number=attempt + 1,
            caused_by={"edge": "review", "reason": "revise", "max_rounds": 3, "edge_round": 1},
        )
    page = data(tool(execution, "list_workflow_execution_stages", execution_id=str(run.guid), limit=1))[
        "execution"
    ]
    assert page["temporal_run_id"] == run.run_id
    cursor = page["stages"]["next_cursor"]
    assert cursor and page["stages"]["total_count"] is None
    assert page["stages"]["items"][0]["stage_kind"] == "checkpoint"
    assert page["stages"]["items"][0]["caused_by"]["reason"] == "revise"
    next_page = data(
        tool(execution, "list_workflow_execution_stages", execution_id=str(run.guid), limit=1, cursor=cursor)
    )["execution"]
    assert next_page["stages"]["items"] != page["stages"]["items"]
    for identifier in (str(other.guid),):
        result = tool(execution, "list_workflow_execution_stages", execution_id=identifier, cursor=cursor)
        assert result["isError"] and result["structuredContent"]["code"] == "invalid_arguments"


def test_stage_temporal_identity_and_pending_gate_ids_have_api_mcp_parity(execution):
    from types import SimpleNamespace

    from config.schema import schema
    from core.tenancy import TenantContext, tenant_context
    from workflows.stage_incarnations import StageIncarnation

    run = recorded_run(execution)
    execution.stage.kind = "human_gate"
    execution.stage.save()
    identity = StageIncarnation("default", run.workflow_id, run.run_id, "3")
    pk = activities._create_stage_execution_sync(
        str(run.pk), str(execution.stage.pk), 1, temporal_identity=identity
    )
    row = WorkflowStageExecution.objects.get(pk=pk)
    observed = data(tool(execution, "list_workflow_execution_stages", execution_id=str(run.guid)))[
        "execution"
    ]
    assert observed["stages"]["items"][0]["temporal_execution"] == {
        "namespace": "default",
        "workflow_id": run.workflow_id,
        "run_id": run.run_id,
    }
    with tenant_context(TenantContext(actor_user_id=execution.user.pk, organization_id=run.organization_id)):
        result = schema.execute_sync(
            "query($id:ID!){workflowExecutionStages(executionId:$id){stages{items{guid temporalExecution{namespace workflowId runId}}}} pendingHumanGates{executionGuid stageGuid temporalExecution{namespace workflowId runId}}}",
            variable_values={"id": str(run.guid)},
            context_value=SimpleNamespace(user=execution.user),
        )
    assert result.errors is None
    stage = result.data["workflowExecutionStages"]["stages"]["items"][0]
    gate = result.data["pendingHumanGates"][0]
    assert stage["guid"] == gate["executionGuid"] == str(row.guid)
    assert gate["stageGuid"] == str(execution.stage.guid)
    assert (
        stage["temporalExecution"]
        == gate["temporalExecution"]
        == {"namespace": "default", "workflowId": run.workflow_id, "runId": run.run_id}
    )


@pytest.mark.parametrize("change", ["revoke", "foreign_team", "role", "owner"])
def test_recovery_and_observation_recheck_live_authority(execution, change):
    args = reviewed_args(execution)
    reserved = tool(execution, "start_workflow_definition", **args)["structuredContent"]["data"]
    if change == "revoke":
        execution.token.is_revoked = True
        execution.token.save(update_fields=["is_revoked"])
    elif change == "foreign_team":
        execution.token.team = execution.world.platform
        execution.token.save(update_fields=["team"])
    elif change == "role":
        execution.binding.deleted_at = timezone.now()
        execution.binding.save(update_fields=["deleted_at"])
    else:
        execution.definition.project = execution.world.platform_project
        execution.definition.save(update_fields=["project"])
    for name, arguments in (
        ("astrolift_get_workflow_start", {"request_id": args["request_id"]}),
        ("astrolift_get_workflow_execution", {"execution_id": reserved["execution_id"]}),
    ):
        response = rpc(execution, "tools/call", {"name": name, "arguments": arguments})
        if change == "revoke":
            assert response.status_code == 401
        else:
            assert response.json()["result"]["isError"]
    assert WorkflowDefinitionStart.objects.count() == 1


async def connect_real_engine(temporal_env, monkeypatch, settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    queue = f"mcp-execution-{uuid4()}"
    settings.TEMPORAL_TASK_QUEUE = queue

    async def real_client():
        return temporal_env.client

    monkeypatch.setattr(workflow_client, "_get_client_async", real_client)
    return temporal_worker(
        temporal_env,
        task_queue=queue,
        workflows=[WorkflowDefinitionRunWorkflow],
        activities=[
            activities.get_workflow_stages,
            activities.create_stage_execution,
            activities.update_stage_execution,
            activities.snapshot_checkpoint,
            activities.mark_workflow_run,
            activities.record_human_gate_decision,
        ],
    )


async def test_real_start_completion_and_duplicate_request_recovery(
    execution, temporal_env, monkeypatch, settings
):
    args = await sync_to_async(reviewed_args)(execution)
    async with await connect_real_engine(temporal_env, monkeypatch, settings):
        result = data(await sync_to_async(tool)(execution, "start_workflow_definition", **args))
        assert result["ok"]
        started = result["data"]
        handle = temporal_env.client.get_workflow_handle(
            started["temporal_workflow_id"], run_id=started["temporal_run_id"]
        )
        await asyncio.wait_for(handle.result(), 20)
        recovered = data(
            await sync_to_async(tool)(execution, "get_workflow_start", request_id=args["request_id"])
        )["start"]
        duplicate = data(await sync_to_async(tool)(execution, "start_workflow_definition", **args))["data"]
        assert recovered == started == duplicate
        observed = data(
            await sync_to_async(tool)(
                execution, "get_workflow_execution", execution_id=started["execution_id"]
            )
        )["execution"]
        assert observed["is_terminal"] and observed["status"] == "completed"
        assert not observed["observation_error"]
        cleanup = data(
            await sync_to_async(tool)(
                execution,
                "control_workflow_execution",
                execution_id=started["execution_id"],
                workflow_id=started["temporal_workflow_id"],
                run_id=started["temporal_run_id"],
                action="cleanup",
            )
        )
        assert cleanup["ok"] and cleanup["requested"]
        assert cleanup["execution"]["is_terminal"]
        assert await sync_to_async(WorkflowDefinitionStart.objects.count)() == 1


async def test_uncertain_response_preserves_ids_and_recovers_actual_engine_execution(
    execution, temporal_env, monkeypatch, settings
):
    args = await sync_to_async(reviewed_args)(execution)
    real_start = workflow_client.start_workflow_once
    accepted = []

    def lose_response(*a, **kw):
        accepted.append(real_start(*a, **kw))
        raise TimeoutError("injected lost response after actual Temporal acceptance")

    async with await connect_real_engine(temporal_env, monkeypatch, settings):
        with monkeypatch.context() as failure:
            failure.setattr(workflow_client, "start_workflow_once", lose_response)
            result = await sync_to_async(tool)(execution, "start_workflow_definition", **args)
        assert result["isError"] and result["structuredContent"]["data"]
        reserved = result["structuredContent"]["data"]
        assert reserved["temporal_run_id"] is None
        assert len(accepted) == 1 and accepted[0].workflow_id == reserved["temporal_workflow_id"]
        recovered = data(
            await sync_to_async(tool)(execution, "get_workflow_start", request_id=args["request_id"])
        )["start"]
        assert recovered["execution_id"] == reserved["execution_id"]
        assert recovered["temporal_run_id"] == accepted[0].run_id
        repeated = data(await sync_to_async(tool)(execution, "start_workflow_definition", **args))["data"]
        assert repeated == recovered
        handle = temporal_env.client.get_workflow_handle(
            recovered["temporal_workflow_id"], run_id=recovered["temporal_run_id"]
        )
        await asyncio.wait_for(handle.result(), 20)
        assert await sync_to_async(WorkflowDefinitionStart.objects.count)() == 1


@pytest.mark.parametrize("action", ["cancel", "terminate"])
async def test_real_exact_control_does_not_approve_pending_human_gate(
    execution, temporal_env, monkeypatch, settings, action
):
    def human_gate():
        execution.stage.kind = "human_gate"
        execution.stage.timeout_seconds = 3600
        execution.stage.save()

    await sync_to_async(human_gate)()
    args = await sync_to_async(reviewed_args)(execution)
    async with await connect_real_engine(temporal_env, monkeypatch, settings):
        started = data(await sync_to_async(tool)(execution, "start_workflow_definition", **args))["data"]
        handle = temporal_env.client.get_workflow_handle(
            started["temporal_workflow_id"], run_id=started["temporal_run_id"]
        )
        try:
            for _ in range(100):
                gates = await sync_to_async(
                    lambda: WorkflowStageExecution.objects.filter(
                        stage=execution.stage, status="running"
                    ).exists()
                )()
                if gates:
                    break
                await asyncio.sleep(0.05)
            assert gates
            history = data(
                await sync_to_async(tool)(
                    execution,
                    "list_workflow_execution_stages",
                    execution_id=started["execution_id"],
                )
            )["execution"]
            assert history["stages"]["items"][0]["human_gate_state"] == "pending"
            controls = {
                "execution_id": started["execution_id"],
                "workflow_id": started["temporal_workflow_id"],
                "run_id": started["temporal_run_id"],
                "action": action,
            }
            if action == "terminate":
                controls["reason"] = "Disposable MCP integration verification"
            wrong = await sync_to_async(tool)(
                execution, "control_workflow_execution", **{**controls, "run_id": str(uuid4())}
            )
            assert wrong["isError"] and not wrong["structuredContent"]["ok"]
            assert (await handle.describe()).status.name == "RUNNING"
            controlled = data(await sync_to_async(tool)(execution, "control_workflow_execution", **controls))
            assert controlled["ok"] and controlled["requested"]
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(handle.result(), 20)
            assert (await handle.describe()).status.name == (
                "CANCELED" if action == "cancel" else "TERMINATED"
            )
            assert not await sync_to_async(
                lambda: WorkflowStageExecution.objects.filter(
                    stage=execution.stage, output__human_gate__decision="approved"
                ).exists()
            )()
        finally:
            if (await handle.describe()).status.name == "RUNNING":
                await handle.terminate(reason="MCP test cleanup")
