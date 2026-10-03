"""Actual registered dispatch, PostgreSQL outbox, native Temporal and HTTPS proof."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import timedelta
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from astrolift_agents.models import AgentTask, AgentTaskCompletionCallback
from astrolift_agents.services.task_completion_callbacks import (
    configure_policy,
    deliver_attempt,
    set_callback_secret,
)
from astrolift_dispatch.spawners import registry
from astrolift_dispatch.spawners.base import SpawnResult
from astrolift_workflows.activities import agent_dispatch_finalization as finalization
from astrolift_workflows.activities import agent_stage
from astrolift_workflows.inputs import Actor, DispatchAgentTaskInput, WorkflowResult
from astrolift_workflows.workflows import dispatch_agent_task as dispatch_workflow
from core.secrets import EncryptedSecret, decrypt
from core.testing.callback_receiver import callback_receiver  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
MARKER = "private-workflow-error-must-not-reach-history-or-outbox"
KEY = "disposable-finalization-signing-key-32-bytes"


@pytest.fixture
def callback_task(org, callback_receiver, monkeypatch):  # noqa: F811
    from astrolift_agents.services import task_completion_callbacks

    monkeypatch.setattr(task_completion_callbacks, "enqueue_callback", lambda *args: None)
    configure_policy(org.pk, [callback_receiver.host])
    set_callback_secret(org.pk, "finalization", KEY)
    task = AgentTask.objects.create(organization=org, status=AgentTask.Status.QUEUED)
    row = AgentTaskCompletionCallback.objects.create(
        task=task,
        organization=org,
        callback_url=callback_receiver.url,
        secret_ref="finalization",
        correlation_id="literal caller correlation",
        mode="FULL",
    )
    return task, row, callback_receiver


def execution(task, *, run="original-run", attempt=3):
    return {
        "namespace": "default",
        "workflow_id": f"DispatchAgentTaskWorkflow-{task.guid}",
        "run_id": run,
        "actor": {"kind": "user", "user_id": 123, "token_id": None},
        "activity_id": "dispatch-activity",
        "activity_attempt": attempt,
    }


@pytest.fixture
def physical(cluster, monkeypatch):
    state = {"spawned": [], "stopped": [], "confirmed": True}

    class Spawner:
        def spawn(self, task):
            state["spawned"].append(task.pk)
            return SpawnResult(external_id=f"agent-task-{str(task.guid).replace('-', '')}")

        def stop(self, external_id, *, expected_task_guid):
            assert external_id == f"agent-task-{expected_task_guid.replace('-', '')}"
            state["stopped"].append((external_id, expected_task_guid))
            if state.get("stop_error"):
                raise ConnectionError(MARKER)

        def confirm_stopped(self, external_id):
            return state["confirmed"]

    monkeypatch.setattr(registry, "get_spawner", lambda *args, **kwargs: Spawner())
    monkeypatch.setattr(agent_stage, "_resolve_managed_cluster", lambda _: cluster)
    return state


@pytest.mark.parametrize("reason", ["failed", "timed_out"])
def test_finalization_uses_locked_transition_and_one_encrypted_event(callback_task, physical, reason):
    task, row, _ = callback_task
    agent_stage._spawn_agent_task_sync(task.pk, execution(task))
    result = finalization.finalize_dispatch_sync(task.pk, execution(task), reason)
    assert result["status"] == reason
    task.refresh_from_db()
    row.refresh_from_db()
    assert task.failure["code"] == (
        "AGENT_DISPATCH_TIMEOUT" if reason == "timed_out" else "AGENT_DISPATCH_EXHAUSTED"
    )
    assert MARKER not in json.dumps(task.failure)
    body = json.loads(decrypt(EncryptedSecret(row.payload_backend_kind, bytes(row.payload_ciphertext))))
    assert body["status"] == reason and body["correlation_id"] == "literal caller correlation"
    event_id, ciphertext = row.final_event_id, bytes(row.payload_ciphertext)
    finalization.finalize_dispatch_sync(task.pk, execution(task), reason)
    row.refresh_from_db()
    assert row.final_event_id == event_id and bytes(row.payload_ciphertext) == ciphertext
    assert row.generation == 1 and AgentTaskCompletionCallback.objects.filter(task=task).count() == 1
    assert task.dispatch_execution["dispatch_activity_attempt"] == 3


def test_uncertain_stop_is_durable_and_blocks_old_dispatch_callbacks(callback_task, physical):
    task, row, _ = callback_task
    proof = execution(task)
    agent_stage._spawn_agent_task_sync(task.pk, proof)
    physical["confirmed"] = False
    with pytest.raises(ApplicationError, match="not yet confirmed"):
        finalization.finalize_dispatch_sync(task.pk, proof, "timed_out")
    task.refresh_from_db()
    row.refresh_from_db()
    assert task.status == "provisioning" and not row.final_event_id
    assert task.dispatch_execution["final_reason"] == "timed_out"
    with pytest.raises(ApplicationError, match="being finalized"):
        agent_stage._spawn_agent_task_sync(task.pk, proof)
    with pytest.raises(ApplicationError, match="being finalized"):
        agent_stage._poll_agent_task_sync(task.pk, proof)
    assert len(physical["spawned"]) == 1
    physical["confirmed"] = True
    assert finalization.finalize_dispatch_sync(task.pk, proof, "timed_out")["status"] == "timed_out"


@pytest.mark.parametrize("status", ["completed", "cancelled", "failed", "timed_out"])
def test_existing_terminal_task_event_and_result_win(callback_task, physical, status):
    task, row, _ = callback_task
    task.status = "running"
    task.result = {"keep": "literal result"}
    task.failure = {"keep": "literal failure"}
    task.save(update_fields=["status", "result", "failure"])
    task.transition_to(status)
    row.refresh_from_db()
    event = row.final_event_id
    assert finalization.finalize_dispatch_sync(task.pk, execution(task), "failed")["status"] == status
    task.refresh_from_db()
    row.refresh_from_db()
    assert task.result == {"keep": "literal result"} and task.failure == {"keep": "literal failure"}
    assert row.final_event_id == event and not physical["stopped"]


def test_operator_stop_wins_platform_timeout(callback_task, physical):
    task, row, _ = callback_task
    agent_stage._spawn_agent_task_sync(task.pk, execution(task))
    task.refresh_from_db()
    task.cancel_requested_at = timezone.now()
    task.save(update_fields=["cancel_requested_at"])
    assert finalization.finalize_dispatch_sync(task.pk, execution(task), "timed_out")["status"] == "cancelled"
    task.refresh_from_db()
    row.refresh_from_db()
    assert task.failure is None and row.event_metadata["status"] == "cancelled"


def test_stale_run_and_changed_final_reason_cannot_mutate(callback_task, physical):
    task, row, _ = callback_task
    agent_stage._spawn_agent_task_sync(task.pk, execution(task))
    with pytest.raises(ApplicationError, match="execution has changed"):
        finalization.finalize_dispatch_sync(task.pk, execution(task, run="different-run"), "failed")
    physical["confirmed"] = False
    with pytest.raises(ApplicationError):
        finalization.finalize_dispatch_sync(task.pk, execution(task), "timed_out")
    with pytest.raises(ApplicationError, match="finalization has changed"):
        finalization.finalize_dispatch_sync(task.pk, execution(task), "failed")
    row.refresh_from_db()
    assert not row.final_event_id


def test_unknown_placement_never_falls_back_or_claims_stopped(callback_task, physical):
    task, row, _ = callback_task
    task.status = "running"
    task.external_id = "legacy-unproven-job"
    task.save(update_fields=["status", "external_id"])
    with pytest.raises(ApplicationError, match="not yet confirmed"):
        finalization.finalize_dispatch_sync(task.pk, execution(task), "failed")
    row.refresh_from_db()
    assert not row.final_event_id
    assert not physical["stopped"]


def test_changed_frozen_cluster_keeps_finalization_pending(callback_task, cluster, physical):
    task, row, _ = callback_task
    agent_stage._spawn_agent_task_sync(task.pk, execution(task))
    cluster.endpoint = "https://different.invalid"
    cluster.save(update_fields=["endpoint"])
    with pytest.raises(RuntimeError, match="endpoint"):
        finalization.finalize_dispatch_sync(task.pk, execution(task), "failed")
    row.refresh_from_db()
    assert not row.final_event_id and not physical["stopped"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["exhaustion", "timeout", "permanent"])
async def test_actual_dispatch_final_exhaustion_timeout_has_one_callback(
    temporal_env, callback_task, physical, monkeypatch, failure_kind, caplog
):
    task, row, receiver = callback_task
    attempts = []

    def fail_poll(task_pk, proof=None):
        attempts.append(proof["activity_attempt"])
        if failure_kind == "timeout":
            # The production activity keeps heartbeating during the bounded retry window.
            return {"terminal": False, "status": "provisioning"}
        raise ApplicationError(MARKER, non_retryable=failure_kind == "permanent")

    monkeypatch.setattr(agent_stage, "_poll_agent_task_sync", fail_poll)
    monkeypatch.setattr(agent_stage, "_POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(
        dispatch_workflow,
        "_RETRY",
        RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(milliseconds=20),
            maximum_interval=timedelta(milliseconds=20),
        ),
    )
    if failure_kind == "timeout":
        monkeypatch.setattr(dispatch_workflow, "_DISPATCH_TIMEOUT", timedelta(milliseconds=200))
    # Both patched timeout branches use a shared budget below, not fake workflow logic.
    monkeypatch.setattr(
        dispatch_workflow,
        "_INPUT_WAIT_DISPATCH_TIMEOUT",
        timedelta(milliseconds=200) if failure_kind == "timeout" else timedelta(minutes=1),
    )
    queue = f"finalization-{uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[dispatch_workflow.DispatchAgentTaskWorkflow],
        activities=[agent_stage.dispatch_agent_task, finalization.finalize_agent_dispatch],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await temporal_env.client.start_workflow(
            dispatch_workflow.DispatchAgentTaskWorkflow.run,
            DispatchAgentTaskInput(task.pk, Actor(kind="user", user_id=42, display=MARKER)),
            id=f"DispatchAgentTaskWorkflow-{task.guid}",
            task_queue=queue,
        )
        result = await asyncio.wait_for(handle.result(), 20)
    expected = "timed_out" if failure_kind == "timeout" else "failed"
    assert not result.ok and result.data["status"] == expected
    await sync_to_async(task.refresh_from_db)()
    await sync_to_async(row.refresh_from_db)()
    assert task.status == expected and row.final_event_id and row.generation == 1
    assert task.dispatch_execution["run_id"] == handle.first_execution_run_id
    assert task.dispatch_execution["actor"] == {"kind": "user", "user_id": 42, "token_id": None}
    assert "display" not in json.dumps(task.dispatch_execution)
    assert MARKER not in json.dumps(task.failure) and MARKER not in caplog.text
    history = await handle.fetch_history()
    await Replayer(
        workflows=[dispatch_workflow.DispatchAgentTaskWorkflow], workflow_runner=UnsandboxedWorkflowRunner()
    ).replay_workflow(history)
    # Original start input contains display by existing contract; new failures and commands do not.
    for event in history.events[1:]:
        assert MARKER not in str(event)
    if failure_kind == "exhaustion":
        assert attempts == [1, 2, 3]
        assert task.dispatch_execution["dispatch_activity_attempt"] == 3
    elif failure_kind == "permanent":
        assert attempts == [1]
    assert len(physical["spawned"]) == 1 and len(physical["stopped"]) == 1
    # A real HTTPS 503 then recovery preserves the frozen final event/body.
    receiver.responses[:] = [503, 200]
    first = await sync_to_async(deliver_attempt)(row.pk, row.generation, now=row.retry_started_at, jitter=0.5)
    assert first["state"] == "retry" and first["delay_seconds"] == 30
    await sync_to_async(row.refresh_from_db)()
    second = await sync_to_async(deliver_attempt)(row.pk, row.generation, now=row.next_attempt_at)
    assert second["state"] == "finished"
    assert receiver.requests[0][1] == receiver.requests[1][1]
    await sync_to_async(row.refresh_from_db)()
    assert row.status == "delivered" and not row.payload_ciphertext


@workflow.defn(name="DispatchAgentTaskWorkflow", sandboxed=False)
class LegacyDispatchWorkflow:
    """N1 command sequence from published80a; run against actual test server."""

    @workflow.run
    async def run(self, input: DispatchAgentTaskInput) -> WorkflowResult:
        try:
            outcome = await workflow.execute_activity(
                agent_stage.dispatch_agent_task,
                input.agent_task_id,
                start_to_close_timeout=(
                    timedelta(days=8) if workflow.patched("agent-input-wait-budget") else timedelta(hours=24)
                ),
                schedule_to_close_timeout=(
                    timedelta(days=8) if workflow.patched("agent-input-wait-budget") else timedelta(hours=24)
                ),
                heartbeat_timeout=timedelta(minutes=2),
                retry_policy=dispatch_workflow._RETRY,
            )
        except Exception as exc:
            return WorkflowResult(ok=False, message=f"agent dispatch failed: {exc}")
        status = outcome.get("status", "")
        return WorkflowResult(
            ok=status == "completed",
            message=f"agent task {status}" if status else "agent task dispatched",
            data=outcome,
        )


@activity.defn(name="astrolift.agent.dispatch_task")
async def legacy_failed_dispatch(task_pk: int) -> dict:
    raise ApplicationError("Static legacy dispatch failure.", non_retryable=True)


@pytest.mark.asyncio
async def test_completed_n1_failure_history_replays_without_new_commands(temporal_env, callback_task):
    task, row, _ = callback_task
    queue = f"legacy-completed-{uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[LegacyDispatchWorkflow],
        activities=[legacy_failed_dispatch],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await temporal_env.client.start_workflow(
            LegacyDispatchWorkflow.run,
            DispatchAgentTaskInput(task.pk, Actor(kind="system")),
            id=f"DispatchAgentTaskWorkflow-{task.guid}",
            task_queue=queue,
        )
        result = await handle.result()
    assert not result.ok
    history = await handle.fetch_history()
    await Replayer(
        workflows=[dispatch_workflow.DispatchAgentTaskWorkflow], workflow_runner=UnsandboxedWorkflowRunner()
    ).replay_workflow(history)
    await sync_to_async(task.refresh_from_db)()
    await sync_to_async(row.refresh_from_db)()
    assert task.status == "queued" and not task.dispatch_execution and not row.final_event_id


@pytest.mark.asyncio
async def test_unfinished_n1_run_enters_patched_finalization_with_original_actor(
    temporal_env, callback_task, physical, monkeypatch
):
    task, row, _ = callback_task
    first_attempt = threading.Event()

    @activity.defn(name="astrolift.agent.dispatch_task")
    async def legacy_interrupted_dispatch(task_pk: int) -> dict:
        # Published N1 dispatch has physical placement but no execution binding.
        await sync_to_async(agent_stage._spawn_agent_task_sync, thread_sensitive=False)(task_pk)
        first_attempt.set()
        raise ApplicationError("Static retryable legacy dispatch failure.")

    monkeypatch.setattr(
        dispatch_workflow,
        "_RETRY",
        RetryPolicy(initial_interval=timedelta(seconds=1), maximum_interval=timedelta(seconds=1)),
    )
    queue = f"legacy-unfinished-{uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[LegacyDispatchWorkflow],
        activities=[legacy_interrupted_dispatch],
        max_cached_workflows=0,
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await temporal_env.client.start_workflow(
            LegacyDispatchWorkflow.run,
            DispatchAgentTaskInput(task.pk, Actor(kind="api_token", user_id=11, token_id=29)),
            id=f"DispatchAgentTaskWorkflow-{task.guid}",
            task_queue=queue,
        )
        assert await asyncio.to_thread(first_attempt.wait, 10)
    await sync_to_async(task.refresh_from_db)()
    assert not task.dispatch_execution

    def fail_poll(*args):
        raise ApplicationError(MARKER, non_retryable=True)

    monkeypatch.setattr(agent_stage, "_poll_agent_task_sync", fail_poll)
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[dispatch_workflow.DispatchAgentTaskWorkflow],
        activities=[agent_stage.dispatch_agent_task, finalization.finalize_agent_dispatch],
        max_cached_workflows=0,
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        result = await asyncio.wait_for(handle.result(), 20)
    await sync_to_async(task.refresh_from_db)()
    await sync_to_async(row.refresh_from_db)()
    assert result.data["status"] == "failed" and row.final_event_id
    assert task.dispatch_execution["actor"] == {"kind": "api_token", "user_id": 11, "token_id": 29}
    assert task.dispatch_execution["run_id"] == handle.first_execution_run_id
    assert len(physical["spawned"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_uncertain_finalizer_survives_worker_replacement_without_intermediate_callback(
    temporal_env, callback_task, physical, monkeypatch, cancel
):
    task, row, _ = callback_task
    physical["confirmed"] = False

    def fail_poll(*args):
        raise ApplicationError(MARKER, non_retryable=True)

    monkeypatch.setattr(agent_stage, "_poll_agent_task_sync", fail_poll)
    queue = f"finalizer-recovery-{uuid4()}"

    def worker():
        return Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[dispatch_workflow.DispatchAgentTaskWorkflow],
            activities=[agent_stage.dispatch_agent_task, finalization.finalize_agent_dispatch],
            max_cached_workflows=0,
            workflow_runner=UnsandboxedWorkflowRunner(),
        )

    async with worker():
        handle = await temporal_env.client.start_workflow(
            dispatch_workflow.DispatchAgentTaskWorkflow.run,
            DispatchAgentTaskInput(task.pk, Actor(kind="system")),
            id=f"DispatchAgentTaskWorkflow-{task.guid}",
            task_queue=queue,
        )
        async with asyncio.timeout(15):
            while not physical["stopped"]:
                await asyncio.sleep(0.02)
        await sync_to_async(task.refresh_from_db)()
        await sync_to_async(row.refresh_from_db)()
        assert task.dispatch_execution["final_reason"] == "failed" and not row.final_event_id
        assert task.status == "provisioning"
    if cancel:
        task.cancel_requested_at = timezone.now()
        await sync_to_async(task.save)(update_fields=["cancel_requested_at"])
    physical["confirmed"] = True
    async with worker():
        result = await asyncio.wait_for(handle.result(), 20)
    await sync_to_async(task.refresh_from_db)()
    await sync_to_async(row.refresh_from_db)()
    assert result.data["status"] == ("cancelled" if cancel else "failed")
    assert row.final_event_id and row.generation == 1 and len(physical["spawned"]) == 1
    assert task.dispatch_execution["run_id"] == handle.first_execution_run_id


@pytest.mark.parametrize("boundary", ["delete", "filesystem"])
def test_real_k8s_stop_failure_does_not_log_sensitive_provider_exception(
    callback_task, cluster, monkeypatch, boundary, caplog
):
    from types import SimpleNamespace

    from astrolift_agents.services.task_target import freeze_task_target
    from astrolift_dispatch.spawners.k8s_job import K8sJobSpawner
    from core import cluster_management

    task, row, _ = callback_task
    task.status = "running"
    task.external_id = f"agent-task-{str(task.guid).replace('-', '')}"
    task.save(update_fields=["status", "external_id"])
    freeze_task_target(task, backend="k8s_job", cluster=cluster, namespace="owned-finalization")

    class Driver:
        def get_manifest(self, *args):
            return {"metadata": {"uid": "exact-job-uid", "labels": {"astrolift.dev/task-id": str(task.guid)}}}

        def list_manifests(self, *args):
            return []

        def delete_manifests(self, cluster_slug, namespace, refs, *, propagation_policy):
            assert namespace == "owned-finalization" and propagation_policy == "Foreground"
            assert refs[0]["metadata"]["uid"] == "exact-job-uid"
            if boundary == "delete":
                raise ConnectionError(MARKER)
            return SimpleNamespace(ok=True)

    def private_error(*args):
        raise RuntimeError(MARKER)

    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda _: Driver())
    monkeypatch.setattr(cluster_management, "_context_for_cluster", lambda _: SimpleNamespace(slug="owned"))
    monkeypatch.setattr("astrolift_dispatch.spawners.k8s_job._revoke_task_gateway_key", lambda _: None)
    monkeypatch.setattr("astrolift_dispatch.spawners.k8s_job._task_for_external_id", private_error)
    spawner = K8sJobSpawner(cluster=cluster, namespace="owned-finalization")
    monkeypatch.setattr(registry, "get_spawner", lambda *args, **kwargs: spawner)
    # Caller still sees the original error type for deletion; new activity redacts it.
    with pytest.raises(ConnectionError if boundary == "delete" else RuntimeError):
        finalization.finalize_dispatch_sync(task.pk, execution(task), "timed_out")
    task.refresh_from_db()
    row.refresh_from_db()
    assert task.status == "running" and not row.final_event_id
    assert MARKER not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_task_retry_count_is_not_temporal_dispatch_attempt(callback_task, app, physical):
    from astrolift_lifecycle.models import AgentRun
    from astrolift_registry.models import Workload

    task, row, _ = callback_task
    workload = Workload.objects.create(
        registered_app=app, name="Retry boundary", slug="retry-boundary", kind="agent", max_retries=7
    )
    run = AgentRun.objects.create(workload=workload, retry_count=2)
    task.agent_run = run
    task.save(update_fields=["agent_run"])
    for attempt in (1, 2, 3):
        agent_stage._spawn_agent_task_sync(task.pk, execution(task, attempt=attempt))
        row.refresh_from_db()
        assert not row.final_event_id
    finalization.finalize_dispatch_sync(task.pk, execution(task, attempt=3), "failed")
    row.refresh_from_db()
    run.refresh_from_db()
    workload.refresh_from_db()
    body = json.loads(decrypt(EncryptedSecret(row.payload_backend_kind, bytes(row.payload_ciphertext))))
    assert body["attempt"] == 3 and run.retry_count == 2 and workload.max_retries == 7


def test_actual_completion_racing_stop_preserves_its_terminal_event(callback_task, physical, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connection

    task, row, _ = callback_task
    proof = execution(task)
    agent_stage._spawn_agent_task_sync(task.pk, proof)
    task.refresh_from_db()
    task.transition_to("running")
    started = threading.Event()
    resume = threading.Event()

    class BlockingSpawner:
        def stop(self, external_id, **kwargs):
            started.set()
            assert resume.wait(10)

        def confirm_stopped(self, external_id):
            return True

    monkeypatch.setattr(registry, "get_spawner", lambda *args, **kwargs: BlockingSpawner())

    def finalize():
        try:
            return finalization.finalize_dispatch_sync(task.pk, proof, "failed")
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(finalize)
        assert started.wait(10)
        task.result = {"preserved": "completed while cleanup was pending"}
        task.save(update_fields=["result"])
        task.transition_to("completed")
        row.refresh_from_db()
        event = row.final_event_id
        resume.set()
        result = future.result(timeout=10)
    row.refresh_from_db()
    task.refresh_from_db()
    assert result["status"] == "completed" and row.final_event_id == event and row.generation == 1
    assert task.result == {"preserved": "completed while cleanup was pending"} and task.failure is None


@pytest.mark.asyncio
async def test_actual_operator_cancellation_has_one_final_event(
    temporal_env, callback_task, physical, monkeypatch
):
    task, row, _ = callback_task
    polling = threading.Event()

    def ongoing(task_pk, proof=None):
        polling.set()
        return {"terminal": False, "status": "provisioning"}

    monkeypatch.setattr(agent_stage, "_poll_agent_task_sync", ongoing)
    monkeypatch.setattr(agent_stage, "_POLL_INTERVAL_SECONDS", 0.02)
    queue = f"dispatch-cancel-{uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[dispatch_workflow.DispatchAgentTaskWorkflow],
        activities=[agent_stage.dispatch_agent_task, finalization.finalize_agent_dispatch],
        max_heartbeat_throttle_interval=timedelta(milliseconds=20),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await temporal_env.client.start_workflow(
            dispatch_workflow.DispatchAgentTaskWorkflow.run,
            DispatchAgentTaskInput(task.pk, Actor(kind="system")),
            id=f"DispatchAgentTaskWorkflow-{task.guid}",
            task_queue=queue,
        )
        assert await asyncio.to_thread(polling.wait, 10)
        await handle.cancel()
        result = await asyncio.wait_for(handle.result(), 20)
    await sync_to_async(task.refresh_from_db)()
    await sync_to_async(row.refresh_from_db)()
    assert result.data["status"] == "cancelled" and task.status == "cancelled"
    assert row.final_event_id and row.generation == 1 and len(physical["stopped"]) == 1
    assert task.failure is None


@pytest.mark.asyncio
async def test_schedule_close_before_any_dispatch_effect_finalizes_queued_task(
    temporal_env, callback_task, physical, monkeypatch
):
    task, row, _ = callback_task
    monkeypatch.setattr(dispatch_workflow, "_INPUT_WAIT_DISPATCH_TIMEOUT", timedelta(milliseconds=200))
    monkeypatch.setattr(
        dispatch_workflow,
        "_RETRY",
        RetryPolicy(initial_interval=timedelta(milliseconds=20), maximum_interval=timedelta(milliseconds=20)),
    )
    queue = f"dispatch-unstarted-{uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[dispatch_workflow.DispatchAgentTaskWorkflow],
        no_remote_activities=True,
        max_cached_workflows=0,
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await temporal_env.client.start_workflow(
            dispatch_workflow.DispatchAgentTaskWorkflow.run,
            DispatchAgentTaskInput(task.pk, Actor(kind="api_token", user_id=11, token_id=29)),
            id=f"DispatchAgentTaskWorkflow-{task.guid}",
            task_queue=queue,
        )
        async with asyncio.timeout(10):
            while True:
                history = await handle.fetch_history()
                if any(
                    event.HasField("activity_task_scheduled_event_attributes") for event in history.events
                ):
                    break
                await asyncio.sleep(0.02)
        await temporal_env.sleep(timedelta(seconds=1))
        async with asyncio.timeout(10):
            while True:
                history = await handle.fetch_history()
                scheduled = [
                    event
                    for event in history.events
                    if event.HasField("activity_task_scheduled_event_attributes")
                ]
                if len(scheduled) == 2:
                    break
                await asyncio.sleep(0.02)
        assert any(event.HasField("activity_task_timed_out_event_attributes") for event in history.events)
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[dispatch_workflow.DispatchAgentTaskWorkflow],
        activities=[finalization.finalize_agent_dispatch],
        max_cached_workflows=0,
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        result = await asyncio.wait_for(handle.result(), 20)
    await sync_to_async(task.refresh_from_db)()
    await sync_to_async(row.refresh_from_db)()
    assert result.data["status"] == "timed_out" and row.final_event_id
    assert task.dispatch_execution["actor"] == {"kind": "api_token", "user_id": 11, "token_id": 29}
    assert "dispatch_activity_attempt" not in task.dispatch_execution
    assert not physical["stopped"] and not physical["spawned"]


@pytest.mark.asyncio
async def test_cancelled_dispatch_uncertain_sensitive_stop_retries_without_logging_body(
    temporal_env, callback_task, physical, monkeypatch, caplog
):
    task, row, _ = callback_task
    polling = threading.Event()

    def ongoing(task_pk, proof=None):
        polling.set()
        return {"terminal": False, "status": "provisioning"}

    monkeypatch.setattr(agent_stage, "_poll_agent_task_sync", ongoing)
    monkeypatch.setattr(agent_stage, "_POLL_INTERVAL_SECONDS", 0.02)
    physical["stop_error"] = True
    queue = f"cancel-sensitive-stop-{uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[dispatch_workflow.DispatchAgentTaskWorkflow],
        activities=[agent_stage.dispatch_agent_task, finalization.finalize_agent_dispatch],
        max_heartbeat_throttle_interval=timedelta(milliseconds=20),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await temporal_env.client.start_workflow(
            dispatch_workflow.DispatchAgentTaskWorkflow.run,
            DispatchAgentTaskInput(task.pk, Actor(kind="system")),
            id=f"DispatchAgentTaskWorkflow-{task.guid}",
            task_queue=queue,
        )
        assert await asyncio.to_thread(polling.wait, 10)
        await handle.cancel()
        async with asyncio.timeout(15):
            while True:
                await sync_to_async(task.refresh_from_db)()
                if (
                    task.dispatch_execution.get("final_reason") == "cancelled"
                    and len(physical["stopped"]) >= 2
                ):
                    break
                await asyncio.sleep(0.02)
        await sync_to_async(row.refresh_from_db)()
        assert task.status == "provisioning" and task.cancel_requested_at and not row.final_event_id
        assert MARKER not in caplog.text
        physical["stop_error"] = False
        result = await asyncio.wait_for(handle.result(), 20)
    await sync_to_async(task.refresh_from_db)()
    await sync_to_async(row.refresh_from_db)()
    assert result.data["status"] == "cancelled" and row.final_event_id and row.generation == 1
    assert task.failure is None and MARKER not in caplog.text
    for event in (await handle.fetch_history()).events:
        assert MARKER not in str(event)


def test_unknown_running_effect_keeps_committed_final_intent(callback_task, physical):
    task, row, _ = callback_task
    task.status = "running"
    task.save(update_fields=["status"])
    proof = execution(task)
    with pytest.raises(ApplicationError, match="not yet confirmed"):
        finalization.finalize_dispatch_sync(task.pk, proof, "timed_out")
    task.refresh_from_db()
    row.refresh_from_db()
    assert task.dispatch_execution["final_reason"] == "timed_out" and not row.final_event_id
    with pytest.raises(ApplicationError, match="being finalized"):
        agent_stage._spawn_agent_task_sync(task.pk, proof)
    assert not physical["stopped"] and not physical["spawned"]
