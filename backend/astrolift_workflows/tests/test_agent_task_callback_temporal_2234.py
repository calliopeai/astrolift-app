"""Exercise completion retry orchestration against the actual Temporal server."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone
from temporalio import activity
from temporalio.worker import Replayer, Worker

from astrolift_workflows.activities.agent_task_callback import (
    deliver_agent_task_callback,
    reconcile_agent_task_callbacks,
)
from astrolift_workflows.schedule_boot import PHASE_3A_ACTIVE_KINDS
from astrolift_workflows.schedule_registry import ScheduleKind, get_schedule
from astrolift_workflows.worker import ACTIVITIES, WORKFLOWS
from astrolift_workflows.workflows.deliver_agent_task_callback import (
    AgentTaskCallbackReconcileWorkflow,
    DeliverAgentTaskCallbackWorkflow,
)
from core.testing.callback_receiver import callback_receiver as _callback_receiver
from core.testing.temporal import temporal_worker

callback_receiver = _callback_receiver


def test_real_https_request_timeout_is_bounded_to_ten_seconds(callback_receiver):
    from astrolift_agents.services.task_callback_transport import post_completion_callback

    callback_receiver.delay_seconds = 11
    started = time.monotonic()
    with pytest.raises((OSError, TimeoutError)):
        post_completion_callback(
            callback_receiver.url,
            [callback_receiver.host],
            b'{"event":"agent_task.finished"}',
            "disposable-timeout-completion-key-2234",
        )
    elapsed = time.monotonic() - started
    assert 9 <= elapsed <= 14
    assert len(callback_receiver.requests) == 1


def test_completion_outbox_recovery_is_active_and_served():
    kind = ScheduleKind.AGENT_TASK_CALLBACK_RECONCILE
    assert kind in PHASE_3A_ACTIVE_KINDS
    schedule = get_schedule(kind=kind)
    assert schedule.interval_seconds == 60
    assert schedule.workflow_name == "AgentTaskCallbackReconcileWorkflow"
    assert AgentTaskCallbackReconcileWorkflow in WORKFLOWS
    assert DeliverAgentTaskCallbackWorkflow in WORKFLOWS
    assert deliver_agent_task_callback in ACTIVITIES
    assert reconcile_agent_task_callbacks in ACTIVITIES


@pytest.mark.asyncio
async def test_retry_timer_survives_worker_replacement_and_history_replay(temporal_env):
    # Script the activity boundary to isolate SDK timer/worker recovery. The
    # production transport and persistence have separate PostgreSQL/HTTPS tests.
    first_attempt = asyncio.Event()
    calls = []

    @activity.defn(name="astrolift.agent_task_callback.deliver")
    async def receive_ids(callback_id: int, generation: int) -> dict:
        calls.append((callback_id, generation))
        first_attempt.set()
        return {
            "state": "retry" if len(calls) == 1 else "finished",
            "delay_seconds": 60.0,
            "attempts": len(calls),
        }

    queue = f"agent-callback-restart-{uuid4()}"

    def worker():
        return Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[DeliverAgentTaskCallbackWorkflow],
            activities=[receive_ids],
            # The SDK Java test server can strand a sticky task at the old
            # worker. Require replay at the replacement rather than its cache.
            max_cached_workflows=0,
        )

    async with worker():
        handle = await temporal_env.client.start_workflow(
            DeliverAgentTaskCallbackWorkflow.run,
            args=[123, 7],
            id=f"agent-callback-restart-{uuid4()}",
            task_queue=queue,
        )
        await asyncio.wait_for(first_attempt.wait(), timeout=15)
        # Wait until the server, rather than the retiring worker, owns the timer.
        for _ in range(100):
            history = await handle.fetch_history()
            if any(event.HasField("timer_started_event_attributes") for event in history.events):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("completion backoff timer was not persisted")

    assert calls == [(123, 7)]
    async with worker():
        outcome = await asyncio.wait_for(handle.result(), timeout=45)
        history = await handle.fetch_history()
    assert outcome["state"] == "finished"
    assert outcome["attempts"] == 2
    assert calls == [(123, 7), (123, 7)]
    for event in history.events:
        if event.HasField("activity_task_scheduled_event_attributes"):
            payloads = event.activity_task_scheduled_event_attributes.input.payloads
            assert [payload.data for payload in payloads] == [b"123", b"7"]
    await Replayer(workflows=[DeliverAgentTaskCallbackWorkflow]).replay_workflow(history)
    assert calls == [(123, 7), (123, 7)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["FULL", "NOTIFY"])
async def test_production_activity_posts_tls_and_keeps_bodies_out_of_history_and_logs(
    temporal_env, callback_receiver, settings, caplog, mode
):
    from astrolift_agents.completion_webhook import verify_signature
    from astrolift_agents.models import AgentTask, AgentTaskCompletionCallback
    from astrolift_agents.services.task_completion_callbacks import configure_policy, set_callback_secret
    from astrolift_identity.models import Organization

    # The final transition persists the real encrypted outbox. This test starts
    # its workflow directly on the disposable server rather than production.
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    marker = f"sensitive-result-{uuid4()}"
    secret = "disposable-completion-signing-key-2234"
    callback_receiver.response_body = f"sensitive-response-{uuid4()}".encode()

    def dispatch_final():
        org = Organization.objects.create(name="Callback", slug=f"callback-{uuid4().hex}")
        configure_policy(org.pk, [callback_receiver.host])
        set_callback_secret(org.pk, "receiver", secret)
        task = AgentTask.objects.create(
            organization=org,
            status=AgentTask.Status.RUNNING,
            started_at=timezone.now() - timedelta(seconds=12),
            result={"output": {"marker": marker}, "exit_code": 0},
            completion_usage={"input_tokens": 12, "output_tokens": 7, "total_cost_usd": 0.03},
        )
        row = AgentTaskCompletionCallback.objects.create(
            organization=org,
            task=task,
            callback_url=callback_receiver.url,
            secret_ref="receiver",
            correlation_id="origin-result-2234",
            mode=mode,
        )
        task.transition_to(AgentTask.Status.COMPLETED)
        row.refresh_from_db()
        assert row.final_event_id is not None
        assert marker.encode() not in bytes(row.payload_ciphertext)
        return row

    row = await sync_to_async(dispatch_final)()
    queue = f"agent-callback-tls-{uuid4()}"
    async with temporal_worker(
        temporal_env,
        task_queue=queue,
        workflows=[DeliverAgentTaskCallbackWorkflow],
        activities=[deliver_agent_task_callback],
    ):
        handle = await temporal_env.client.start_workflow(
            DeliverAgentTaskCallbackWorkflow.run,
            args=[row.pk, row.generation],
            id=f"agent-callback-tls-{uuid4()}",
            task_queue=queue,
        )
        outcome = await asyncio.wait_for(handle.result(), timeout=20)
        history = await handle.fetch_history()
    assert outcome == {"state": "finished", "delay_seconds": 0.0, "attempts": 1}
    assert len(callback_receiver.requests) == 1
    headers, raw_body = callback_receiver.requests[0]
    assert headers["X-Astrolift-Event"] == "agent_task.finished"
    timestamp = headers["X-Astrolift-Timestamp"]
    assert verify_signature(
        secret.encode(),
        timestamp,
        raw_body,
        headers["X-Astrolift-Signature"],
        now=int(timezone.now().timestamp()),
    )
    body = json.loads(raw_body)
    assert body["correlation_id"] == "origin-result-2234"
    assert body["status"] == "completed"
    assert body["usage"]["input_tokens"] == 12
    assert body["usage"]["duration_ms"] >= 12000
    assert ("result" in body) == (mode == "FULL")
    if mode == "FULL":
        assert marker.encode() in raw_body
    else:
        assert marker.encode() not in raw_body
    await sync_to_async(row.refresh_from_db)()
    assert row.status == "delivered"
    assert row.attempts == 1
    assert bytes(row.payload_ciphertext) == b""
    history_bytes = b"".join(event.SerializeToString() for event in history.events)
    assert marker.encode() not in history_bytes
    assert callback_receiver.response_body not in history_bytes
    assert marker not in caplog.text
    assert callback_receiver.response_body.decode() not in caplog.text
    await Replayer(workflows=[DeliverAgentTaskCallbackWorkflow]).replay_workflow(history)
    assert len(callback_receiver.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_running", [False, True])
async def test_sync_client_joins_callback_execution_and_preserves_deploy_replacement(
    temporal_env, settings, monkeypatch, replace_running
):
    from astrolift_workflows import client as sync_client

    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    @activity.defn(name="astrolift.agent_task_callback.deliver")
    async def wait_for_receiver(callback_id: int, generation: int) -> dict:
        calls.append((callback_id, generation))
        started.set()
        await release.wait()
        return {"state": "finished", "delay_seconds": 0.0, "attempts": 1}

    queue = f"agent-callback-idempotency-{uuid4()}"
    monkeypatch.setattr(sync_client, "_client", temporal_env.client)
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    async with temporal_worker(
        temporal_env,
        task_queue=queue,
        workflows=[DeliverAgentTaskCallbackWorkflow],
        activities=[wait_for_receiver],
    ):
        invoke = sync_to_async(sync_client.start_workflow, thread_sensitive=False)
        first = await invoke(
            "DeliverAgentTaskCallbackWorkflow", [123, 7], workflow_id=queue, task_queue=queue
        )
        await asyncio.wait_for(started.wait(), timeout=15)
        second = await invoke(
            "DeliverAgentTaskCallbackWorkflow",
            [123, 7],
            workflow_id=queue,
            task_queue=queue,
            replace_running=replace_running,
        )
        assert first.enqueued and second.enqueued
        assert (first.run_id != second.run_id) == replace_running
        if replace_running:
            async with asyncio.timeout(15):
                while len(calls) != 2:
                    await asyncio.sleep(0.01)
        else:
            assert calls == [(123, 7)]
        release.set()
        handle = temporal_env.client.get_workflow_handle(queue, run_id=second.run_id)
        outcome = await asyncio.wait_for(handle.result(), timeout=15)
    assert outcome["state"] == "finished"
    assert calls == [(123, 7)] * (2 if replace_running else 1)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_real_outbox_reconciler_recovers_an_event_frozen_without_temporal(
    temporal_env, callback_receiver, settings, monkeypatch, org
):
    from astrolift_agents.models import AgentTask, AgentTaskCompletionCallback
    from astrolift_agents.services.task_completion_callbacks import configure_policy, set_callback_secret
    from astrolift_workflows import client as sync_client

    settings.ASTROLIFT_TEMPORAL_ENABLED = False

    def complete_while_offline():
        configure_policy(org.pk, [callback_receiver.host])
        set_callback_secret(org.pk, "origin", "disposable-origin-completion-key-2234")
        task = AgentTask.objects.create(
            organization=org,
            status=AgentTask.Status.RUNNING,
            started_at=timezone.now(),
            result={"output": {"ready": True}},
        )
        callback = AgentTaskCompletionCallback.objects.create(
            task=task,
            organization=org,
            callback_url=callback_receiver.url,
            secret_ref="origin",
        )
        task.transition_to(AgentTask.Status.COMPLETED)
        callback.refresh_from_db()
        assert callback.status == "pending"
        assert callback.final_event_id is not None
        assert callback.payload_ciphertext
        return callback, task.guid

    row, task_guid = await sync_to_async(complete_while_offline)()
    assert not callback_receiver.requests
    queue = f"agent-callback-reconcile-{uuid4()}"
    monkeypatch.setattr(sync_client, "_client", temporal_env.client)
    settings.ASTROLIFT_TEMPORAL_ENABLED = True
    settings.TEMPORAL_TASK_QUEUE = queue
    async with temporal_worker(
        temporal_env,
        task_queue=queue,
        workflows=[AgentTaskCallbackReconcileWorkflow, DeliverAgentTaskCallbackWorkflow],
        activities=[reconcile_agent_task_callbacks, deliver_agent_task_callback],
    ):
        sweep = await temporal_env.client.start_workflow(
            AgentTaskCallbackReconcileWorkflow.run,
            id=f"agent-callback-reconcile-sweep-{uuid4()}",
            task_queue=queue,
        )
        assert await asyncio.wait_for(sweep.result(), timeout=20) == 1
        delivery = temporal_env.client.get_workflow_handle(
            f"DeliverAgentTaskCallbackWorkflow-{task_guid}-{row.generation}"
        )
        outcome = await asyncio.wait_for(delivery.result(), timeout=20)
        history = await delivery.fetch_history()
    assert outcome["state"] == "finished"
    assert outcome["attempts"] == 1
    assert len(callback_receiver.requests) == 1
    await sync_to_async(row.refresh_from_db)()
    assert row.status == "delivered"
    assert not row.payload_ciphertext
    assert json.loads(callback_receiver.requests[0][1])["task_id"] == str(task_guid)
    await Replayer(workflows=[DeliverAgentTaskCallbackWorkflow]).replay_workflow(history)
    assert len(callback_receiver.requests) == 1


@pytest.mark.asyncio
async def test_worker_activity_failure_resumes_delivery_without_automatic_http_retry(temporal_env):
    attempts = []

    @activity.defn(name="astrolift.agent_task_callback.deliver")
    async def crash_once(callback_id: int, generation: int) -> dict:
        attempts.append((callback_id, generation))
        if len(attempts) == 1:
            raise RuntimeError("disposable worker interrupted")
        return {"state": "finished", "delay_seconds": 0.0, "attempts": 1}

    queue = f"agent-callback-crash-{uuid4()}"
    async with temporal_worker(
        temporal_env,
        task_queue=queue,
        workflows=[DeliverAgentTaskCallbackWorkflow],
        activities=[crash_once],
    ):
        handle = await temporal_env.client.start_workflow(
            DeliverAgentTaskCallbackWorkflow.run,
            args=[123, 7],
            id=f"agent-callback-crash-{uuid4()}",
            task_queue=queue,
        )
        outcome = await asyncio.wait_for(handle.result(), timeout=15)
        history = await handle.fetch_history()
    assert outcome["state"] == "finished"
    assert attempts == [(123, 7), (123, 7)]
    scheduled = [
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]
    assert len(scheduled) == 2
    assert all(item.retry_policy.maximum_attempts == 1 for item in scheduled)
    assert any(event.HasField("timer_started_event_attributes") for event in history.events)
    await Replayer(workflows=[DeliverAgentTaskCallbackWorkflow]).replay_workflow(history)
