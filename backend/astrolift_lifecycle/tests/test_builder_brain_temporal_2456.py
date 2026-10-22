"""Real PostgreSQL/Temporal with native SDK doubles, no cloud/runtime writes."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from temporalio import activity, workflow
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from astrolift_lifecycle.builder_brain_dispatch import payload
from astrolift_lifecycle.models import BuilderBrainBuild
from astrolift_lifecycle.tests import test_builder_brain_source_2456 as fixtures
from astrolift_lifecycle.tests.test_builder_brain_source_2456 import (
    create,
    host,
)
from astrolift_workflows import client
from astrolift_workflows.activities.builder_brain_source import build_brain_source
from astrolift_workflows.inputs import DeployAppInput
from astrolift_workflows.workflows.builder_brain_source import BuilderBrainSourceBuildWorkflow

pytestmark = pytest.mark.django_db(transaction=True)
org = fixtures.org
team = fixtures.team
user = fixtures.user
provider_plugin = fixtures.provider_plugin
cluster = fixtures.cluster
api_token = fixtures.api_token
auth_headers = fixtures.auth_headers
world = fixtures.world


def test_recorded_attempt_without_retained_history_never_resubmits(world, monkeypatch):
    reply = create(world)
    assert reply.status_code == 202, reply.content
    row = BuilderBrainBuild.objects.get()
    assert row.build_first_submission_at is not None and row.build_run_id
    attempted = []
    monkeypatch.setattr(client, "recover_workflow_once", lambda *a, **k: None)
    monkeypatch.setattr(client, "start_workflow_once", lambda *a, **k: attempted.append(k))
    repeated = create(world)
    assert repeated.status_code == 202, repeated.content
    assert repeated.json()["failure_code"] == "EXECUTION_RECOVERY_REQUIRED"
    assert repeated.json()["build_submission"] == "refused"
    assert attempted == []
    assert BuilderBrainBuild.objects.count() == 1


@workflow.defn(name="DeployAppWorkflow", sandboxed=False)
class AcceptedDeploymentControl:
    @workflow.run
    async def run(self, value: DeployAppInput) -> dict:
        return await workflow.execute_activity(
            "builder2456.fixture_effect",
            value.deployment_id,
            start_to_close_timeout=timedelta(seconds=10),
        )


async def test_lost_build_and_host_ack_recover_same_closed_executions_without_extra_effects(
    world,
    temporal_env,
    monkeypatch,
    settings,
):
    queue = "builder-brain-2456-" + str(uuid4())
    calls = []

    @activity.defn(name="builder2456.fixture_effect")
    async def effect(deployment_id: int) -> dict:
        calls.append(deployment_id)
        return {"synthetic": True, "deployment_id": deployment_id}

    async def get_client():
        return temporal_env.client

    monkeypatch.setattr(client, "_get_client_async", get_client)
    monkeypatch.setattr(client, "_temporal_enabled", lambda: True)
    monkeypatch.setattr(client, "_task_queue", lambda: queue)
    monkeypatch.setattr(client, "recover_workflow_once", world.original_recover)
    monkeypatch.setattr(client, "describe_workflow_instance", world.original_describe)
    monkeypatch.setattr("astrolift_registry.expiry.arm", lambda *a: None)
    attempted = []

    def lose_ack(name, args, **kwargs):
        attempted.append((name, kwargs["workflow_id"]))
        result = world.original_once(name, args, **kwargs)
        assert result.run_id
        raise TimeoutError("synthetic accepted start response was lost")

    monkeypatch.setattr(client, "start_workflow_once", lose_ack)
    worker = Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[BuilderBrainSourceBuildWorkflow, AcceptedDeploymentControl],
        activities=[build_brain_source, effect],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    async with worker:
        replies = await asyncio.gather(
            sync_to_async(create, thread_sensitive=False)(world),
            sync_to_async(create, thread_sensitive=False)(world),
        )
        assert all(reply.status_code == 202 for reply in replies), [reply.content for reply in replies]
        assert replies[0].json()["id"] == replies[1].json()["id"]
        row = await sync_to_async(BuilderBrainBuild.objects.get)()
        assert row.build_first_submission_at is not None
        identity = f"BuilderBrainSourceBuildWorkflow-{row.guid}"
        await asyncio.wait_for(temporal_env.client.get_workflow_handle(identity).result(), timeout=30)
        recovered = await sync_to_async(create)(world)
        assert recovered.status_code == 202, recovered.content
        assert recovered.json()["status"] == "artifact_ready"
        assert recovered.json()["build_submission"] == "enqueued"
        assert recovered.json()["build_execution_status"] == "COMPLETED"
        row = await sync_to_async(BuilderBrainBuild.objects.get)()
        build_run = row.build_run_id
        hosted = await sync_to_async(host)(world, row)
        assert hosted.status_code == 202, hosted.content
        assert hosted.json()["hosting_status"] == "created"
        assert hosted.json()["deployment_submission"] == "uncertain"
        assert hosted.json()["runtime_verified"] is False
        row = await sync_to_async(BuilderBrainBuild.objects.get)()
        assert row.dispatch_ciphertext and row.deployment_first_submission_at
        await asyncio.wait_for(
            temporal_env.client.get_workflow_handle(row.deployment_workflow_id).result(), timeout=30
        )
        repeated = await sync_to_async(host)(world, row)
        assert repeated.status_code == 202, repeated.content
        assert repeated.json()["deployment_submission"] == "enqueued"
        assert repeated.json()["deployment_execution_status"] == "COMPLETED"
        assert repeated.json()["runtime_verified"] is False
        row = await sync_to_async(BuilderBrainBuild.objects.get)()
        original_run = row.deployment_run_id
        again = await sync_to_async(host)(world, row)
        assert again.status_code == 202
        row = await sync_to_async(BuilderBrainBuild.objects.get)()
        assert row.deployment_run_id == original_run and row.build_run_id == build_run
        assert len(calls) == 1
        assert [name for name, identity in attempted].count("BuilderBrainSourceBuildWorkflow") == 1
        assert [name for name, identity in attempted].count("DeployAppWorkflow") == 1
        assert await sync_to_async(BuilderBrainBuild.objects.count)() == 1
        _, args = await sync_to_async(payload)(row)
        wrong = replace(args[0], image_tags={"app": "different"})
        with pytest.raises(RuntimeError, match="inputs differ"):
            await sync_to_async(world.original_once)(
                "DeployAppWorkflow", [wrong], workflow_id=row.deployment_workflow_id, task_queue=queue
            )
        assert len(calls) == 1


@workflow.defn(name="Builder2456HeartbeatControl", sandboxed=False)
class HeartbeatBuildControl:
    @workflow.run
    async def run(self, value: int) -> dict:
        from temporalio.common import RetryPolicy

        return await workflow.execute_activity(
            build_brain_source,
            value,
            start_to_close_timeout=timedelta(seconds=5),
            heartbeat_timeout=timedelta(milliseconds=200),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


async def test_long_native_build_keeps_activity_heartbeat_alive(world, temporal_env, monkeypatch):
    import time

    from astrolift_lifecycle import builder_brain_source
    from astrolift_workflows import activity_heartbeat

    calls = []

    def bounded_native_control(intent_id):
        assert BuilderBrainBuild.objects.filter(pk=intent_id).exists()
        calls.append(intent_id)
        time.sleep(0.65)
        return {"ok": True, "synthetic_native_control": True}

    assert await sync_to_async(create)(world)
    intent_id = await sync_to_async(lambda: BuilderBrainBuild.objects.get().pk)()
    monkeypatch.setattr(builder_brain_source, "build", bounded_native_control)
    monkeypatch.setattr(activity_heartbeat, "HEARTBEAT_EVERY", timedelta(seconds=0.03))
    queue = "builder-brain-heartbeat-2456-" + str(uuid4())
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[HeartbeatBuildControl],
        activities=[build_brain_source],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        result = await temporal_env.client.execute_workflow(
            HeartbeatBuildControl.run,
            intent_id,
            id=queue,
            task_queue=queue,
        )
    assert result == {"ok": True, "synthetic_native_control": True}
    assert calls == [intent_id]
