"""Continue-as-new preserves input but cannot borrow an accepted actual run."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from temporalio import workflow
from temporalio.exceptions import ActivityError
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from astrolift_lifecycle.models import DeploymentExecutionReceipt
from astrolift_lifecycle.tests.test_native_deployment_origin_2278 import start_public
from astrolift_lifecycle.tests.test_native_deployment_origin_2278 import world as public_world
from astrolift_workflows.activities.deployment_identity_origin import validate_deployment_identity_origin
from astrolift_workflows.inputs import DeployAppInput

pytestmark = pytest.mark.django_db(transaction=True)


@workflow.defn(name="DeployAppWorkflow")
class ContinueGate:
    @workflow.run
    async def run(self, input: DeployAppInput) -> dict:
        try:
            await workflow.execute_activity(
                validate_deployment_identity_origin,
                input,
                start_to_close_timeout=timedelta(seconds=10),
            )
        except ActivityError as exc:
            reason = exc.cause.message
        if not workflow.info().continued_run_id:
            assert reason == "NATIVE_IDENTITY_PIPELINE_NOT_CONFIGURED"
            workflow.continue_as_new(input)
        return {
            "reason": reason,
            "different_run": workflow.info().run_id != workflow.info().first_execution_run_id,
        }


@pytest.fixture
def world(monkeypatch):
    return public_world.__wrapped__(monkeypatch)


@pytest.mark.asyncio
async def test_actual_continue_as_new_refuses_original_input_and_retains_first_receipt(
    world,
    client,
    temporal_env,
):
    await sync_to_async(start_public)(world, client)
    payload = world.starts[0][1]
    intent = await sync_to_async(DeploymentExecutionReceipt.objects.get)(
        deployment_id=payload.deployment_id,
        kind="EXPECTED",
    )
    queue = "execution-continuation-" + uuid4().hex
    with ThreadPoolExecutor(max_workers=2) as executor:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[ContinueGate],
            activities=[validate_deployment_identity_origin],
            activity_executor=executor,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await temporal_env.client.start_workflow(
                ContinueGate.run,
                payload,
                id=intent.workflow_id,
                task_queue=queue,
            )
            result = await handle.result()
            history = await handle.fetch_history()
    assert result == {"reason": "DEPLOYMENT_EXECUTION_REFUSED", "different_run": True}
    bound = await sync_to_async(DeploymentExecutionReceipt.objects.get)(
        deployment_id=payload.deployment_id,
        kind="BOUND",
    )
    assert bound.run_id == handle.first_execution_run_id
    assert world.headers["HTTP_AUTHORIZATION"] not in history.to_json()
    await Replayer(workflows=[ContinueGate], workflow_runner=UnsandboxedWorkflowRunner()).replay_workflow(
        history
    )
