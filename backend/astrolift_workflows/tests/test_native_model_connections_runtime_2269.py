"""Actual Temporal worker/history with real PG intents and native SDK boundaries."""

import json
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async

from astrolift_services.models import ManagedServiceAttachment
from astrolift_services.tests.test_bedrock_subscription_reconcile_2269 import (
    SUBSCRIBE,
    finish,
    public,
    reconcile,
    revoke,
    runtime,  # noqa: F401 -- real PG and protocol boundary fixture
    subscribe,
    subscription,
    world,  # noqa: F401 -- dependency of the shared runtime fixture
)
from astrolift_services.tests.test_bedrock_subscription_reconcile_2269 import (
    queue_fixture as queue,  # noqa: F401 -- fixture prepares real HTTP intents
)
from astrolift_services.tests.test_model_connection_2270 import graphql_http
from astrolift_workflows.inputs import Actor, SharedModelReconcileInput
from astrolift_workflows.workflows.native_model_connections import NativeModelConnectionReconcileWorkflow
from core.testing.temporal import temporal_worker

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def prepared(runtime, client, queue):  # noqa: F811 -- imported pytest fixtures
    row, _ = subscribe(runtime, client)
    return runtime, row


async def run_workflow(env, w, *, revision=None, action="apply", delete_data=False):
    from astrolift_workflows.worker import ACTIVITIES, WORKFLOWS

    activities = [
        reconcile.apply_native_model_connection,
        reconcile.finish_native_model_connection,
        reconcile.fail_native_model_connection,
    ]
    assert NativeModelConnectionReconcileWorkflow in WORKFLOWS
    assert set(activities).issubset(ACTIVITIES)
    task_queue = f"native-bedrock-2269-{uuid4().hex}"
    workflow_id = f"native-bedrock-2269-{uuid4().hex}"
    async with temporal_worker(
        env, task_queue=task_queue, workflows=[NativeModelConnectionReconcileWorkflow], activities=activities
    ):
        result = await env.client.execute_workflow(
            NativeModelConnectionReconcileWorkflow.run,
            SharedModelReconcileInput(
                w.model.pk,
                w.model.subscription_revision if revision is None else revision,
                Actor(kind="system"),
                action,
                delete_data,
            ),
            id=workflow_id,
            task_queue=task_queue,
        )
        history = (await env.client.get_workflow_handle(workflow_id).fetch_history()).to_json()
        assert "synthetic-secret" not in history and "synthetic-key" not in history
        w.workflow_history = json.loads(history)
        return result


async def test_actual_worker_observes_binding_without_invoke_readiness(temporal_env, prepared):
    w, row = prepared
    w.driver.converge = True
    result = await run_workflow(temporal_env, w)
    assert result.ok and "invocation access remains unverified" in result.message
    await sync_to_async(row.refresh_from_db)()
    await sync_to_async(w.model.refresh_from_db)()
    assert row.subscription_status == "active" and row.applied_revision == row.desired_revision
    assert w.model.applied_subscription_revision == w.model.subscription_revision
    assert w.model.model_ready_observed_at is None


async def test_actual_worker_observation_timeout_is_bounded_and_failed(temporal_env, prepared, monkeypatch):
    w, row = prepared
    observed = []
    original = reconcile._finish_sync

    def count(*args):
        observed.append(args)
        return original(*args)

    monkeypatch.setattr(reconcile, "_finish_sync", count)
    result = await run_workflow(temporal_env, w)
    assert not result.ok and len(observed) == 60
    await sync_to_async(row.refresh_from_db)()
    await sync_to_async(w.model.refresh_from_db)()
    assert row.subscription_status == "failed" and row.applied_revision == 0
    assert w.model.status == "failed" and w.model.applied_subscription_revision == 0


@pytest.mark.parametrize("mode", ["stale_revision", "provider_error", "delete", "delete_data"])
async def test_actual_worker_refuses_stale_error_and_external_deletion(temporal_env, prepared, mode):
    w, row = prepared
    old_revision = w.model.subscription_revision
    if mode == "stale_revision":
        w.model.subscription_revision += 1
        await sync_to_async(w.model.save)()
    elif mode == "provider_error":
        w.driver.rejected = True
    result = await run_workflow(
        temporal_env,
        w,
        revision=old_revision,
        action="delete" if mode == "delete" else "apply",
        delete_data=mode == "delete_data",
    )
    assert not result.ok
    await sync_to_async(row.refresh_from_db)()
    await sync_to_async(w.model.refresh_from_db)()
    assert row.applied_revision == 0
    if mode == "stale_revision":
        assert row.subscription_status == "pending" and w.model.status == "updating"
        assert not w.iam.effects and not w.driver.writes
    else:
        assert row.subscription_status == "failed" and w.model.status == "failed"
    if mode in ("delete", "delete_data"):
        assert not w.iam.effects and not w.driver.writes


@pytest.fixture
def mixed(prepared, client):
    from dataclasses import asdict

    from aws.bedrock_catalogue import BedrockCatalogueDetail, CatalogueState

    w, first = prepared
    reconcile._apply_sync(w.model.pk, first.desired_revision)
    finish(w, first)
    result = graphql_http(
        client, w.app_headers, SUBSCRIBE, {"input": public(asdict(subscription(w, alias="alternate")))}
    )
    assert result["data"]["subscribeClusterModel"]["ok"], result
    second = ManagedServiceAttachment.objects.get(
        guid=result["data"]["subscribeClusterModel"]["data"]["subscription"]["id"]
    )
    w.model.refresh_from_db()
    reconcile._apply_sync(w.model.pk, second.desired_revision)
    finish(w, second)
    first.refresh_from_db()
    w.detail = BedrockCatalogueDetail(CatalogueState.NOT_FOUND)
    revoke(w, first, client)
    return w, first, second


async def test_actual_worker_mixed_removal_success_does_not_admit_retained_source(temporal_env, mixed):
    w, first, second = mixed
    prior_applied = second.applied_revision
    result = await run_workflow(temporal_env, w)
    assert result.ok and "inspect retained-source admission separately" in result.message
    await sync_to_async(first.refresh_from_db)()
    await sync_to_async(second.refresh_from_db)()
    await sync_to_async(w.model.refresh_from_db)()
    assert first.subscription_status == "revoked" and first.applied_revision == first.desired_revision
    assert second.subscription_status == "failed" and second.applied_revision == prior_applied
    assert (
        w.model.status == "failed" and w.model.applied_subscription_revision < w.model.subscription_revision
    )
    assert not w.iam.policies
