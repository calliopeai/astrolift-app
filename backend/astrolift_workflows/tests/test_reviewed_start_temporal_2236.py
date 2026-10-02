from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from temporalio import workflow

from astrolift_identity.models import Organization
from astrolift_workflows import client
from astrolift_workflows.activities import workflow_stage_activities as activities
from astrolift_workflows.workflows.workflow_definition_run import WorkflowDefinitionRunWorkflow
from core.permissions import Permission
from core.run_input_contract import digest
from core.tenancy import TenantContext, tenant_context
from core.testing.temporal import temporal_worker
from workflows.models import (
    WorkflowDefinition,
    WorkflowDefinitionStart,
    WorkflowInstance,
    WorkflowStage,
    WorkflowStageExecution,
)
from workflows.reviewed_starts import ReviewedStartError, definition_revision, dispatch_start, reserve_start

pytestmark = pytest.mark.django_db(transaction=True)


@workflow.defn(name="ReviewedImmediate", sandboxed=False)
class Immediate:
    @workflow.run
    async def run(self, value: int):
        return value


@pytest.fixture(autouse=True)
def local_cache(settings, monkeypatch):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    monkeypatch.setattr("constance.settings.DATABASE_CACHE_BACKEND", None)


@pytest.fixture
def world(permission_resolver):
    org = Organization.objects.create(name="Temporal reviewed starts", slug="temporal-reviewed-starts")
    user = get_user_model().objects.create_user(username="temporal-reviewed-actor")
    definition = WorkflowDefinition.objects.create(
        name="Disposable reviewed workflow", slug="disposable-reviewed", organization=org, model_label=""
    )
    WorkflowStage.objects.create(
        definition=definition, slug="disposable-review-stage", order=0, kind="checkpoint"
    )
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    return SimpleNamespace(
        org=org,
        user=user,
        definition=definition,
        tenant=TenantContext(organization_id=org.pk, actor_user_id=user.pk),
    )


def reserve(world):
    with tenant_context(world.tenant):
        return reserve_start(
            definition_id=world.definition.guid,
            expected_revision=definition_revision(world.definition),
            expected_input_schema_digest=digest(world.definition.input_schema),
            request_id="stable-reviewed-request",
            inputs={},
            user=world.user,
        )


def dispatch(world, row):
    with tenant_context(world.tenant):
        return dispatch_start(row)


async def setup_client(temporal_env, monkeypatch):
    async def get_client():
        return temporal_env.client

    monkeypatch.setattr(client, "_get_client_async", get_client)
    monkeypatch.setattr(client, "_temporal_enabled", lambda: True)
    monkeypatch.setattr(client, "_task_queue", lambda: "astrolift-test")


@pytest.mark.parametrize("outcome", ["completed", "failed"])
async def test_real_executor_disposable_acceptance_and_duplicate_recovery(
    world, temporal_env, monkeypatch, outcome, permission_resolver
):
    await setup_client(temporal_env, monkeypatch)
    if outcome == "failed":

        def gate():
            stage = world.definition.stages.get()
            stage.kind = "human_gate"
            stage.timeout_seconds = 1
            stage.save()

        await sync_to_async(gate)()
    row = await sync_to_async(reserve)(world)
    registered = [
        activities.get_workflow_stages,
        activities.create_stage_execution,
        activities.update_stage_execution,
        activities.snapshot_checkpoint,
        activities.mark_workflow_run,
        activities.record_human_gate_decision,
    ]
    async with temporal_worker(
        temporal_env, workflows=[WorkflowDefinitionRunWorkflow], activities=registered
    ):
        started = await sync_to_async(dispatch)(world, row)
        assert started.dispatch_status == "submitted"
        assert started.execution.run_id
        handle = temporal_env.client.get_workflow_handle(
            started.execution.workflow_id, run_id=started.execution.run_id
        )
        if outcome == "failed":
            for _ in range(100):
                execution = await sync_to_async(
                    lambda: WorkflowStageExecution.objects.filter(
                        workflow_run_id=started.execution_id, status="running"
                    ).first()
                )()
                if execution is not None:
                    break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError("The disposable human gate was not opened")
            await handle.signal(
                "human_gate_decision",
                {
                    "execution_id": str(execution.pk),
                    "decision": "rejected",
                    "decided_by_user_id": world.user.pk,
                    "note": "Disposable acceptance rejection",
                },
            )
        result = await asyncio.wait_for(handle.result(), 15)
        assert result["ok"] == (outcome == "completed")
        repeated = await sync_to_async(dispatch)(world, row)
        assert repeated.execution.run_id == started.execution.run_id
        assert (await handle.describe()).status.name == "COMPLETED"

        def proof():
            repeated.execution.refresh_from_db()
            assert repeated.execution.status == outcome
            instance = WorkflowInstance.objects.get(temporal_workflow_id=handle.id)
            assert instance.current_state == outcome
            assert WorkflowDefinitionStart.objects.count() == 1

        await sync_to_async(proof)()
        # A browser reload has only request metadata. Stored arguments recover
        # the original closed engine execution even after a definition rename.
        permission_resolver.grant(Permission.WORKFLOW_READ)

        def erase_response():
            started.execution.run_id = ""
            started.execution.save()
            started.dispatch_status = "uncertain"
            started.save()
            world.definition.slug = "renamed-after-response"
            world.definition.save()

        await sync_to_async(erase_response)()
        from workflows.reviewed_starts import recover_start

        def read_recovery():
            with tenant_context(world.tenant):
                return recover_start(started)

        recovered = await sync_to_async(read_recovery)()
        assert recovered.execution.run_id == handle.run_id


async def test_start_once_recovers_lost_response_and_never_repeats_closed_execution(
    temporal_env, monkeypatch
):
    await setup_client(temporal_env, monkeypatch)
    async with temporal_worker(temporal_env, workflows=[Immediate]):
        identity = f"reviewed-lost-{uuid.uuid4()}"
        original = await temporal_env.client.start_workflow(
            Immediate.run, 17, id=identity, task_queue="astrolift-test"
        )
        assert await original.result() == 17
        recovered = await sync_to_async(client.start_workflow_once)(
            "ReviewedImmediate", [17], workflow_id=identity
        )
        assert recovered.run_id == original.first_execution_run_id
        with pytest.raises(RuntimeError, match="inputs differ"):
            await sync_to_async(client.start_workflow_once)("ReviewedImmediate", [18], workflow_id=identity)
        assert (await original.describe()).status.name == "COMPLETED"


async def test_disabled_pending_start_does_not_dispatch(world, temporal_env, monkeypatch):
    await setup_client(temporal_env, monkeypatch)
    row = await sync_to_async(reserve)(world)

    def disable():
        world.definition.is_enabled = False
        world.definition.save()

    await sync_to_async(disable)()
    with pytest.raises(ReviewedStartError, match="changed before submission"):
        await sync_to_async(dispatch)(world, row)
    from temporalio.service import RPCError, RPCStatusCode

    with pytest.raises(RPCError) as missing:
        await temporal_env.client.get_workflow_handle(row.execution.workflow_id).describe()
    assert missing.value.status == RPCStatusCode.NOT_FOUND
