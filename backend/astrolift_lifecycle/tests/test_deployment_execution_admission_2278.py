"""Protected deployment votes are execution facts, never pre-vote deferral."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from temporalio.worker import Replayer, Worker

from astrolift_identity.models import Policy
from astrolift_lifecycle.deployment_identity_origin import (
    DeploymentOriginError,
    deployment_app_identity_authority,
    deployment_origin,
)
from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.tests.test_native_deployment_origin_2278 import APPROVE, reviewer, start_public
from astrolift_lifecycle.tests.test_native_deployment_origin_2278 import world as public_world
from astrolift_services.native_identity_authority import current_app_identity_authority
from astrolift_services.tests.test_model_connection_2270 import graphql_http
from astrolift_workflows.activities.deployment_identity_origin import (
    mark_deployment_identity_refused,
    validate_deployment_identity_origin,
)
from astrolift_workflows.native_identity_inputs import DeploymentAuthorityContext
from astrolift_workflows.workflows.deploy_app import DeployAppWorkflow
from core.permissions import PermissionDenied

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world(monkeypatch):
    return public_world.__wrapped__(monkeypatch)


def pending(w, client):
    w.env.required_approvals = 1
    w.env.save()
    result = start_public(w, client)
    assert result["ok"], result["errors"]
    row = Deployment.objects.get(guid=result["data"]["id"])
    assert row.status == "pending_approval" and not row.approval_votes.exists() and not w.starts
    return row, deployment_origin(row)


def policy(w, minimum):
    return Policy.objects.create(
        organization=w.org,
        slug="execution-approval-policy",
        effect="ALLOW",
        action_pattern="app.deploy",
        scope_level="APP",
        scope_id=w.medops_app.pk,
        conditions=[{"kind": "approval_required", "min_approvers": minimum}],
    )


def test_execution_refuses_pending_but_actual_pre_vote_deferral_remains(world, client):
    row, ref = pending(world, client)
    policy(world, 1)
    with current_app_identity_authority(ref, deployment_guid=row.guid):
        pass
    with pytest.raises(DeploymentOriginError, match="DEPLOYMENT_APPROVAL_PENDING"):
        with deployment_app_identity_authority(ref, DeploymentAuthorityContext(str(row.guid))):
            pytest.fail("pending quorum must not be executable")
    assert not row.approval_votes.exists() and not world.starts


def test_execution_full_current_policy_refuses_greater_minimum_after_actual_quorum(world, client):
    row, ref = pending(world, client)
    _, headers = reviewer(world, "execution-quorum-")
    result = graphql_http(client, headers, APPROVE, {"input": {"id": str(row.guid)}})["data"][
        "approveDeployment"
    ]
    assert result["ok"], result["errors"]
    row.refresh_from_db()
    assert row.approvals_received == 1 and row.approval_votes.count() == 1
    context = DeploymentAuthorityContext(str(row.guid))
    with deployment_app_identity_authority(ref, context):
        pass
    policy(world, 2)
    with pytest.raises(PermissionDenied):
        with deployment_app_identity_authority(ref, context):
            pytest.fail("retained quorum cannot bypass a stricter current policy")
    assert row.approval_votes.count() == 1


def test_execution_does_not_trust_counter_or_nonhuman_credential_vote(world, client):
    from astrolift_lifecycle.models import DeploymentApproval

    row, ref = pending(world, client)
    DeploymentApproval.objects.create(deployment=row, credential_hash="a" * 64)
    Deployment.objects.filter(pk=row.pk).update(approvals_received=99, status="pending")
    with pytest.raises(DeploymentOriginError, match="DEPLOYMENT_APPROVAL_PENDING"):
        with deployment_app_identity_authority(ref, DeploymentAuthorityContext(str(row.guid))):
            pytest.fail("a counter or credential vote is not a distinct identified approver")


@pytest.mark.asyncio
async def test_actual_worker_refuses_genuinely_pending_protected_receipt(world, client, temporal_env):
    from astrolift_workflows.inputs import Actor, DeployAppInput

    row, ref = await sync_to_async(pending)(world, client)
    await sync_to_async(policy)(world, 1)
    payload = DeployAppInput(
        registered_app_id=row.registered_app_id,
        app_environment_id=row.app_environment_id,
        deployment_id=row.pk,
        image_tags={"web": "origin-v1"},
        trigger_kind="manual",
        actor=Actor(kind="user", user_id=world.user.pk),
        identity_authority=ref,
    )
    queue = "pending-execution-" + uuid4().hex
    with ThreadPoolExecutor(max_workers=2) as executor:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[DeployAppWorkflow],
            activities=[validate_deployment_identity_origin, mark_deployment_identity_refused],
            activity_executor=executor,
        ):
            handle = await temporal_env.client.start_workflow(
                DeployAppWorkflow.run, payload, id=queue, task_queue=queue
            )
            result = await handle.result()
            history = await handle.fetch_history()
    assert not result.ok and result.message == "DEPLOYMENT_ORIGIN_REFUSED"
    scheduled = [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]
    assert scheduled == ["validate_deployment_identity_origin", "mark_deployment_identity_refused"]
    assert world.headers["HTTP_AUTHORIZATION"] not in history.to_json()
    await Replayer(workflows=[DeployAppWorkflow]).replay_workflow(history)
    row = await sync_to_async(Deployment.objects.get)(pk=row.pk)
    assert row.aborted_reason == ""
    assert row.status == "pending_approval"
    assert not await sync_to_async(row.approval_votes.exists)()
