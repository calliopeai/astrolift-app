"""GraphQL types for the configured-Workflow surface (spec 40 §6).

Plain ``@strawberry.type`` types (not ``strawberry_django``) + mapper
functions, mirroring the Temporal-viewer types in this package. Kept free of
module-level ``workflows`` model imports so ``astrolift_workflows.schema``
imports cleanly even when the WORKFLOWS feature (and its ``workflows`` app)
is disabled — the resolvers that build these do their model imports locally.
"""

from __future__ import annotations

from datetime import datetime

import strawberry

JSON = strawberry.scalars.JSON


@strawberry.type(name="WorkflowDefinitionSummary")
class WorkflowDefinitionSummaryType:
    """A tier-1 ``WorkflowDefinition`` (template) as the catalogue + builder
    list it. ``is_global`` (null org) means a read-only platform template
    (clone to edit, spec 40 §2.1)."""

    guid: str
    name: str
    slug: str
    description: str
    pattern_kind: str
    is_enabled: bool
    is_global: bool
    organization_guid: str | None
    stage_count: int
    created_at: datetime


@strawberry.type(name="WorkflowRun")
class WorkflowRunType:
    """A tier-3 run of a configured ``Workflow`` (a ``WorkflowInstance`` on
    the ``configured_workflow`` path, spec 40 §2.3)."""

    guid: str
    current_state: str
    temporal_workflow_id: str | None
    started_at: datetime
    completed_at: datetime | None
    is_completed: bool


@strawberry.type(name="ConfiguredWorkflow")
class ConfiguredWorkflowType:
    """A tier-2 ``Workflow`` — the tenant's named, runnable application of a
    definition to a config (spec 40 §2.2). The entity the Workflows module
    entitlement gates."""

    guid: str
    name: str
    slug: str
    description: str
    trigger_kind: str
    schedule_cron: str | None
    is_enabled: bool
    inputs: JSON
    stage_bindings: JSON
    organization_guid: str | None
    definition_slug: str
    definition_name: str
    pattern_kind: str
    run_count: int
    created_at: datetime
    runs: list[WorkflowRunType]


def definition_summary(definition) -> WorkflowDefinitionSummaryType:
    return WorkflowDefinitionSummaryType(
        guid=str(definition.guid),
        name=definition.name,
        slug=definition.slug or "",
        description=definition.description or "",
        pattern_kind=definition.pattern_kind,
        is_enabled=definition.is_enabled,
        is_global=definition.organization_id is None,
        organization_guid=(None if definition.organization_id is None else str(definition.organization.guid)),
        stage_count=definition.stages.filter(deleted_at__isnull=True).count(),
        created_at=definition.created_at,
    )


def run_to_type(instance) -> WorkflowRunType:
    # WorkflowInstance is a ``Tracking`` model (no ``guid``/``slug``) — its pk
    # is the stable id the run viewer keys on.
    return WorkflowRunType(
        guid=str(instance.pk),
        current_state=instance.current_state,
        temporal_workflow_id=instance.temporal_workflow_id or None,
        started_at=instance.started_at,
        completed_at=instance.completed_at,
        is_completed=instance.completed_at is not None,
    )


def workflow_to_type(workflow, *, with_runs: bool = False) -> ConfiguredWorkflowType:
    runs: list[WorkflowRunType] = []
    if with_runs:
        runs = [
            run_to_type(r) for r in workflow.runs.filter(deleted_at__isnull=True).order_by("-started_at")[:50]
        ]
    return ConfiguredWorkflowType(
        guid=str(workflow.guid),
        name=workflow.name,
        slug=workflow.slug or "",
        description=workflow.description or "",
        trigger_kind=workflow.trigger_kind,
        schedule_cron=workflow.schedule_cron or None,
        is_enabled=workflow.is_enabled,
        inputs=workflow.inputs or {},
        stage_bindings=workflow.stage_bindings or {},
        organization_guid=(None if workflow.organization_id is None else str(workflow.organization.guid)),
        definition_slug=workflow.definition.slug or "",
        definition_name=workflow.definition.name,
        pattern_kind=workflow.definition.pattern_kind,
        run_count=workflow.runs.filter(deleted_at__isnull=True).count(),
        created_at=workflow.created_at,
        runs=runs,
    )
