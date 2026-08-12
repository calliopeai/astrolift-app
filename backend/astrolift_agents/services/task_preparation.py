"""One preparation contract for every AgentTask trigger path.

The task source (manual, webhook, cron, loop, or workflow stage) must not alter
what actually runs.  This module freezes the matching environment spec, VNC
flag, and immutable Agent Package Brief before a task enters QUEUED.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


class AgentTaskPreparationError(RuntimeError):
    """The agent definition cannot be turned into a runnable task packet."""


def default_environment_spec(workload):
    """Return the org-scoped environment recipe matching ``workload.slug``."""
    if workload is None or getattr(workload, "registered_app", None) is None:
        return None
    from astrolift_agents.models import AgentEnvironmentSpec

    return AgentEnvironmentSpec.objects.filter(
        organization_id=workload.registered_app.organization_id,
        slug=workload.slug,
        deleted_at__isnull=True,
    ).first()


def _compatibility_brief(workload, environment_spec):
    """Normalize a pre-package/self-contained workload into canonical IR."""
    from django.utils import timezone

    from astrolift_agents.models import Brief
    from astrolift_agents.services.agent_package import (
        build_agent_package,
        compatibility_snapshot,
    )

    app = workload.registered_app
    primary = workload.containers.filter(is_primary=True).first()
    image = getattr(environment_spec, "image_tag", "") or getattr(primary, "image_ref", "") or ""
    manifest_path = app.manifest_path or "astrolift.toml"
    environment = dict(getattr(environment_spec, "env_vars", None) or {})
    secret_refs = list(getattr(environment_spec, "secret_refs", None) or [])
    package = build_agent_package(
        agent_name=workload.name or workload.slug,
        manifest_path=manifest_path,
        package_config={"root": "."},
        brief_text="",
        context_files={},
        skills=[],
        tools=[],
        environment=environment,
        secret_refs=secret_refs,
        runtime={"image": image, "compatibility_adapter": "registered_workload"},
        execution={
            "run_family": workload.run_family,
            "timeout_seconds": int(workload.tool_timeout_seconds or 300),
        },
    )
    canonical = json.dumps(
        {"organization": str(app.organization.guid), "package": package},
        sort_keys=True,
        separators=(",", ":"),
    )
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    brief = Brief.objects.filter(
        organization=app.organization,
        content_hash=content_hash,
    ).first()
    if brief is None:
        brief = Brief.objects.create(
            organization=app.organization,
            registered_app=app,
            content_hash=content_hash,
            status=Brief.Status.READY,
            manifest_snapshot=compatibility_snapshot(package),
            secrets_refs=secret_refs,
            ttl_seconds=0,
            context={"source": "registered_workload_compatibility"},
            assembled_at=timezone.now(),
        )
    return brief


def prepare_agent_task(task, *, context: dict[str, Any] | None = None):
    """Freeze environment + Agent Package onto an existing DRAFT task.

    Modern registered agents reuse their content-addressed ``Workload.brief``.
    Legacy inline-config agents fall back to dispatch-time repository assembly
    until they are migrated.  No caller-specific prompt/config behavior lives
    here, so all triggers receive the same package.
    """
    from astrolift_agents.models import AgentTask, Brief

    if task.status != AgentTask.Status.DRAFT:
        raise AgentTaskPreparationError(f"task must be draft during preparation (got {task.status})")
    workload = task.agent_definition
    if workload is None:
        raise AgentTaskPreparationError("task has no agent definition")

    fields: list[str] = []
    if task.environment_spec_id is None:
        task.environment_spec = default_environment_spec(workload)
        fields.append("environment_spec")
    task.vnc_enabled = bool(task.environment_spec and task.environment_spec.vnc_enabled)
    fields.append("vnc_enabled")

    package_brief = getattr(workload, "brief", None)
    if package_brief is not None and package_brief.status == Brief.Status.READY:
        task.brief = package_brief
        fields.append("brief")
    elif task.environment_spec is not None and task.environment_spec.config_repo:
        # Compatibility adapter for old [skills.<slug>] config repositories.
        from astrolift_agents.services.brief_assembler import assemble_agent_brief

        task_context = {"task_guid": str(task.guid)}
        task_context.update(context or {})
        task.brief = assemble_agent_brief(
            organization=task.organization,
            config_repo=task.environment_spec.config_repo,
            config_branch=task.environment_spec.config_branch or "main",
            manifest_path=task.environment_spec.config_manifest_path or "",
            context=task_context,
            ttl_seconds=task.timeout_seconds,
        )
        fields.append("brief")
    else:
        task.brief = _compatibility_brief(workload, task.environment_spec)
        fields.append("brief")

    if task.brief_id is None:
        raise AgentTaskPreparationError(
            f"agent {workload.slug!r} has no runnable Agent Package Brief; re-sync its source repo"
        )
    task.save(update_fields=[*dict.fromkeys(fields), "updated_at", "version"])
    return task


def settle_preparation_failure(task, exc: Exception) -> None:
    """Persist a value-safe preparation error and terminalize the task."""
    from astrolift_agents.models import AgentTask

    task.failure = {"message": f"agent package preparation failed: {exc}"}
    task.save(update_fields=["failure", "updated_at", "version"])
    if task.status in {AgentTask.Status.DRAFT, AgentTask.Status.QUEUED}:
        task.transition_to(AgentTask.Status.FAILED)


def settle_dispatch_start_failure(task, exc: Exception) -> None:
    """Terminalize a prepared task whose durable workflow did not enqueue."""
    from astrolift_agents.models import AgentTask

    task.refresh_from_db()
    task.failure = {"message": f"agent dispatch workflow failed to start: {exc}"}
    task.save(update_fields=["failure", "updated_at", "version"])
    if task.status in {AgentTask.Status.DRAFT, AgentTask.Status.QUEUED}:
        task.transition_to(AgentTask.Status.FAILED)


__all__ = [
    "AgentTaskPreparationError",
    "_compatibility_brief",
    "default_environment_spec",
    "prepare_agent_task",
    "settle_dispatch_start_failure",
    "settle_preparation_failure",
]
