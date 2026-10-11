"""Actual public enqueue and trusted Temporal runtime binding, with PG enforcement."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from time import monotonic, sleep
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.db import DatabaseError, transaction
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError
from temporalio.worker import Replayer, Worker

from astrolift_lifecycle.deployment_execution_receipt import normalized_input_sha256
from astrolift_lifecycle.deployment_identity_origin import deployment_origin, original_actor
from astrolift_lifecycle.models import Deployment, DeploymentExecutionReceipt
from astrolift_lifecycle.tests.test_native_deployment_origin_2278 import start_public
from astrolift_lifecycle.tests.test_native_deployment_origin_2278 import world as public_world
from astrolift_workflows.activities.deployment_identity_origin import (
    mark_deployment_identity_refused,
    validate_deployment_identity_origin,
)
from astrolift_workflows.inputs import DeployAppInput, WorkflowResult
from astrolift_workflows.workflows.deploy_app import DeployAppWorkflow

pytestmark = pytest.mark.django_db(transaction=True)


@workflow.defn(name="DeployAppWorkflow")
class RepeatedGate:
    @workflow.run
    async def run(self, input: DeployAppInput) -> dict:
        try:
            return await workflow.execute_activity(
                "repeat_original_gate",
                input,
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
        except ActivityError:
            return {"refused": True}


@pytest.fixture
def world(monkeypatch):
    return public_world.__wrapped__(monkeypatch)


def test_http_intent_committed_before_sdk_without_payload_storage(world, client, monkeypatch):
    from astrolift_lifecycle.schema.mutations import helpers

    original = helpers.start_workflow

    def start(name, args, *, workflow_id, **kwargs):
        assert transaction.get_autocommit()
        with ThreadPoolExecutor(max_workers=1) as pool:

            def committed():
                from django.db import close_old_connections

                close_old_connections()
                try:
                    return DeploymentExecutionReceipt.objects.get(
                        deployment_id=args[0].deployment_id, kind="EXPECTED"
                    ).input_sha256
                finally:
                    close_old_connections()

            assert pool.submit(committed).result(timeout=5) == normalized_input_sha256(args[0])
        return original(name, args, workflow_id=workflow_id, **kwargs)

    monkeypatch.setattr(helpers, "start_workflow", start)
    result = start_public(world, client)
    assert result["ok"], result["errors"]
    row = DeploymentExecutionReceipt.objects.get(deployment__guid=result["data"]["id"])
    assert row.kind == "EXPECTED" and not row.run_id and row.expected_id is None
    assert not hasattr(row, "input_payload")
    assert world.headers["HTTP_AUTHORIZATION"] not in str(row.__dict__)


@pytest.mark.parametrize("operation", ["update", "delete", "save", "retire"])
def test_append_only_history_refuses_mutation(world, client, operation):
    assert start_public(world, client)["ok"]
    row = DeploymentExecutionReceipt.objects.get()
    if operation in ("save", "retire"):
        with pytest.raises(ValueError, match="DEPLOYMENT_EXECUTION_IMMUTABLE"):
            row.save() if operation == "save" else row.soft_delete()
    else:
        with pytest.raises(DatabaseError, match="DEPLOYMENT_EXECUTION_IMMUTABLE"):
            with transaction.atomic():
                query = DeploymentExecutionReceipt.all_objects.filter(pk=row.pk)
                query.update(run_id=str(uuid4())) if operation == "update" else query._raw_delete(query.db)
    row.refresh_from_db()
    assert row.run_id == "" and row.deleted_at is None


def test_retained_history_refuses_reverse_before_trigger_removed(world, client):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    assert start_public(world, client)["ok"]
    with pytest.raises(RuntimeError, match="DEPLOYMENT_EXECUTION_ROLLBACK_REQUIRES_EMPTY_HISTORY"):
        MigrationExecutor(connection).migrate([("astrolift_lifecycle", "0046_deploymentidentityorigin")])
    assert DeploymentExecutionReceipt.objects.count() == 1
    with pytest.raises(DatabaseError, match="DEPLOYMENT_EXECUTION_IMMUTABLE"):
        with transaction.atomic():
            DeploymentExecutionReceipt.all_objects.update(run_id="must-refuse")


def test_empty_real_migration_roundtrip_restores_full_targets():
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    original = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([("astrolift_lifecycle", "0046_deploymentidentityorigin")])
        MigrationExecutor(connection).migrate([("astrolift_lifecycle", "0047_deployment_execution_receipts")])
        assert not DeploymentExecutionReceipt.all_objects.exists()
    finally:
        MigrationExecutor(connection).migrate(original)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "image", "actor", "commit", "workflow_id", "withdrawn"])
async def test_actual_worker_binds_exact_intent_only(world, client, temporal_env, change):
    await sync_to_async(start_public)(world, client)
    original = world.starts[0][1]
    expected = await sync_to_async(DeploymentExecutionReceipt.objects.get)(
        deployment_id=original.deployment_id, kind="EXPECTED"
    )
    payload = original
    if change == "image":
        payload = replace(original, image_tags={"web": "unreviewed"})
    elif change == "actor":
        payload = replace(original, actor=replace(original.actor, display="changed"))
    elif change == "commit":
        payload = replace(original, commit_sha="f" * 40)
    elif change == "withdrawn":
        await sync_to_async(
            lambda: type(world.token).objects.filter(pk=world.token.pk).update(is_revoked=True)
        )()
    queue = "execution-receipts-" + uuid4().hex
    workflow_id = expected.workflow_id if change != "workflow_id" else queue
    with ThreadPoolExecutor(max_workers=2) as executor:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[DeployAppWorkflow],
            activities=[validate_deployment_identity_origin, mark_deployment_identity_refused],
            activity_executor=executor,
        ):
            handle = await temporal_env.client.start_workflow(
                DeployAppWorkflow.run,
                payload,
                id=workflow_id,
                task_queue=queue,
            )
            result = await handle.result()
            history = await handle.fetch_history()
    assert not result.ok
    assert result.message == (
        "NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED"
        if change == "none"
        else "DEPLOYMENT_ORIGIN_REFUSED"
        if change == "withdrawn"
        else "DEPLOYMENT_EXECUTION_REFUSED"
    )
    rows = await sync_to_async(list)(
        DeploymentExecutionReceipt.objects.filter(deployment_id=original.deployment_id, kind="BOUND")
    )
    assert len(rows) == (1 if change == "none" else 0)
    if rows:
        assert rows[0].run_id == handle.first_execution_run_id
        assert rows[0].expected_id == expected.pk and rows[0].operation_uuid == expected.operation_uuid
    assert world.headers["HTTP_AUTHORIZATION"] not in history.to_json()
    assert [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ] == ["validate_deployment_identity_origin", "mark_deployment_identity_refused"]
    row = await sync_to_async(Deployment.objects.get)(pk=original.deployment_id)
    assert row.status == ("failed" if change in ("none", "withdrawn") else "pending")
    await Replayer(workflows=[DeployAppWorkflow]).replay_workflow(history)


@pytest.mark.asyncio
async def test_first_activity_before_mirror_and_new_run_refusal(world, client, temporal_env, monkeypatch):
    from astrolift_lifecycle.schema.mutations import helpers
    from astrolift_workflows import client as native_client

    queue = "execution-mirror-race-" + uuid4().hex

    async def current_client():
        return temporal_env.client

    monkeypatch.setattr(native_client, "_get_client_async", current_client)
    monkeypatch.setattr(native_client, "_temporal_enabled", lambda: True)
    monkeypatch.setattr(native_client, "_task_queue", lambda: queue)
    monkeypatch.setattr(helpers, "start_workflow", native_client.start_workflow)
    original_record = helpers._record_workflow_run
    observed = []

    def delayed_record(**kwargs):
        limit = monotonic() + 10
        while monotonic() < limit:
            receipt = DeploymentExecutionReceipt.objects.filter(
                kind="BOUND", workflow_id=kwargs["workflow_id"]
            ).first()
            if receipt:
                assert Deployment.objects.get(pk=receipt.deployment_id).workflow_run_id is None
                observed.append(receipt.run_id)
                return original_record(**kwargs)
            sleep(0.02)
        pytest.fail("first activity did not bind independently of mirror")

    monkeypatch.setattr(helpers, "_record_workflow_run", delayed_record)
    with ThreadPoolExecutor(max_workers=2) as executor:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[DeployAppWorkflow],
            activities=[validate_deployment_identity_origin, mark_deployment_identity_refused],
            activity_executor=executor,
        ):
            result = await sync_to_async(start_public)(world, client)
            assert result["ok"], result["errors"]
            row = await sync_to_async(Deployment.objects.select_related("workflow_run").get)(
                guid=result["data"]["id"]
            )
            first = temporal_env.client.get_workflow_handle(
                row.workflow_run.workflow_id,
                run_id=row.workflow_run.run_id,
                result_type=WorkflowResult,
            )
            assert (await first.result()).message == "NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED"
            await Replayer(workflows=[DeployAppWorkflow]).replay_workflow(await first.fetch_history())
            assert observed == [row.workflow_run.run_id]

            def payload():
                ref = deployment_origin(row)
                return DeployAppInput(
                    registered_app_id=row.registered_app_id,
                    app_environment_id=row.app_environment_id,
                    deployment_id=row.pk,
                    image_tags={"web": "origin-v1"},
                    trigger_kind="manual",
                    actor=original_actor(ref),
                    identity_authority=ref,
                )

            second = await temporal_env.client.start_workflow(
                DeployAppWorkflow.run,
                await sync_to_async(payload)(),
                id=row.workflow_run.workflow_id,
                task_queue=queue,
            )
            assert (await second.result()).message == "DEPLOYMENT_EXECUTION_REFUSED"
    bound = await sync_to_async(DeploymentExecutionReceipt.objects.get)(deployment=row, kind="BOUND")
    assert bound.run_id == row.workflow_run.run_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "withdraw",
    [
        "none",
        "token",
        "failed",
        "superseded",
        "rolled_back",
        "pending_approval",
        "running",
        "redeploying",
        "deploying",
    ],
)
async def test_actual_same_run_retry_reuses_receipt_and_rechecks_origin(
    world,
    client,
    temporal_env,
    withdraw,
):
    from temporalio import activity
    from temporalio.exceptions import ApplicationError
    from temporalio.worker import UnsandboxedWorkflowRunner

    from astrolift_lifecycle.deployment_execution_receipt import admitted_deployment_execution

    observed = []

    @activity.defn(name="repeat_original_gate")
    def repeat_original_gate(input: DeployAppInput) -> dict:
        try:
            validate_deployment_identity_origin(input)
        except ApplicationError as exc:
            if exc.message != "NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED":
                raise
        with admitted_deployment_execution(input) as execution:
            observed.append((execution.receipt_guid, execution.run_id, activity.info().attempt))
        if activity.info().attempt == 1:
            if withdraw == "token":
                type(world.token).objects.filter(pk=world.token.pk).update(is_revoked=True)
            elif withdraw != "none":
                Deployment.objects.filter(pk=input.deployment_id).update(status=withdraw)
            raise ApplicationError("CONTROLLED_LOST_ACTIVITY_REPLY")
        return {"receipt_guid": execution.receipt_guid, "run_id": execution.run_id}

    await sync_to_async(start_public)(world, client)
    payload = world.starts[0][1]
    expected = await sync_to_async(DeploymentExecutionReceipt.objects.get)(
        deployment_id=payload.deployment_id, kind="EXPECTED"
    )
    queue = "execution-repeat-" + uuid4().hex
    with ThreadPoolExecutor(max_workers=2) as executor:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[RepeatedGate],
            activities=[repeat_original_gate],
            activity_executor=executor,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await temporal_env.client.start_workflow(
                RepeatedGate.run,
                payload,
                id=expected.workflow_id,
                task_queue=queue,
            )
            result = await handle.result()
            history = await handle.fetch_history()
    bound = await sync_to_async(DeploymentExecutionReceipt.objects.get)(
        deployment_id=payload.deployment_id, kind="BOUND"
    )
    assert bound.run_id == handle.first_execution_run_id
    refused = withdraw not in ("none", "deploying")
    assert [item[2] for item in observed] == ([1] if refused else [1, 2])
    assert all(item[:2] == (str(bound.guid), bound.run_id) for item in observed)
    assert result == (
        {"refused": True}
        if refused
        else {
            "receipt_guid": str(bound.guid),
            "run_id": bound.run_id,
        }
    )
    assert world.headers["HTTP_AUTHORIZATION"] not in history.to_json()
    await Replayer(workflows=[RepeatedGate], workflow_runner=UnsandboxedWorkflowRunner()).replay_workflow(
        history
    )


@pytest.mark.asyncio
async def test_actual_sdk_enqueue_reply_loss_retains_intent_and_original_bound_run(
    world,
    client,
    temporal_env,
    monkeypatch,
):
    from astrolift_lifecycle.schema.mutations import helpers
    from astrolift_workflows import client as native_client

    queue = "execution-enqueue-loss-" + uuid4().hex
    acknowledged = []

    async def current_client():
        return temporal_env.client

    monkeypatch.setattr(native_client, "_get_client_async", current_client)
    monkeypatch.setattr(native_client, "_temporal_enabled", lambda: True)
    monkeypatch.setattr(native_client, "_task_queue", lambda: queue)

    def lost_reply(*args, **kwargs):
        handle = native_client.start_workflow(*args, **kwargs)
        acknowledged.append(handle)
        raise RuntimeError("CONTROLLED_ENQUEUE_REPLY_LOSS")

    monkeypatch.setattr(helpers, "start_workflow", lost_reply)
    with ThreadPoolExecutor(max_workers=2) as executor:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[DeployAppWorkflow],
            activities=[validate_deployment_identity_origin, mark_deployment_identity_refused],
            activity_executor=executor,
        ):
            result = await sync_to_async(start_public)(world, client)
            assert not result["ok"]
            handle = temporal_env.client.get_workflow_handle(
                acknowledged[0].workflow_id,
                run_id=acknowledged[0].run_id,
                result_type=WorkflowResult,
            )
            outcome = await handle.result()
            history = await handle.fetch_history()
    assert outcome.message == "NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED"
    expected = await sync_to_async(DeploymentExecutionReceipt.objects.get)(kind="EXPECTED")
    bound = await sync_to_async(DeploymentExecutionReceipt.objects.get)(kind="BOUND")
    row = await sync_to_async(Deployment.objects.get)(pk=expected.deployment_id)
    assert bound.expected_id == expected.pk and bound.run_id == acknowledged[0].run_id
    assert row.workflow_run_id is None
    assert row.status == "failed" and row.config_snapshot == {}
    assert world.headers["HTTP_AUTHORIZATION"] not in history.to_json()
    await Replayer(workflows=[DeployAppWorkflow]).replay_workflow(history)
