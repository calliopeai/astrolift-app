from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone
from nvke.client import NvkeError
from temporalio import activity, workflow
from temporalio.api.common.v1 import WorkflowExecution
from temporalio.api.enums.v1 import EventType, ResetReapplyType, WorkflowTaskFailedCause
from temporalio.api.workflowservice.v1 import ResetWorkflowExecutionRequest
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from astrolift_clusters import nvke_native_reset as reset
from astrolift_clusters import nvke_native_reset_review as review
from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_runtime as runtime
from astrolift_clusters.tests import test_nvke_access as support
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_native_history import (
    HistoryWorkflow,
    prepare_activity,
    private_activity,
)
from astrolift_clusters.tests.test_nvke_operations import configured as _configured
from astrolift_clusters.tests.test_nvke_operations import world as _world
from astrolift_clusters.tests.test_nvke_schema import execute
from astrolift_clusters.tests.test_nvke_sites import context, put
from astrolift_clusters.tests.test_nvke_temporal import configure
from core.schema.audit import MutationAuditLog
from core.testing.temporal import temporal_test_env

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    return _world.__wrapped__()


@pytest.fixture
def configured(world):
    return _configured.__wrapped__(world)


@contextmanager
def cluster_source(state, address, namespace, queue, owner):
    authority = configure(
        state.world, state.site, address=address, namespace=namespace, task_queue=queue, use_tls=False
    )
    cluster_id = str(uuid4())
    details = {
        "parent_override": {
            "conditions": {
                "updateInProgress": {"type": "etcd_key_rotation"},
                "etcdKeyRotation": {"workflowID": owner, "state": "failed", "phase": "rewrite"},
            }
        }
    }
    with support.controller({"body": {"name": "reset-cluster"}}, cluster_id, details) as (
        endpoint,
        _,
        _calls,
    ):
        with context(state.world):
            state.connection = put(
                state.site,
                expected_connection_version=state.connection.version,
                endpoint=endpoint,
                allow_http=True,
            )
            state.site.refresh_from_db()
        source = support.reserve(
            state, operation_key="getCluster", parameters={"path": {"clusterID": cluster_id}}
        )
        assert (
            runtime.attempt(
                source.pk,
                0,
                execution=(
                    source.workflow_id,
                    source.workflow_run_id or "reset-source-read",
                    "NvkeOperationWorkflow",
                ),
            )
            == "succeeded"
        )
        source.refresh_from_db()
        yield authority, source, cluster_id, details


REWRITE_CALLS: dict[str, int] = {}
PHASE_OVERRIDE: dict[str, str] = {}


@activity.defn(name="StartEtcdEncryptionRewrite")
async def rewrite_activity(parameters: dict) -> dict:
    identity = parameters["NodeID"]
    REWRITE_CALLS[identity] = REWRITE_CALLS.get(identity, 0) + 1
    if REWRITE_CALLS[identity] == 1:
        raise ApplicationError("PRIVATE_REWRITE_FAILURE", non_retryable=True)
    return {"OperationID": "PRIVATE_REWRITE_RESULT", "Phase": "running"}


@activity.defn(name="BeginEtcdEncryptionKeyRotation")
async def begin_activity(parameters: dict) -> None:
    return None


@activity.defn(name="RecordEtcdKeyRotationPhase")
async def record_phase(parameters: dict) -> None:
    return None


@workflow.defn(name="RotateEtcdEncryptionKey")
class FailedRotation:
    @workflow.run
    async def run(self, parameters: dict) -> None:
        await workflow.execute_activity(
            begin_activity,
            {"ClusterID": parameters["clusterID"], "WorkflowID": workflow.info().workflow_id},
            start_to_close_timeout=timedelta(seconds=10),
        )
        await workflow.execute_activity(
            prepare_activity,
            {"clusterID": parameters["clusterID"]},
            start_to_close_timeout=timedelta(seconds=10),
        )
        await workflow.execute_activity(
            record_phase,
            {
                "ClusterID": parameters["clusterID"],
                "WorkflowID": workflow.info().workflow_id,
                "Phase": PHASE_OVERRIDE.get(parameters["clusterID"], "rewrite"),
            },
            start_to_close_timeout=timedelta(seconds=10),
        )
        await workflow.execute_activity(
            rewrite_activity,
            {"NodeID": parameters["clusterID"], "OperationID": "PRIVATE_REQUESTED_REWRITE"},
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


def reserve(state, authority, source, owner, run_id, scheduled, **changes):
    values = {
        "site_id": str(state.site.guid),
        "expected_site_version": state.site.version,
        "expected_actor_id": state.world.user.pk,
        "expected_authority_version": authority.version,
        "source_operation_id": str(source.guid),
        "expected_source_version": source.version,
        "request_id": str(uuid4()),
        "workflow": "RotateEtcdEncryptionKey",
        "workflow_id": owner,
        "base_run_id": run_id,
        "workflow_task_event_id": str(
            scheduled.activity_task_scheduled_event_attributes.workflow_task_completed_event_id
        ),
        "activity_event_id": str(scheduled.event_id),
        "activity_type": "StartEtcdEncryptionRewrite",
        "phase": "rewrite",
        "mode": "restart_rewrite",
        "rationale": "PRIVATE_RESET_RATIONALE",
        "quiescence_evidence": "PRIVATE_NODE_EVIDENCE",
        "acknowledge_boundary_reviewed": True,
        "acknowledge_conflicting_work_stopped": True,
    }
    values.update(changes)
    with context(state.world):
        return reset.reserve(**values)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "lost_ack",
    [
        False,
        True,
        "crash_before_rpc",
        "live_phase_changed",
        "completed_owner",
        "first_task",
        "astro_worker",
        "history_phase_mismatch",
    ],
)
async def test_real_native_reset_never_replays_after_ack_loss_or_completion(
    configured, settings, monkeypatch, lost_ack
):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    async with temporal_test_env() as env:
        queue, owner = "reset-runtime-" + uuid4().hex, "reset-owner-" + uuid4().hex
        source_context = cluster_source(
            configured, env.client.service_client.config.target_host, env.client.namespace, queue, owner
        )
        authority, source, cluster_id, details = await sync_to_async(source_context.__enter__)()
        if lost_ack == "history_phase_mismatch":
            PHASE_OVERRIDE[cluster_id] = "promote_primary"
        calls = []
        original = reset.send_reset

        async def observed_reset(*args):
            calls.append(args[3])
            result = await original(*args)
            if lost_ack is True:
                raise RuntimeError("PRIVATE_LOST_RESET_ACK")
            return result

        monkeypatch.setattr(reset, "send_reset", observed_reset)
        try:
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[FailedRotation],
                activities=[begin_activity, record_phase, prepare_activity, rewrite_activity],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    "RotateEtcdEncryptionKey", {"clusterID": cluster_id}, id=owner, task_queue=queue
                )
                with pytest.raises(WorkflowFailureError):
                    await asyncio.wait_for(handle.result(), timeout=10)
                events = (await handle.fetch_history()).events
                scheduled = next(
                    e
                    for e in events
                    if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
                    and e.activity_task_scheduled_event_attributes.activity_type.name
                    == "StartEtcdEncryptionRewrite"
                )
                if lost_ack == "astro_worker":
                    from astrolift_workflows import client as workflow_client
                    from astrolift_workflows.activities.nvke import nvke_operation_attempt
                    from astrolift_workflows.workflows.nvke import NvkeOperationWorkflow
                    from core.testing.temporal import temporal_worker

                    async def get_client():
                        return env.client

                    astro_queue = "astro-reset-" + uuid4().hex
                    monkeypatch.setattr(workflow_client, "_get_client_async", get_client)
                    monkeypatch.setattr(workflow_client, "_temporal_enabled", lambda: True)
                    monkeypatch.setattr(workflow_client, "_task_queue", lambda: astro_queue)
                    async with temporal_worker(
                        env,
                        task_queue=astro_queue,
                        workflows=[NvkeOperationWorkflow],
                        activities=[nvke_operation_attempt],
                    ):
                        row = await sync_to_async(reserve)(
                            configured, authority, source, owner, handle.result_run_id, scheduled
                        )
                        parent = env.client.get_workflow_handle(row.workflow_id, run_id=row.workflow_run_id)
                        assert await asyncio.wait_for(parent.result(), timeout=45) == "succeeded"
                        history = await parent.fetch_history()
                        first = history.events[0].workflow_execution_started_event_attributes
                        assert await env.client.data_converter.decode(first.input.payloads) == [row.pk, 0]
                        assert "PRIVATE" not in history.to_json() and cluster_id not in history.to_json()
                        assert len(calls) == 1 and REWRITE_CALLS[cluster_id] == 2
                    return
                changes = {}
                if lost_ack == "first_task":
                    changes["workflow_task_event_id"] = str(
                        next(
                            e.event_id
                            for e in events
                            if e.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED
                        )
                    )
                row = await sync_to_async(reserve)(
                    configured, authority, source, owner, handle.result_run_id, scheduled, **changes
                )
                await sync_to_async(row.refresh_from_db)()
                assert b"PRIVATE_RESET_RATIONALE" not in bytes(row.input_ciphertext)
                execution = (
                    row.workflow_id,
                    row.workflow_run_id or "reset-runtime-run",
                    "NvkeOperationWorkflow",
                )
                if lost_ack == "crash_before_rpc":
                    await sync_to_async(outbox.save_result)(
                        row,
                        {
                            "sending": True,
                            "target": {"workflow_id": owner, "base_run_id": handle.result_run_id},
                        },
                    )
                    await sync_to_async(row.save)(
                        update_fields=["result_backend_kind", "result_ciphertext", "version", "updated_at"]
                    )
                    assert await sync_to_async(runtime.attempt)(row.pk, 0, execution=execution) == "uncertain"
                    await sync_to_async(row.refresh_from_db)()

                    def reconcile():
                        with context(configured.world):
                            return outbox.resume(str(row.guid), expected_version=row.version)

                    await sync_to_async(reconcile)()
                    assert await sync_to_async(runtime.attempt)(row.pk, 0, execution=execution) == "uncertain"
                    assert not calls and REWRITE_CALLS[cluster_id] == 1
                    return
                if lost_ack == "live_phase_changed":
                    details["parent_override"]["conditions"]["etcdKeyRotation"]["phase"] = "finalize"
                if lost_ack == "completed_owner":
                    boundary = next(
                        e.event_id
                        for e in events
                        if e.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED
                    )
                    completed = await env.client.workflow_service.reset_workflow_execution(
                        ResetWorkflowExecutionRequest(
                            namespace=env.client.namespace,
                            workflow_execution=WorkflowExecution(
                                workflow_id=owner, run_id=handle.result_run_id
                            ),
                            workflow_task_finish_event_id=boundary,
                            request_id=str(uuid4()),
                            reason="owned fixture completion before reset review",
                        ),
                        retry=False,
                    )
                    await env.client.get_workflow_handle(owner, run_id=completed.run_id).result()
                if lost_ack in {
                    "live_phase_changed",
                    "completed_owner",
                    "first_task",
                    "history_phase_mismatch",
                }:
                    assert await sync_to_async(runtime.attempt)(row.pk, 0, execution=execution) == "failed"
                    assert not calls
                    return
                status = await sync_to_async(runtime.attempt)(row.pk, 0, execution=execution)
                await sync_to_async(row.refresh_from_db)()
                assert status in {"awaiting_ready", "succeeded"}, (status, row.error_code, row.stage)
                current = env.client.get_workflow_handle(owner)
                await asyncio.wait_for(current.result(), timeout=10)
                assert await sync_to_async(runtime.attempt)(row.pk, 0, execution=execution) == "succeeded"
                assert await sync_to_async(runtime.attempt)(row.pk, 0, execution=execution) == "succeeded"
                await sync_to_async(row.refresh_from_db)()
                assert len(calls) == 1 and REWRITE_CALLS[cluster_id] == 2
                assert row.remote_reference["base_run_id"] == handle.result_run_id
                assert row.remote_reference["run_id"] != handle.result_run_id
                assert "PRIVATE" not in str(await sync_to_async(outbox.decode_result)(row))
        finally:
            await sync_to_async(source_context.__exit__)(None, None, None)


async def test_real_reset_history_retains_exact_intent_and_base_run():
    async with temporal_test_env() as env:
        queue, owner, intent = "reset-proof-" + uuid4().hex, "reset-proof-owner-" + uuid4().hex, str(uuid4())
        reason, identity = "owned reset intent " + intent, "astro-reset-proof-" + intent
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[HistoryWorkflow],
            activities=[prepare_activity, private_activity],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            original = await env.client.start_workflow(
                "RotateEtcdEncryptionKey", {"clusterID": str(uuid4())}, id=owner, task_queue=queue
            )
            await asyncio.wait_for(original.result(), timeout=10)
            events = (await original.fetch_history()).events
            boundary = next(
                event.event_id
                for event in events
                if event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED
            )
            response = await env.client.workflow_service.reset_workflow_execution(
                ResetWorkflowExecutionRequest(
                    namespace=env.client.namespace,
                    workflow_execution=WorkflowExecution(workflow_id=owner, run_id=original.result_run_id),
                    workflow_task_finish_event_id=boundary,
                    request_id=intent,
                    reason=reason,
                    identity=identity,
                    reset_reapply_type=ResetReapplyType.RESET_REAPPLY_TYPE_NONE,
                ),
                retry=False,
                timeout=timedelta(seconds=10),
            )
            handle = env.client.get_workflow_handle(owner, run_id=response.run_id)
            await asyncio.wait_for(handle.result(), timeout=10)
            reset_events = (await handle.fetch_history()).events
            proof = next(
                event.workflow_task_failed_event_attributes
                for event in reset_events
                if event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED
                and event.workflow_task_failed_event_attributes.cause
                == WorkflowTaskFailedCause.WORKFLOW_TASK_FAILED_CAUSE_RESET_WORKFLOW
            )
            assert proof.base_run_id == original.result_run_id
            assert proof.new_run_id == response.run_id
            assert intent in proof.failure.message


@pytest.fixture
def reset_source(configured):
    owner = "reviewed-reset-" + uuid4().hex
    with cluster_source(configured, "127.0.0.1:17433", "astrolift-nvke", "reset-test", owner) as values:
        authority, source, cluster_id, details = values
        scheduled = SimpleNamespace(
            event_id=15,
            activity_task_scheduled_event_attributes=SimpleNamespace(workflow_task_completed_event_id=14),
        )
        yield authority, source, owner, str(uuid4()), scheduled


@pytest.mark.parametrize(
    "changes",
    [
        {"acknowledge_boundary_reviewed": False},
        {"acknowledge_conflicting_work_stopped": False},
        {"workflow": "RenewK8sCertsV2"},
        {"workflow_task_event_id": 14},
        {"activity_event_id": "01"},
        {"activity_event_id": "9223372036854775808"},
        {"activity_event_id": "١٥"},
        {"base_run_id": "00000000-0000-0000-0000-000000000000"},
        {"expected_source_version": 0},
        {"expected_authority_version": 0},
        {"mode": "refresh_survivors", "retirement_workflow_id": ""},
        {"rationale": ""},
    ],
)
def test_reset_refuses_incomplete_or_stale_admission(configured, reset_source, changes):
    with pytest.raises((NvkeError, support.NvkeSiteError)):
        reserve(configured, *reset_source, **changes)
    assert not outbox.NvkeOperation.all_objects.filter(operation_key=reset.KEY).exists()


def test_reset_review_idempotency_and_hidden_busy_guard(configured, reset_source):
    request_id = str(uuid4())
    row = reserve(configured, *reset_source, request_id=request_id)
    assert reserve(configured, *reset_source, request_id=request_id).pk == row.pk
    with pytest.raises(NvkeError) as conflict:
        reserve(configured, *reset_source, request_id=request_id, rationale="Different reviewed reason")
    assert conflict.value.code == "CONFLICT"
    row.refresh_from_db()
    row.soft_delete()
    with pytest.raises(NvkeError) as busy:
        reserve(configured, *reset_source)
    assert busy.value.code == "RESOURCE_BUSY"
    from astrolift_clusters.tests.test_nvke_native_operations import reserve as start

    authority, source, *_ = reset_source
    with pytest.raises(NvkeError) as busy_start:
        start(configured, authority, source, row.authority["native_cluster_id"])
    assert busy_start.value.code == "RESOURCE_BUSY"


def test_reset_graphql_masks_review_evidence_with_alias(configured, reset_source):
    authority, source, owner, run_id, _ = reset_source
    values = {
        "siteId": str(configured.site.guid),
        "expectedSiteVersion": configured.site.version,
        "expectedActorId": configured.world.user.pk,
        "expectedAuthorityVersion": authority.version,
        "sourceOperationId": str(source.guid),
        "expectedSourceVersion": source.version,
        "requestId": str(uuid4()),
        "workflow": "RotateEtcdEncryptionKey",
        "workflowId": owner,
        "baseRunId": run_id,
        "workflowTaskEventId": "14",
        "activityEventId": "15",
        "activityType": "StartEtcdEncryptionRewrite",
        "phase": "rewrite",
        "mode": "restart_rewrite",
        "rationale": "PRIVATE_RESET_RATIONALE",
        "quiescenceEvidence": "PRIVATE_NODE_EVIDENCE",
        "acknowledgeBoundaryReviewed": True,
        "acknowledgeConflictingWorkStopped": True,
    }
    query = """mutation($review:RequestNvkeNativeResetInput!){admit:requestNvkeNativeReset(input:$review){ok errors{code message} data{id operationKey}}}"""
    result = execute(configured.world, query, {"review": values})
    assert not result.errors and result.data["admit"]["ok"], result
    values.update(requestId=str(uuid4()), acknowledgeBoundaryReviewed=False)
    result = execute(configured.world, query, {"review": values})
    assert not result.errors and not result.data["admit"]["ok"]
    logs = MutationAuditLog.objects.filter(operation="nvke.native_reset_request")
    assert logs.count() == 2
    for log in logs:
        assert "PRIVATE" not in str(log.variables)
        assert log.variables["review"]["rationale"] == "***REDACTED***"
        assert log.variables["review"]["quiescenceEvidence"] == "***REDACTED***"


@pytest.mark.parametrize(
    "case", ["wrong_cluster", "wrong_phase", "success", "before_rewrite", "expired_resume", "first_task"]
)
def test_reset_boundary_policy_preserves_successful_work(case):
    request = {
        "phase": "rewrite",
        "mode": "retry_activity",
        "workflow_task_event_id": 20,
        "clusterID": str(uuid4()),
        "recorded_state": "failed",
    }
    parameters = {"ClusterID": request["clusterID"], "OperationName": review.OPERATIONS["rewrite"]}
    name, completed, acknowledged, start_time = "rollout.apply_to_node", False, True, timezone.now()
    if case == "wrong_cluster":
        parameters["ClusterID"] = str(uuid4())
    elif case == "wrong_phase":
        parameters["OperationName"] = review.OPERATIONS["remove_old_keys"]
    elif case == "success":
        completed = True
    elif case == "before_rewrite":
        acknowledged = False
    elif case == "expired_resume":
        request["mode"] = "resume_rewrite"
        name, start_time = "GetEtcdEncryptionRewriteStatus", timezone.now() - timedelta(hours=6)
    elif case == "first_task":
        request["workflow_task_event_id"] = 4
    with pytest.raises(NvkeError):
        review.target_policy(request, name, parameters, completed, False, acknowledged, start_time, 4)


def test_reset_policy_accepts_recorded_rollout_business_failure():
    request = {
        "phase": "remove_old_keys",
        "mode": "retry_activity",
        "workflow_task_event_id": 20,
        "clusterID": str(uuid4()),
        "recorded_state": "failed",
    }
    review.target_policy(
        request,
        "rollout.apply_to_node",
        {"ClusterID": request["clusterID"], "OperationName": review.OPERATIONS["remove_old_keys"]},
        True,
        True,
        False,
        None,
        4,
    )


POLL_CALLS: dict[str, int] = {}
POLL_MISMATCH: dict[str, str] = {}


@activity.defn(name="GetEtcdEncryptionRewriteStatus")
async def poll_activity(parameters: dict) -> None:
    identity = parameters["NodeID"]
    POLL_CALLS[identity] = POLL_CALLS.get(identity, 0) + 1
    if POLL_CALLS[identity] == 1:
        raise ApplicationError("PRIVATE_POLL_FAILURE", non_retryable=True)


@workflow.defn(name="RotateEtcdEncryptionKey")
class PollingRotation:
    @workflow.run
    async def run(self, parameters: dict) -> None:
        await workflow.execute_activity(
            begin_activity,
            {"ClusterID": parameters["clusterID"], "WorkflowID": workflow.info().workflow_id},
            start_to_close_timeout=timedelta(seconds=10),
        )
        await workflow.execute_activity(
            record_phase,
            {
                "ClusterID": parameters["clusterID"],
                "WorkflowID": workflow.info().workflow_id,
                "Phase": "rewrite",
            },
            start_to_close_timeout=timedelta(seconds=10),
        )
        operation = await workflow.execute_activity(
            rewrite_activity,
            {"NodeID": parameters["clusterID"], "OperationID": "PRIVATE_REQUESTED_REWRITE"},
            start_to_close_timeout=timedelta(seconds=10),
        )
        await workflow.execute_activity(
            poll_activity,
            {
                "NodeID": parameters["clusterID"],
                "OperationID": POLL_MISMATCH.get(parameters["clusterID"], operation["OperationID"]),
            },
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mismatched_operation", [False, True])
async def test_real_reset_poll_preserves_acknowledged_operation(configured, settings, mismatched_operation):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    async with temporal_test_env() as env:
        queue, owner = "reset-poll-" + uuid4().hex, "poll-owner-" + uuid4().hex
        source_context = cluster_source(
            configured, env.client.service_client.config.target_host, env.client.namespace, queue, owner
        )
        authority, source, cluster_id, _ = await sync_to_async(source_context.__enter__)()
        REWRITE_CALLS[cluster_id] = 1
        if mismatched_operation:
            POLL_MISMATCH[cluster_id] = "PRIVATE_OTHER_OPERATION"
        try:
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[PollingRotation],
                activities=[begin_activity, record_phase, rewrite_activity, poll_activity],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    "RotateEtcdEncryptionKey", {"clusterID": cluster_id}, id=owner, task_queue=queue
                )
                with pytest.raises(WorkflowFailureError):
                    await asyncio.wait_for(handle.result(), timeout=10)
                events = (await handle.fetch_history()).events
                selected = next(
                    e
                    for e in events
                    if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
                    and e.activity_task_scheduled_event_attributes.activity_type.name
                    == "GetEtcdEncryptionRewriteStatus"
                )
                row = await sync_to_async(reserve)(
                    configured,
                    authority,
                    source,
                    owner,
                    handle.result_run_id,
                    selected,
                    mode="resume_rewrite",
                    activity_type="GetEtcdEncryptionRewriteStatus",
                )
                execution = (
                    row.workflow_id,
                    row.workflow_run_id or "reset-poll-run",
                    "NvkeOperationWorkflow",
                )
                status = await sync_to_async(runtime.attempt)(row.pk, 0, execution=execution)
                if mismatched_operation:
                    assert status == "failed"
                    assert (
                        await env.client.get_workflow_handle(owner).describe()
                    ).run_id == handle.result_run_id
                else:
                    assert status in {"awaiting_ready", "succeeded"}
                    await asyncio.wait_for(env.client.get_workflow_handle(owner).result(), timeout=10)
                    assert await sync_to_async(runtime.attempt)(row.pk, 0, execution=execution) == "succeeded"
                    assert POLL_CALLS[cluster_id] == 2
                assert REWRITE_CALLS[cluster_id] == 2
        finally:
            await sync_to_async(source_context.__exit__)(None, None, None)


@pytest.mark.parametrize("phase", ["add_secondary", "promote_primary", "remove_old_keys"])
@pytest.mark.parametrize(
    "name",
    [
        "rollout.validate",
        "rollout.build_apply_to_node_payload",
        "rollout.cluster_preflight",
        "rollout.node_preflight",
    ],
)
def test_reset_policy_covers_failed_rollout_prerequisites(phase, name):
    request = {
        "phase": phase,
        "mode": "retry_activity",
        "workflow_task_event_id": 20,
        "clusterID": str(uuid4()),
        "recorded_state": "failed",
    }
    review.target_policy(
        request,
        name,
        {"ClusterID": request["clusterID"], "OperationName": review.OPERATIONS[phase]},
        False,
        False,
        False,
        None,
        4,
    )


@pytest.mark.parametrize("completed", [False, True])
def test_reset_phase_transition_retains_completed_phase(completed):
    request = {
        "phase": "promote_primary",
        "mode": "retry_activity",
        "workflow_task_event_id": 20,
        "clusterID": str(uuid4()),
        "workflow_id": "retained-owner",
        "recorded_state": "failed",
    }
    parameters = {"ClusterID": request["clusterID"], "WorkflowID": request["workflow_id"], "Phase": "rewrite"}
    if completed:
        with pytest.raises(NvkeError):
            review.target_policy(
                request, "RecordEtcdKeyRotationPhase", parameters, True, False, False, None, 4
            )
    else:
        review.target_policy(request, "RecordEtcdKeyRotationPhase", parameters, False, False, False, None, 4)
