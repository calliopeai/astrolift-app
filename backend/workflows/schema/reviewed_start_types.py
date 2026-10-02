from __future__ import annotations

import strawberry

from astrolift_graphql import GUID
from astrolift_workflows.schema.workflow_config_types import WorkflowDefinitionSummaryType, definition_summary
from core.run_input_contract import input_contract

JSON = strawberry.scalars.JSON


@strawberry.type(name="RunInputField")
class RunInputField:
    name: str
    kind: str
    required: bool
    has_default: bool
    default: JSON | None
    sensitive: bool
    simple: bool
    enum_values: JSON | None
    constraints: JSON


@strawberry.type(name="RunInputContract")
class RunInputContract:
    schema: JSON | None
    digest: str
    supported: bool
    error: str
    fields: list[RunInputField]
    accepts_inputs: bool
    supports_simple_form: bool


@strawberry.type(name="ReviewedWorkflowDefinition")
class ReviewedWorkflowDefinition:
    guid: GUID
    revision: str
    input_contract: RunInputContract
    definition: WorkflowDefinitionSummaryType


@strawberry.input(name="StartWorkflowDefinitionInput")
class StartWorkflowDefinitionInput:
    definition_id: GUID
    expected_revision: str
    expected_input_schema_digest: str
    request_id: str
    inputs: JSON | None = None
    confirmed: bool = False


@strawberry.type(name="WorkflowDefinitionStart")
class WorkflowDefinitionStartType:
    id: GUID
    request_id: str
    definition_id: GUID
    organization_id: GUID
    definition_revision: str
    input_schema_digest: str
    execution_id: GUID
    temporal_workflow_id: str
    temporal_run_id: str | None
    dispatch_status: str
    dispatch_last_error: str | None


def review_to_type(definition, *, environment_models=None):
    from workflows.reviewed_starts import definition_revision

    contract = input_contract(definition.input_schema)
    return ReviewedWorkflowDefinition(
        guid=GUID(str(definition.guid)),
        revision=definition_revision(definition),
        input_contract=RunInputContract(
            **{**contract, "fields": [RunInputField(**field) for field in contract["fields"]]}
        ),
        definition=definition_summary(definition, environment_models=environment_models),
    )


def start_to_type(row):
    return WorkflowDefinitionStartType(
        id=GUID(str(row.guid)),
        request_id=row.request_id,
        definition_id=GUID(str(row.definition.guid)),
        organization_id=GUID(str(row.organization.guid)),
        definition_revision=row.definition_revision,
        input_schema_digest=row.input_schema_digest,
        execution_id=GUID(str(row.execution.guid)),
        temporal_workflow_id=row.execution.workflow_id,
        temporal_run_id=row.execution.run_id or None,
        dispatch_status=row.dispatch_status,
        dispatch_last_error=row.dispatch_last_error or None,
    )
