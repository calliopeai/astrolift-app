"""Actual public capture, worker/callee admission and deterministic history replay."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from temporalio.worker import Replayer, Worker

from astrolift_lifecycle.tests.test_native_deployment_origin_2278 import start_public
from astrolift_lifecycle.tests.test_native_deployment_origin_2278 import world as public_world
from astrolift_workflows.activities.deployment_identity_origin import (
    mark_deployment_identity_refused,
    validate_deployment_identity_origin,
)
from astrolift_workflows.workflows.deploy_app import DeployAppWorkflow

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world(monkeypatch):
    return public_world.__wrapped__(monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "lost_reference", "token_withdrawal", "retarget"])
async def test_actual_start_and_worker_refuse_before_any_downstream_activity(
    world, client, temporal_env, change
):
    from astrolift_lifecycle.models import Deployment, DeploymentExecutionReceipt

    result = await sync_to_async(start_public)(world, client)
    assert result["ok"], result["errors"]
    original = world.starts[0][1]
    payload = original
    if change == "lost_reference":
        payload = replace(original, identity_authority=None)
    elif change == "retarget":
        payload = replace(original, app_environment_id=original.app_environment_id + 999999)
    elif change == "token_withdrawal":

        def revoke():
            world.token.is_revoked = True
            world.token.save()

        await sync_to_async(revoke)()
    queue = "deployment-original-authority-" + uuid4().hex
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
                id=await sync_to_async(
                    lambda: DeploymentExecutionReceipt.objects.get(
                        deployment_id=original.deployment_id, kind="EXPECTED"
                    ).workflow_id
                )(),
                task_queue=queue,
            )
            outcome = await handle.result()
            history = await handle.fetch_history()
    assert not outcome.ok
    assert ("NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED" in outcome.message) == (change == "none")
    scheduled = [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]
    assert scheduled == ["validate_deployment_identity_origin", "mark_deployment_identity_refused"]
    assert world.headers["HTTP_AUTHORIZATION"].encode() not in history.to_json().encode()
    await Replayer(workflows=[DeployAppWorkflow]).replay_workflow(history)
    row = await sync_to_async(Deployment.objects.get)(pk=original.deployment_id)
    assert row.status == ("pending" if change in ("retarget", "lost_reference") else "failed")
    assert row.config_snapshot == {}
    assert row.aborted_reason == (
        ""
        if change in ("retarget", "lost_reference")
        else "NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED"
        if change == "none"
        else "DEPLOYMENT_ORIGIN_REFUSED"
    )


def test_both_actual_activities_are_registered_once():
    from astrolift_workflows import worker

    assert worker.ACTIVITIES.count(validate_deployment_identity_origin) == 1
    assert worker.ACTIVITIES.count(mark_deployment_identity_refused) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bearer", "browser"])
async def test_actual_http_quorum_uses_canonical_start_and_real_worker(
    world, client, temporal_env, monkeypatch, kind
):
    from astrolift_identity.models import Policy
    from astrolift_lifecycle.models import Deployment
    from astrolift_lifecycle.tests.test_native_deployment_origin_2278 import APPROVE, reviewer
    from astrolift_services.tests.test_model_connection_2270 import graphql_http
    from astrolift_workflows import client as native_client
    from astrolift_workflows.inputs import WorkflowResult

    queue = "native-public-start-" + uuid4().hex

    async def current_client():
        return temporal_env.client

    monkeypatch.setattr(native_client, "_get_client_async", current_client)
    monkeypatch.setattr(native_client, "_temporal_enabled", lambda: True)
    monkeypatch.setattr(native_client, "_task_queue", lambda: queue)
    monkeypatch.setattr(
        "astrolift_lifecycle.schema.mutations.helpers.start_workflow", native_client.start_workflow
    )

    def request_and_approve():
        if kind == "browser":
            client.force_login(world.user)
            world.headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid), "HTTP_X_PLATFORM": "web"}
            graphql_http(client, world.headers, "query{__typename}", {})
        world.env.required_approvals = 1
        world.env.save()
        result = start_public(world, client)
        assert result["ok"], result["errors"]
        row = Deployment.objects.get(guid=result["data"]["id"])
        assert row.workflow_run_id is None
        _, headers = reviewer(world, "canonical-quorum-")
        Policy.objects.create(
            organization=world.org,
            slug="canonical-original-quorum",
            effect="ALLOW",
            action_pattern="app.deploy",
            scope_level="APP",
            scope_id=world.medops_app.pk,
            conditions=[{"kind": "approval_required", "min_approvers": 1}],
        )
        approved = graphql_http(client, headers, APPROVE, {"input": {"id": str(row.guid)}})["data"][
            "approveDeployment"
        ]
        assert approved["ok"], approved["errors"]
        row.refresh_from_db()
        assert row.workflow_run.trigger_actor_user_id == world.user.pk
        return row.pk, row.workflow_run.workflow_id, row.workflow_run.run_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[DeployAppWorkflow],
            activities=[validate_deployment_identity_origin, mark_deployment_identity_refused],
            activity_executor=executor,
        ):
            pk, workflow_id, run_id = await sync_to_async(request_and_approve)()
            handle = temporal_env.client.get_workflow_handle(
                workflow_id, run_id=run_id, result_type=WorkflowResult
            )
            result = await handle.result()
            history = await handle.fetch_history()
    assert not result.ok and "NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED" in result.message
    scheduled = [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]
    assert scheduled == ["validate_deployment_identity_origin", "mark_deployment_identity_refused"]
    row = await sync_to_async(Deployment.objects.get)(pk=pk)
    assert row.status == "failed" and row.aborted_reason == "NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED"
    await Replayer(workflows=[DeployAppWorkflow]).replay_workflow(history)
