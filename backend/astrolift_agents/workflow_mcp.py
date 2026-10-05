"""MCP adapters for native workflow discovery, reviewed starts and exact execution controls."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import cast

from django.core.serializers.json import DjangoJSONEncoder

from core.tenancy import get_current_tenant


def _info(request):
    return SimpleNamespace(context=SimpleNamespace(request=request, user=request.user))


def _payload(value):
    # Serialize declared query types, never ORM objects or their private state.
    return json.loads(json.dumps(dataclasses.asdict(value), cls=DjangoJSONEncoder))


def list_definitions(request, args):
    from astrolift_workflows.schema.queries import WorkflowsQuery

    page = WorkflowsQuery().workflow_definitions_page(
        _info(request),
        search=args.get("search"),
        project_id=args.get("project_id"),
        limit=args.get("limit", 50),
        after=args.get("cursor"),
    )
    return _payload(page)


def get_definition(request, args):
    from astrolift_workflows.schema.queries import WorkflowsQuery

    reviewed = WorkflowsQuery().workflow_definition_by_id(_info(request), id=args["definition_id"])
    return {"definition": _payload(reviewed) if reviewed is not None else None}


def set_definition_enabled(request, args):
    from astrolift_agents.views.mcp import _authorize
    from astrolift_identity.api_tokens import SCOPE_MCP_DISPATCH, SCOPE_WORKFLOW_TRIGGER
    from workflows.schema.mutations import Mutation

    if args["is_enabled"]:
        _authorize(request, (SCOPE_MCP_DISPATCH, SCOPE_WORKFLOW_TRIGGER))
    activate = cast(Callable[..., object], Mutation().set_workflow_definition_enabled)
    return _mutation_payload(activate(_info(request), **args))


def list_workflows(request, args):
    from astrolift_workflows.schema.queries import WorkflowsQuery

    page = WorkflowsQuery().workflows_page(
        _info(request),
        search=args.get("search"),
        limit=args.get("limit", 50),
        after=args.get("cursor"),
    )
    return _payload(page)


def get_workflow(request, args):
    from astrolift_workflows.schema.queries import WorkflowsQuery

    row = WorkflowsQuery().workflow(_info(request), workflow_id=args["workflow_id"])
    return {"workflow": _payload(row) if row is not None else None}


def _configuration_info(request):
    from astrolift_agents.views.mcp import McpCallError, _authorize
    from astrolift_identity.api_tokens import SCOPE_MCP_DISPATCH, SCOPE_WORKFLOW_TRIGGER
    from core.permissions import Permission, PermissionDenied

    def activation_check():
        try:
            _authorize(request, (SCOPE_MCP_DISPATCH, SCOPE_WORKFLOW_TRIGGER))
        except McpCallError as exc:
            raise PermissionDenied(Permission.WORKFLOW_TRIGGER, None, str(exc)) from exc

    info = _info(request)
    # Native writes call this on the locked resulting configuration and during
    # post-save schedule application, not on a potentially stale gateway read.
    info.context.workflow_activation_check = activation_check
    return info


def create_workflow(request, args):
    from astrolift_workflows.schema.mutations import WorkflowsMutation

    create = cast(Callable[..., object], WorkflowsMutation().create_workflow)
    return _mutation_payload(create(_configuration_info(request), **{"is_enabled": False, **args}))


def update_workflow(request, args):
    from astrolift_workflows.schema.mutations import WorkflowsMutation

    update = cast(Callable[..., object], WorkflowsMutation().update_workflow)
    return _mutation_payload(update(_configuration_info(request), **args))


def delete_workflow(request, args):
    from astrolift_workflows.schema.mutations import WorkflowsMutation

    delete = cast(Callable[..., object], WorkflowsMutation().delete_workflow)
    return _mutation_payload(delete(_configuration_info(request), **args))


def preview_manifest(request, args):
    from astrolift_workflows.schema.manifest import WorkflowManifestQuery

    # Strawberry binds this field to its decorated resolver at runtime.
    preview = cast(Callable[..., object], WorkflowManifestQuery().preview_workflow_manifest)
    return _payload(preview(_info(request), toml=args["toml"]))


def export_manifest(request, args):
    from astrolift_workflows.schema.queries import WorkflowsQuery
    from workflows.manifest import definition_to_manifest, emit_workflow_manifest
    from workflows.models import WorkflowDefinition

    reviewed = WorkflowsQuery().workflow_definition_by_id(_info(request), id=args["definition_id"])
    if reviewed is None:
        return {"ok": False, "toml": None, "error": "no visible workflow definition with this ID"}
    tenant = get_current_tenant()
    if tenant is None:
        return {"ok": False, "toml": None, "error": "no active organization"}
    # Export the exact authorized ID; a slug lookup could select a different
    # organization's definition or substitute an organization/global template.
    definition = WorkflowDefinition.visible_to_org(tenant.organization_id).get(
        guid=args["definition_id"], deleted_at__isnull=True
    )
    return {"ok": True, "toml": emit_workflow_manifest(definition_to_manifest(definition)), "error": None}


def import_manifest(request, args):
    from astrolift_workflows.schema.manifest import WorkflowManifestMutation

    native_import = cast(Callable[..., object], WorkflowManifestMutation().import_workflow_manifest)
    return _mutation_payload(
        native_import(
            _info(request),
            toml=args["toml"],
            preview=args.get("preview", True),
            replace=args.get("replace", False),
            project_id=args.get("project_id"),
        )
    )


def _mutation_payload(result):
    from astrolift_agents.views.mcp import McpCallError

    payload = _payload(result)
    if result.ok:
        return payload
    error = payload["errors"][0] if payload.get("errors") else {}
    message = error.get("message") or "; ".join(error.get("messages") or []) or "workflow operation failed"
    # An uncertain native start can fail with a reserved execution in data.
    # Preserve that identity so the client can recover rather than start again.
    raise McpCallError(message, code=str(error.get("code", "validation")).lower(), result=payload)


def get_schedule(request, args):
    from astrolift_workflows.schema.queries import WorkflowsQuery

    row = WorkflowsQuery().workflow_schedule(_info(request), workflow_id=args["workflow_id"])
    return {"schedule": _payload(row) if row is not None else None}


def reconcile_schedule(request, args):
    from astrolift_agents.views.mcp import _authorize
    from astrolift_identity.api_tokens import SCOPE_MCP_DISPATCH, SCOPE_WORKFLOW_TRIGGER
    from astrolift_workflows.schema.mutations import WorkflowsMutation

    if args["expected_active"]:
        _authorize(request, (SCOPE_MCP_DISPATCH, SCOPE_WORKFLOW_TRIGGER))
    reconcile = cast(Callable[..., object], WorkflowsMutation().reconcile_workflow_schedule)
    return _mutation_payload(
        reconcile(
            _info(request),
            workflow_id=args["workflow_id"],
            expected_version=args["expected_version"],
            expected_active=args["expected_active"],
        )
    )


def start_definition(request, args):
    from astrolift_graphql import GUID
    from workflows.schema.mutations import Mutation
    from workflows.schema.reviewed_start_types import StartWorkflowDefinitionInput

    start = cast(Callable[..., object], Mutation().start_workflow_definition)
    return _mutation_payload(
        start(
            _info(request),
            input=StartWorkflowDefinitionInput(
                definition_id=GUID(args["definition_id"]),
                expected_revision=args["expected_revision"],
                expected_input_schema_digest=args["expected_input_schema_digest"],
                request_id=args["request_id"],
                inputs=args.get("inputs"),
                confirmed=args["confirmed"],
            ),
        )
    )


def get_start(request, args):
    from astrolift_workflows.schema.queries import WorkflowsQuery

    row = WorkflowsQuery().workflow_definition_start_request(_info(request), request_id=args["request_id"])
    return {"start": _payload(row) if row is not None else None}


def list_runs(request, args):
    from astrolift_workflows.schema.queries import WorkflowsQuery
    from astrolift_workflows.schema.workflow_config_types import WorkflowDefinitionRunsFilterInput

    page = WorkflowsQuery().workflow_definition_runs_page(
        _info(request),
        search=args.get("search"),
        limit=args.get("limit", 50),
        after=args.get("cursor"),
        filter=WorkflowDefinitionRunsFilterInput(
            status=args.get("statuses"),
            definition=args.get("definition_slugs"),
            project=args.get("project_slugs"),
            started_by_me=args.get("started_by_me"),
        ),
    )
    return _payload(page)


def get_execution(request, args):
    from astrolift_workflows.schema.queries import WorkflowsQuery

    row = WorkflowsQuery().workflow_execution(_info(request), execution_id=args["execution_id"])
    return {"execution": _payload(row) if row is not None else None}


def _stage_payload(row):
    from strawberry.types.base import get_object_definition

    from workflows.schema.types import WorkflowStageExecutionType

    public_fields = (
        "guid",
        "execution_id",
        "status",
        "attempt_number",
        "round_number",
        "collection_index",
        "fanout_index",
        "started_at",
        "ended_at",
        "output",
        "failure",
        "error_message",
        "created_at",
        "caused_by",
        "fanout_stage_id",
        "fanout_parent_execution_guid",
        "collection_stage_id",
        "collection_parent_execution_guid",
        "stage_guid",
        "stage_kind",
        "stage_order",
        "stage_role",
        "stage_approvers",
        "human_gate_state",
        "human_gate_note",
        "agent_run_guid",
        "child_workflow_run_guid",
        "child_workflow_definition_slug",
        "child_workflow_status",
    )
    definition = get_object_definition(WorkflowStageExecutionType, strict=True)
    payload = {}
    for name in public_fields:
        field = definition.get_field(name)
        if field is None:
            raise TypeError(f"native workflow stage field {name!r} is unavailable")
        # Django-backed query types carry ORM rows; use the same declared field
        # resolvers as GraphQL, without exposing model internals or duplicating
        # gate, child-run and loop-state derivation.
        value = field.base_resolver(row) if field.base_resolver is not None else getattr(row, name)
        payload[name] = (
            dataclasses.asdict(value)
            if dataclasses.is_dataclass(value) and not isinstance(value, type)
            else value
        )
    return payload


def list_execution_stages(request, args):
    from astrolift_agents.views.mcp import McpCallError
    from astrolift_workflows.schema.queries import WorkflowsQuery

    try:
        row = WorkflowsQuery().workflow_execution_stages(
            _info(request),
            execution_id=args["execution_id"],
            limit=args.get("limit", 100),
            after=args.get("cursor"),
        )
    except ValueError as exc:
        raise McpCallError(str(exc), code="invalid_arguments") from exc
    if row is None:
        return {"execution": None}
    projected = dataclasses.replace(
        row,
        stages=dataclasses.replace(
            row.stages,
            items=[_stage_payload(stage) for stage in row.stages.items],
        ),
    )
    return {"execution": _payload(projected)}


def control_execution(request, args):
    from astrolift_workflows.schema.mutations import WorkflowsMutation

    control = cast(Callable[..., object], WorkflowsMutation().control_workflow_execution)
    return _mutation_payload(
        control(
            _info(request),
            execution_id=args["execution_id"],
            workflow_id=args["workflow_id"],
            run_id=args["run_id"],
            action=args["action"],
            reason=args.get("reason", ""),
        )
    )
