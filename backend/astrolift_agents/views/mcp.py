"""Authenticated, stateless Streamable-HTTP MCP gateway for agent ops.

The gateway deliberately exposes a small least-privilege surface. It does not
proxy arbitrary GraphQL and never offers secret reveal/write operations.
Existing ``alft_at_`` API tokens authenticate requests; token scopes narrow
the caller's normal RBAC grants rather than widening them.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from django.conf import settings
from django.core import signing
from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from astrolift_agents import workflow_mcp
from astrolift_agents.mcp_contract import (
    MCP_TOOL_META,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    WORKFLOW_TOOL_NAMES,
)
from astrolift_agents.scopes import agent_project_scope, agent_task_scope, agent_workload_app_scope
from astrolift_identity.api_tokens import (
    SCOPE_MCP_DISPATCH,
    SCOPE_MCP_READ,
    SCOPE_MCP_WRITE,
    has_scope,
)
from astrolift_services.scopes import (
    managed_service_attachment_scope,
    managed_service_scope_by_guid,
    services_project_scope_by_guid,
)
from astrolift_workflows.import_scopes import workflow_manifest_import_scope
from astrolift_workflows.schedule_operations import workflow_schedule_scope
from config.features import Feature, is_enabled
from core.permissions import (
    Permission,
    PermissionDenied,
    PermissionScope,
    check_permission,
    check_permission_any_scope,
    route_auth,
)
from core.run_trigger import RunTrigger
from core.tenancy import get_current_tenant
from workflows.scopes import definition_scope_by_guid, definition_start_scope, execution_scope_by_id

log = logging.getLogger(__name__)

_SESSION_SALT = "astrolift.mcp.session.v1"


class McpCallError(RuntimeError):
    def __init__(
        self, message: str, *, code: str = "tool_error", result: dict[str, Any] | None = None
    ) -> None:
        self.code = code
        self.result = result
        super().__init__(message)


ToolHandler = Callable[[HttpRequest, dict[str, Any]], dict[str, Any]]


def _reject_nonfinite_json(value: str):
    raise ValueError(f"non-finite JSON number {value!r} is not allowed")


_TOOL_META = MCP_TOOL_META

#: The collection form of a tool's permission scope: held anywhere in the
#: org, with the handler narrowing its rows to the grant.
ANY_SCOPE = "any_scope"

#: Where each tool's permissions are checked (#1866): ``ANY_SCOPE`` for a
#: collection, else a factory over the tool's arguments naming its target.
#: A tool missing here runs the targetless check, where the token's team
#: stands in for the target; the surface guardrail allows that only for the
#: tools on its allowlist.
TOOL_SCOPES: dict[str, Any] = {
    "astrolift_get_workflow_schedule": workflow_schedule_scope(Permission.WORKFLOW_READ),
    "astrolift_reconcile_workflow_schedule": workflow_schedule_scope(Permission.WORKFLOW_UPDATE),
    "astrolift_start_workflow_definition": definition_scope_by_guid(),
    "astrolift_get_workflow_start": definition_start_scope(),
    "astrolift_list_workflow_runs": ANY_SCOPE,
    "astrolift_get_workflow_execution": execution_scope_by_id(),
    "astrolift_list_workflow_execution_stages": execution_scope_by_id(),
    "astrolift_control_workflow_execution": execution_scope_by_id(permission=Permission.WORKFLOW_TRIGGER),
    "astrolift_list_workflow_definitions": ANY_SCOPE,
    "astrolift_get_workflow_definition": definition_scope_by_guid(permission=Permission.WORKFLOW_READ),
    "astrolift_list_workflows": ANY_SCOPE,
    "astrolift_preview_workflow_manifest": ANY_SCOPE,
    "astrolift_export_workflow_manifest": definition_scope_by_guid(permission=Permission.WORKFLOW_READ),
    "astrolift_import_workflow_manifest": workflow_manifest_import_scope,
    "astrolift_list_agents": ANY_SCOPE,
    "astrolift_list_tasks": ANY_SCOPE,
    "astrolift_get_task_by_client_request_id": ANY_SCOPE,
    "astrolift_list_runtimes": ANY_SCOPE,
    "astrolift_get_agent": agent_workload_app_scope("agent_slug"),
    "astrolift_get_task": agent_task_scope("task_id"),
    "astrolift_run_agent": agent_workload_app_scope("agent_slug", Permission.AGENT_DISPATCH),
    "astrolift_cancel_task": agent_task_scope("task_id", Permission.AGENT_DISPATCH),
    "astrolift_sync_agent_repo": agent_project_scope("project_id"),
    "astrolift_import_agent_spec": agent_project_scope("project_id"),
    "astrolift_list_project_resource_clusters": services_project_scope_by_guid(
        permissions=(Permission.PROJECT_READ,)
    ),
    "astrolift_list_project_resource_catalog": services_project_scope_by_guid(
        permissions=(Permission.PROJECT_READ,)
    ),
    "astrolift_list_project_resources": services_project_scope_by_guid(
        permissions=(Permission.PROJECT_READ,)
    ),
    "astrolift_provision_project_resource": services_project_scope_by_guid(
        permissions=(Permission.PROJECT_UPDATE,)
    ),
    "astrolift_preview_project_resource_cost": managed_service_scope_by_guid(
        permissions=(Permission.PROJECT_READ,)
    ),
    "astrolift_attach_project_resource": managed_service_scope_by_guid(
        permissions=(Permission.PROJECT_UPDATE,)
    ),
    "astrolift_update_project_resource": managed_service_scope_by_guid(
        permissions=(Permission.PROJECT_UPDATE,)
    ),
    "astrolift_reprovision_project_resource": managed_service_scope_by_guid(
        permissions=(Permission.PROJECT_UPDATE,)
    ),
    "astrolift_deprovision_project_resource": managed_service_scope_by_guid(
        permissions=(Permission.PROJECT_UPDATE,)
    ),
    "astrolift_detach_project_resource": managed_service_attachment_scope(
        "attachment_id", permissions=(Permission.PROJECT_UPDATE,)
    ),
}


def _operation_for_tool(name: str, args: dict[str, Any]):
    from astrolift_identity.operation_context import agent_region_operation, agent_task_operation

    if name in {"astrolift_start_workflow_definition", "astrolift_get_workflow_start"}:
        return agent_region_operation(args)[0]
    if name in {
        "astrolift_get_workflow_execution",
        "astrolift_list_workflow_execution_stages",
        "astrolift_control_workflow_execution",
    }:
        from astrolift_identity.operation_context import execution_operation

        return execution_operation(args)[0]
    if name in {"astrolift_get_task", "astrolift_cancel_task"}:
        return agent_task_operation("task_id")(args)[0]
    if name in {"astrolift_get_agent", "astrolift_run_agent"}:
        return agent_region_operation(args)[0]
    if name == "astrolift_provision_project_resource":
        from astrolift_services.schema.mutations.managed_services import _creation_operation

        return _creation_operation({"input": args})[0]
    if name in {
        "astrolift_preview_project_resource_cost",
        "astrolift_attach_project_resource",
        "astrolift_update_project_resource",
        "astrolift_reprovision_project_resource",
        "astrolift_deprovision_project_resource",
    }:
        from astrolift_identity.operation_context import managed_service_operation

        return managed_service_operation("managed_service_id")(args)[0]
    if name == "astrolift_detach_project_resource":
        from astrolift_identity.operation_context import managed_service_operation
        from astrolift_services.models import ManagedServiceAttachment
        from core.scope_args import read_guid

        service = (
            ManagedServiceAttachment.objects.filter(
                guid=read_guid(args, "attachment_id"),
                managed_service__project__organization_id=_org_id(),
            )
            .values_list("managed_service__guid", flat=True)
            .first()
        )
        return managed_service_operation("managed_service_id")({"managed_service_id": str(service)})[0]
    return None


def _token(request: HttpRequest):
    token = getattr(request, "_api_token", None)
    if token is None:
        raise McpCallError("an alft_at_ API token is required", code="unauthorized")
    return token


def _authorize(
    request: HttpRequest,
    scopes: str | tuple[str, ...],
    *permissions: Permission,
    permission_scope: PermissionScope | None = None,
    any_scope: bool = False,
) -> None:
    token = _token(request)
    if token.organization_id != _org_id():
        raise McpCallError("API token does not belong to the active organization", code="permission_denied")
    required_scopes = (scopes,) if isinstance(scopes, str) else scopes
    for scope in required_scopes:
        if not has_scope(token, scope):
            raise McpCallError(f"API token is missing scope {scope!r}", code="permission_denied")
    for permission in permissions:
        try:
            if any_scope:
                check_permission_any_scope(permission)
            else:
                check_permission(permission, scope=permission_scope)
        except PermissionDenied as exc:
            raise McpCallError(exc.reason, code="permission_denied") from exc


def _may(request: HttpRequest, meta: dict[str, Any], *, any_scope=False) -> bool:
    try:
        permissions = tuple(meta.get("permissions") or (meta.get("permission"),))
        scopes = (meta["scope"], *meta.get("additional_scopes", ()))
        _authorize(request, scopes, *(p for p in permissions if p is not None), any_scope=any_scope)
    except McpCallError:
        return False
    return True


def _org_id() -> int:
    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None:
        raise McpCallError("no active organization", code="precondition")
    return tenant.organization_id


def _team_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.team_id if tenant is not None else None


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _public_id(value: Any, field: str) -> str:
    raw = str(value or "").strip()
    try:
        uuid.UUID(raw)
    except (AttributeError, TypeError, ValueError) as exc:
        raise McpCallError(f"{field} must be a UUID", code="invalid_arguments") from exc
    return raw


def _agent_rows(org_id: int, *, project_slug: str = ""):
    from astrolift_agents.visibility import agent_workloads

    rows = agent_workloads(org_id).select_related("registered_app", "registered_app__project", "brief")
    if project_slug:
        rows = rows.filter(registered_app__project__slug=project_slug)
    return rows.order_by("slug")


def _serialize_agent(workload, *, include_package: bool) -> dict[str, Any]:
    app = workload.registered_app
    brief = workload.brief
    if brief is not None and brief.organization_id != app.organization_id:
        brief = None
    project = app.project
    if project is not None and project.organization_id != app.organization_id:
        project = None
    snapshot = brief.manifest_snapshot if brief and isinstance(brief.manifest_snapshot, dict) else {}
    data: dict[str, Any] = {
        "id": str(workload.guid),
        "name": workload.name,
        "slug": workload.slug,
        "project_slug": project.slug if project else "",
        "source": {
            "kind": app.source_kind,
            "repo": app.source_repo,
            "ref": app.deploy_branch or app.default_branch,
            "manifest_path": app.manifest_path,
            "last_resynced_at": _iso(app.last_resync_at),
            "webhook_installed": bool(app.source_webhook_installed_at),
            "agent_auto_sync": bool(app.source_webhook_installed_at),
        },
        "run": {
            "family": workload.run_family,
            "mode": workload.run_mode,
            "paused": workload.run_paused,
            "timeout_seconds": workload.tool_timeout_seconds,
            "max_retries": workload.max_retries,
            "max_parallel": workload.run_max_parallel,
        },
        "package": {
            "brief_id": str(brief.guid) if brief else None,
            "content_hash": brief.content_hash if brief else "",
            "status": brief.status if brief else "missing",
            "payload_required": bool(snapshot.get("requires_payload")),
            "payload_storage_ready": bool(
                snapshot.get("payload_storage_ready", brief.storage_key if brief else False)
            ),
        },
    }
    if include_package:
        data["package"]["definition"] = snapshot.get("agent_package") or {}
    return data


def _list_agents(_request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    rows = _agent_rows(_org_id(), project_slug=str(args.get("project_slug") or ""))
    return {"agents": [_serialize_agent(row, include_package=False) for row in rows[:500]]}


def _get_agent(_request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    slug = str(args.get("agent_slug") or "").strip()
    matches = list(_agent_rows(_org_id()).filter(slug=slug)[:2])
    if not matches:
        raise McpCallError("agent not found", code="not_found")
    if len(matches) > 1:
        raise McpCallError(
            f"agent slug {slug!r} is ambiguous; make agent workload slugs unique",
            code="conflict",
        )
    return _serialize_agent(matches[0], include_package=True)


def _task_rows(permission=Permission.AGENT_READ):
    from astrolift_agents.visibility import agent_tasks

    return agent_tasks(_org_id(), permission).select_related("dispatcher")


def _serialize_task(task, *, include_result: bool = True) -> dict[str, Any]:
    dispatcher = task.dispatcher
    if dispatcher is not None and dispatcher.organization_id != task.organization_id:
        dispatcher = None
    definition = task.agent_definition
    if definition is not None and definition.registered_app.organization_id != task.organization_id:
        definition = None
    data = {
        "id": str(task.guid),
        "agent_slug": definition.slug if definition else "",
        "status": task.status,
        "timeout_seconds": task.timeout_seconds,
        "external_id": task.external_id,
        "pod_name": task.pod_name,
        "namespace": task.namespace,
        "created_at": _iso(task.created_at),
        "updated_at": _iso(task.updated_at),
        "queued_at": _iso(task.queued_at),
        "provisioning_at": _iso(task.provisioning_at),
        "started_at": _iso(task.started_at),
        "ended_at": _iso(task.ended_at),
        "dispatcher": {
            "id": str(dispatcher.guid),
            "name": dispatcher.name,
            "backend": dispatcher.backend,
            "cloud": dispatcher.cloud,
            "region": dispatcher.region,
            "status": dispatcher.status,
            "last_heartbeat_at": _iso(dispatcher.last_heartbeat_at),
        }
        if dispatcher and dispatcher.deleted_at is None
        else None,
    }
    if include_result:
        data.update(result=task.result, failure=task.failure)
    return data


def _get_task(_request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    task = _task_rows().filter(guid=_public_id(args.get("task_id"), "task_id")).first()
    if task is None:
        raise McpCallError("task not found", code="not_found")
    return _serialize_task(task)


def _get_task_by_client_request_id(request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    request_id = _public_id(args.get("client_request_id"), "client_request_id")
    task = (
        _task_rows()
        .filter(
            organization_id=_org_id(),
            created_by_id=_token(request).user_id,
            client_request_id=request_id,
        )
        .first()
    )
    return {"task": _serialize_task(task) if task is not None else None}


def _list_tasks(_request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from django.core.exceptions import ValidationError

    from astrolift_agents.models import AgentTask
    from astrolift_graphql import KeysetPage, keyset_page

    tasks = _task_rows()
    status = args.get("status")
    if status:
        if status not in AgentTask.Status.values:
            raise McpCallError("unknown task status", code="invalid_arguments")
        tasks = tasks.filter(status=status)
    if args.get("agent_slug"):
        tasks = tasks.filter(agent_definition__slug=args["agent_slug"])
    if args.get("project_slug"):
        tasks = tasks.filter(agent_definition__registered_app__project__slug=args["project_slug"])
    scope = json.dumps(
        {key: args.get(key) for key in ("status", "agent_slug", "project_slug")}, sort_keys=True
    )
    try:
        page: KeysetPage[AgentTask] = keyset_page(
            tasks,
            cursor=args.get("cursor"),
            limit=args.get("limit"),
            with_total=False,
            cursor_scope=f"mcp.agent_tasks:{scope}",
        )
    except (ValidationError, ValueError) as exc:
        raise McpCallError("invalid task cursor", code="invalid_arguments") from exc
    return {
        "tasks": [_serialize_task(task, include_result=False) for task in page.rows],
        "next_cursor": page.next_cursor,
    }


def _list_runtimes(_request: HttpRequest, _args: dict[str, Any]) -> dict[str, Any]:
    from astrolift_agents.runtime_catalog import catalog_entries

    return {"runtimes": catalog_entries()}


def _run_agent(request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from astrolift_agents.services.agent_dispatch import AgentDispatchError, dispatch_registered_agent
    from astrolift_agents.visibility import workload_rows
    from astrolift_workflows.inputs import Actor

    token = _token(request)
    slug = str(args.get("agent_slug") or "").strip()
    matches = list(
        workload_rows(_org_id(), Permission.AGENT_DISPATCH).filter(
            slug=slug, registered_app__organization_id=_org_id()
        )[:2]
    )
    if not matches:
        raise McpCallError("agent not found", code="not_found")
    if len(matches) > 1:
        raise McpCallError("agent slug is ambiguous", code="conflict")
    actor = Actor(
        kind="api_token",
        user_id=token.user_id,
        token_id=token.pk,
        display=token.name,
    )
    try:
        task = dispatch_registered_agent(
            organization_id=_org_id(),
            team_id=token.team_id,
            agent_slug=slug,
            workload_id=matches[0].pk,
            actor=actor,
            environment_spec_guid=str(args.get("environment_spec_id") or ""),
            trigger_payload=args.get("trigger_payload") or None,
            timeout_seconds=args.get("timeout_seconds"),
            client_request_id=args.get("client_request_id"),
            trigger="mcp",
            trigger_kind=RunTrigger.API,
        )
    except AgentDispatchError as exc:
        raise McpCallError(exc.message, code=exc.code) from exc
    return {"task_id": str(task.guid), "status": task.status, "timeout_seconds": task.timeout_seconds}


def _cancel_task(_request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from astrolift_workflows.activities.agent_stage import _cancel_agent_task_sync

    task_id = _public_id(args.get("task_id"), "task_id")
    task = _task_rows(Permission.AGENT_DISPATCH).filter(guid=task_id, organization_id=_org_id()).first()
    if task is None:
        raise McpCallError("task not found", code="not_found")
    result = _cancel_agent_task_sync(task_id)
    if not result.get("ok"):
        raise McpCallError(str(result.get("error") or "task could not be cancelled"), code="precondition")
    return {"task_id": task_id, "status": result["status"], "workload_deleted": True}


def _sync_agent_repo(_request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from django.db.models import Q

    from astrolift_clusters.models import TenantCluster
    from astrolift_identity.models import Project
    from astrolift_registry.models import RegisteredApp
    from astrolift_registry.services.manifest_sync import register_agent_repo

    org_id = _org_id()
    project = (
        Project.objects.select_related("organization", "team")
        .filter(
            guid=str(args.get("project_id") or ""),
            organization_id=org_id,
            deleted_at__isnull=True,
        )
        .first()
    )
    if project is not None and _team_id() is not None and project.team_id != _team_id():
        project = None
    if project is None:
        raise McpCallError("project not found", code="not_found")
    source_repo = str(args.get("source_repo") or "").strip()
    if not source_repo:
        raise McpCallError("source_repo is required", code="validation")
    default_cluster = (
        TenantCluster.objects.filter(
            Q(organization=project.organization) | Q(organization__isnull=True),
            deleted_at__isnull=True,
            is_active=True,
            lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        )
        .order_by("pk")
        .first()
    )
    anchor = (
        RegisteredApp.objects.filter(
            organization_id=org_id,
            project=project,
            source_repo=source_repo,
            deleted_at__isnull=True,
        )
        .order_by("pk")
        .first()
    )
    source_kind = str(args.get("source_kind") or (anchor.source_kind if anchor else "") or "github")
    default_branch = str(args.get("default_branch") or (anchor.default_branch if anchor else "") or "main")
    deploy_branch = str(
        args.get("deploy_branch") or (anchor.deploy_branch if anchor else "") or default_branch
    )
    ref = str(args.get("ref") or deploy_branch)
    result = register_agent_repo(
        project=project,
        source_kind=source_kind,
        source_repo=source_repo,
        ref=ref,
        default_branch=default_branch,
        deploy_branch=deploy_branch,
        default_cluster=default_cluster,
        manifest_paths=args.get("manifest_paths") or None,
    )
    if result.status != "ok":
        raise McpCallError(result.error or result.status, code=result.status)
    return {
        "status": result.status,
        "agents": [
            {
                "manifest_path": item.manifest_path,
                "slug": item.slug,
                "app_id": str(item.app_guid),
                "created": item.created,
                "notes": item.skill_notes,
            }
            for item in result.agents
        ],
        "workflows": [
            {
                "manifest_path": item.path,
                "slug": item.slug,
                "created": item.created,
            }
            for item in getattr(result, "workflows", [])
        ],
    }


def _import_agent_spec(_request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from astrolift_agents.services.agent_importers import AgentImportError, import_agent_spec

    try:
        result = import_agent_spec(
            str(args.get("format") or ""),
            args.get("payload") or {},
            options=args.get("options") or {},
        )
    except AgentImportError as exc:
        raise McpCallError(str(exc), code="validation") from exc

    persisted: dict[str, Any] | None = None
    if bool(args.get("persist", False)):
        if not result.runnable:
            raise McpCallError(
                "import has blocking gaps and cannot be persisted as runnable",
                code="precondition",
            )
        from astrolift_agents.services.imported_agent_registration import (
            ImportedAgentRegistrationError,
            persist_imported_agent_package,
        )
        from astrolift_identity.models import Project

        project = (
            Project.objects.select_related("organization", "team")
            .filter(
                guid=str(args.get("project_id") or ""),
                organization_id=_org_id(),
                deleted_at__isnull=True,
            )
            .first()
        )
        if project is not None and _team_id() is not None and project.team_id != _team_id():
            project = None
        if project is None:
            raise McpCallError("project not found", code="not_found")
        try:
            registration = persist_imported_agent_package(
                project=project,
                package=result.package,
                slug=str(args.get("slug") or ""),
            )
        except ImportedAgentRegistrationError as exc:
            raise McpCallError(str(exc), code="validation") from exc
        persisted = {
            "app_id": str(registration.app.guid),
            "agent_id": str(registration.workload.guid),
            "agent_slug": registration.workload.slug,
            "environment_spec_id": str(registration.environment_spec.guid),
            "created": registration.created,
        }
    return {
        "format": result.format,
        "runnable": result.runnable,
        "package": result.package,
        "gaps": [dataclasses.asdict(gap) for gap in result.gaps],
        "persisted": persisted,
    }


def _project_for_request(project_id: str):
    from astrolift_identity.models import Project

    rows = Project.objects.select_related("organization", "team").filter(
        guid=_public_id(project_id, "project_id"),
        organization_id=_org_id(),
        deleted_at__isnull=True,
    )
    team_id = _team_id()
    if team_id is not None:
        rows = rows.filter(team_id=team_id)
    project = rows.first()
    if project is None:
        raise McpCallError("project not found", code="not_found")
    return project


def _project_service_for_request(managed_service_id: str):
    from astrolift_services.models import ManagedService

    rows = (
        ManagedService.objects.select_related("project", "tenant_cluster__provider_plugin")
        .prefetch_related(
            "attachments__agent_environment_spec",
            "attachments__app_environment__registered_app",
        )
        .filter(
            guid=_public_id(managed_service_id, "managed_service_id"),
            project__organization_id=_org_id(),
            deleted_at__isnull=True,
        )
    )
    team_id = _team_id()
    if team_id is not None:
        rows = rows.filter(project__team_id=team_id)
    service = rows.first()
    if service is None:
        raise McpCallError("project managed resource not found", code="not_found")
    return service


def _project_attachment_for_request(attachment_id: str):
    from astrolift_services.models import ManagedServiceAttachment

    rows = ManagedServiceAttachment.objects.select_related(
        "managed_service__project",
        "agent_environment_spec",
        "app_environment__registered_app",
    ).filter(
        guid=_public_id(attachment_id, "attachment_id"),
        managed_service__project__organization_id=_org_id(),
        deleted_at__isnull=True,
    )
    team_id = _team_id()
    if team_id is not None:
        rows = rows.filter(managed_service__project__team_id=team_id)
    attachment = rows.first()
    if attachment is None:
        raise McpCallError("project resource attachment not found", code="not_found")
    return attachment


def _serialize_project_resource_attachment(row) -> dict[str, Any]:
    if row.agent_environment_spec_id:
        return {
            "id": str(row.guid),
            "consumer_kind": "agent",
            "consumer_slug": row.agent_environment_spec.slug,
            "environment_name": "default",
        }
    return {
        "id": str(row.guid),
        "consumer_kind": "app",
        "consumer_slug": row.app_environment.registered_app.slug,
        "environment_name": row.app_environment.name,
    }


def _serialize_project_resource(service) -> dict[str, Any]:
    from astrolift_services.provider_links import provider_portal_url
    from astrolift_services.schema.types import _editable_fields_for

    cluster = service.tenant_cluster
    return {
        "id": str(service.guid),
        "project_id": str(service.project.guid),
        "project_slug": service.project.slug,
        "cluster_id": str(cluster.guid),
        "cluster_slug": cluster.slug,
        "provider_plugin_slug": cluster.provider_plugin.slug,
        "name": service.name,
        "kind": service.kind,
        "variant": service.variant or "",
        "environment_name": service.effective_environment_name,
        "status": service.status,
        "status_error": service.status_error or "",
        "config": service.config or {},
        "provider_portal_url": provider_portal_url(service),
        "editable_fields": _editable_fields_for(service),
        "attachments": [
            _serialize_project_resource_attachment(row)
            for row in service.attachments.all()
            if row.deleted_at is None
        ],
        "created_at": _iso(service.created_at),
        "updated_at": _iso(service.updated_at),
    }


def _serialize_catalog_item(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "provider_plugin_slug": row.provider_plugin_slug,
        "kind": row.kind,
        "variant": row.variant,
        "display_name": row.display_name,
        "description": row.description,
        "status": row.status,
        "tier": row.tier,
        "available": row.available,
        "unavailable_reason": row.unavailable_reason,
        "is_default_for_kind": row.is_default_for_kind,
        "size_options": list(row.size_options),
        "config_schema": row.config_schema,
        "binding_envs": list(row.binding_envs),
        "issue_url": row.issue_url,
    }


def _mutation_info(request: HttpRequest):
    return SimpleNamespace(context=SimpleNamespace(request=request, user=request.user))


def _unwrap_project_resource_mutation(result):
    if result.ok:
        return result.data
    if not result.errors:
        raise McpCallError("project resource operation failed")
    error = result.errors[0]
    code = str(getattr(error.code, "value", error.code)).lower()
    raise McpCallError(error.message, code=code)


def _list_project_resource_clusters(_request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from django.db.models import Q

    from astrolift_clusters.models import TenantCluster

    project = _project_for_request(str(args.get("project_id") or ""))
    rows = (
        TenantCluster.objects.select_related("provider_plugin")
        .filter(
            Q(organization_id=project.organization_id) | Q(organization_id__isnull=True),
            deleted_at__isnull=True,
            is_active=True,
            lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        )
        .order_by("name", "guid")
    )
    return {
        "clusters": [
            {
                "id": str(row.guid),
                "name": row.name,
                "slug": row.slug,
                "provider_plugin_slug": row.provider_plugin.slug,
                "region": row.region or "",
                "lifecycle": row.lifecycle,
                "is_active": row.is_active,
            }
            for row in rows
        ]
    }


def _list_project_resource_catalog(_request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from django.db.models import Q

    from astrolift_clusters.models import TenantCluster
    from astrolift_services.managed_service_catalog import list_catalog

    project = _project_for_request(str(args.get("project_id") or ""))
    cluster = (
        TenantCluster.objects.select_related("provider_plugin")
        .filter(
            Q(organization_id=project.organization_id) | Q(organization_id__isnull=True),
            guid=_public_id(args.get("cluster_id"), "cluster_id"),
            deleted_at__isnull=True,
            is_active=True,
            lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        )
        .first()
    )
    if cluster is None:
        raise McpCallError("cluster not found", code="not_found")
    return {
        "project_id": str(project.guid),
        "cluster_id": str(cluster.guid),
        "resources": [
            _serialize_catalog_item(row)
            for row in list_catalog(
                cluster.provider_plugin.slug,
                include_unprovisionable=True,
                include_extended=True,
            )
        ],
    }


def _list_project_resources(_request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from astrolift_services.models import ManagedService

    project = _project_for_request(str(args.get("project_id") or ""))
    rows = (
        ManagedService.objects.select_related("project", "tenant_cluster__provider_plugin")
        .prefetch_related(
            "attachments__agent_environment_spec",
            "attachments__app_environment__registered_app",
        )
        .filter(project=project, deleted_at__isnull=True)
        .order_by("kind", "name", "guid")
    )
    return {
        "project_id": str(project.guid),
        "resources": [_serialize_project_resource(row) for row in rows],
    }


def _preview_project_resource_cost(_request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from dataclasses import asdict

    from astrolift_services.cost_preview import preview_managed_service_cost

    service = _project_service_for_request(str(args.get("managed_service_id") or ""))
    return {"managed_service_id": str(service.guid), **asdict(preview_managed_service_cost(service))}


def _provision_project_resource(request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from astrolift_graphql import GUID
    from astrolift_services.schema.mutations import ProvisionProjectManagedServiceInput, ServicesMutation

    project = _project_for_request(str(args.get("project_id") or ""))
    result = ServicesMutation().provision_project_managed_service(
        _mutation_info(request),
        input=ProvisionProjectManagedServiceInput(
            project_id=GUID(str(project.guid)),
            cluster_id=GUID(_public_id(args.get("cluster_id"), "cluster_id")),
            environment_name=str(args.get("environment_name") or "production"),
            kind=str(args.get("kind") or ""),
            name=str(args["name"]) if "name" in args else None,
            variant=str(args["variant"]) if "variant" in args else None,
            config=dict(args.get("config") or {}),
            agent_environment_spec_slugs=list(args.get("agent_environment_spec_slugs") or []),
            app_environment_ids=[
                GUID(_public_id(value, "app_environment_ids"))
                for value in args.get("app_environment_ids") or []
            ],
        ),
    )
    data = _unwrap_project_resource_mutation(result)
    return _serialize_project_resource(_project_service_for_request(str(data.id)))


def _attach_project_resource(request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from astrolift_graphql import GUID
    from astrolift_services.schema.mutations import AttachProjectManagedServiceInput, ServicesMutation

    service = _project_service_for_request(str(args.get("managed_service_id") or ""))
    result = ServicesMutation().attach_project_managed_service(
        _mutation_info(request),
        input=AttachProjectManagedServiceInput(
            managed_service_id=GUID(str(service.guid)),
            agent_environment_spec_slug=args.get("agent_environment_spec_slug"),
            app_environment_id=(
                GUID(_public_id(args["app_environment_id"], "app_environment_id"))
                if "app_environment_id" in args
                else None
            ),
        ),
    )
    data = _unwrap_project_resource_mutation(result)
    return _serialize_project_resource_attachment(_project_attachment_for_request(str(data.id)))


def _detach_project_resource(request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from astrolift_graphql import GUID
    from astrolift_services.schema.mutations import DetachProjectManagedServiceInput, ServicesMutation

    attachment = _project_attachment_for_request(str(args.get("attachment_id") or ""))
    result = ServicesMutation().detach_project_managed_service(
        _mutation_info(request),
        input=DetachProjectManagedServiceInput(attachment_id=GUID(str(attachment.guid))),
    )
    data = _unwrap_project_resource_mutation(result)
    return {
        "attachment": {
            "id": str(data.id),
            "consumer_kind": data.consumer_kind,
            "consumer_slug": data.consumer_slug,
            "environment_name": data.environment_name,
        },
        "detached": True,
    }


def _update_project_resource(request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from astrolift_graphql import GUID
    from astrolift_services.schema.mutations import ServicesMutation, UpdateManagedServiceInput

    service = _project_service_for_request(str(args.get("managed_service_id") or ""))
    result = ServicesMutation().update_project_managed_service(
        _mutation_info(request),
        input=UpdateManagedServiceInput(
            id=GUID(str(service.guid)),
            name=str(args["name"]) if "name" in args else None,
            config=dict(args["config"]) if "config" in args else None,
        ),
    )
    data = _unwrap_project_resource_mutation(result)
    return _serialize_project_resource(_project_service_for_request(str(data.id)))


def _reprovision_project_resource(request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from astrolift_graphql import GUID
    from astrolift_services.schema.mutations import ReprovisionManagedServiceInput, ServicesMutation

    service = _project_service_for_request(str(args.get("managed_service_id") or ""))
    result = ServicesMutation().reprovision_project_managed_service(
        _mutation_info(request),
        input=ReprovisionManagedServiceInput(managed_service_id=GUID(str(service.guid))),
    )
    data = _unwrap_project_resource_mutation(result)
    return _serialize_project_resource(_project_service_for_request(str(data.id)))


def _deprovision_project_resource(request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from astrolift_graphql import GUID
    from astrolift_services.schema.mutations import DeprovisionManagedServiceInput, ServicesMutation

    service = _project_service_for_request(str(args.get("managed_service_id") or ""))
    if str(args.get("confirm_managed_service_id") or "") != str(service.guid):
        raise McpCallError(
            "confirm_managed_service_id must exactly match managed_service_id",
            code="precondition",
        )
    result = ServicesMutation().deprovision_project_managed_service(
        _mutation_info(request),
        input=DeprovisionManagedServiceInput(
            id=GUID(str(service.guid)),
            delete_data=bool(args.get("delete_data", False)),
            force_destroy=bool(args.get("force_destroy", False)),
        ),
    )
    _unwrap_project_resource_mutation(result)
    service.refresh_from_db()
    return {
        "id": str(service.guid),
        "status": service.status,
        "delete_data": bool(args.get("delete_data", False)),
        "force_destroy": bool(args.get("force_destroy", False)),
    }


_HANDLERS: dict[str, ToolHandler] = {
    "astrolift_start_workflow_definition": workflow_mcp.start_definition,
    "astrolift_get_workflow_start": workflow_mcp.get_start,
    "astrolift_list_workflow_runs": workflow_mcp.list_runs,
    "astrolift_get_workflow_execution": workflow_mcp.get_execution,
    "astrolift_list_workflow_execution_stages": workflow_mcp.list_execution_stages,
    "astrolift_control_workflow_execution": workflow_mcp.control_execution,
    "astrolift_list_workflow_definitions": workflow_mcp.list_definitions,
    "astrolift_get_workflow_definition": workflow_mcp.get_definition,
    "astrolift_list_workflows": workflow_mcp.list_workflows,
    "astrolift_preview_workflow_manifest": workflow_mcp.preview_manifest,
    "astrolift_export_workflow_manifest": workflow_mcp.export_manifest,
    "astrolift_import_workflow_manifest": workflow_mcp.import_manifest,
    "astrolift_get_workflow_schedule": workflow_mcp.get_schedule,
    "astrolift_reconcile_workflow_schedule": workflow_mcp.reconcile_schedule,
    "astrolift_list_agents": _list_agents,
    "astrolift_get_agent": _get_agent,
    "astrolift_get_task": _get_task,
    "astrolift_get_task_by_client_request_id": _get_task_by_client_request_id,
    "astrolift_list_tasks": _list_tasks,
    "astrolift_list_runtimes": _list_runtimes,
    "astrolift_run_agent": _run_agent,
    "astrolift_cancel_task": _cancel_task,
    "astrolift_sync_agent_repo": _sync_agent_repo,
    "astrolift_import_agent_spec": _import_agent_spec,
    "astrolift_list_project_resource_clusters": _list_project_resource_clusters,
    "astrolift_list_project_resource_catalog": _list_project_resource_catalog,
    "astrolift_list_project_resources": _list_project_resources,
    "astrolift_preview_project_resource_cost": _preview_project_resource_cost,
    "astrolift_provision_project_resource": _provision_project_resource,
    "astrolift_attach_project_resource": _attach_project_resource,
    "astrolift_detach_project_resource": _detach_project_resource,
    "astrolift_update_project_resource": _update_project_resource,
    "astrolift_reprovision_project_resource": _reprovision_project_resource,
    "astrolift_deprovision_project_resource": _deprovision_project_resource,
}


def _audit(request: HttpRequest, name: str, *, decision: str, duration_ms: int, error: str = "") -> None:
    try:
        from astrolift_operations.models import AuditEvent

        token = getattr(request, "_api_token", None)
        safe_name = (name or "unknown")[:64]
        AuditEvent.objects.create(
            organization_id=getattr(token, "organization_id", None),
            actor_kind="api_token",
            actor_id=str(getattr(token, "guid", "")),
            actor_display=getattr(token, "name", ""),
            action=f"mcp.tool.{safe_name}",
            decision=decision,
            target_kind="mcp_tool",
            target_id=safe_name,
            request_ip=(request.META.get("REMOTE_ADDR") or "")[:64],
            request_user_agent=(request.headers.get("User-Agent") or "")[:1024],
            data={"duration_ms": duration_ms, "error": error[:512] if error else ""},
        )
    except Exception:  # noqa: BLE001 — audit failure must not corrupt the MCP response
        log.exception("failed to audit MCP tool call %s", name)


def _has_project_resource_target(request: HttpRequest, meta: dict[str, Any]) -> bool:
    from astrolift_identity.models import Project
    from astrolift_identity.scope_visibility import visible_projects
    from astrolift_services.scopes import live_projects
    from core.permissions import granted_scopes

    projects = live_projects(Project.objects.all())
    token = _token(request)
    permissions = meta.get("permissions") or (meta["permission"],)
    # Organization-wide capability metadata remains available before the first
    # project exists. Scoped credentials must reach an existing live project.
    if token.team_id is None and all(
        granted_scopes(get_current_tenant(), permission).org for permission in permissions
    ):
        return True
    if token.team_id is not None:
        projects = projects.filter(team_id=token.team_id)
    for permission in permissions:
        projects = visible_projects(projects, permission)
    return projects.exists()


def _tool_list(request: HttpRequest) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, meta in _TOOL_META.items():
        if name in WORKFLOW_TOOL_NAMES and not is_enabled(Feature.WORKFLOWS):
            continue
        # Listing has no target: a scoped tool is offered where its grant is
        # held anywhere; the call itself checks the target.
        if not _may(request, meta, any_scope=name in TOOL_SCOPES):
            continue
        # An APP grant cannot authorize its parent project. Project-targeted
        # resource capabilities require at least one live reachable project.
        if name in {
            "astrolift_list_project_resource_clusters",
            "astrolift_list_project_resource_catalog",
            "astrolift_list_project_resources",
            "astrolift_provision_project_resource",
        } and not _has_project_resource_target(request, meta):
            continue
        out.append(
            {
                "name": name,
                "description": meta["description"],
                "inputSchema": meta["inputSchema"],
            }
        )
    return out


def _validate_tool_arguments(meta: dict[str, Any], args: dict[str, Any]) -> None:
    schema = meta["inputSchema"]
    properties = schema.get("properties") or {}
    unknown = sorted(set(args) - set(properties))
    if unknown:
        raise McpCallError(
            f"unknown tool argument(s): {', '.join(unknown)}",
            code="invalid_arguments",
        )
    missing = [name for name in schema.get("required") or [] if name not in args]
    if missing:
        raise McpCallError(
            f"missing required tool argument(s): {', '.join(missing)}",
            code="invalid_arguments",
        )
    type_checks = {
        "string": lambda value: isinstance(value, str),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
        "boolean": lambda value: isinstance(value, bool),
    }
    for name, value in args.items():
        rule = properties[name]
        expected = rule.get("type")
        alternatives = rule.get("oneOf") or []
        if alternatives and not any(
            alternative.get("type") in type_checks and type_checks[alternative["type"]](value)
            for alternative in alternatives
        ):
            labels = ", ".join(str(alternative.get("type")) for alternative in alternatives)
            raise McpCallError(f"{name} must match one of: {labels}", code="invalid_arguments")
        if expected in type_checks and not type_checks[expected](value):
            raise McpCallError(f"{name} must be a {expected}", code="invalid_arguments")
        if "enum" in rule and value not in rule["enum"]:
            raise McpCallError(f"{name} must be one of {rule['enum']}", code="invalid_arguments")
        if expected == "string":
            if "minLength" in rule and len(value) < rule["minLength"]:
                raise McpCallError(f"{name} is below its minimum length", code="invalid_arguments")
            if "maxLength" in rule and len(value) > rule["maxLength"]:
                raise McpCallError(f"{name} exceeds its maximum length", code="invalid_arguments")
            if rule.get("format") == "uuid":
                _public_id(value, name)
        if expected == "integer":
            if "minimum" in rule and value < rule["minimum"]:
                raise McpCallError(f"{name} is below its minimum", code="invalid_arguments")
            if "maximum" in rule and value > rule["maximum"]:
                raise McpCallError(f"{name} exceeds its maximum", code="invalid_arguments")
        if expected == "array" and (rule.get("items") or {}).get("type") == "string":
            if "maxItems" in rule and len(value) > rule["maxItems"]:
                raise McpCallError(f"{name} exceeds its maximum size", code="invalid_arguments")
            if any(not isinstance(item, str) for item in value):
                raise McpCallError(f"{name} items must be strings", code="invalid_arguments")


def _tool_call(request: HttpRequest, params: dict[str, Any]) -> dict[str, Any]:
    name = str(params.get("name") or "")
    started = time.monotonic()
    try:
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            raise McpCallError("tool arguments must be an object", code="invalid_arguments")
        meta = _TOOL_META.get(name)
        handler = _HANDLERS.get(name)
        if meta is None or handler is None:
            raise McpCallError(f"unknown tool {name!r}", code="not_found")
        if name in WORKFLOW_TOOL_NAMES and not is_enabled(Feature.WORKFLOWS):
            raise McpCallError("workflow tools are disabled", code="not_found")
        _validate_tool_arguments(meta, args)
        permissions = tuple(meta.get("permissions") or (meta.get("permission"),))
        scopes = (meta["scope"], *meta.get("additional_scopes", ()))
        from astrolift_identity.abac import operation_attributes

        operation = _operation_for_tool(name, args)
        with operation_attributes(**(operation.attributes() if operation is not None else {})):
            declared = TOOL_SCOPES.get(name)
            try:
                permission_scope = declared(args) if callable(declared) else None
            except PermissionDenied as exc:
                raise McpCallError(exc.reason, code="permission_denied") from exc
            _authorize(
                request,
                scopes,
                *(p for p in permissions if p is not None),
                permission_scope=permission_scope,
                any_scope=declared == ANY_SCOPE,
            )
            try:
                payload = handler(request, args)
            except PermissionDenied as exc:
                raise McpCallError(exc.reason, code="permission_denied") from exc
    except Exception as exc:
        decision = "DENY" if isinstance(exc, McpCallError) else "UNKNOWN"
        _audit(
            request,
            name[:128] or "unknown",
            decision=decision,
            duration_ms=int((time.monotonic() - started) * 1000),
            error=str(exc),
        )
        raise
    _audit(request, name, decision="ALLOW", duration_ms=int((time.monotonic() - started) * 1000))
    return payload


def _origin_allowed(request: HttpRequest) -> bool:
    origin = (request.headers.get("Origin") or "").rstrip("/")
    if not origin:
        return True
    allowed = {str(value).rstrip("/") for value in getattr(settings, "MCP_ALLOWED_ORIGINS", ())}
    return origin in allowed


def _request_protocol(request: HttpRequest) -> str:
    value = str(request.headers.get("MCP-Protocol-Version") or "").strip()
    # MCP requires pre-version-header clients to be interpreted as 2025-03-26.
    return value or "2025-03-26"


def _mint_session(request: HttpRequest) -> str:
    token = _token(request)
    return signing.dumps(
        {"token": str(token.guid), "organization": token.organization_id},
        salt=_SESSION_SALT,
        compress=True,
    )


def _validate_session(request: HttpRequest, *, required: bool) -> None:
    value = str(request.headers.get("Mcp-Session-Id") or "").strip()
    if not value:
        if required:
            raise McpCallError("Mcp-Session-Id is required", code="invalid_session")
        return
    try:
        payload = signing.loads(
            value,
            salt=_SESSION_SALT,
            max_age=int(getattr(settings, "MCP_SESSION_TTL_SECONDS", 3600)),
        )
    except (signing.BadSignature, signing.SignatureExpired) as exc:
        raise McpCallError("Mcp-Session-Id is invalid or expired", code="invalid_session") from exc
    token = _token(request)
    if payload != {"token": str(token.guid), "organization": token.organization_id}:
        raise McpCallError("Mcp-Session-Id does not belong to this token", code="invalid_session")


def _transport_error(request: HttpRequest) -> str:
    if request.method != "POST":
        return ""
    if not str(request.content_type or "").lower().startswith("application/json"):
        return "Content-Type must be application/json"
    accepted = {part.split(";", 1)[0].strip() for part in request.headers.get("Accept", "").split(",")}
    if not {"application/json", "text/event-stream"}.issubset(accepted):
        return "Accept must include application/json and text/event-stream"
    return ""


def _request_body(request: HttpRequest) -> bytes:
    limit = int(getattr(settings, "MCP_MAX_REQUEST_BYTES", 2 * 1024 * 1024))
    try:
        content_length = int(request.META.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        content_length = 0
    if content_length > limit:
        raise McpCallError(f"request body exceeds {limit} bytes", code="request_too_large")
    try:
        body = request.body
    except RequestDataTooBig as exc:
        raise McpCallError(f"request body exceeds {limit} bytes", code="request_too_large") from exc
    if len(body) > limit:
        raise McpCallError(f"request body exceeds {limit} bytes", code="request_too_large")
    return body


def _rpc_result(
    request_id: Any,
    result: dict[str, Any],
    *,
    protocol: str = PROTOCOL_VERSION,
    session_id: str = "",
) -> JsonResponse:
    response = JsonResponse({"jsonrpc": "2.0", "id": request_id, "result": result})
    response["MCP-Protocol-Version"] = protocol
    if session_id:
        response["Mcp-Session-Id"] = session_id
    return response


def _rpc_error(
    request_id: Any,
    code: int,
    message: str,
    *,
    status: int = 200,
    protocol: str = PROTOCOL_VERSION,
) -> JsonResponse:
    response = JsonResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        status=status,
    )
    response["MCP-Protocol-Version"] = protocol
    return response


@csrf_exempt
@ratelimit(key="user_or_ip", rate="240/m", block=True)
@require_http_methods(["POST", "DELETE"])
@route_auth(
    credential="alft_at_ API token with an MCP scope; sessions are refused",
    scope="per tool: its token scope, permission and TOOL_SCOPES entry; resources at the agent's app",
)
def mcp_gateway(request: HttpRequest) -> HttpResponse:
    """Authenticated MCP Streamable HTTP endpoint (JSON response mode)."""
    if not _origin_allowed(request):
        return _rpc_error(None, -32001, "origin is not allowed", status=403)
    try:
        token = _token(request)
    except McpCallError as exc:
        return _rpc_error(None, -32001, str(exc), status=401)
    if not any(
        has_scope(token, scope) for scope in (SCOPE_MCP_READ, SCOPE_MCP_DISPATCH, SCOPE_MCP_WRITE, "admin")
    ):
        return _rpc_error(None, -32001, "API token has no MCP scope", status=403)

    protocol = _request_protocol(request)
    if protocol not in SUPPORTED_PROTOCOL_VERSIONS:
        return _rpc_error(
            None,
            -32022,
            f"unsupported MCP protocol version {protocol!r}",
            status=400,
        )
    if request.method == "DELETE":
        try:
            _validate_session(request, required=protocol == PROTOCOL_VERSION)
        except McpCallError as exc:
            return _rpc_error(None, -32001, str(exc), status=400, protocol=protocol)
        return HttpResponse(status=204)
    transport_error = _transport_error(request)
    if transport_error:
        return _rpc_error(None, -32600, transport_error, status=400, protocol=protocol)

    try:
        body = json.loads(
            _request_body(request) or b"{}",
            parse_constant=_reject_nonfinite_json,
        )
    except McpCallError as exc:
        return _rpc_error(None, -32600, str(exc), status=413, protocol=protocol)
    except (TypeError, ValueError):
        return _rpc_error(None, -32700, "invalid JSON", protocol=protocol)
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        return _rpc_error(
            body.get("id") if isinstance(body, dict) else None,
            -32600,
            "invalid request",
            protocol=protocol,
        )

    request_id = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}
    if method != "initialize":
        try:
            _validate_session(request, required=protocol == PROTOCOL_VERSION)
        except McpCallError as exc:
            return _rpc_error(request_id, -32001, str(exc), status=400, protocol=protocol)
    if method == "notifications/initialized":
        return HttpResponse(status=202)
    if method == "initialize":
        requested = str(params.get("protocolVersion") or "") if isinstance(params, dict) else ""
        negotiated = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        session_id = _mint_session(request)
        return _rpc_result(
            request_id,
            {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Use Astrolift tools to inspect immutable agent packages, dispatch/kill runs, "
                    "sync source repos, inspect native workflows and preview manifests, and "
                    "manage shared project resources. Secret values are "
                    "intentionally unavailable over MCP."
                ),
            },
            protocol=negotiated,
            session_id=session_id,
        )
    if method == "ping":
        return _rpc_result(request_id, {}, protocol=protocol)
    if method == "tools/list":
        return _rpc_result(request_id, {"tools": _tool_list(request)}, protocol=protocol)
    if method == "tools/call":
        try:
            payload = _tool_call(request, params if isinstance(params, dict) else {})
            text = json.dumps(payload, sort_keys=True, default=str)
            return _rpc_result(
                request_id,
                {
                    "content": [{"type": "text", "text": text}],
                    "structuredContent": payload,
                    "isError": False,
                },
                protocol=protocol,
            )
        except McpCallError as exc:
            return _rpc_result(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(exc.result, sort_keys=True, default=str)
                            if exc.result is not None
                            else str(exc),
                        }
                    ],
                    "structuredContent": exc.result
                    if exc.result is not None
                    else {"code": exc.code, "message": str(exc)},
                    "isError": True,
                },
                protocol=protocol,
            )
        except Exception:
            log.exception("MCP tool call failed")
            return _rpc_result(
                request_id,
                {
                    "content": [{"type": "text", "text": "internal tool error"}],
                    "structuredContent": {"code": "internal", "message": "internal tool error"},
                    "isError": True,
                },
                protocol=protocol,
            )
    if method == "resources/list":
        try:
            _authorize(request, SCOPE_MCP_READ, Permission.AGENT_READ, any_scope=True)
        except McpCallError as exc:
            return _rpc_error(request_id, -32001, str(exc), protocol=protocol)
        resources = [
            {
                "uri": f"astrolift://agents/{row.slug}/package",
                "name": f"{row.name} Agent Package",
                "mimeType": "application/json",
            }
            for row in _agent_rows(_org_id())[:500]
        ]
        return _rpc_result(request_id, {"resources": resources}, protocol=protocol)
    if method == "resources/read":
        uri = str(params.get("uri") or "") if isinstance(params, dict) else ""
        prefix, suffix = "astrolift://agents/", "/package"
        if not uri.startswith(prefix) or not uri.endswith(suffix):
            return _rpc_error(request_id, -32002, "resource not found", protocol=protocol)
        slug = uri[len(prefix) : -len(suffix)]
        try:
            _authorize(
                request,
                SCOPE_MCP_READ,
                Permission.AGENT_READ,
                permission_scope=agent_workload_app_scope("agent_slug")({"agent_slug": slug}),
            )
        except McpCallError as exc:
            return _rpc_error(request_id, -32001, str(exc), protocol=protocol)
        try:
            payload = _get_agent(request, {"agent_slug": slug})
        except McpCallError as exc:
            return _rpc_error(request_id, -32002, str(exc), protocol=protocol)
        return _rpc_result(
            request_id,
            {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(payload, sort_keys=True, default=str),
                    }
                ]
            },
            protocol=protocol,
        )
    if method == "prompts/list":
        return _rpc_result(request_id, {"prompts": []}, protocol=protocol)
    return _rpc_error(request_id, -32601, f"method not found: {method}", protocol=protocol)


__all__ = ["mcp_gateway"]
