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


@strawberry.type(name="WorkflowTopologyStage")
class WorkflowTopologyStageType:
    guid: str
    order: int
    kind: str
    role: str
    agent_ref: str
    agent_guid: str | None
    agent_name: str
    agent_slug: str
    environment_spec_slug: str
    resolved_model: str
    has_prompt: bool
    output_key: str
    skill_refs: list[str]
    fan_out_count: int | None
    fan_out_dynamic: bool
    on_failure: str
    timeout_seconds: int


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
    project_guid: str | None
    project_slug: str
    project_team_slug: str
    source_repo: str
    source_path: str
    source_ref: str
    stage_count: int
    stages: list[WorkflowTopologyStageType]
    created_at: datetime


@strawberry.type(name="WorkflowRun")
class WorkflowRunType:
    """A tier-3 run of a configured ``Workflow`` (a ``WorkflowInstance`` on
    the ``configured_workflow`` path, spec 40 §2.3)."""

    guid: str
    current_state: str
    temporal_workflow_id: str | None
    temporal_run_id: str | None
    started_at: datetime
    completed_at: datetime | None
    is_completed: bool


@strawberry.type(name="WorkflowDefinitionRun")
class WorkflowDefinitionRunType:
    guid: str
    definition_guid: str
    definition_slug: str
    definition_name: str
    project_guid: str | None
    project_slug: str
    status: str
    temporal_workflow_id: str
    temporal_run_id: str | None
    current_stage_order: int | None
    current_stage_role: str
    started_at: datetime | None
    ended_at: datetime | None


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


def environment_model_map(definitions) -> dict[tuple[int, str], str]:
    from astrolift_agents.models import AgentEnvironmentSpec

    refs = {
        (definition.organization_id, stage.environment_spec_slug)
        for definition in definitions
        if definition.organization_id is not None
        for stage in definition.stages.all()
        if stage.deleted_at is None and stage.environment_spec_slug
    }
    if not refs:
        return {}
    org_ids = {org_id for org_id, _slug in refs}
    slugs = {slug for _org_id, slug in refs}
    rows = AgentEnvironmentSpec.objects.filter(
        organization_id__in=org_ids,
        slug__in=slugs,
        deleted_at__isnull=True,
    ).values_list("organization_id", "slug", "env_vars")
    models: dict[tuple[int, str], str] = {}
    for org_id, slug, env_vars in rows:
        values = env_vars if isinstance(env_vars, dict) else {}
        model = next(
            (
                str(values[key])
                for key in ("ANTHROPIC_MODEL", "OPENAI_MODEL", "MODEL_ID", "MODEL")
                if values.get(key)
            ),
            "",
        )
        models[(org_id, slug)] = model
    return models


def definition_summary(
    definition,
    *,
    environment_models: dict[tuple[int, str], str] | None = None,
) -> WorkflowDefinitionSummaryType:
    stages = [stage for stage in definition.stages.all() if stage.deleted_at is None]
    stages.sort(key=lambda stage: stage.order)
    model_map = environment_models or {}
    return WorkflowDefinitionSummaryType(
        guid=str(definition.guid),
        name=definition.name,
        slug=definition.slug or "",
        description=definition.description or "",
        pattern_kind=definition.pattern_kind,
        is_enabled=definition.is_enabled,
        is_global=definition.organization_id is None,
        organization_guid=(None if definition.organization_id is None else str(definition.organization.guid)),
        project_guid=(None if definition.project_id is None else str(definition.project.guid)),
        project_slug=(definition.project.slug if definition.project_id is not None else ""),
        project_team_slug=(definition.project.team.slug if definition.project_id is not None else ""),
        source_repo=definition.source_repo or "",
        source_path=definition.source_path or "",
        source_ref=definition.source_ref or "",
        stage_count=len(stages),
        stages=[
            WorkflowTopologyStageType(
                guid=str(stage.guid),
                order=stage.order,
                kind=stage.kind,
                role=stage.role or "",
                agent_ref=stage.agent_ref or "",
                agent_guid=(
                    str(stage.agent_definition.guid) if stage.agent_definition_id is not None else None
                ),
                agent_name=(stage.agent_definition.name if stage.agent_definition_id is not None else ""),
                agent_slug=(stage.agent_definition.slug if stage.agent_definition_id is not None else ""),
                environment_spec_slug=stage.environment_spec_slug or "",
                resolved_model=model_map.get(
                    (definition.organization_id, stage.environment_spec_slug),
                    "",
                ),
                has_prompt=bool((stage.prompt or "").strip()),
                output_key=stage.output_key or "",
                skill_refs=list(stage.skill_refs or []),
                fan_out_count=stage.fan_out_count,
                fan_out_dynamic=stage.fan_out_dynamic,
                on_failure=stage.on_failure,
                timeout_seconds=stage.timeout_seconds,
            )
            for stage in stages
        ],
        created_at=definition.created_at,
    )


def run_to_type(instance) -> WorkflowRunType:
    # WorkflowInstance is a ``Tracking`` model (no ``guid``/``slug``) — its pk
    # is the stable id the run viewer keys on.
    return WorkflowRunType(
        guid=str(instance.pk),
        current_state=instance.current_state,
        temporal_workflow_id=instance.temporal_workflow_id or None,
        temporal_run_id=instance.temporal_run_id or None,
        started_at=instance.started_at,
        completed_at=instance.completed_at,
        is_completed=instance.completed_at is not None,
    )


def definition_run_to_type(run) -> WorkflowDefinitionRunType:
    definition = run.workflow_definition
    execution = run.current_stage_execution
    stage = execution.stage if execution is not None else None
    return WorkflowDefinitionRunType(
        guid=str(run.guid),
        definition_guid=str(definition.guid),
        definition_slug=definition.slug or "",
        definition_name=definition.name,
        project_guid=(None if definition.project_id is None else str(definition.project.guid)),
        project_slug=(definition.project.slug if definition.project_id is not None else ""),
        status=run.status,
        temporal_workflow_id=run.workflow_id,
        temporal_run_id=run.run_id or None,
        current_stage_order=(stage.order if stage is not None else None),
        current_stage_role=((stage.role or "") if stage is not None else ""),
        started_at=run.started_at,
        ended_at=run.ended_at,
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
