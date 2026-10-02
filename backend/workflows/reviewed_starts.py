"""Reviewed definition starts with a durable, never-reused engine identity."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.current_credential import current_dispatch_credential
from core.permissions import Permission, PermissionDenied, check_permission
from core.run_input_contract import InputContractError, canonical_bytes, digest, validate_inputs
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest
from core.tenancy import get_current_tenant
from workflows.models import WorkflowDefinition, WorkflowDefinitionStart, WorkflowInstance, WorkflowStage
from workflows.scopes import reviewed_definition_scope as definition_scope


class ReviewedStartError(ValueError):
    pass


def actor_key() -> str:
    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None or tenant.actor_user_id is None:
        raise ReviewedStartError("An authenticated organization actor is required")
    return f"user:{tenant.actor_user_id}"


MAX_REVIEWED_DEFINITIONS = 128
MAX_REVIEWED_STAGES = 2048


def _definition_graph(definition, *, stages=None, lock=False):
    """Load visible child candidates and stages in bounded breadth-first batches."""
    from workflows.composition import MAX_WORKFLOW_NESTING_DEPTH, resolve_child_definition_from_candidates

    stage_query = WorkflowStage.objects.filter(deleted_at__isnull=True).order_by("pk")
    if lock:
        stage_query = stage_query.select_for_update()
    root_stages = (
        list(stage_query.filter(definition=definition)[: MAX_REVIEWED_STAGES + 1])
        if stages is None
        else list(stages)
    )
    if len(root_stages) > MAX_REVIEWED_STAGES:
        raise ReviewedStartError("The reviewed workflow graph exceeds the stage limit")
    definitions = {definition.pk: definition}
    stage_rows = {definition.pk: root_stages}
    edges = {}
    candidates = [definition]
    fetched_refs = set()
    pending = [definition]
    for depth in range(MAX_WORKFLOW_NESTING_DEPTH + 1):
        refs = {
            stage.workflow_ref.strip()
            for parent in pending
            for stage in stage_rows[parent.pk]
            if stage.kind == WorkflowStage.StageKind.WORKFLOW and stage.workflow_ref.strip()
        }
        missing_refs = refs - fetched_refs
        if missing_refs:
            query = (
                WorkflowDefinition.visible_to_org(definition.organization_id)
                .filter(slug__in=missing_refs, is_enabled=True, deleted_at__isnull=True)
                .select_related("project__team")
                .order_by("pk")
            )
            if lock:
                query = query.select_for_update(of=("self",))
            loaded = list(query[: MAX_REVIEWED_DEFINITIONS + 1])
            if len(candidates) + len(loaded) > MAX_REVIEWED_DEFINITIONS:
                raise ReviewedStartError("The reviewed workflow graph exceeds the definition limit")
            rows = list(
                stage_query.filter(definition_id__in=[item.pk for item in loaded])[: MAX_REVIEWED_STAGES + 1]
            )
            if len(rows) > MAX_REVIEWED_STAGES:
                raise ReviewedStartError("The reviewed workflow graph exceeds the stage limit")
            for item in loaded:
                item.reviewed_stages = [stage for stage in rows if stage.definition_id == item.pk]
            candidates.extend(loaded)
            fetched_refs.update(missing_refs)
        following = []
        for parent in pending:
            for stage in stage_rows[parent.pk]:
                if stage.kind != WorkflowStage.StageKind.WORKFLOW:
                    continue
                child = resolve_child_definition_from_candidates(parent, stage.workflow_ref, candidates)
                edges[stage.pk] = child
                if child is None or child.pk in definitions:
                    continue
                if depth == MAX_WORKFLOW_NESTING_DEPTH:
                    raise ReviewedStartError("Nested workflow depth exceeds the supported limit")
                definitions[child.pk] = child
                stage_rows[child.pk] = child.reviewed_stages
                following.append(child)
        if sum(len(rows) for rows in stage_rows.values()) > MAX_REVIEWED_STAGES:
            raise ReviewedStartError("The reviewed workflow graph exceeds the stage limit")
        if not following:
            break
        pending = following
    return {"definitions": definitions, "stages": stage_rows, "edges": edges}


def definition_revision(definition, *, stages=None, graph=None) -> str:
    graph = graph or _definition_graph(definition, stages=stages)
    cache = {}
    heights = {}

    def height(item, ancestry=()):
        from workflows.composition import MAX_WORKFLOW_NESTING_DEPTH

        if item.pk in ancestry:
            raise ReviewedStartError("The reviewed workflow graph contains invalid nesting")
        if item.pk not in heights:
            children = [
                graph["edges"].get(stage.pk)
                for stage in graph["stages"][item.pk]
                if stage.kind == WorkflowStage.StageKind.WORKFLOW
            ]
            heights[item.pk] = max(
                (1 + height(child, (*ancestry, item.pk)) for child in children if child is not None),
                default=0,
            )
        if heights[item.pk] > MAX_WORKFLOW_NESTING_DEPTH:
            raise ReviewedStartError("Nested workflow depth exceeds the supported limit")
        return heights[item.pk]

    height(definition)

    def revision(item, ancestry=()):
        from workflows.composition import MAX_WORKFLOW_NESTING_DEPTH

        if item.pk in ancestry or len(ancestry) > MAX_WORKFLOW_NESTING_DEPTH:
            raise ReviewedStartError("The reviewed workflow graph contains invalid nesting")
        if item.pk not in cache:
            cache[item.pk] = _graph_definition_revision(item, graph, revision, ancestry)
        return cache[item.pk]

    return revision(definition)


def _graph_definition_revision(definition, graph, revision, ancestry):
    stages = graph["stages"][definition.pk]
    stage_fields = [
        f
        for f in WorkflowStage._meta.concrete_fields
        if f.name
        not in {
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
            "deleted_by",
            "description",
            "name",
        }
    ]
    rows = []
    for stage in sorted(stages, key=lambda s: (s.order, str(s.guid))):
        if stage.deleted_at is not None:
            continue
        rows.append(
            {
                f.name: str(getattr(stage, f.attname)) if f.name in {"guid"} else getattr(stage, f.attname)
                for f in stage_fields
            }
        )
    children = []
    for stage in sorted(stages, key=lambda stage: (stage.order, str(stage.guid))):
        if stage.deleted_at is None and stage.kind == WorkflowStage.StageKind.WORKFLOW:
            child = graph["edges"].get(stage.pk)
            children.append(
                {
                    "stage": str(stage.guid),
                    "guid": str(child.guid) if child else None,
                    "revision": revision(child, (*ancestry, definition.pk)) if child else None,
                }
            )
    return digest(
        {
            "children": children,
            "guid": str(definition.guid),
            "organization": definition.organization_id,
            "project": definition.project_id,
            "enabled": definition.is_enabled,
            "slug": definition.slug,
            "version": definition.version,
            "pattern": definition.pattern_kind,
            "input_schema": definition.input_schema,
            "source_ref": definition.source_ref,
            "states": definition.states,
            "transitions": definition.transitions,
            "stages": rows,
        }
    )


def request_payload(row) -> dict:
    return json.loads(decrypt(EncryptedSecret(row.payload_backend_kind, bytes(row.payload_ciphertext))))


def frozen_plan(run, definition_id: str | None) -> dict | None:
    current = run
    for _ in range(10):
        start = WorkflowDefinitionStart.objects.filter(execution_id=current.pk).first()
        if start is not None:
            return (
                request_payload(start)
                .get("plans", {})
                .get(str(definition_id or current.workflow_definition_id))
            )
        if current.parent_run_id is None:
            return None
        current = current.parent_run
    raise ReviewedStartError("The workflow ancestry exceeds the supported limit")


def find_start(request_id: str):
    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None or tenant.actor_user_id is None:
        return None
    return (
        WorkflowDefinitionStart.objects.filter(
            organization_id=tenant.organization_id,
            actor_key=actor_key(),
            request_id=request_id,
        )
        .select_related("execution", "definition__project", "organization")
        .first()
    )


def _freeze_plans(run, definition, graph=None) -> dict:
    from astrolift_registry.models import RegisteredApp, Workload
    from astrolift_registry.scopes import _credential_app_ids, _credential_scope, live_app_owners
    from astrolift_workflows.activities.workflow_stage_activities import _get_workflow_stages_sync
    from core.permissions import PermissionScope, ScopeKind

    graph = graph or _definition_graph(definition)
    all_stages = [stage for rows in graph["stages"].values() for stage in rows]
    agents = (
        Workload.objects.filter(
            registered_app__in=live_app_owners(
                RegisteredApp.objects.filter(
                    organization_id=run.organization_id,
                    organization__deleted_at__isnull=True,
                    deleted_at__isnull=True,
                )
            ),
            kind=Workload.Kind.AGENT,
            deleted_at__isnull=True,
        )
        .filter(
            Q(pk__in={stage.agent_definition_id for stage in all_stages if stage.agent_definition_id})
            | Q(slug__in={stage.agent_ref for stage in all_stages if stage.agent_ref})
        )
        .order_by("pk")
    )
    workloads = {workload.pk: workload for workload in agents}
    app_ids = {workload.registered_app_id for workload in workloads.values()}
    credential_apps = _credential_app_ids(app_ids, Permission.AGENT_DISPATCH)
    plans = {}
    checked_apps = set()
    for item in graph["definitions"].values():
        if item.pk != definition.pk:
            check_permission(Permission.WORKFLOW_TRIGGER, scope=definition_scope(item, run.organization_id))
        plan = _get_workflow_stages_sync(
            item.slug,
            None,
            None,
            str(item.pk),
            review_organization_id=run.organization_id,
            review_definition_graph=graph,
            review_agent_workloads=workloads,
        )
        plans[str(item.pk)] = plan
        for stage in plan["stages"]:
            if stage["agent_definition_id"] is not None:
                workload = workloads[stage["agent_definition_id"]]
                if workload.registered_app_id in checked_apps:
                    continue
                scope = _credential_scope(
                    PermissionScope(ScopeKind.APP, workload.registered_app_id),
                    Permission.AGENT_DISPATCH,
                    allowed_app_ids=credential_apps,
                )
                check_permission(Permission.AGENT_DISPATCH, scope=scope)
                checked_apps.add(workload.registered_app_id)
    return plans


def reserve_start(
    *, definition_id, expected_revision, expected_input_schema_digest, request_id, inputs, user
):
    tenant = get_current_tenant()
    key = actor_key()
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise InputContractError("requestId must contain 1 to 128 characters")
    if inputs is not None and not isinstance(inputs, dict):
        raise InputContractError("Run inputs must be an object")
    requested = {
        "definition": str(definition_id),
        "revision": expected_revision,
        "schema": expected_input_schema_digest,
        "inputs": inputs or {},
    }
    body_digest = hmac.new(
        settings.SECRET_KEY.encode(), canonical_bytes(requested), hashlib.sha256
    ).hexdigest()
    with transaction.atomic(), current_dispatch_credential(Permission.WORKFLOW_TRIGGER):
        definition = (
            WorkflowDefinition.visible_to_org(tenant.organization_id)
            .select_for_update(of=("self",))
            .select_related("project__team")
            .filter(guid=str(definition_id), deleted_at__isnull=True)
            .first()
        )
        if definition is None:
            raise ReviewedStartError("Workflow definition not found")
        check_permission(
            Permission.WORKFLOW_TRIGGER, scope=definition_scope(definition, tenant.organization_id)
        )
        # The definition lock serializes same-target requests; the actor-scoped key
        # also prevents a request from being silently retargeted to another definition.
        existing = (
            WorkflowDefinitionStart._unscoped.filter(
                organization_id=tenant.organization_id, actor_key=key, request_id=request_id
            )
            .select_related("execution", "definition")
            .first()
        )
        if existing is not None:
            if existing.deleted_at is not None or existing.request_digest != body_digest:
                raise ReviewedStartError("requestId already belongs to a different or deleted start")
            return existing
        stages = list(
            WorkflowStage.objects.select_for_update()
            .filter(definition=definition, deleted_at__isnull=True)
            .order_by("order")[: MAX_REVIEWED_STAGES + 1]
        )
        if not definition.is_enabled or not stages:
            raise ReviewedStartError("Workflow definition is disabled or has no executable stages")
        graph = _definition_graph(definition, stages=stages, lock=True)
        revision = definition_revision(definition, graph=graph)
        if revision != expected_revision or digest(definition.input_schema) != expected_input_schema_digest:
            raise ReviewedStartError("Workflow definition or input contract changed; review it again")
        values = validate_inputs(definition.input_schema, inputs)
        from astrolift_workflows.inputs import Actor
        from core.run_trigger import request_trigger
        from workflows.run_service import build_workflow_definition_run_input

        run, _, _ = build_workflow_definition_run_input(
            definition,
            organization_id=tenant.organization_id,
            actor=Actor(kind="user", user_id=user.pk, display=""),
            trigger_kind=request_trigger(),
        )
        plans = _freeze_plans(run, definition, graph=graph)
        sealed = encrypt_at_rest(
            canonical_bytes({"inputs": values, "plans": plans, "definition_slug": definition.slug})
        )
        row = WorkflowDefinitionStart.objects.create(
            organization_id=tenant.organization_id,
            definition=definition,
            execution=run,
            actor_key=key,
            request_id=request_id,
            request_digest=body_digest,
            definition_revision=revision,
            input_schema_digest=expected_input_schema_digest,
            payload_backend_kind=sealed.backend_kind,
            payload_ciphertext=sealed.backend_ref,
            created_by=user,
            updated_by=user,
        )
        WorkflowInstance.objects.create(
            workflow=definition,
            organization_id=tenant.organization_id,
            current_state="pending",
            temporal_workflow_id=run.workflow_id,
            created_by=user,
            updated_by=user,
        )
        return row


def dispatch_start(row):
    from astrolift_workflows.client import recover_workflow_once, start_workflow_once
    from astrolift_workflows.inputs import Actor, WorkflowDefinitionRunInput

    with transaction.atomic(), current_dispatch_credential(Permission.WORKFLOW_TRIGGER):
        tenant = get_current_tenant()
        current = (
            WorkflowDefinitionStart.objects.select_for_update(of=("self",))
            .select_related("execution", "definition__project")
            .get(pk=row.pk, organization_id=tenant.organization_id, actor_key=actor_key())
        )
        check_permission(
            Permission.WORKFLOW_TRIGGER, scope=definition_scope(current.definition, current.organization_id)
        )
        if current.execution.run_id:
            return current
        if timezone.now() - current.created_at > timedelta(hours=24):
            raise ReviewedStartError(
                "Uncertain start is older than 24 hours; reconcile its exact engine identity without resubmitting"
            )
        if current.dispatch_status == "refused":
            raise ReviewedStartError("This start was refused and cannot be resubmitted")
        payload = request_payload(current)
        argument = WorkflowDefinitionRunInput(
            workflow_definition_slug=payload["definition_slug"],
            workflow_definition_id=str(current.definition_id),
            workflow_run_id=str(current.execution_id),
            trigger_payload=payload["inputs"],
            actor=Actor(kind="user", user_id=current.execution.trigger_actor_user_id, display=""),
        )
        try:
            definition = (
                WorkflowDefinition.visible_to_org(current.organization_id)
                .select_for_update(of=("self",))
                .select_related("project__team")
                .get(pk=current.definition_id)
            )
            stages = list(
                WorkflowStage.objects.select_for_update().filter(
                    definition=definition, deleted_at__isnull=True
                )[: MAX_REVIEWED_STAGES + 1]
            )
            graph = _definition_graph(definition, stages=stages, lock=True)
            stale = (
                not definition.is_enabled
                or definition_revision(definition, graph=graph) != current.definition_revision
            )
            if stale:
                handle = recover_workflow_once(
                    "WorkflowDefinitionRunWorkflow", [argument], workflow_id=current.execution.workflow_id
                )
                if handle is None:
                    raise ReviewedStartError(
                        "Workflow definition changed before submission; review and start with a new requestId"
                    )
            else:
                # Re-evaluate authority at submission, including nested definitions
                # and agent targets; the encrypted reviewed plan remains immutable.
                _freeze_plans(current.execution, definition, graph=graph)
                handle = start_workflow_once(
                    "WorkflowDefinitionRunWorkflow", [argument], workflow_id=current.execution.workflow_id
                )
        except (ReviewedStartError, InputContractError, PermissionDenied):
            raise
        except Exception:
            current.dispatch_status = "uncertain"
            current.dispatch_last_error = (
                "Workflow submission unavailable or response uncertain; retry with the same requestId"
            )
            current.save(update_fields=["dispatch_status", "dispatch_last_error", "updated_at", "version"])
            return current
        current.execution.run_id = handle.run_id
        current.execution.save(update_fields=["run_id", "updated_at", "version"])
        current.dispatch_status = "submitted"
        current.dispatch_last_error = ""
        current.save(update_fields=["dispatch_status", "dispatch_last_error", "updated_at", "version"])
        WorkflowInstance.objects.filter(
            organization_id=current.organization_id,
            temporal_workflow_id=handle.workflow_id,
            completed_at__isnull=True,
        ).update(temporal_run_id=handle.run_id, current_state="running")
        from workflows.run_status import synchronize_workflow_instances

        current.execution.refresh_from_db()
        synchronize_workflow_instances(current.execution)
        return current


def recover_start(row):
    """Read-only engine recovery from the stored encrypted argument and reserved ID."""
    from astrolift_workflows.client import recover_workflow_once
    from astrolift_workflows.inputs import Actor, WorkflowDefinitionRunInput

    tenant = get_current_tenant()
    with transaction.atomic(), current_dispatch_credential(Permission.WORKFLOW_READ):
        current = (
            WorkflowDefinitionStart.objects.select_for_update(of=("self",))
            .select_related("execution", "definition__project")
            .get(pk=row.pk, organization_id=tenant.organization_id, actor_key=actor_key())
        )
        check_permission(
            Permission.WORKFLOW_READ, scope=definition_scope(current.definition, current.organization_id)
        )
        if current.execution.run_id:
            return current
        payload = request_payload(current)
        argument = WorkflowDefinitionRunInput(
            workflow_definition_slug=payload["definition_slug"],
            workflow_definition_id=str(current.definition_id),
            workflow_run_id=str(current.execution_id),
            trigger_payload=payload["inputs"],
            actor=Actor(kind="user", user_id=current.execution.trigger_actor_user_id, display=""),
        )
        try:
            handle = recover_workflow_once(
                "WorkflowDefinitionRunWorkflow", [argument], workflow_id=current.execution.workflow_id
            )
        except Exception:
            return current
        if handle is None:
            return current
        current.execution.run_id = handle.run_id
        current.execution.save(update_fields=["run_id", "updated_at", "version"])
        current.dispatch_status = "submitted"
        current.dispatch_last_error = ""
        current.save(update_fields=["dispatch_status", "dispatch_last_error", "updated_at", "version"])
        from workflows.run_status import synchronize_workflow_instances

        current.execution.refresh_from_db()
        synchronize_workflow_instances(current.execution)
        return current
