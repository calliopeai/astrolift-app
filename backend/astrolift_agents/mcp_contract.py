"""Authoritative, serializable contract for Astrolift's MCP tool surface."""

from __future__ import annotations

from typing import Any

from astrolift_identity.api_tokens import (
    PLAINTEXT_PREFIX,
    SCOPE_MCP_DISPATCH,
    SCOPE_MCP_READ,
    SCOPE_MCP_WRITE,
    SCOPE_PROJECT_WRITE,
    SCOPE_READ_APPS,
)
from core.permissions import Permission

PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-03-26", "2025-06-18", PROTOCOL_VERSION)
SERVER_NAME = "astrolift"
SERVER_VERSION = "1.0"
MCP_ROUTE = "api/mcp/v1/"


def _schema(properties: dict[str, Any], *, required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


MCP_TOOL_META: dict[str, dict[str, Any]] = {
    "astrolift_list_agents": {
        "description": "List registered agents and their source/package delivery state.",
        "scope": SCOPE_MCP_READ,
        "permission": Permission.AGENT_READ,
        "inputSchema": _schema(
            {"project_slug": {"type": "string", "description": "Optional project slug filter."}}
        ),
    },
    "astrolift_get_agent": {
        "description": "Read one canonical Agent Package, including skills/tools and source slice.",
        "scope": SCOPE_MCP_READ,
        "permission": Permission.AGENT_READ,
        "inputSchema": _schema({"agent_slug": {"type": "string"}}, required=("agent_slug",)),
    },
    "astrolift_get_task": {
        "description": "Read one agent task's lifecycle, result, and failure telemetry.",
        "scope": SCOPE_MCP_READ,
        "permission": Permission.AGENT_READ,
        "inputSchema": _schema({"task_id": {"type": "string"}}, required=("task_id",)),
    },
    "astrolift_get_task_by_client_request_id": {
        "description": (
            "Recover the caller's visible task by dispatch request UUID without dispatching. "
            "Returns task: null when no accessible task matches."
        ),
        "scope": SCOPE_MCP_READ,
        "permission": Permission.AGENT_READ,
        "inputSchema": _schema(
            {"client_request_id": {"type": "string", "format": "uuid"}},
            required=("client_request_id",),
        ),
    },
    "astrolift_list_tasks": {
        "description": "Discover agent runs, lifecycle and runtime placement, newest first, with pagination.",
        "scope": SCOPE_MCP_READ,
        "permission": Permission.AGENT_READ,
        "inputSchema": _schema(
            {
                "status": {"type": "string", "description": "Filter by task lifecycle status, e.g. running."},
                "agent_slug": {"type": "string"},
                "project_slug": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                "cursor": {"type": "string", "description": "next_cursor from the preceding page."},
            }
        ),
    },
    "astrolift_list_runtimes": {
        "description": "List selectable agent runtimes and their configured container images.",
        "scope": SCOPE_MCP_READ,
        "permission": Permission.AGENT_READ,
        "inputSchema": _schema({}),
    },
    "astrolift_run_agent": {
        "description": "Dispatch one registered Task-family agent from its immutable package.",
        "scope": SCOPE_MCP_DISPATCH,
        "permission": Permission.AGENT_DISPATCH,
        "inputSchema": _schema(
            {
                "agent_slug": {"type": "string"},
                "environment_spec_id": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 604800},
                "trigger_payload": {"type": "object"},
                "client_request_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": (
                        "Persist a UUID before dispatch. Reusing it with the same request returns "
                        "the same task for this organization and requester; changed inputs conflict."
                    ),
                },
            },
            required=("agent_slug",),
        ),
    },
    "astrolift_cancel_task": {
        "description": "Hard-stop a running/queued agent and delete its spawned workload.",
        "scope": SCOPE_MCP_DISPATCH,
        "permission": Permission.AGENT_DISPATCH,
        "inputSchema": _schema({"task_id": {"type": "string"}}, required=("task_id",)),
    },
    "astrolift_sync_agent_repo": {
        "description": (
            "Re-scan a single-agent or monorepo source, reconcile selected manifests, "
            "and freeze new immutable package snapshots."
        ),
        "scope": SCOPE_MCP_WRITE,
        "permissions": (Permission.AGENT_CREATE, Permission.AGENT_UPDATE),
        "inputSchema": _schema(
            {
                "project_id": {"type": "string", "description": "Target Astrolift project UUID."},
                "source_repo": {"type": "string", "description": "Host repo handle, e.g. owner/name."},
                "source_kind": {"type": "string", "enum": ["github", "gitlab"]},
                "ref": {"type": "string", "description": "Branch, tag, or immutable commit SHA."},
                "default_branch": {"type": "string"},
                "deploy_branch": {"type": "string"},
                "manifest_paths": {"type": "array", "items": {"type": "string"}},
            },
            required=("project_id", "source_repo"),
        ),
    },
    "astrolift_import_agent_spec": {
        "description": (
            "Preview or persist an AGENTS.md, native Agent Package, Langflow, or Flowise "
            "definition. Returns explicit conversion gaps and never persists a non-runnable result."
        ),
        "scope": SCOPE_MCP_WRITE,
        "permission": Permission.AGENT_CREATE,
        "inputSchema": _schema(
            {
                "format": {
                    "type": "string",
                    "enum": ["agents_md", "astrolift_package", "langflow", "flowise"],
                },
                "payload": {
                    "description": "Definition object, or raw Markdown for AGENTS.md.",
                    "oneOf": [{"type": "object"}, {"type": "string"}],
                },
                "options": {"type": "object"},
                "persist": {"type": "boolean"},
                "project_id": {"type": "string"},
                "slug": {"type": "string"},
            },
            required=("format", "payload"),
        ),
    },
    "astrolift_list_project_resource_clusters": {
        "description": "List managed clusters that can host shared resources for a project.",
        "scope": SCOPE_MCP_READ,
        "additional_scopes": (SCOPE_READ_APPS,),
        "permission": Permission.PROJECT_READ,
        "inputSchema": _schema({"project_id": {"type": "string"}}, required=("project_id",)),
    },
    "astrolift_list_project_resource_catalog": {
        "description": (
            "List available and planned managed-resource variants and configuration schemas "
            "for a project's target cluster."
        ),
        "scope": SCOPE_MCP_READ,
        "additional_scopes": (SCOPE_READ_APPS,),
        "permission": Permission.PROJECT_READ,
        "inputSchema": _schema(
            {
                "project_id": {"type": "string"},
                "cluster_id": {"type": "string"},
            },
            required=("project_id", "cluster_id"),
        ),
    },
    "astrolift_list_project_resources": {
        "description": (
            "List a project's shared managed resources, lifecycle state, and app/agent attachments."
        ),
        "scope": SCOPE_MCP_READ,
        "additional_scopes": (SCOPE_READ_APPS,),
        "permission": Permission.PROJECT_READ,
        "inputSchema": _schema({"project_id": {"type": "string"}}, required=("project_id",)),
    },
    "astrolift_preview_project_resource_cost": {
        "description": "Preview one shared resource through the provider's live pricing API.",
        "scope": SCOPE_MCP_READ,
        "additional_scopes": (SCOPE_READ_APPS,),
        "permission": Permission.PROJECT_READ,
        "inputSchema": _schema(
            {"managed_service_id": {"type": "string"}},
            required=("managed_service_id",),
        ),
    },
    "astrolift_provision_project_resource": {
        "description": (
            "Provision a shared managed resource and optionally attach project app or agent consumers."
        ),
        "scope": SCOPE_MCP_WRITE,
        "additional_scopes": (SCOPE_PROJECT_WRITE,),
        "permission": Permission.PROJECT_UPDATE,
        "inputSchema": _schema(
            {
                "project_id": {"type": "string"},
                "cluster_id": {"type": "string"},
                "kind": {"type": "string"},
                "name": {"type": "string"},
                "variant": {"type": "string"},
                "environment_name": {"type": "string"},
                "config": {"type": "object"},
                "agent_environment_spec_slugs": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "app_environment_ids": {"type": "array", "items": {"type": "string"}},
            },
            required=("project_id", "cluster_id", "kind"),
        ),
    },
    "astrolift_attach_project_resource": {
        "description": "Attach a shared resource to exactly one project app environment or agent spec.",
        "scope": SCOPE_MCP_WRITE,
        "additional_scopes": (SCOPE_PROJECT_WRITE,),
        "permission": Permission.PROJECT_UPDATE,
        "inputSchema": _schema(
            {
                "managed_service_id": {"type": "string"},
                "agent_environment_spec_slug": {"type": "string"},
                "app_environment_id": {"type": "string"},
            },
            required=("managed_service_id",),
        ),
    },
    "astrolift_detach_project_resource": {
        "description": "Detach one app or agent consumer from a shared managed resource.",
        "scope": SCOPE_MCP_WRITE,
        "additional_scopes": (SCOPE_PROJECT_WRITE,),
        "permission": Permission.PROJECT_UPDATE,
        "inputSchema": _schema({"attachment_id": {"type": "string"}}, required=("attachment_id",)),
    },
    "astrolift_update_project_resource": {
        "description": (
            "Update a shared resource's name or in-place-editable configuration and reconcile it."
        ),
        "scope": SCOPE_MCP_WRITE,
        "additional_scopes": (SCOPE_PROJECT_WRITE,),
        "permission": Permission.PROJECT_UPDATE,
        "inputSchema": _schema(
            {
                "managed_service_id": {"type": "string"},
                "name": {"type": "string"},
                "config": {"type": "object"},
            },
            required=("managed_service_id",),
        ),
    },
    "astrolift_reprovision_project_resource": {
        "description": "Start a full reprovision cycle for an existing shared managed resource.",
        "scope": SCOPE_MCP_WRITE,
        "additional_scopes": (SCOPE_PROJECT_WRITE,),
        "permission": Permission.PROJECT_UPDATE,
        "inputSchema": _schema({"managed_service_id": {"type": "string"}}, required=("managed_service_id",)),
    },
    "astrolift_deprovision_project_resource": {
        "description": (
            "Destructively deprovision a shared resource. Echo its exact ID in "
            "confirm_managed_service_id; delete_data and force_destroy default false."
        ),
        "scope": SCOPE_MCP_WRITE,
        "additional_scopes": (SCOPE_PROJECT_WRITE,),
        "permission": Permission.PROJECT_UPDATE,
        "inputSchema": _schema(
            {
                "managed_service_id": {"type": "string"},
                "confirm_managed_service_id": {"type": "string"},
                "delete_data": {"type": "boolean"},
                "force_destroy": {"type": "boolean"},
            },
            required=("managed_service_id", "confirm_managed_service_id"),
        ),
    },
}


def mcp_contract_document() -> dict[str, Any]:
    """Return the caller-independent capability superset published to clients."""
    tools = []
    for name, meta in MCP_TOOL_META.items():
        permissions = tuple(meta.get("permissions") or (meta.get("permission"),))
        tools.append(
            {
                "name": name,
                "description": meta["description"],
                "required_scopes": [meta["scope"], *meta.get("additional_scopes", ())],
                "required_permissions": [str(value) for value in permissions if value is not None],
                "inputSchema": meta["inputSchema"],
            }
        )
    return {
        "schema": "astrolift.mcp.capabilities/v1",
        "endpoint": f"/{MCP_ROUTE}",
        "transport": "streamable-http",
        "protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
        "authorization": {
            "scheme": "bearer",
            "token_prefix": PLAINTEXT_PREFIX,
            "rule": "token scope and user RBAC permission are both required",
        },
        "server": {"name": SERVER_NAME, "protocol_implementation_version": SERVER_VERSION},
        "tools": tools,
    }


__all__ = [
    "MCP_TOOL_META",
    "MCP_ROUTE",
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "mcp_contract_document",
]
