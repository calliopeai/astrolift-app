"""The factory control room's read (#2556).

Everything here comes from Astrolift's own workflow run records for a factory
project. Astrolift records no halt state, spend, budget, tier, quarantined
items or pull request links for a factory, so these types carry none of them:
the room says they are not reported instead of showing a zero.
"""

from __future__ import annotations

import datetime as dt

import strawberry

ACTIVE_RUNS_LIMIT = 25
FINISHED_RUNS_LIMIT = 10


@strawberry.type(name="AstroliftFactoryRun")
class FactoryRunType:
    id: str = strawberry.field(description="The run's guid.")
    flow: str = strawberry.field(description="The slug of the workflow definition the run follows.")
    flow_name: str = strawberry.field(description="That definition's name.")
    stage: str = strawberry.field(
        description=(
            "The step the run is on, or ended on: the stage's output key, else its role. Empty before "
            "the first stage starts."
        )
    )
    round: int | None = strawberry.field(
        description="That stage's round; null before the first stage starts."
    )
    status: str = strawberry.field(
        description="running, completed, failed, cancelled, terminated, timed_out or expired."
    )
    waiting_on_person: bool = strawberry.field(description="The run's current stage is an open human gate.")
    started_at: dt.datetime | None
    ended_at: dt.datetime | None


@strawberry.type(name="AstroliftFactoryRoom")
class FactoryRoomType:
    project_id: str = strawberry.field(description="The factory project's guid.")
    project_slug: str
    name: str = strawberry.field(description="The factory project's name.")
    active_runs: list[FactoryRunType] = strawberry.field(
        description=f"The factory's running runs the caller can read, newest first, at most {ACTIVE_RUNS_LIMIT}."
    )
    approvals_waiting: int = strawberry.field(
        description=(
            "Open human gates across the factory's running runs the caller can read: work waiting on a "
            "person, whoever may decide it."
        )
    )
    finished_runs: list[FactoryRunType] = strawberry.field(
        description=(
            f"The factory's finished runs the caller can read, most recently ended first, at most "
            f"{FINISHED_RUNS_LIMIT}: the outcome each run ended with."
        )
    )


def factory_run_to_type(run) -> FactoryRunType:
    """``run`` is a definition ``WorkflowRun`` with its current stage execution joined."""
    from workflows.models import WorkflowStage, WorkflowStageExecution

    execution = run.current_stage_execution
    stage = execution.effective_stage if execution is not None else None
    return FactoryRunType(
        id=str(run.guid),
        flow=run.workflow_definition.slug or "",
        flow_name=run.workflow_definition.name,
        stage=(stage.output_key or stage.role or "") if stage is not None else "",
        round=execution.round_number if execution is not None else None,
        status=run.status,
        waiting_on_person=(
            stage is not None
            and stage.kind == WorkflowStage.StageKind.HUMAN_GATE
            and execution.status == WorkflowStageExecution.Status.RUNNING
        ),
        started_at=run.started_at,
        ended_at=run.ended_at,
    )
