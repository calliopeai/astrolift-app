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
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.core import signing
from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from astrolift_agents.mcp_contract import (
    MCP_TOOL_META,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
)
from astrolift_identity.api_tokens import (
    SCOPE_MCP_DISPATCH,
    SCOPE_MCP_READ,
    SCOPE_MCP_WRITE,
    has_scope,
)
from core.permissions import Permission, PermissionDenied, check_permission
from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)

_SESSION_SALT = "astrolift.mcp.session.v1"


class McpCallError(RuntimeError):
    def __init__(self, message: str, *, code: str = "tool_error") -> None:
        self.code = code
        super().__init__(message)


ToolHandler = Callable[[HttpRequest, dict[str, Any]], dict[str, Any]]


def _reject_nonfinite_json(value: str):
    raise ValueError(f"non-finite JSON number {value!r} is not allowed")


_TOOL_META = MCP_TOOL_META


def _token(request: HttpRequest):
    token = getattr(request, "_api_token", None)
    if token is None:
        raise McpCallError("an alft_at_ API token is required", code="unauthorized")
    return token


def _authorize(request: HttpRequest, scope: str, *permissions: Permission) -> None:
    token = _token(request)
    if not has_scope(token, scope):
        raise McpCallError(f"API token is missing scope {scope!r}", code="permission_denied")
    for permission in permissions:
        try:
            check_permission(permission)
        except PermissionDenied as exc:
            raise McpCallError(exc.reason, code="permission_denied") from exc


def _may(request: HttpRequest, meta: dict[str, Any]) -> bool:
    try:
        permissions = tuple(meta.get("permissions") or (meta.get("permission"),))
        _authorize(request, meta["scope"], *(p for p in permissions if p is not None))
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


def _agent_rows(org_id: int, *, project_slug: str = ""):
    from django.db.models import Q

    from astrolift_registry.models import Workload

    rows = Workload.objects.filter(
        kind=Workload.Kind.AGENT,
        registered_app__organization_id=org_id,
        registered_app__deleted_at__isnull=True,
        deleted_at__isnull=True,
    ).select_related("registered_app", "registered_app__project", "brief")
    team_id = _team_id()
    if team_id is not None:
        rows = rows.filter(
            Q(registered_app__team_id=team_id)
            | Q(
                registered_app__team_accesses__team_id=team_id,
                registered_app__team_accesses__deleted_at__isnull=True,
            )
        ).distinct()
    if project_slug:
        rows = rows.filter(registered_app__project__slug=project_slug)
    return rows.order_by("slug")


def _serialize_agent(workload, *, include_package: bool) -> dict[str, Any]:
    app = workload.registered_app
    brief = workload.brief
    snapshot = brief.manifest_snapshot if brief and isinstance(brief.manifest_snapshot, dict) else {}
    data: dict[str, Any] = {
        "id": str(workload.guid),
        "name": workload.name,
        "slug": workload.slug,
        "project_slug": app.project.slug if app.project_id else "",
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


def _get_task(_request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from django.db.models import Q

    from astrolift_agents.models import AgentTask

    tasks = AgentTask.objects.select_related("agent_definition").filter(
        guid=str(args.get("task_id") or ""),
        organization_id=_org_id(),
        deleted_at__isnull=True,
    )
    team_id = _team_id()
    if team_id is not None:
        tasks = tasks.filter(
            Q(agent_definition__registered_app__team_id=team_id)
            | Q(
                agent_definition__registered_app__team_accesses__team_id=team_id,
                agent_definition__registered_app__team_accesses__deleted_at__isnull=True,
            )
        )
    task = tasks.distinct().first()
    if task is None:
        raise McpCallError("task not found", code="not_found")
    return {
        "id": str(task.guid),
        "agent_slug": task.agent_definition.slug if task.agent_definition_id else "",
        "status": task.status,
        "timeout_seconds": task.timeout_seconds,
        "external_id": task.external_id,
        "pod_name": task.pod_name,
        "namespace": task.namespace,
        "created_at": _iso(task.created_at),
        "queued_at": _iso(task.queued_at),
        "started_at": _iso(task.started_at),
        "ended_at": _iso(task.ended_at),
        "result": task.result,
        "failure": task.failure,
    }


def _run_agent(request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from astrolift_agents.services.agent_dispatch import AgentDispatchError, dispatch_registered_agent
    from astrolift_workflows.inputs import Actor

    token = _token(request)
    actor = Actor(
        kind="api_token",
        user_id=token.user_id,
        token_id=token.pk,
        display=token.name,
    )
    try:
        task = dispatch_registered_agent(
            organization_id=_org_id(),
            team_id=_team_id(),
            agent_slug=str(args.get("agent_slug") or ""),
            actor=actor,
            environment_spec_guid=str(args.get("environment_spec_id") or ""),
            trigger_payload=args.get("trigger_payload") or None,
            timeout_seconds=args.get("timeout_seconds"),
            trigger="mcp",
        )
    except AgentDispatchError as exc:
        raise McpCallError(exc.message, code=exc.code) from exc
    return {"task_id": str(task.guid), "status": task.status, "timeout_seconds": task.timeout_seconds}


def _cancel_task(_request: HttpRequest, args: dict[str, Any]) -> dict[str, Any]:
    from django.db.models import Q

    from astrolift_agents.models import AgentTask
    from astrolift_registry.models import AppTeamAccess
    from astrolift_workflows.activities.agent_stage import _cancel_agent_task_sync

    task_id = str(args.get("task_id") or "")
    tasks = AgentTask.objects.filter(
        guid=task_id,
        organization_id=_org_id(),
        deleted_at__isnull=True,
    )
    team_id = _team_id()
    if team_id is not None:
        tasks = tasks.filter(
            Q(agent_definition__registered_app__team_id=team_id)
            | Q(
                agent_definition__registered_app__team_accesses__team_id=team_id,
                agent_definition__registered_app__team_accesses__deleted_at__isnull=True,
                agent_definition__registered_app__team_accesses__access_level__in=(
                    AppTeamAccess.AccessLevel.DEPLOYER.value,
                    AppTeamAccess.AccessLevel.OWNER.value,
                ),
            )
        )
    task = tasks.distinct().first()
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


_HANDLERS: dict[str, ToolHandler] = {
    "astrolift_list_agents": _list_agents,
    "astrolift_get_agent": _get_agent,
    "astrolift_get_task": _get_task,
    "astrolift_run_agent": _run_agent,
    "astrolift_cancel_task": _cancel_task,
    "astrolift_sync_agent_repo": _sync_agent_repo,
    "astrolift_import_agent_spec": _import_agent_spec,
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


def _tool_list(request: HttpRequest) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, meta in _TOOL_META.items():
        if not _may(request, meta):
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
        if expected == "integer":
            if "minimum" in rule and value < rule["minimum"]:
                raise McpCallError(f"{name} is below its minimum", code="invalid_arguments")
            if "maximum" in rule and value > rule["maximum"]:
                raise McpCallError(f"{name} exceeds its maximum", code="invalid_arguments")
        if expected == "array" and (rule.get("items") or {}).get("type") == "string":
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
        _validate_tool_arguments(meta, args)
        permissions = tuple(meta.get("permissions") or (meta.get("permission"),))
        _authorize(request, meta["scope"], *(p for p in permissions if p is not None))
        payload = handler(request, args)
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
                    "and sync source repos. Secret values are intentionally unavailable over MCP."
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
                    "content": [{"type": "text", "text": str(exc)}],
                    "structuredContent": {"code": exc.code, "message": str(exc)},
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
            _authorize(request, SCOPE_MCP_READ, Permission.AGENT_READ)
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
        try:
            _authorize(request, SCOPE_MCP_READ, Permission.AGENT_READ)
        except McpCallError as exc:
            return _rpc_error(request_id, -32001, str(exc), protocol=protocol)
        uri = str(params.get("uri") or "") if isinstance(params, dict) else ""
        prefix, suffix = "astrolift://agents/", "/package"
        if not uri.startswith(prefix) or not uri.endswith(suffix):
            return _rpc_error(request_id, -32002, "resource not found", protocol=protocol)
        slug = uri[len(prefix) : -len(suffix)]
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
