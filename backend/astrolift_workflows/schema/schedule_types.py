"""Public observations preserve configuration identity when engine state is uncertain."""

import dataclasses
from datetime import datetime

import strawberry

from core.schema.common import MutationResult


@strawberry.type
class WorkflowScheduleState:
    workflow_id: str
    schedule_id: str
    configuration_version: int
    desired_revision: str
    desired_active: bool
    observed_state: str
    confirmed: bool
    observed_at: datetime
    error_code: str
    message: str
    engine_created_at: datetime | None
    engine_updated_at: datetime | None
    action_count: int | None


def schedule_to_type(observation):
    return WorkflowScheduleState(**dataclasses.asdict(observation))


@strawberry.type
class WorkflowScheduleResult(MutationResult):
    configuration_saved: bool = False
    schedule: WorkflowScheduleState | None = None
