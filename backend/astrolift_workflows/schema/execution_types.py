from __future__ import annotations

from datetime import datetime

import strawberry

from core.schema.common import MutationResult


@strawberry.type(name="WorkflowExecution")
class WorkflowExecutionType:
    guid: str
    record_id: str
    organization_guid: str
    definition_slug: str
    status: str
    temporal_workflow_id: str
    temporal_run_id: str | None
    started_at: datetime | None
    ended_at: datetime | None
    is_terminal: bool
    failure: strawberry.scalars.JSON | None
    task_cleanup: strawberry.scalars.JSON
    observation_error: str


@strawberry.type
class WorkflowExecutionControlResult(MutationResult):
    requested: bool = False
    execution: WorkflowExecutionType | None = None
