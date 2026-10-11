"""Public decision receipts never expose the internal approver primary key."""

import strawberry

from astrolift_workflows.schema.stage_temporal_types import WorkflowStageTemporalExecution
from core.schema.common import MutationResult


@strawberry.type
class HumanGateDecisionState:
    execution_guid: str
    stage_execution_guid: str
    stage_guid: str
    temporal_execution: WorkflowStageTemporalExecution | None
    stage_status: str
    run_status: str
    request_state: str
    requested_decision: str | None
    recorded_decision: str | None
    note: str
    decided_by_me: bool | None
    observation_error: str


@strawberry.type
class HumanGateDecisionResult(MutationResult):
    gate: HumanGateDecisionState | None = None
