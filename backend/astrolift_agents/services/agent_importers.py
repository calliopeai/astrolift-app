"""Normalize external agent definitions into the canonical Agent Package.

Adapters are deliberately pure: conversion reports every semantic gap and
does not persist. ``persist_imported_agent_package`` is the separate, explicit
write boundary used by GraphQL/MCP after a preview has been reviewed.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any

from django.utils.text import slugify

from astrolift_agents.services.agent_package import (
    AgentPackageError,
    build_agent_package,
    validate_agent_package,
)


@dataclasses.dataclass(frozen=True, slots=True)
class AgentImportGap:
    code: str
    message: str
    severity: str = "warning"
    blocking: bool = False
    source_id: str = ""
    source_type: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class AgentImportResult:
    format: str
    package: dict[str, Any]
    gaps: list[AgentImportGap]
    runnable: bool


class AgentImportError(ValueError):
    """The source is malformed or not the requested format."""


_FORMAT_ALIASES = {
    "agents.md": "agents_md",
    "agents-md": "agents_md",
    "agents_md": "agents_md",
    "astrolift": "astrolift_package",
    "astrolift_package": "astrolift_package",
    "langflow": "langflow",
    "flowise": "flowise",
}


def supported_agent_import_formats() -> list[str]:
    return ["agents_md", "astrolift_package", "flowise", "langflow"]


def _format(value: str) -> str:
    normalized = _FORMAT_ALIASES.get(str(value or "").strip().lower())
    if normalized is None:
        raise AgentImportError(
            f"unknown agent format {value!r}; supported: {', '.join(supported_agent_import_formats())}"
        )
    return normalized


def _source_digest(payload: Any) -> str:
    if isinstance(payload, str):
        body = payload
    else:
        try:
            body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        except (TypeError, ValueError) as exc:
            raise AgentImportError(f"source payload is not JSON serializable: {exc}") from exc
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _runtime_gap(package: dict[str, Any]) -> list[AgentImportGap]:
    if str((package.get("runtime") or {}).get("image") or "").strip():
        return []
    return [
        AgentImportGap(
            code="runtime_image_required",
            message="Select a runnable agent image before importing this package.",
            severity="error",
            blocking=True,
        )
    ]


def _timeout_seconds(options: dict[str, Any]) -> int:
    raw = options.get("timeout_seconds", 300)
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise AgentImportError("timeout_seconds must be an integer")
    if raw < 1 or raw > 604800:
        raise AgentImportError("timeout_seconds must be between 1 and 604800")
    return raw


def _runtime_image(options: dict[str, Any]) -> str:
    raw = options.get("runtime_image", "")
    if not isinstance(raw, str):
        raise AgentImportError("runtime_image must be a string")
    return raw.strip()


def _agents_md(payload: Any, *, options: dict[str, Any]) -> AgentImportResult:
    if isinstance(payload, str):
        content = payload
        source_name = "Imported AGENTS.md agent"
    elif isinstance(payload, dict):
        content = str(payload.get("content") or payload.get("instructions") or "")
        source_name = str(payload.get("name") or "Imported AGENTS.md agent")
    else:
        raise AgentImportError("AGENTS.md payload must be Markdown text or an object with content")
    if not content.strip():
        raise AgentImportError("AGENTS.md content is empty")
    name = str(options.get("name") or source_name).strip()
    image = _runtime_image(options)
    package = build_agent_package(
        agent_name=name,
        manifest_path="AGENTS.md",
        package_config={},
        brief_text=content,
        context_files={},
        skills=[],
        tools=[],
        environment={},
        secret_refs=[],
        runtime={"image": image},
        execution={"run_family": "task", "timeout_seconds": _timeout_seconds(options)},
        imports=[
            {
                "format": "agents_md",
                "mode": "agent",
                "source_sha256": _source_digest(payload),
            }
        ],
    )
    gaps = _runtime_gap(package)
    return AgentImportResult("agents_md", package, gaps, not any(gap.blocking for gap in gaps))


def _native_package(payload: Any, *, options: dict[str, Any]) -> AgentImportResult:
    raw = payload.get("package") if isinstance(payload, dict) and "package" in payload else payload
    try:
        package = validate_agent_package(raw)
    except AgentPackageError as exc:
        raise AgentImportError(str(exc)) from exc
    image = _runtime_image(options)
    if image:
        package["runtime"]["image"] = image
    gaps = _runtime_gap(package)
    if (package.get("delivery") or {}).get("payload_required"):
        gaps.append(
            AgentImportGap(
                code="source_payload_required",
                message=(
                    "This native package requires source bytes. Import it from a repository "
                    "or attach an immutable payload; JSON-only import cannot fabricate them."
                ),
                severity="error",
                blocking=True,
            )
        )
    return AgentImportResult(
        "astrolift_package",
        package,
        gaps,
        not any(gap.blocking for gap in gaps),
    )


def _flow(payload: Any, *, fmt: str, options: dict[str, Any]) -> AgentImportResult:
    if not isinstance(payload, (dict, str)):
        raise AgentImportError(f"{fmt} payload must be a JSON object or JSON string")
    from workflows.importers.base import FlowImportError
    from workflows.importers.registry import import_flow

    try:
        result = import_flow(fmt, payload)
    except FlowImportError as exc:
        raise AgentImportError(str(exc)) from exc
    workflow = dataclasses.asdict(result.manifest)
    definition = workflow["definition"]
    stages = workflow["stages"]
    name = str(options.get("name") or definition.get("name") or f"Imported {fmt} agent")
    mode = str(options.get("mode") or "single_agent").strip().lower()
    if mode not in {"single_agent", "federation"}:
        raise AgentImportError("flow import mode must be 'single_agent' or 'federation'")

    gaps = [
        AgentImportGap(
            code=gap.code,
            message=gap.message,
            severity=gap.severity,
            blocking=False,
            source_id=gap.node_id or "",
            source_type=gap.node_type or "",
        )
        for gap in result.gaps
    ]
    if not stages:
        gaps.append(
            AgentImportGap(
                code="no_runnable_stages",
                message="The imported flow contains no agent, aggregation, or gate stages.",
                severity="error",
                blocking=True,
            )
        )

    stage_lines: list[str] = []
    unresolved_tools: set[str] = set()
    for index, stage in enumerate(stages, start=1):
        role = stage.get("role") or stage.get("prompt") or stage.get("kind") or "stage"
        skills = [str(value) for value in stage.get("skills") or []]
        unresolved_tools.update(skills)
        suffix = f"; requested tools: {', '.join(skills)}" if skills else ""
        stage_lines.append(f"{index}. {role} ({stage.get('kind')}){suffix}")
    brief = f"Imported from {fmt}. Execute the following roles in order as one task:\n\n" + "\n".join(
        stage_lines
    )
    if mode == "single_agent" and len(stages) > 1:
        gaps.append(
            AgentImportGap(
                code="flow_flattened",
                message=(
                    "Multiple visual-flow stages were flattened into one agent task. "
                    "The original graph remains in package.federation for later native orchestration."
                ),
                severity="warning",
            )
        )
    if unresolved_tools:
        gaps.append(
            AgentImportGap(
                code="tool_bindings_unresolved",
                message=(
                    "Imported tool names are prompt hints until matching Astrolift ToolDefs are bound: "
                    + ", ".join(sorted(unresolved_tools))
                ),
                severity="warning",
            )
        )

    image = _runtime_image(options)
    package = build_agent_package(
        agent_name=name,
        manifest_path=f"imports/{fmt}.json",
        package_config={},
        brief_text=brief,
        context_files={},
        skills=[],
        tools=[
            {
                "slug": slugify(tool) or tool,
                "name": tool,
                "adapter": "unresolved_import",
            }
            for tool in sorted(unresolved_tools)
        ],
        environment={},
        secret_refs=[],
        runtime={"image": image},
        execution={
            "run_family": "task",
            "timeout_seconds": _timeout_seconds(options),
            "import_mode": mode,
        },
        imports=[
            {
                "format": fmt,
                "mode": "agent" if mode == "single_agent" else "federation",
                "source_sha256": _source_digest(payload),
            }
        ],
        federation={
            "schema": "astrolift.agent.flow/v1",
            "source_format": fmt,
            "workflow": workflow,
        },
    )
    gaps.extend(_runtime_gap(package))
    if mode == "federation":
        gaps.append(
            AgentImportGap(
                code="stage_bindings_required",
                message=(
                    "Federation mode preserves the graph but needs each agent stage bound to a "
                    "registered Agent Package before it can run."
                ),
                severity="error",
                blocking=True,
            )
        )
    return AgentImportResult(fmt, package, gaps, not any(gap.blocking for gap in gaps))


def import_agent_spec(
    fmt: str,
    payload: Any,
    *,
    options: dict[str, Any] | None = None,
) -> AgentImportResult:
    """Convert one known external definition without writing platform state."""
    normalized = _format(fmt)
    options = options or {}
    if not isinstance(options, dict):
        raise AgentImportError("import options must be an object")
    if normalized == "agents_md":
        return _agents_md(payload, options=options)
    if normalized == "astrolift_package":
        return _native_package(payload, options=options)
    return _flow(payload, fmt=normalized, options=options)


__all__ = [
    "AgentImportError",
    "AgentImportGap",
    "AgentImportResult",
    "import_agent_spec",
    "supported_agent_import_formats",
]
