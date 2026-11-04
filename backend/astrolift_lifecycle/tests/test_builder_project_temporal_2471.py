"""Project intents on real PostgreSQL/Temporal (#2471): recovery and concurrency.

Native SDK doubles only; nothing contacts a cloud or a runtime.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.db import close_old_connections
from temporalio import activity
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from astrolift_lifecycle import builder_brain_source as source
from astrolift_lifecycle.builder_brain_dispatch import payload
from astrolift_lifecycle.models import BuilderBrainBuild, Deployment
from astrolift_lifecycle.tests import test_builder_brain_source_2456 as fixtures
from astrolift_lifecycle.tests.test_builder_brain_temporal_2456 import AcceptedDeploymentControl
from astrolift_lifecycle.tests.test_builder_project_source_2471 import create, host
from astrolift_registry.models import BrainAppBinding, RegisteredApp
from astrolift_workflows import client
from astrolift_workflows.activities.builder_brain_source import build_brain_source
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


def test_recorded_project_attempt_without_retained_history_never_resubmits(world, monkeypatch):
    reply = create(world)
    assert reply.status_code == 202, reply.content
    row = BuilderBrainBuild.objects.get()
    assert row.hosting_mode == "project" and row.build_first_submission_at is not None and row.build_run_id
    attempted = []
    monkeypatch.setattr(client, "recover_workflow_once", lambda *a, **k: None)
    monkeypatch.setattr(client, "start_workflow_once", lambda *a, **k: attempted.append(k))
    repeated = create(world)
    assert repeated.status_code == 202, repeated.content
    assert repeated.json()["failure_code"] == "EXECUTION_RECOVERY_REQUIRED"
    assert repeated.json()["build_submission"] == "refused"
    assert attempted == []
    assert BuilderBrainBuild.objects.count() == 1


async def test_lost_project_build_and_host_acks_recover_the_same_executions(
    world, temporal_env, monkeypatch, settings
):
    queue = "builder-project-2471-" + str(uuid4())
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
        assert replies[0].json()["protocol"] == "builder-project-source-v1"
        row = await sync_to_async(BuilderBrainBuild.objects.get)()
        assert row.hosting_mode == "project" and row.brain_id is None
        identity = f"BuilderBrainSourceBuildWorkflow-{row.guid}"
        await asyncio.wait_for(temporal_env.client.get_workflow_handle(identity).result(), timeout=30)
        recovered = await sync_to_async(create)(world)
        assert recovered.status_code == 202, recovered.content
        assert recovered.json()["status"] == "artifact_ready"
        assert recovered.json()["build_submission"] == "enqueued"
        assert recovered.json()["build_execution_status"] == "COMPLETED"
        assert recovered.json()["hosting_status"] == "not_created"
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
        assert not await sync_to_async(BrainAppBinding.objects.exists)()
        _, args = await sync_to_async(payload)(row)
        assert args[0].registered_app_id == row.hosted_app_id
        assert args[0].image_tags == {"app": source._tag(row)}
        wrong = replace(args[0], image_tags={"app": "different"})
        with pytest.raises(RuntimeError, match="inputs differ"):
            await sync_to_async(world.original_once)(
                "DeployAppWorkflow", [wrong], workflow_id=row.deployment_workflow_id, task_queue=queue
            )
        assert len(calls) == 1


def test_concurrent_identical_project_requests_keep_one_intent_app_and_deployment(world, monkeypatch):
    monkeypatch.setattr("astrolift_registry.expiry.arm", lambda *a: None)

    def call(fn, *args):
        close_old_connections()
        try:
            return fn(*args)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        created = list(pool.map(lambda _: call(create, world), range(2)))
    assert [reply.status_code for reply in created] == [202, 202], [reply.content for reply in created]
    assert created[0].json()["id"] == created[1].json()["id"]
    row = BuilderBrainBuild.objects.get()
    source.build(row.pk)
    row.refresh_from_db()
    with ThreadPoolExecutor(max_workers=2) as pool:
        hosted = list(pool.map(lambda _: call(host, world, row), range(2)))
    assert [reply.status_code for reply in hosted] == [202, 202], [reply.content for reply in hosted]
    assert hosted[0].json()["app_guid"] == hosted[1].json()["app_guid"]
    assert hosted[0].json()["deployment_guid"] == hosted[1].json()["deployment_guid"]
    assert RegisteredApp.objects.filter(organization=world.org, slug="source-app").count() == 1
    assert Deployment.objects.filter(registered_app__slug="source-app").count() == 1
    started = [name for name, _, _ in world.starts]
    assert started.count("BuilderBrainSourceBuildWorkflow") == 1
    assert started.count("DeployAppWorkflow") == 1
