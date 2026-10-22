"""One read-only PostgreSQL capture for full native workflow review (#2459)."""

from __future__ import annotations

import dataclasses
import hashlib
from contextlib import contextmanager
from uuid import uuid4

from django.db import connections, transaction
from django.db.models import Q

from astrolift_identity import abac
from astrolift_identity.api_tokens import (
    active_member_organizations,
    get_current_api_token,
    token_scope_allows_permission,
)
from astrolift_identity.permission_resolver import resolve_effective_permissions_for_apps
from core.current_credential import current_dispatch_credential
from core.permissions import (
    Permission,
    PermissionDenied,
    PermissionScope,
    ScopeKind,
    _check_permission_decision,
    _scopes_memo,
    check_permission,
    check_permission_any_scope,
    is_platform_operator,
)
from core.run_input_contract import canonical_bytes, digest, validate_inputs
from core.tenancy import get_current_tenant
from workflows.manifest import definition_to_manifest, emit_workflow_manifest
from workflows.models import Workflow, WorkflowDefinition
from workflows.reviewed_starts import ReviewedStartError, _definition_graph, definition_revision
from workflows.scopes import reviewed_definition_scope, visible_project_owned

CANONICALIZATION = "ASTROLIFT_WORKFLOW_REVIEW_V1"
MAX_REVIEW_BYTES = 2 * 1024 * 1024
MAX_PLAN_BYTES = 2 * 1024 * 1024
FROZEN_STAGE_FIELDS = (
    "order",
    "kind",
    "role",
    "name",
    "slug",
    "agent_definition_id",
    "agent_ref",
    "definition_id",
    "workflow_ref",
    "environment_spec_slug",
    "skill_refs",
    "on_failure",
    "max_attempts",
    "back_edge",
    "iteration",
    "timeout_seconds",
    "fan_out_count",
    "fan_out_dynamic",
    "prompt",
    "output_key",
    "approvers",
)


class WorkflowReviewError(ReviewedStartError):
    pass


@dataclasses.dataclass
class ReviewCapture:
    definition: object
    graph: dict
    canonical_review: str
    revision: str
    review_digest: str
    root_manifest: str
    captured_at: object
    input_schema_digest: str
    agents: dict
    plans: dict | None = None
    workflow: object | None = None
    configuration: dict | None = None
    configuration_digest: str | None = None
    compiled_plans_digest: str | None = None
    schedule_review_digest: str | None = None


@contextmanager
def capture_connection():
    source = connections["default"]
    if source.vendor != "postgresql":
        raise WorkflowReviewError("Atomic workflow review requires PostgreSQL")
    alias = f"workflow_review_capture_{uuid4().hex}"
    capture = source.copy(alias=alias)
    connections[alias] = capture
    try:
        # This independent connection has not run a query in the caller's transaction.
        with transaction.atomic(using=alias):
            with capture.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                cursor.execute("SELECT statement_timestamp()")
                captured_at = cursor.fetchone()[0]
            yield alias, captured_at
    finally:
        capture.close()
        del connections[alias]


@contextmanager
def current_review_authority(permission=Permission.WORKFLOW_READ):
    from django.contrib.auth import get_user_model

    credential = get_current_api_token()
    original_hash = credential.token_hash if credential else None
    with current_dispatch_credential(permission):
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None or tenant.actor_user_id is None:
            raise PermissionDenied(permission, None, "An authenticated organization actor is required")
        actor = get_user_model().objects.filter(pk=tenant.actor_user_id, is_active=True).first()
        credential = get_current_api_token()
        if actor is None or (credential and credential.token_hash != original_hash):
            raise PermissionDenied(permission, None, "The review credential is unavailable")
        if (
            not (credential is None and is_platform_operator(actor))
            and not active_member_organizations(actor.pk).filter(pk=tenant.organization_id).exists()
        ):
            raise PermissionDenied(permission, None, "Organization membership is unavailable")
        from astrolift_identity.operation_context import agent_region_operation

        attrs = dataclasses.replace(
            abac.attributes_for(actor.pk), cache={}, **agent_region_operation({})[0].attributes()
        )
        memo = _scopes_memo.set({})
        try:
            with abac.request_attributes(attrs):
                check_permission_any_scope(permission)
                yield actor
        finally:
            _scopes_memo.reset(memo)


def authorize_definitions(graph, permission):
    tenant = get_current_tenant()
    check_permission_any_scope(permission)
    ids = set(graph["definitions"])
    rows = WorkflowDefinition.objects.filter(
        Q(organization_id=tenant.organization_id) | Q(organization__isnull=True),
        pk__in=ids,
        deleted_at__isnull=True,
    ).select_related("project__team")
    rows = list(visible_project_owned(rows, tenant.organization_id, permission, "project"))
    if {row.pk for row in rows} != ids:
        raise PermissionDenied(permission, None, "A reviewed definition is outside current authority")
    checked = set()
    for row in rows:
        scope = reviewed_definition_scope(row, tenant.organization_id, permission)
        if scope not in checked:
            check_permission(permission, scope=scope)
            checked.add(scope)


def authorize_agents(agents, permission):
    if not agents:
        return
    from astrolift_registry.models import RegisteredApp, Workload
    from astrolift_registry.scopes import _credential_app_ids, live_app_owners

    tenant = get_current_tenant()
    credential = get_current_api_token()
    if credential and not token_scope_allows_permission(credential, permission.value):
        raise PermissionDenied(permission, None, "The credential does not cover reviewed agents")
    current = list(
        Workload.objects.filter(
            pk__in=agents,
            kind=Workload.Kind.AGENT,
            registered_app__organization_id=tenant.organization_id,
            registered_app__organization__deleted_at__isnull=True,
            registered_app__deleted_at__isnull=True,
        ).select_related("registered_app__project__team", "registered_app__team")
    )
    if {row.pk for row in current} != set(agents):
        raise PermissionDenied(permission, None, "A reviewed agent is unavailable")
    apps = {row.registered_app_id: row.registered_app for row in current}
    live_ids = set(live_app_owners(RegisteredApp.objects.filter(pk__in=apps)).values_list("pk", flat=True))
    ceilings = _credential_app_ids(apps, permission)
    effective = resolve_effective_permissions_for_apps(tenant, apps.values())
    if any(
        ident not in live_ids or ident not in ceilings or permission.value not in effective.get(ident, set())
        for ident in apps
    ):
        raise PermissionDenied(permission, None, "A reviewed agent is outside current authority")
    for app_id in apps:
        _check_permission_decision(
            permission,
            PermissionScope(ScopeKind.APP, app_id),
            lambda app_id=app_id: (
                permission.value in effective.get(app_id, set()),
                "The reviewed agent is outside current authority",
            ),
        )


def _bounded_bytes(value, maximum, label):
    encoded = canonical_bytes(value)
    if len(encoded) > maximum:
        raise WorkflowReviewError(f"The {label} exceeds its supported byte limit")
    return encoded


def _stage_values(stage):
    return {"guid": str(stage.guid), **{name: getattr(stage, name) for name in FROZEN_STAGE_FIELDS}}


def _agent_candidates(graph, organization_id, using, bindings):
    from astrolift_registry.models import RegisteredApp, Workload
    from astrolift_registry.scopes import live_app_owners
    from workflows.target_references import references_filter

    stages = [stage for rows in graph["stages"].values() for stage in rows]
    if bindings is not None and not isinstance(bindings, dict):
        raise WorkflowReviewError("Configured stage bindings must be an object")
    bound_guids = {
        str(binding["agent_workload_id"])
        for binding in (bindings or {}).values()
        if isinstance(binding, dict) and binding.get("agent_workload_id")
    }
    query = (
        Workload.objects.using(using)
        .filter(
            registered_app__in=live_app_owners(
                RegisteredApp.objects.using(using).filter(
                    organization_id=organization_id,
                    organization__deleted_at__isnull=True,
                )
            ),
            kind=Workload.Kind.AGENT,
        )
        .filter(
            Q(pk__in={stage.agent_definition_id for stage in stages if stage.agent_definition_id})
            | references_filter({stage.agent_ref for stage in stages if stage.agent_ref})
            | Q(guid__in=bound_guids)
        )
        .select_related("registered_app")
        .order_by("pk")
    )
    rows = list(query[:2049])
    if len(rows) > 2048:
        raise WorkflowReviewError("The reviewed agent bindings exceed the supported limit")
    found_ids = {row.pk for row in rows}
    source_ids = {stage.agent_definition_id for stage in stages if stage.agent_definition_id is not None}
    if not source_ids <= found_ids:
        raise PermissionDenied(
            Permission.AGENT_READ, None, "A source agent is unavailable or outside current authority"
        )
    if not bound_guids <= {str(row.guid) for row in rows}:
        raise WorkflowReviewError("A configured agent binding is unavailable")
    return {row.pk: row for row in rows}


def compile_plans(definition, graph, agents, organization_id, bindings=None):
    from astrolift_workflows.activities.workflow_stage_activities import _get_workflow_stages_sync

    plans = {}
    for item in graph["definitions"].values():
        plan = _get_workflow_stages_sync(
            item.slug,
            workflow_definition_id=str(item.pk),
            stage_bindings=bindings if item.pk == definition.pk else None,
            review_organization_id=organization_id,
            review_definition_graph=graph,
            review_agent_workloads=agents,
            review_bounded_loops=True,
        )
        freeze_stage_fields(plan, graph["stages"][item.pk], agents)
        plans[str(item.pk)] = plan
    _bounded_bytes(plans, MAX_PLAN_BYTES, "compiled workflow plan")
    return plans


def freeze_stage_fields(plan, stages, agents):
    """Persist every execution-significant field beside the compiled binding."""
    rows = {str(stage.pk): stage for stage in stages}
    for compiled in plan["stages"]:
        compiled["reviewed_stage"] = _stage_values(rows[compiled["stage_id"]])
        for field in ("prompt", "output_key", "environment_spec_slug", "skill_refs", "agent_definition_id"):
            compiled["reviewed_stage"][field] = compiled[field]
        agent = agents.get(compiled["agent_definition_id"])
        if agent:
            compiled["reviewed_agent_guid"] = str(agent.guid)
            compiled["reviewed_agent_version"] = agent.version
            compiled["reviewed_app_guid"] = str(agent.registered_app.guid)
            compiled["reviewed_app_version"] = agent.registered_app.version
            compiled["reviewed_app_team_id"] = agent.registered_app.team_id
            compiled["reviewed_app_project_id"] = agent.registered_app.project_id


def configuration_value(workflow, channels):
    return {
        "workflowGuid": str(workflow.guid),
        "definitionGuid": str(workflow.definition.guid),
        "organizationGuid": str(workflow.organization.guid),
        "ownerUserId": str(workflow.created_by_id) if workflow.created_by_id else None,
        "name": workflow.name,
        "slug": workflow.slug,
        "description": workflow.description,
        "inputs": workflow.inputs,
        "stageBindings": workflow.stage_bindings,
        "triggerKind": workflow.trigger_kind,
        "scheduleCron": workflow.schedule_cron,
        "triggerRef": workflow.trigger_ref,
        "isEnabled": workflow.is_enabled,
        "deliveryChannels": [
            {
                "guid": str(channel.guid),
                "version": channel.version,
                "kind": channel.kind,
                "isActive": channel.is_active,
            }
            for channel in sorted(channels, key=lambda row: str(row.guid))
        ],
    }


def capture_review(*, definition_id=None, workflow_id=None):
    tenant = get_current_tenant()
    with current_review_authority(), capture_connection() as (using, captured_at):
        workflow = None
        if workflow_id is not None:
            workflow = (
                Workflow.objects.using(using)
                .filter(
                    guid=str(workflow_id),
                    organization_id=tenant.organization_id,
                    deleted_at__isnull=True,
                )
                .select_related("definition__project__team", "definition__organization", "organization")
                .first()
            )
            if workflow is None:
                raise WorkflowReviewError("Configured workflow not found")
            definition = workflow.definition
            if definition.deleted_at is not None or definition.organization_id not in (
                None,
                tenant.organization_id,
            ):
                raise WorkflowReviewError("Workflow definition is unavailable")
        else:
            definition = (
                WorkflowDefinition.visible_to_org(tenant.organization_id)
                .using(using)
                .filter(
                    guid=str(definition_id),
                    deleted_at__isnull=True,
                )
                .select_related("project__team", "organization")
                .first()
            )
            if definition is None:
                raise WorkflowReviewError("Workflow definition not found")
        graph = _definition_graph(definition, using=using)
        return materialize_review(definition, graph, workflow, using=using, captured_at=captured_at)


def materialize_review(definition, graph, workflow, *, using, captured_at):
    tenant = get_current_tenant()
    authorize_definitions(graph, Permission.WORKFLOW_READ)
    agents = _agent_candidates(
        graph, tenant.organization_id, using, workflow.stage_bindings if workflow else None
    )
    authorize_agents(agents, Permission.AGENT_READ)
    revision = definition_revision(definition, graph=graph)
    definitions = []
    for row in sorted(graph["definitions"].values(), key=lambda item: str(item.guid)):
        stages = []
        for stage in sorted(graph["stages"][row.pk], key=lambda item: (item.order, str(item.guid))):
            raw = _stage_values(stage)
            raw.pop("definition_id")
            agent = agents.get(raw.pop("agent_definition_id"))
            raw["agentGuid"] = str(agent.guid) if agent else None
            child = graph["edges"].get(stage.pk)
            raw["resolvedWorkflowGuid"] = str(child.guid) if child else None
            stages.append(raw)
        definitions.append(
            {
                "guid": str(row.guid),
                "version": row.version,
                "slug": row.slug,
                "name": row.name,
                "description": row.description,
                "isEnabled": row.is_enabled,
                "organizationGuid": str(row.organization.guid) if row.organization_id else None,
                "projectGuid": str(row.project.guid) if row.project_id else None,
                "modelLabel": row.model_label,
                "pattern": row.pattern_kind,
                "sourceRepo": row.source_repo,
                "sourcePath": row.source_path,
                "sourceRef": row.source_ref,
                "inputSchema": row.input_schema,
                "states": row.states,
                "transitions": row.transitions,
                "stages": stages,
            }
        )
    review = {
        "canonicalization": CANONICALIZATION,
        "definitionGuid": str(definition.guid),
        "revision": revision,
        "definitions": definitions,
        "agents": [
            {
                "guid": str(agent.guid),
                "version": agent.version,
                "appGuid": str(agent.registered_app.guid),
                "appVersion": agent.registered_app.version,
                "appTeamId": str(agent.registered_app.team_id) if agent.registered_app.team_id else None,
                "appProjectId": str(agent.registered_app.project_id)
                if agent.registered_app.project_id
                else None,
            }
            for agent in sorted(agents.values(), key=lambda item: str(item.guid))
        ],
    }
    encoded = _bounded_bytes(review, MAX_REVIEW_BYTES, "workflow review")
    capture = ReviewCapture(
        definition=definition,
        graph=graph,
        canonical_review=encoded.decode(),
        revision=revision,
        review_digest=hashlib.sha256(encoded).hexdigest(),
        root_manifest=emit_workflow_manifest(
            definition_to_manifest(definition, stage_rows=graph["stages"][definition.pk])
        ),
        captured_at=captured_at,
        input_schema_digest=digest(definition.input_schema),
        agents=agents,
        workflow=workflow,
    )
    if workflow:
        from workflows.models import DeliveryChannel

        channels = list(
            DeliveryChannel.all_objects.using(using).filter(workflows=workflow).order_by("pk")[:51]
        )
        if len(channels) > 50 or any(
            channel.organization_id != tenant.organization_id or channel.deleted_at is not None
            for channel in channels
        ):
            raise WorkflowReviewError(
                "A reviewed delivery channel is unavailable or exceeds the supported limit"
            )
        config = configuration_value(workflow, channels)
        config["resolvedInputs"] = validate_inputs(definition.input_schema, workflow.inputs)
        plans = compile_plans(definition, graph, agents, tenant.organization_id, workflow.stage_bindings)
        capture.configuration = config
        capture.configuration_digest = digest(config)
        capture.compiled_plans_digest = digest(plans)
        capture.plans = plans
        capture.schedule_review_digest = digest(
            {
                "reviewDigest": capture.review_digest,
                "configurationDigest": capture.configuration_digest,
                "compiledPlansDigest": capture.compiled_plans_digest,
                "activationIntent": "ENABLE_REVIEWED_SCHEDULE",
            }
        )
    return capture
