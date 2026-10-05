"""Public stage ownership, distinct from its parent run's Temporal identity."""

import strawberry

from workflows.stage_incarnations import StageIncarnation


@strawberry.type
class WorkflowStageTemporalExecution:
    namespace: str
    workflow_id: str
    run_id: str


def stage_temporal_execution(row) -> WorkflowStageTemporalExecution | None:
    if not row.temporal_activity_key:
        return None
    try:
        identity = StageIncarnation(
            row.temporal_namespace, row.temporal_workflow_id, row.temporal_run_id, row.temporal_activity_id
        )
    except (TypeError, ValueError):
        return None
    if any(getattr(row, key) != value for key, value in identity.fields().items()):
        return None
    return WorkflowStageTemporalExecution(
        namespace=identity.namespace, workflow_id=identity.workflow_id, run_id=identity.run_id
    )
