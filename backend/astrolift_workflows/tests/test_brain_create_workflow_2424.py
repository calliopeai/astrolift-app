"""BrainCreateWorkflow orders its steps, waits on provisioning and the first
deployment, and records a step that keeps failing (#2424). The activities are
stand-ins with the real names; brain_manage's own tests cover what they do."""

from __future__ import annotations

from uuid import uuid4

from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from astrolift_workflows.workflows.brain_create import BrainCreateWorkflow


def fakes(script: dict[str, list[str]], log: list[str]):
    def make(name):
        @activity.defn(name=f"astrolift.brain_create.{name}")
        async def run(creation_id: int) -> dict:
            log.append(name)
            answers = script[name]
            answer = answers.pop(0) if len(answers) > 1 else answers[0]
            if answer == "raise":
                raise ApplicationError("host down", non_retryable=True)
            return {"state": answer}

        return run

    @activity.defn(name="astrolift.brain_create.fail")
    async def fail(creation_id: int, code: str, message: str) -> dict:
        log.append(f"fail:{message}")
        return {"state": "failed"}

    return [make(n) for n in ("repo", "scaffold", "register", "deploy", "observe")] + [fail]


async def _run(temporal_env, script):
    log: list[str] = []
    queue = f"brain-create-{uuid4().hex}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[BrainCreateWorkflow],
        activities=fakes(script, log),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        result = await temporal_env.client.execute_workflow(
            BrainCreateWorkflow.run, 7, id=f"BrainCreateWorkflow-{uuid4()}", task_queue=queue
        )
    return result, log


async def test_steps_run_in_order_and_wait_for_provisioning_then_the_deploy(temporal_env):
    result, log = await _run(
        temporal_env,
        {
            "repo": ["repo_ready"],
            "scaffold": ["scaffolded"],
            "register": ["registered"],
            "deploy": ["registered", "registered", "deploying"],
            "observe": ["deploying", "ready"],
        },
    )
    assert result == "ready"
    assert log == ["repo", "scaffold", "register", "deploy", "deploy", "deploy", "observe", "observe"]


async def test_a_refused_step_stops_the_workflow(temporal_env):
    result, log = await _run(
        temporal_env,
        {"repo": ["failed"], "scaffold": ["x"], "register": ["x"], "deploy": ["x"], "observe": ["x"]},
    )
    assert result == "failed" and log == ["repo"]


async def test_a_step_that_keeps_failing_is_recorded(temporal_env):
    result, log = await _run(
        temporal_env,
        {"repo": ["repo_ready"], "scaffold": ["raise"], "register": ["x"], "deploy": ["x"], "observe": ["x"]},
    )
    assert result == "failed"
    assert log[:2] == ["repo", "scaffold"] and log[-1].startswith("fail:")


async def test_a_brain_that_never_provisions_fails_after_the_deadline(temporal_env):
    result, log = await _run(
        temporal_env,
        {
            "repo": ["repo_ready"],
            "scaffold": ["scaffolded"],
            "register": ["registered"],
            "deploy": ["registered"],
            "observe": ["x"],
        },
    )
    assert result == "failed" and "provisioning" in log[-1]
