"""One preparation contract for every AgentTask trigger path.

The task source (manual, webhook, cron, loop, or workflow stage) must not alter
what actually runs.  This module freezes the matching environment spec, VNC
flag, and immutable Agent Package Brief before a task enters QUEUED.
"""

from __future__ import annotations

import copy
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
        # A compatibility brief freezes prose + image only. There is no source
        # to slice (the workload was registered without a brief), so it must
        # never claim a payload bundle the spawner then fails to find (#1762).
        payload_required=False,
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


def _stage_skill_rows(organization, skill_refs: list[str]):
    """Resolve ordered org/global skills, with org ownership winning.

    Workflow TOML deliberately accepts the same reference grammar as agent
    manifests.  By dispatch time those packages have already been imported
    into ``Skill`` rows, whose stable key is the imported skill slug rather
    than its source path/ref.  Prefer an exact slug, then the normalized leaf
    (``./skills/evidence`` or ``runbooks/evidence@main`` -> ``evidence``).
    """
    from django.db.models import Q
    from django.utils.text import slugify

    from astrolift_agents.models import Skill

    wanted = list(dict.fromkeys(ref.strip() for ref in skill_refs if ref.strip()))
    if not wanted:
        return []

    candidate_slugs: dict[str, list[str]] = {}
    for ref in wanted:
        without_ref = ref.rsplit("@", 1)[0]
        leaf = without_ref.rstrip("/").rsplit("/", 1)[-1]
        candidates = [ref]
        normalized = slugify(leaf)[:128]
        if normalized and normalized not in candidates:
            candidates.append(normalized)
        candidate_slugs[ref] = candidates

    all_candidates = {slug for candidates in candidate_slugs.values() for slug in candidates}
    candidates = Skill.objects.filter(
        slug__in=all_candidates,
        deleted_at__isnull=True,
        is_active=True,
    ).filter(Q(organization=organization) | Q(organization__isnull=True))
    by_slug: dict[str, Any] = {}
    for skill in candidates.order_by("slug", "organization_id"):
        current = by_slug.get(skill.slug)
        if current is None or skill.organization_id == organization.id:
            by_slug[skill.slug] = skill
    resolved = {
        ref: next((by_slug[slug] for slug in slugs if slug in by_slug), None)
        for ref, slugs in candidate_slugs.items()
    }
    missing = [ref for ref, skill in resolved.items() if skill is None]
    if missing:
        raise AgentTaskPreparationError(
            "workflow stage skill(s) not found for organization: " + ", ".join(missing)
        )
    return list(dict.fromkeys(resolved[ref] for ref in wanted))


def _tool_packet(tool) -> dict[str, Any]:
    return {
        "slug": tool.slug,
        "name": tool.name,
        "description": tool.description,
        "adapter": tool.adapter,
        "handler_ref": tool.handler_ref,
        "input_schema": tool.input_schema or {},
        "output_schema": tool.output_schema or {},
        "implementation_config": tool.implementation_config or {},
        "commands": list(tool.commands or []),
        "required_packages": list(tool.required_packages or []),
        "capability_group": tool.capability_group,
        "agent_type_bindings": list(tool.agent_type_bindings or []),
        "is_builtin": bool(tool.is_builtin),
    }


def _derive_task_brief(
    base_brief,
    *,
    task,
    context: dict[str, Any],
    skill_refs: list[str],
    prompt: str,
    output_key: str,
):
    """Create an immutable task packet over a definition-level Brief."""
    from django.utils import timezone

    from astrolift_agents.models import Brief, BriefSkillRef
    from astrolift_agents.services.agent_package import (
        compatibility_snapshot,
        compose_system_prompt,
        validate_agent_package,
    )

    stage_skills = _stage_skill_rows(task.organization, skill_refs)
    snapshot = copy.deepcopy(base_brief.manifest_snapshot or {})
    package = snapshot.get("agent_package")
    if isinstance(package, dict):
        package = copy.deepcopy(package)
        skills = list(package.get("skills") or [])
        tools = list(package.get("tools") or [])
        skill_slugs = {str(row.get("slug") or "") for row in skills if isinstance(row, dict)}
        tool_slugs = {str(row.get("slug") or "") for row in tools if isinstance(row, dict)}
        for skill in stage_skills:
            if skill.slug not in skill_slugs:
                skills.append(
                    {
                        "slug": skill.slug,
                        "name": skill.name,
                        "description": skill.description,
                        "version": skill.skill_version,
                        "content_hash": skill.content_hash,
                        "instructions": skill.content,
                    }
                )
                skill_slugs.add(skill.slug)
            for tool in skill.tool_defs.filter(deleted_at__isnull=True).order_by("slug"):
                if tool.slug not in tool_slugs:
                    tools.append(_tool_packet(tool))
                    tool_slugs.add(tool.slug)
        prompt_packet = dict(package.get("prompt") or {})
        system = compose_system_prompt(
            brief=str(prompt_packet.get("brief") or ""),
            skills=skills,
            tools=tools,
        )
        if prompt:
            stage_section = f"# Workflow stage instructions\n\n{prompt}"
            system = f"{system}\n\n---\n\n{stage_section}" if system else stage_section
            prompt_packet["stage"] = prompt
        prompt_packet["system"] = system
        package["prompt"] = prompt_packet
        package["skills"] = skills
        package["tools"] = tools
        package = validate_agent_package(package)
        snapshot.update(compatibility_snapshot(package))
    else:
        sections = [str(snapshot.get("system_prompt") or "").strip()]
        sections.extend(
            f"# Skill: {skill.name}\n\n{skill.content.strip()}"
            for skill in stage_skills
            if skill.content.strip()
        )
        if prompt:
            sections.append(f"# Workflow stage instructions\n\n{prompt}")
        snapshot["system_prompt"] = "\n\n---\n\n".join(section for section in sections if section)
        if stage_skills:
            snapshot["workflow_skill_slugs"] = [skill.slug for skill in stage_skills]

    derived_context = {
        **(base_brief.context or {}),
        **context,
        "task_guid": str(task.guid),
        "output_key": output_key,
        "source_brief_guid": str(base_brief.guid),
    }
    canonical = json.dumps(
        {
            "organization": str(task.organization.guid),
            "source_brief_hash": base_brief.content_hash,
            "snapshot": snapshot,
            "context": derived_context,
            "secret_refs": base_brief.secrets_refs or [],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    brief, _ = Brief.objects.get_or_create(
        organization=task.organization,
        content_hash=content_hash,
        defaults={
            "registered_app": base_brief.registered_app,
            "storage_key": base_brief.storage_key,
            "status": Brief.Status.READY,
            "manifest_snapshot": snapshot,
            "secrets_refs": list(base_brief.secrets_refs or []),
            "context": derived_context,
            "ttl_seconds": base_brief.ttl_seconds,
            "assembled_at": timezone.now(),
        },
    )
    base_refs = list(base_brief.skill_refs.select_related("skill"))
    refs_by_skill = {ref.skill_id: ref.skill_version for ref in base_refs}
    refs_by_skill.update({skill.id: skill.skill_version for skill in stage_skills})
    for skill_id, version in refs_by_skill.items():
        BriefSkillRef.objects.get_or_create(
            brief=brief,
            skill_id=skill_id,
            defaults={"skill_version": version},
        )
    return brief


def prepare_agent_task(
    task,
    *,
    context: dict[str, Any] | None = None,
    skill_refs: list[str] | None = None,
    prompt: str = "",
    output_key: str = "",
):
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
    # ``context`` already predates workflow stages and is used by every
    # standalone trigger (manual/MCP/webhook).  A context-only dispatch must
    # keep reusing its exact registered Brief.  Only explicit stage overlays
    # derive a task-specific package; the stage executor always supplies a
    # non-empty effective ``output_key``.
    if skill_refs or prompt or output_key:
        task.brief = _derive_task_brief(
            task.brief,
            task=task,
            context=dict(context or {}),
            skill_refs=list(skill_refs or []),
            prompt=prompt,
            output_key=output_key,
        )
        fields.append("brief")
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
