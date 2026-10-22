"""Execution projections of sealed reviewed plans, without per-row plan reads."""

from copy import deepcopy

from django.db.models import Q


def stage_kind_filter(kind):
    """Legacy executions use their stage; reviewed executions use their snapshot."""
    return Q(reviewed_stage_snapshot__kind=kind) | Q(reviewed_stage_snapshot__isnull=True, stage__kind=kind)


def compiled_stage(run, stage):
    from workflows.reviewed_starts import ReviewedStartError, frozen_plan

    plan = frozen_plan(run, str(stage.definition_id))
    if plan is None:
        return None
    matches = [item for item in plan.get("stages", []) if item.get("stage_id") == str(stage.pk)]
    if len(matches) != 1:
        raise ReviewedStartError("The stage is absent from the approved plan")
    return matches[0]


def stage_snapshot(run, stage):
    from workflows.models import WorkflowStageExecution

    compiled = compiled_stage(run, stage)
    # Existing manual receipts predate the full stage snapshot contract.
    if compiled is None or "reviewed_stage" not in compiled:
        return None
    snapshot = deepcopy(compiled["reviewed_stage"])
    execution = WorkflowStageExecution(stage=stage, reviewed_stage_snapshot=snapshot)
    _ = execution.effective_stage  # Validate before writing the execution.
    return snapshot


def effective_stage(run, stage):
    from workflows.models import WorkflowStageExecution

    return WorkflowStageExecution(
        stage=stage, reviewed_stage_snapshot=stage_snapshot(run, stage)
    ).effective_stage
