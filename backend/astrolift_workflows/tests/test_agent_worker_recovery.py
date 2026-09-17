from __future__ import annotations

import asyncio
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.db import connection
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from astrolift_agents.models import AgentTask
from astrolift_dispatch.spawners import registry
from astrolift_dispatch.spawners.base import SpawnResult, TaskStatus
from astrolift_dispatch.spawners.k8s_job import K8sJobSpawner
from astrolift_workflows.activities import agent_stage
from astrolift_workflows.inputs import Actor, DispatchAgentTaskInput
from astrolift_workflows.workflows.dispatch_agent_task import DispatchAgentTaskWorkflow


@workflow.defn(sandboxed=False)
class StageRecoveryWorkflow:
    @workflow.run
    async def run(self, params: dict) -> dict:
        return await workflow.execute_activity(
            agent_stage.execute_agent_stage,
            params,
            start_to_close_timeout=timedelta(minutes=1),
            heartbeat_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1)),
        )


@pytest.mark.django_db
@pytest.mark.parametrize("existing", ["owned", "foreign", "deleting", "wrong_token", "unreadable"])
@pytest.mark.parametrize("status", [AgentTask.Status.PROVISIONING, AgentTask.Status.RUNNING])
def test_interrupted_spawn_adopts_only_its_authenticated_job(org, monkeypatch, existing, status):
    from core import cluster_management

    token = "disposable-test-callback"
    digest = hashlib.sha256(token.encode()).hexdigest()
    task = AgentTask.objects.create(organization=org, status=status, callback_token_hash=digest)
    name = f"agent-task-{str(task.guid).replace('-', '')[:12]}"
    manifest = {
        "metadata": {"labels": {"astrolift.dev/task-id": str(task.guid)}},
        "spec": {
            "template": {
                "spec": {"containers": [{"env": [{"name": "ASTROLIFT_CLUSTER_KEY", "value": token}]}]}
            }
        },
    }
    if existing == "foreign":
        manifest["metadata"]["labels"]["astrolift.dev/task-id"] = str(uuid4())
    elif existing == "deleting":
        manifest["metadata"]["deletionTimestamp"] = "2026-09-17T00:00:00Z"
    elif existing == "wrong_token":
        task.callback_token_hash = "different"
        task.save(update_fields=["callback_token_hash"])

    def read_job(*args):
        assert args[2:] == ("Job", name)
        if existing == "unreadable":
            raise ConnectionError("temporary cluster outage")
        return manifest

    spawner = K8sJobSpawner(cluster=object(), namespace="astrolift-agents-test")
    monkeypatch.setattr(registry, "get_spawner", lambda *args, **kwargs: spawner)
    monkeypatch.setattr(agent_stage, "_resolve_managed_cluster", lambda org: object())
    monkeypatch.setattr(cluster_management, "_context_for_cluster", lambda _: SimpleNamespace(slug="test"))
    monkeypatch.setattr(
        cluster_management, "_driver_for_cluster", lambda _: SimpleNamespace(get_manifest=read_job)
    )
    monkeypatch.setattr(spawner, "spawn", lambda _: pytest.fail("must not respawn an existing Job"))
    if existing == "unreadable":
        with pytest.raises(ConnectionError):
            agent_stage._spawn_agent_task_sync(task.pk)
        task.refresh_from_db()
        assert task.status == status
        assert task.callback_token_hash == digest
        return
    result = agent_stage._spawn_agent_task_sync(task.pk)
    task.refresh_from_db()
    if existing == "owned":
        assert result["ok"]
        assert task.external_id == name
        assert task.callback_token_hash == digest
    else:
        assert not result["ok"]
        assert task.status == AgentTask.Status.FAILED


@pytest.mark.django_db(transaction=True)
def test_overlapping_spawn_attempt_retries_without_mutating_task(org):
    task = AgentTask.objects.create(organization=org, status=AgentTask.Status.QUEUED)
    key = f"agent-spawn:{task.pk}"

    def competing_attempt():
        try:
            return agent_stage._spawn_agent_task_sync(task.pk)
        finally:
            connection.close()

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", [key])
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                with pytest.raises(RuntimeError, match="Another activity attempt"):
                    executor.submit(competing_attempt).result(timeout=5)
        finally:
            cursor.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", [key])
    task.refresh_from_db()
    assert task.status == AgentTask.Status.QUEUED
    assert not task.external_id


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("operator_stop", [False, True])
@pytest.mark.parametrize("kind", ["registered", "stage"])
async def test_worker_replacement_keeps_task_and_container(
    temporal_env, org, monkeypatch, operator_stop, kind
):
    polling = threading.Event()
    completed = threading.Event()
    spawned = []
    stopped = []

    class Spawner:
        def spawn(self, task):
            spawned.append(task.pk)
            task.callback_token_hash = "retained-token-hash"
            task.save(update_fields=["callback_token_hash"])
            return SpawnResult(external_id="existing-agent-job")

        def status(self, external_id):
            assert external_id == "existing-agent-job"
            polling.set()
            return TaskStatus(running=not completed.is_set(), succeeded=completed.is_set())

        def stop(self, external_id):
            stopped.append(external_id)

        def cleanup_task_secret(self, external_id):
            pass

    monkeypatch.setattr(registry, "get_spawner", lambda *args, **kwargs: Spawner())
    monkeypatch.setattr(agent_stage, "_resolve_managed_cluster", lambda org: object())
    monkeypatch.setattr(agent_stage, "_fatal_pod_wait_reason", lambda *args: "")
    monkeypatch.setattr(agent_stage, "_POLL_INTERVAL_SECONDS", 0.05)
    if kind == "registered":
        task = await sync_to_async(AgentTask.objects.create)(organization=org, status=AgentTask.Status.QUEUED)
    queue = f"agent-recovery-{uuid4()}"

    def worker():
        return Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[DispatchAgentTaskWorkflow, StageRecoveryWorkflow],
            activities=[agent_stage.dispatch_agent_task, agent_stage.execute_agent_stage],
            max_heartbeat_throttle_interval=timedelta(milliseconds=50),
            # Require the replacement to replay durable history. The Java test
            # server can strand a sticky workflow task at the stopped worker.
            max_cached_workflows=0,
        )

    async with worker():
        if kind == "registered":
            handle = await temporal_env.client.start_workflow(
                DispatchAgentTaskWorkflow.run,
                DispatchAgentTaskInput(agent_task_id=task.pk, actor=Actor(kind="system")),
                id=queue,
                task_queue=queue,
            )
        else:
            handle = await temporal_env.client.start_workflow(
                StageRecoveryWorkflow.run,
                {"org_slug": org.slug, "output_key": "result"},
                id=queue,
                task_queue=queue,
            )
        assert await asyncio.to_thread(polling.wait, 15)
        task = await sync_to_async(AgentTask.objects.get)(pk=spawned[0])
        if operator_stop:
            await handle.cancel()
            async with asyncio.timeout(15):
                while not stopped:
                    await asyncio.sleep(0.05)
    await sync_to_async(task.refresh_from_db)()
    if operator_stop:
        assert stopped == ["existing-agent-job"]
        assert task.status == AgentTask.Status.CANCELLED
        assert not task.callback_token_hash
        return
    assert not stopped
    assert task.status == AgentTask.Status.RUNNING
    assert task.callback_token_hash == "retained-token-hash"
    completed.set()
    async with worker():
        result = await asyncio.wait_for(handle.result(), 30)
    if kind == "registered":
        assert result.ok
        assert result.data["task_guid"] == str(task.guid)
    else:
        assert result["status"] == AgentTask.Status.COMPLETED
        assert result["task_guid"] == str(task.guid)
    assert spawned == [task.pk]
    assert not stopped
