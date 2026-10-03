"""The public activity contract operates on a real, immutable Temporal run."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from temporalio import workflow
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.common import WorkflowIDReusePolicy
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from astrolift_identity.models import Organization
from astrolift_workflows import client as platform_client
from config.schema import schema
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import bind_role, make_user
from workflows.models import WorkflowInstance


@workflow.defn
class ActivityViewerExactExecution:
    @workflow.run
    async def run(self, hold: bool) -> str:
        if hold:
            await workflow.wait_condition(lambda: False)
        return "finished"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("operation", ["cancel", "terminate"])
async def test_reviewed_run_cannot_operate_on_replacement(temporal_env, settings, monkeypatch, operation):
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    monkeypatch.setattr(platform_client, "_client", temporal_env.client)
    suffix = str(uuid4())
    queue = f"activity-exact-{suffix}"

    def records():
        org = Organization.objects.create(name="Activity", slug=f"activity-{suffix}")
        user = make_user(suffix)
        reader = make_user(f"reader-{suffix}")
        for actor, permissions in [
            (user, [Permission.AUDIT_LOG_READ, Permission.WORKFLOW_TRIGGER]),
            (reader, [Permission.AUDIT_LOG_READ]),
        ]:
            bind_role(actor, permissions=permissions, kind="ORG", scope_id=org.pk, slug=str(uuid4()))
        WorkflowInstance.objects.create(organization=org, temporal_workflow_id=queue, current_state="running")
        other_org = Organization.objects.create(name="Other Activity", slug=f"other-activity-{suffix}")
        other_user = make_user(f"other-{suffix}")
        bind_role(
            other_user,
            permissions=[Permission.AUDIT_LOG_READ, Permission.WORKFLOW_TRIGGER],
            kind="ORG",
            scope_id=other_org.pk,
            slug=str(uuid4()),
        )
        return org, user, reader, other_org, other_user

    org, user, reader, other_org, other_user = await sync_to_async(records)()

    def execute(document, run_id, actor=user, tenant_org=org):
        context = SimpleNamespace(user=actor, request=SimpleNamespace(user=actor))
        with tenant_context(TenantContext(organization_id=tenant_org.pk, actor_user_id=actor.pk)):
            return schema.execute_sync(
                document, variable_values={"id": queue, "run": run_id}, context_value=context
            )

    read = """query($id:String!,$run:String!){astroliftWorkflowInstanceDetail(workflowId:$id,runId:$run){instance{runId status} history{eventType}}}"""
    name = f"{operation}WorkflowInstance"
    reason = ',reason:"reviewed execution"' if operation == "terminate" else ""
    mutation = f"mutation($id:String!,$run:String!){{{name}(workflowId:$id,runId:$run{reason}){{ok errors{{field messages}}}}}}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[ActivityViewerExactExecution],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        first = await temporal_env.client.start_workflow(
            ActivityViewerExactExecution.run, False, id=queue, task_queue=queue
        )
        assert await first.result() == "finished"
        second = await temporal_env.client.start_workflow(
            ActivityViewerExactExecution.run,
            True,
            id=queue,
            task_queue=queue,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        )
        first_id, second_id = first.result_run_id, second.result_run_id
        assert first_id != second_id
        try:
            historical = await sync_to_async(execute)(read, first_id)
            assert historical.errors is None
            assert historical.data["astroliftWorkflowInstanceDetail"]["instance"] == {
                "runId": first_id,
                "status": "COMPLETED",
            }
            current = await sync_to_async(execute)(read, second_id)
            assert current.data["astroliftWorkflowInstanceDetail"]["instance"] == {
                "runId": second_id,
                "status": "RUNNING",
            }
            assert len(historical.data["astroliftWorkflowInstanceDetail"]["history"]) > 0
            completed_event = str(EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED)
            assert completed_event in {
                event["eventType"] for event in historical.data["astroliftWorkflowInstanceDetail"]["history"]
            }
            assert completed_event not in {
                event["eventType"] for event in current.data["astroliftWorkflowInstanceDetail"]["history"]
            }
            foreign_read = await sync_to_async(execute)(read, second_id, other_user, other_org)
            assert foreign_read.errors is None
            assert foreign_read.data["astroliftWorkflowInstanceDetail"] is None
            foreign_write = await sync_to_async(execute)(mutation, second_id, other_user, other_org)
            assert foreign_write.errors is None
            assert foreign_write.data[name]["ok"] is False
            unknown = str(uuid4())
            missing = await sync_to_async(execute)(read, unknown)
            assert missing.errors is None
            assert missing.data["astroliftWorkflowInstanceDetail"] is None
            rejected = await sync_to_async(execute)(mutation, unknown)
            assert rejected.errors is None
            assert rejected.data[name]["ok"] is False
            await sync_to_async(execute)(mutation, first_id)
            assert (await second.describe()).status == WorkflowExecutionStatus.RUNNING
            denied = await sync_to_async(execute)(mutation, second_id, reader)
            assert denied.errors
            assert (await second.describe()).status == WorkflowExecutionStatus.RUNNING
            blank = await sync_to_async(execute)(mutation, "")
            assert blank.errors is None
            assert blank.data[name]["ok"] is False
            assert (await second.describe()).status == WorkflowExecutionStatus.RUNNING
            accepted = await sync_to_async(execute)(mutation, second_id)
            assert accepted.errors is None
            assert accepted.data[name]["ok"] is True
            with pytest.raises(WorkflowFailureError):
                await second.result(rpc_timeout=timedelta(seconds=10))
            assert (await second.describe()).status == (
                WorkflowExecutionStatus.CANCELED
                if operation == "cancel"
                else WorkflowExecutionStatus.TERMINATED
            )
        finally:
            if (await second.describe()).status == WorkflowExecutionStatus.RUNNING:
                await second.terminate()
