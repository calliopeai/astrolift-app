"""
WorkflowDefinitionRunWorkflow — durable stage executor for an agent
``workflows.WorkflowDefinition``.

Reads the definition's ordered ``WorkflowStage`` rows and runs each one to
completion, durably, honouring per-stage failure policy + timeouts:

  AGENT_DISPATCH
    Open a RUNNING ``WorkflowStageExecution``, dispatch an ``AgentRun``
    (via an ``AgentTask`` through the dispatch spawner), then poll the run
    to a terminal status — bounded by ``stage.timeout_seconds``. On
    failure, apply ``stage.on_failure``: ``fail`` (abort the workflow),
    ``retry`` (re-open a fresh execution, capped attempts), ``skip``
    (mark SKIPPED and continue), ``escalate`` (mark ESCALATED, surface to
    an operator via a human-gate-style signal, then continue once cleared).

  HUMAN_GATE
    Open a RUNNING execution and block on the ``human_gate_decision``
    signal (default 24h for gates, vs the 300s stage default).
    ``approved`` → continue; ``rejected`` → fail the workflow; timeout →
    fail the workflow (the gate expired without a decision).

  CHECKPOINT
    Snapshot the previous stage's output into an immediately-COMPLETED
    execution. No external dispatch.

  AGGREGATION
    Merge the outputs of the immediately-preceding fan-out executions into
    one COMPLETED execution.

FAN_OUT pattern
  When ``WorkflowDefinition.pattern_kind == FAN_OUT``, the first
  AGENT_DISPATCH stage is fanned out: ``stage.fan_out_count`` parallel
  child ``WorkflowDefinitionRunWorkflow`` runs (or a dynamic count taken
  from the prior stage's ``output["items"]`` when ``fan_out_count`` is
  None), each executing only that one stage. The parent waits for every
  child before proceeding; a following AGGREGATION stage merges them.

Workflow id pattern: ``WorkflowDefinitionRunWorkflow-<workflow-run-id>``
(the mutation creates the WorkflowRun mirror row first and passes its pk),
so re-firing joins the in-flight run instead of spawning a parallel one.

The orchestration policy (what to do for each kind / on each failure / how
many fan-out children) lives in the pure ``StagePlan`` helpers below so it
can be unit-tested without a Temporal environment, mirroring the
``pipeline_run.topological_sort`` pattern.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from astrolift_workflows.inputs import WorkflowDefinitionRunInput, WorkflowResult

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities import (
        aggregate_fan_out,
        create_stage_execution,
        dispatch_agent_for_stage,
        get_workflow_stages,
        load_agent_run_outcome,
        mark_workflow_run,
        poll_agent_run_status,
        record_human_gate_decision,
        snapshot_checkpoint,
        update_stage_execution,
    )


# Stage / failure-policy literals — mirror workflows.models without
# importing Django into the sandbox.
KIND_AGENT_DISPATCH = "agent_dispatch"
KIND_HUMAN_GATE = "human_gate"
KIND_CHECKPOINT = "checkpoint"
KIND_AGGREGATION = "aggregation"

ON_FAILURE_FAIL = "fail"
ON_FAILURE_RETRY = "retry"
ON_FAILURE_SKIP = "skip"
ON_FAILURE_ESCALATE = "escalate"

PATTERN_FAN_OUT = "fan_out"

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_ESCALATED = "escalated"

RUN_COMPLETED = "completed"
RUN_FAILED = "failed"

# Default ceiling on retry attempts for an on_failure=retry stage. The
# stage model has no per-stage attempt cap, so the executor imposes one to
# avoid an unbounded retry loop wedging the run.
MAX_STAGE_ATTEMPTS = 3

# A human gate that hasn't been overridden to a longer window still gets a
# day to be answered — 300s (the stage default) is far too short for a
# person. Explicit per-stage timeouts above the default are respected.
DEFAULT_GATE_TIMEOUT_SECONDS = 86_400

# Short, bounded activity timeout for the thin DB activities.
_DB_TIMEOUT = timedelta(minutes=2)
_DB_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=5,
)
# Polling cadence for agent-run status. Uses workflow.sleep (durable timer)
# — never bare asyncio.sleep, which the sandbox forbids.
_AGENT_POLL_INTERVAL = timedelta(seconds=30)
_GATE_POLL_INTERVAL = timedelta(seconds=30)


# ---------------------------------------------------------------------------
# Pure policy helpers (no Temporal / Django — unit-tested directly)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class StageDecision:
    """What the executor should do with a finished AGENT_DISPATCH attempt.

    ``proceed`` — stage is done, advance.
    ``retry`` — open a fresh attempt (only while attempts remain).
    ``abort`` — fail the whole workflow.
    ``escalate`` — mark escalated + wait for an operator clear, then
    advance.
    """

    proceed: bool = False
    retry: bool = False
    abort: bool = False
    escalate: bool = False
    terminal_status: str = STATUS_COMPLETED


def decide_after_agent_run(
    run_status: str,
    on_failure: str,
    attempt_number: int,
    *,
    max_attempts: int = MAX_STAGE_ATTEMPTS,
) -> StageDecision:
    """Map an agent run's terminal status + the stage's ``on_failure`` into
    the executor's next move.

    A ``succeeded`` run always proceeds. Anything else consults
    ``on_failure``; ``retry`` degrades to ``fail`` once attempts are
    exhausted so the run can't loop forever.
    """
    if run_status == "succeeded":
        return StageDecision(proceed=True, terminal_status=STATUS_COMPLETED)

    if on_failure == ON_FAILURE_SKIP:
        return StageDecision(proceed=True, terminal_status=STATUS_SKIPPED)
    if on_failure == ON_FAILURE_ESCALATE:
        return StageDecision(escalate=True, terminal_status=STATUS_ESCALATED)
    if on_failure == ON_FAILURE_RETRY and attempt_number < max_attempts:
        return StageDecision(retry=True, terminal_status=STATUS_FAILED)
    # FAIL, or RETRY budget exhausted.
    return StageDecision(abort=True, terminal_status=STATUS_FAILED)


def resolve_fan_out_count(
    fan_out_count: int | None,
    previous_output: dict | None,
    *,
    cap: int = 50,
) -> int:
    """How many parallel children to spawn for a fan-out stage.

    Static ``fan_out_count`` wins when set (and positive). Otherwise the
    count is the length of the prior stage's ``output["items"]`` list —
    the dynamic-fan-out contract. Always clamped to ``[0, cap]`` so a
    runaway upstream list can't spawn thousands of children.
    """
    if fan_out_count is not None and fan_out_count > 0:
        return min(fan_out_count, cap)
    items = (previous_output or {}).get("items")
    if isinstance(items, list):
        return min(len(items), cap)
    return 0


def is_fan_out_stage(pattern_kind: str, stage: dict, already_fanned: bool) -> bool:
    """True for the single AGENT_DISPATCH stage that should fan out.

    Only the FAN_OUT pattern fans out, only AGENT_DISPATCH stages, and only
    the first such stage (``already_fanned`` guards re-entry).
    """
    return pattern_kind == PATTERN_FAN_OUT and stage["kind"] == KIND_AGENT_DISPATCH and not already_fanned


def build_stage_dispatch_input(
    workflow_input: dict,
    previous_output: Any,
    named_outputs: dict[str, Any],
    stage: dict,
) -> dict:
    """Preserve the prior flat payload while adding explicit chain context."""
    payload = dict(previous_output) if isinstance(previous_output, dict) else {"value": previous_output}
    payload["_astrolift_workflow"] = {
        "input": workflow_input,
        "previous": previous_output,
        "outputs": named_outputs,
        "stage": {
            "order": stage["order"],
            "output_key": stage["output_key"],
        },
    }
    return payload


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


@workflow.defn(name="WorkflowDefinitionRunWorkflow")
class WorkflowDefinitionRunWorkflow:
    def __init__(self) -> None:
        # Latest human-gate decision, keyed by stage execution id. Set by
        # the ``human_gate_decision`` signal; consumed by the gate wait.
        self._gate_decisions: dict[str, dict[str, Any]] = {}
        # Escalation clears, keyed by stage execution id.
        self._escalation_cleared: dict[str, dict[str, Any]] = {}
        self._abort_requested: bool = False
        # Set at the top of _execute so the per-kind handlers can open
        # execution rows without threading the run id through every call.
        self._workflow_run_id: str = ""

    # ---- signals ----------------------------------------------------------

    @workflow.signal(name="human_gate_decision")
    def human_gate_decision(self, payload: dict) -> None:
        """Operator decision for a pending human gate.

        Payload: ``{"execution_id": str, "decision": "approved"|"rejected",
        "decided_by_user_id": int|None, "note": str}``. The newest decision
        for an execution id wins.
        """
        execution_id = str(payload.get("execution_id", ""))
        if execution_id:
            self._gate_decisions[execution_id] = payload

    @workflow.signal(name="escalation_cleared")
    def escalation_cleared(self, payload: dict) -> None:
        """Operator clears an escalated stage so the run can proceed."""
        execution_id = str(payload.get("execution_id", ""))
        if execution_id:
            self._escalation_cleared[execution_id] = payload

    @workflow.signal(name="abort")
    def request_abort(self) -> None:
        self._abort_requested = True

    # ---- run --------------------------------------------------------------

    @workflow.run
    async def run(self, input: WorkflowDefinitionRunInput) -> WorkflowResult:
        run_id = input.workflow_run_id
        try:
            result = await self._execute(input)
        except _WorkflowAbort as abort:
            await self._finalize(run_id, RUN_FAILED, None, {"message": abort.message})
            return WorkflowResult(ok=False, message=abort.message, data=abort.data)
        except Exception as exc:  # noqa: BLE001 — record then re-raise for Temporal
            await self._finalize(run_id, RUN_FAILED, None, {"message": f"unhandled: {exc}"})
            raise

        await self._finalize(run_id, RUN_COMPLETED, result, None)
        return WorkflowResult(ok=True, message="workflow definition completed", data=result)

    async def _execute(self, input: WorkflowDefinitionRunInput) -> dict[str, Any]:
        # Stash the run id so the per-kind handlers can open execution rows
        # without threading it through every signature.
        self._workflow_run_id = input.workflow_run_id
        # This workflow is already deployed and may have durable histories in
        # flight.  Keep replaying the exact pre-stage-runtime command sequence
        # for histories that do not contain this marker; new runs record the
        # marker and use the richer, source-bound dispatch packet below.
        if not workflow.patched("workflow-agent-stage-runtime-v2"):
            return await self._execute_legacy(input)
        return await self._execute_v2(input)

    async def _execute_v2(self, input: WorkflowDefinitionRunInput) -> dict[str, Any]:
        plan = await workflow.execute_activity(
            get_workflow_stages,
            {
                "workflow_definition_slug": input.workflow_definition_slug,
                "workflow_definition_id": input.workflow_definition_id,
                "workflow_run_id": input.workflow_run_id,
                "stage_bindings": input.stage_bindings or {},
            },
            start_to_close_timeout=_DB_TIMEOUT,
            retry_policy=_DB_RETRY,
        )
        pattern_kind: str = plan["pattern_kind"]
        stages: list[dict] = plan["stages"]

        # Child runs execute exactly one stage (the parent picks which via
        # ``only_stage_order``); they never re-fan-out.
        if input.only_stage_order is not None:
            stages = [s for s in stages if s["order"] == input.only_stage_order]
            pattern_kind = "single"

        workflow_input = dict(input.trigger_payload or {})
        previous_output: Any = workflow_input
        # Track the executions produced by the most recent fan-out so a
        # following AGGREGATION stage can merge them.
        last_fan_out_executions: list[str] = []
        already_fanned = False
        stage_outputs: list[dict] = []
        named_outputs: dict[str, Any] = {}

        for stage in stages:
            if self._abort_requested:
                raise _WorkflowAbort("aborted by signal", data={"outputs": stage_outputs})

            kind = stage["kind"]

            if is_fan_out_stage(pattern_kind, stage, already_fanned):
                last_fan_out_executions = await self._run_fan_out(input, stage, previous_output)
                already_fanned = True
                previous_output = {
                    "fan_out_execution_ids": last_fan_out_executions,
                    "count": len(last_fan_out_executions),
                }
                named_outputs[stage["output_key"]] = previous_output
                stage_outputs.append(
                    {
                        "order": stage["order"],
                        "output_key": stage["output_key"],
                        "fan_out": previous_output,
                    }
                )
                continue

            if kind == KIND_AGENT_DISPATCH:
                dispatch_input = build_stage_dispatch_input(
                    workflow_input,
                    previous_output,
                    named_outputs,
                    stage,
                )
                attempt_output = await self._run_agent_stage(input, stage, dispatch_input)
                previous_output = attempt_output.get("result")
                stage_record = {
                    "order": stage["order"],
                    "output_key": stage["output_key"],
                    "output": previous_output,
                    **{key: value for key, value in attempt_output.items() if key != "result"},
                }
            elif kind == KIND_HUMAN_GATE:
                previous_output = await self._run_human_gate(stage)
                stage_record = {
                    "order": stage["order"],
                    "output_key": stage["output_key"],
                    "output": previous_output,
                }
            elif kind == KIND_CHECKPOINT:
                previous_output = await self._run_checkpoint(input, stage, previous_output)
                stage_record = {
                    "order": stage["order"],
                    "output_key": stage["output_key"],
                    "output": previous_output,
                }
            elif kind == KIND_AGGREGATION:
                previous_output = await self._run_aggregation(input, stage, last_fan_out_executions)
                last_fan_out_executions = []
                stage_record = {
                    "order": stage["order"],
                    "output_key": stage["output_key"],
                    "output": previous_output,
                }
            else:
                raise _WorkflowAbort(f"unknown stage kind {kind!r}")

            named_outputs[stage["output_key"]] = previous_output
            stage_outputs.append(stage_record)

        return {
            "outputs": stage_outputs,
            "named_outputs": named_outputs,
            "final_output": previous_output,
        }

    async def _execute_legacy(self, input: WorkflowDefinitionRunInput) -> dict[str, Any]:
        """Replay path for histories created before the v2 stage packet.

        Do not refactor this method or its legacy helpers without another
        Temporal patch marker: their activity and child-workflow arguments
        intentionally mirror the previously deployed implementation.
        """
        plan = await workflow.execute_activity(
            get_workflow_stages,
            input.workflow_definition_slug,
            start_to_close_timeout=_DB_TIMEOUT,
            retry_policy=_DB_RETRY,
        )
        pattern_kind: str = plan["pattern_kind"]
        stages: list[dict] = plan["stages"]

        if input.only_stage_order is not None:
            stages = [s for s in stages if s["order"] == input.only_stage_order]
            pattern_kind = "single"

        previous_output: dict | None = dict(input.trigger_payload or {})
        last_fan_out_executions: list[str] = []
        already_fanned = False
        stage_outputs: list[dict] = []

        for stage in stages:
            if self._abort_requested:
                raise _WorkflowAbort("aborted by signal", data={"outputs": stage_outputs})

            kind = stage["kind"]
            if is_fan_out_stage(pattern_kind, stage, already_fanned):
                last_fan_out_executions = await self._run_fan_out_legacy(
                    input,
                    stage,
                    previous_output,
                )
                already_fanned = True
                previous_output = {
                    "fan_out_execution_ids": last_fan_out_executions,
                    "count": len(last_fan_out_executions),
                }
                stage_outputs.append({"order": stage["order"], "fan_out": previous_output})
                continue

            if kind == KIND_AGENT_DISPATCH:
                previous_output = await self._run_agent_stage_legacy(input, stage, previous_output)
            elif kind == KIND_HUMAN_GATE:
                previous_output = await self._run_human_gate(stage)
            elif kind == KIND_CHECKPOINT:
                previous_output = await self._run_checkpoint_legacy(input, stage, previous_output)
            elif kind == KIND_AGGREGATION:
                previous_output = await self._run_aggregation(input, stage, last_fan_out_executions)
                last_fan_out_executions = []
            else:
                raise _WorkflowAbort(f"unknown stage kind {kind!r}")

            stage_outputs.append({"order": stage["order"], "output": previous_output})

        return {"outputs": stage_outputs, "final_output": previous_output}

    # ---- per-kind handlers ------------------------------------------------

    async def _run_agent_stage_legacy(
        self,
        input: WorkflowDefinitionRunInput,
        stage: dict,
        previous_output: dict | None,
    ) -> dict:
        """Pre-v2 agent dispatch path retained solely for history replay."""
        timeout_seconds = max(1, int(stage["timeout_seconds"]))
        on_failure = stage["on_failure"]
        attempt = 1

        while True:
            execution_id = await workflow.execute_activity(
                create_stage_execution,
                args=[input.workflow_run_id, stage["stage_id"], attempt],
                start_to_close_timeout=_DB_TIMEOUT,
                retry_policy=_DB_RETRY,
            )

            try:
                agent_run_id = await workflow.execute_activity(
                    dispatch_agent_for_stage,
                    args=[stage["stage_id"], execution_id, previous_output or {}],
                    start_to_close_timeout=_DB_TIMEOUT,
                    retry_policy=_DB_RETRY,
                )
                run_status = await self._poll_agent_to_terminal(agent_run_id, timeout_seconds)
            except ActivityError:
                run_status = "failed"

            decision = decide_after_agent_run(run_status, on_failure, attempt)
            if decision.proceed:
                output = {
                    "agent_run_status": run_status,
                    "attempt": attempt,
                    "execution_id": execution_id,
                }
                await workflow.execute_activity(
                    update_stage_execution,
                    args=[execution_id, decision.terminal_status, output, None],
                    start_to_close_timeout=_DB_TIMEOUT,
                    retry_policy=_DB_RETRY,
                )
                return output

            if decision.retry:
                await workflow.execute_activity(
                    update_stage_execution,
                    args=[
                        execution_id,
                        STATUS_FAILED,
                        None,
                        f"attempt {attempt} failed ({run_status}); retrying",
                    ],
                    start_to_close_timeout=_DB_TIMEOUT,
                    retry_policy=_DB_RETRY,
                )
                attempt += 1
                continue

            if decision.escalate:
                await workflow.execute_activity(
                    update_stage_execution,
                    args=[execution_id, STATUS_ESCALATED, None, "escalated to operator"],
                    start_to_close_timeout=_DB_TIMEOUT,
                    retry_policy=_DB_RETRY,
                )
                cleared = await self._wait_escalation_cleared(execution_id, timeout_seconds)
                if cleared:
                    return {"agent_run_status": run_status, "escalation": "cleared"}
                raise _WorkflowAbort(
                    f"stage {stage['order']} escalation not cleared in time",
                    data={"execution_id": execution_id},
                )

            await workflow.execute_activity(
                update_stage_execution,
                args=[execution_id, STATUS_FAILED, None, f"stage failed ({run_status})"],
                start_to_close_timeout=_DB_TIMEOUT,
                retry_policy=_DB_RETRY,
            )
            raise _WorkflowAbort(
                f"stage {stage['order']} failed ({run_status})",
                data={"execution_id": execution_id, "attempt": attempt},
            )

    async def _run_agent_stage(
        self,
        input: WorkflowDefinitionRunInput,
        stage: dict,
        previous_output: dict,
    ) -> dict:
        """Dispatch + poll one AGENT_DISPATCH stage, honouring on_failure.

        Returns the stage output dict to chain into the next stage. Aborts
        the workflow (via ``_WorkflowAbort``) when policy says ``fail`` or
        retries are exhausted.
        """
        timeout_seconds = max(1, int(stage["timeout_seconds"]))
        on_failure = stage["on_failure"]
        attempt = 1

        while True:
            agent_run_id: str | None = None
            execution_id = await workflow.execute_activity(
                create_stage_execution,
                args=[input.workflow_run_id, stage["stage_id"], attempt],
                start_to_close_timeout=_DB_TIMEOUT,
                retry_policy=_DB_RETRY,
            )

            try:
                agent_run_id = await workflow.execute_activity(
                    dispatch_agent_for_stage,
                    args=[
                        stage["stage_id"],
                        execution_id,
                        previous_output,
                        {
                            "agent_definition_id": stage["agent_definition_id"],
                            "environment_spec_slug": stage["environment_spec_slug"],
                            "skill_refs": stage["skill_refs"],
                            "prompt": stage["prompt"],
                            "output_key": stage["output_key"],
                        },
                    ],
                    start_to_close_timeout=_DB_TIMEOUT,
                    retry_policy=_DB_RETRY,
                )
                run_status = await self._poll_agent_to_terminal(agent_run_id, timeout_seconds)
            except ActivityError:
                # The dispatch activity itself failed (e.g. spawn error).
                run_status = "failed"

            decision = decide_after_agent_run(run_status, on_failure, attempt)

            if decision.proceed:
                if agent_run_id is not None:
                    outcome = await workflow.execute_activity(
                        load_agent_run_outcome,
                        agent_run_id,
                        start_to_close_timeout=_DB_TIMEOUT,
                        retry_policy=_DB_RETRY,
                    )
                else:
                    outcome = {
                        "result": None,
                        "failure": {"message": "agent dispatch activity failed"},
                        "task_guid": None,
                    }
                # Include execution_id so a FAN_OUT parent can collect each
                # child's stage execution (it reads final_output["execution_id"])
                # — without it, fan-out always aggregated 0 children (#1017).
                output = {
                    "agent_run_status": run_status,
                    "agent_run_id": agent_run_id,
                    "attempt": attempt,
                    "execution_id": execution_id,
                    "result": outcome.get("result"),
                    "failure": outcome.get("failure"),
                    "task_guid": outcome.get("task_guid"),
                }
                await workflow.execute_activity(
                    update_stage_execution,
                    args=[execution_id, decision.terminal_status, output, None],
                    start_to_close_timeout=_DB_TIMEOUT,
                    retry_policy=_DB_RETRY,
                )
                return output

            if decision.retry:
                await workflow.execute_activity(
                    update_stage_execution,
                    args=[
                        execution_id,
                        STATUS_FAILED,
                        None,
                        f"attempt {attempt} failed ({run_status}); retrying",
                    ],
                    start_to_close_timeout=_DB_TIMEOUT,
                    retry_policy=_DB_RETRY,
                )
                attempt += 1
                continue

            if decision.escalate:
                await workflow.execute_activity(
                    update_stage_execution,
                    args=[execution_id, STATUS_ESCALATED, None, "escalated to operator"],
                    start_to_close_timeout=_DB_TIMEOUT,
                    retry_policy=_DB_RETRY,
                )
                cleared = await self._wait_escalation_cleared(execution_id, timeout_seconds)
                if cleared:
                    return {"agent_run_status": run_status, "escalation": "cleared"}
                raise _WorkflowAbort(
                    f"stage {stage['order']} escalation not cleared in time",
                    data={"execution_id": execution_id},
                )

            # abort
            await workflow.execute_activity(
                update_stage_execution,
                args=[execution_id, STATUS_FAILED, None, f"stage failed ({run_status})"],
                start_to_close_timeout=_DB_TIMEOUT,
                retry_policy=_DB_RETRY,
            )
            raise _WorkflowAbort(
                f"stage {stage['order']} failed ({run_status})",
                data={"execution_id": execution_id, "attempt": attempt},
            )

    async def _poll_agent_to_terminal(self, agent_run_id: str, timeout_seconds: int) -> str:
        """Poll the AgentRun until terminal or the stage timeout elapses.

        Uses a durable ``workflow.sleep`` between polls. Returns the
        terminal status, or ``"timed_out"`` when the budget is exhausted.
        """
        deadline = workflow.now() + timedelta(seconds=timeout_seconds)
        while True:
            status = await workflow.execute_activity(
                poll_agent_run_status,
                agent_run_id,
                start_to_close_timeout=_DB_TIMEOUT,
                retry_policy=_DB_RETRY,
            )
            if status in ("succeeded", "failed", "cancelled"):
                return status
            if workflow.now() >= deadline:
                return "timed_out"
            remaining = deadline - workflow.now()
            await workflow.sleep(min(_AGENT_POLL_INTERVAL, remaining))

    async def _run_human_gate(self, stage: dict) -> dict:
        """Open a gate execution and block on the decision signal."""
        execution_id = await workflow.execute_activity(
            create_stage_execution,
            args=[self._workflow_run_id, stage["stage_id"], 1],
            start_to_close_timeout=_DB_TIMEOUT,
            retry_policy=_DB_RETRY,
        )

        # Gate timeout: respect an explicit per-stage override above the
        # 300s stage default; otherwise grant a full day.
        configured = int(stage["timeout_seconds"])
        timeout_seconds = configured if configured > 300 else DEFAULT_GATE_TIMEOUT_SECONDS

        decision_payload = await self._wait_gate_decision(execution_id, timeout_seconds)

        if decision_payload is None:
            await workflow.execute_activity(
                record_human_gate_decision,
                args=[execution_id, "rejected", None, "gate timed out"],
                start_to_close_timeout=_DB_TIMEOUT,
                retry_policy=_DB_RETRY,
            )
            raise _WorkflowAbort(
                f"human gate at stage {stage['order']} timed out",
                data={"execution_id": execution_id},
            )

        decision = str(decision_payload.get("decision", "rejected"))
        await workflow.execute_activity(
            record_human_gate_decision,
            args=[
                execution_id,
                decision,
                decision_payload.get("decided_by_user_id"),
                str(decision_payload.get("note", "")),
            ],
            start_to_close_timeout=_DB_TIMEOUT,
            retry_policy=_DB_RETRY,
        )

        if decision != "approved":
            raise _WorkflowAbort(
                f"human gate at stage {stage['order']} rejected",
                data={"execution_id": execution_id},
            )
        return {"human_gate": "approved", "execution_id": execution_id}

    async def _run_checkpoint(
        self,
        input: WorkflowDefinitionRunInput,
        stage: dict,
        previous_output: Any,
    ) -> dict:
        await workflow.execute_activity(
            snapshot_checkpoint,
            args=[
                input.workflow_run_id,
                stage["stage_id"],
                previous_output if isinstance(previous_output, dict) else {"value": previous_output},
            ],
            start_to_close_timeout=_DB_TIMEOUT,
            retry_policy=_DB_RETRY,
        )
        # Checkpoints are transparent to chaining: the prior output flows on.
        return previous_output

    async def _run_checkpoint_legacy(
        self,
        input: WorkflowDefinitionRunInput,
        stage: dict,
        previous_output: dict | None,
    ) -> dict:
        """Pre-v2 checkpoint arguments retained solely for history replay."""
        await workflow.execute_activity(
            snapshot_checkpoint,
            args=[input.workflow_run_id, stage["stage_id"], previous_output or {}],
            start_to_close_timeout=_DB_TIMEOUT,
            retry_policy=_DB_RETRY,
        )
        return previous_output or {}

    async def _run_aggregation(
        self,
        input: WorkflowDefinitionRunInput,
        stage: dict,
        source_execution_ids: list[str],
    ) -> dict:
        aggregated = await workflow.execute_activity(
            aggregate_fan_out,
            args=[input.workflow_run_id, stage["stage_id"], source_execution_ids],
            start_to_close_timeout=_DB_TIMEOUT,
            retry_policy=_DB_RETRY,
        )
        return aggregated

    async def _run_fan_out(
        self,
        input: WorkflowDefinitionRunInput,
        stage: dict,
        previous_output: Any,
    ) -> list[str]:
        """Spawn N child runs that each execute only this stage; wait for
        all. Returns the child runs' terminal stage-execution ids so a
        following AGGREGATION stage can merge them."""
        count = resolve_fan_out_count(
            stage["fan_out_count"],
            previous_output if isinstance(previous_output, dict) else None,
        )
        if count == 0:
            return []

        handles = []
        for idx in range(count):
            child_run_id = f"{input.workflow_run_id}:fanout:{stage['order']}:{idx}"
            handle = await workflow.start_child_workflow(
                WorkflowDefinitionRunWorkflow.run,
                WorkflowDefinitionRunInput(
                    workflow_definition_slug=input.workflow_definition_slug,
                    workflow_run_id=child_run_id,
                    trigger_payload=(
                        previous_output if isinstance(previous_output, dict) else {"value": previous_output}
                    ),
                    actor=input.actor,
                    workflow_definition_id=input.workflow_definition_id,
                    only_stage_order=stage["order"],
                    fan_out_index=idx,
                    stage_bindings=input.stage_bindings,
                ),
                id=f"WorkflowDefinitionRunWorkflow-{child_run_id}",
                task_timeout=timedelta(seconds=max(1, int(stage["timeout_seconds"]))),
            )
            handles.append(handle)

        execution_ids: list[str] = []
        for handle in handles:
            try:
                res = await handle
                data = getattr(res, "data", None) or {}
                outputs = data.get("outputs") or []
                final_stage = outputs[-1] if outputs else {}
                exec_id = final_stage.get("execution_id")
                if exec_id:
                    execution_ids.append(str(exec_id))
            except Exception as exc:  # noqa: BLE001 — one child failing is data, not fatal
                workflow.logger.warning("fan-out child failed: %s", exc)
        return execution_ids

    async def _run_fan_out_legacy(
        self,
        input: WorkflowDefinitionRunInput,
        stage: dict,
        previous_output: dict | None,
    ) -> list[str]:
        """Pre-v2 child input/result shape retained for history replay."""
        count = resolve_fan_out_count(stage["fan_out_count"], previous_output)
        if count == 0:
            return []

        handles = []
        for idx in range(count):
            child_run_id = f"{input.workflow_run_id}:fanout:{stage['order']}:{idx}"
            handle = await workflow.start_child_workflow(
                WorkflowDefinitionRunWorkflow.run,
                WorkflowDefinitionRunInput(
                    workflow_definition_slug=input.workflow_definition_slug,
                    workflow_run_id=child_run_id,
                    trigger_payload=previous_output or {},
                    actor=input.actor,
                    only_stage_order=stage["order"],
                    fan_out_index=idx,
                    stage_bindings=input.stage_bindings,
                ),
                id=f"WorkflowDefinitionRunWorkflow-{child_run_id}",
                task_timeout=timedelta(seconds=max(1, int(stage["timeout_seconds"]))),
            )
            handles.append(handle)

        execution_ids: list[str] = []
        for handle in handles:
            try:
                res = await handle
                data = getattr(res, "data", None) or {}
                final = data.get("final_output") or {}
                exec_id = final.get("execution_id")
                if exec_id:
                    execution_ids.append(str(exec_id))
            except Exception as exc:  # noqa: BLE001 — one child failure is data
                workflow.logger.warning("fan-out child failed: %s", exc)
        return execution_ids

    # ---- signal waits -----------------------------------------------------

    async def _wait_gate_decision(self, execution_id: str, timeout_seconds: int) -> dict | None:
        """Block until the gate's decision signal arrives or the timeout
        elapses. Returns the decision payload, or None on timeout."""
        deadline = workflow.now() + timedelta(seconds=timeout_seconds)
        while execution_id not in self._gate_decisions:
            remaining = deadline - workflow.now()
            if remaining <= timedelta(0):
                return None
            try:
                await workflow.wait_condition(
                    lambda: execution_id in self._gate_decisions,
                    timeout=min(_GATE_POLL_INTERVAL, remaining),
                )
            except TimeoutError:
                if workflow.now() >= deadline:
                    return None
        return self._gate_decisions.get(execution_id)

    async def _wait_escalation_cleared(self, execution_id: str, timeout_seconds: int) -> bool:
        """Block until the escalation is cleared or the timeout elapses."""
        deadline = workflow.now() + timedelta(seconds=timeout_seconds)
        while execution_id not in self._escalation_cleared:
            remaining = deadline - workflow.now()
            if remaining <= timedelta(0):
                return False
            try:
                await workflow.wait_condition(
                    lambda: execution_id in self._escalation_cleared,
                    timeout=min(_GATE_POLL_INTERVAL, remaining),
                )
            except TimeoutError:
                if workflow.now() >= deadline:
                    return False
        return True

    # ---- finalize ---------------------------------------------------------

    async def _finalize(
        self,
        run_id: str,
        status: str,
        result: dict | None,
        failure: dict | None,
    ) -> None:
        await workflow.execute_activity(
            mark_workflow_run,
            args=[run_id, status, result, failure],
            start_to_close_timeout=_DB_TIMEOUT,
            retry_policy=_DB_RETRY,
        )


class _WorkflowAbort(Exception):
    """Internal control-flow signal: stop the run and mark it FAILED with a
    structured message. Carries optional ``data`` for the result payload."""

    def __init__(self, message: str, *, data: dict[str, Any] | None = None) -> None:
        self.message = message
        self.data = data or {}
        super().__init__(message)
