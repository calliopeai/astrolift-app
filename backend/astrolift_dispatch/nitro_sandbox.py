"""Resolve the supported task-agent sandbox selector from its own app manifest."""

from __future__ import annotations

from django.conf import settings

from astrolift_agents.services.agent_package import PACKAGE_SCHEMA
from astrolift_dispatch.pod_hardening import AgentRuntimeClassError, validate_agent_runtime


def selected_task_runtime(workload, environment_spec=None) -> str:
    validate_agent_runtime(str(settings.AGENT_RUNTIME_CLASS or "").strip())
    app = getattr(workload, "registered_app", None)
    if app is None:
        return ""
    manifest = getattr(app, "manifest_normalized", None)
    if manifest is None:
        manifest = {}
    if not isinstance(manifest, dict):
        raise AgentRuntimeClassError("Stored agent manifest must be an object before sandbox selection")
    if manifest.get("schema") == PACKAGE_SCHEMA:
        return ""
    rows = manifest.get("workloads", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise AgentRuntimeClassError("Stored agent manifest workloads must be an array of objects")
    matched = [row for row in rows if row.get("name") == workload.slug]
    if len(matched) > 1:
        raise AgentRuntimeClassError("Stored agent manifest has duplicate workload selectors")
    if not matched or "nitro_sandbox" not in matched[0]:
        return ""
    row = matched[0]
    block = row["nitro_sandbox"]
    if (
        row.get("kind") != "agent"
        or row.get("run_family", "task") != "task"
        or not isinstance(block, dict)
        or set(block) - {"mode", "network_isolated", "policy"}
        or not isinstance(block.get("mode"), str)
        or block.get("mode") not in {"isolated", "confidential"}
        or block.get("network_isolated", False) is not False
        or block.get("policy", "") != ""
    ):
        raise AgentRuntimeClassError(
            "Task-family agents require isolated/confidential Nitro Sandboxes without static policy or network isolation; "
            "confidential policy is generated from the final admitted run Pod"
        )
    configured = str(settings.AGENT_RUNTIME_CLASS or "").strip()
    selected = f"{block['mode']}-sandbox"
    if configured and configured != selected:
        raise AgentRuntimeClassError(
            "Workload Nitro Sandbox selection conflicts with operator AGENT_RUNTIME_CLASS"
        )
    if getattr(environment_spec, "gpu", 0):
        raise AgentRuntimeClassError("Nitro Sandbox task agents do not support GPU device passthrough")
    return selected
