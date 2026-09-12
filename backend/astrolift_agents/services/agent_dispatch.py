"""Shared dispatch entry point for UI, MCP, and automation callers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import Q


@dataclass(slots=True)
class AgentDispatchError(RuntimeError):
    code: str
    message: str
    field: str = ""

    def __str__(self) -> str:
        return self.message


def dispatch_registered_agent(
    *,
    organization_id: int,
    team_id: int | None = None,
    agent_slug: str,
    workload_id: int | None = None,
    actor,
    environment_spec_guid: str = "",
    trigger_payload: dict[str, Any] | None = None,
    timeout_seconds: int | None = None,
    trigger: str = "manual",
):
    """Create, prepare, queue, and durably dispatch one registered agent.

    This is deliberately below GraphQL/MCP so every authenticated entry point
    freezes the same environment spec and immutable Agent Package Brief.
    """
    from astrolift_agents.models import AgentEnvironmentSpec, AgentTask
    from astrolift_agents.services.task_preparation import (
        prepare_agent_task,
        settle_dispatch_start_failure,
        settle_preparation_failure,
    )
    from astrolift_identity.models import Organization
    from astrolift_registry.models import AppTeamAccess, Workload
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import DispatchAgentTaskInput

    slug = (agent_slug or "").strip()
    if not slug:
        raise AgentDispatchError("validation", "agent slug is required", "agent_slug")

    workloads = (
        Workload.objects.filter(
            slug=slug,
            registered_app__organization_id=organization_id,
            registered_app__deleted_at__isnull=True,
            deleted_at__isnull=True,
        )
        .select_related("registered_app")
        .order_by("pk")
    )
    if workload_id is not None:
        # The GraphQL adapter has already resolved one authorized match.
        # Keep that identity when another app has the same workload slug.
        workloads = workloads.filter(pk=workload_id)
    if team_id is not None:
        workloads = workloads.filter(
            Q(registered_app__team_id=team_id)
            | Q(
                registered_app__team_accesses__team_id=team_id,
                registered_app__team_accesses__deleted_at__isnull=True,
                registered_app__team_accesses__access_level__in=(
                    AppTeamAccess.AccessLevel.DEPLOYER.value,
                    AppTeamAccess.AccessLevel.OWNER.value,
                ),
            )
        ).distinct()
    matches = list(workloads[:2])
    if not matches:
        raise AgentDispatchError("not_found", "agent not found", "agent_slug")
    if len(matches) > 1:
        raise AgentDispatchError(
            "conflict",
            f"agent slug {slug!r} is ambiguous; make agent workload slugs unique",
            "agent_slug",
        )
    workload = matches[0]
    if workload.kind != Workload.Kind.AGENT:
        raise AgentDispatchError(
            "validation",
            f"workload {slug!r} is not an agent (kind={workload.kind})",
            "agent_slug",
        )
    if workload.run_family != Workload.RunFamily.TASK:
        raise AgentDispatchError(
            "precondition",
            f"agent {slug!r} uses the {workload.run_family!r} run family; only Task agents can be dispatched",
            "agent_slug",
        )

    organization = Organization.objects.filter(
        pk=organization_id,
        deleted_at__isnull=True,
    ).first()
    if organization is None:
        raise AgentDispatchError("not_found", "organization not found")

    if environment_spec_guid:
        environment_specs = AgentEnvironmentSpec.objects.filter(
            guid=environment_spec_guid,
            organization_id=organization_id,
            deleted_at__isnull=True,
        )
        if team_id is not None:
            # Environment specs currently have organization ownership but no
            # team FK. A team-scoped API token may therefore use only the
            # agent's canonical spec (same slug) or a source-bound spec for
            # this exact repo slice; arbitrary org-level reusable specs remain
            # available to session/org-scoped callers.
            allowed_spec = Q(slug=slug)
            if workload.registered_app.source_repo:
                allowed_spec |= Q(
                    config_repo=workload.registered_app.source_repo,
                    config_manifest_path=workload.registered_app.manifest_path,
                )
            environment_specs = environment_specs.filter(allowed_spec)
        environment_spec = environment_specs.first()
        if environment_spec is None:
            raise AgentDispatchError(
                "not_found",
                "environment spec not found",
                "environment_spec_id",
            )
    else:
        environment_spec = AgentEnvironmentSpec.objects.filter(
            slug=slug,
            organization_id=organization_id,
            deleted_at__isnull=True,
        ).first()

    effective_timeout = int(timeout_seconds or workload.tool_timeout_seconds or 300)
    if effective_timeout < 1 or effective_timeout > 604800:
        raise AgentDispatchError(
            "validation",
            "timeout_seconds must be between 1 and 604800",
            "timeout_seconds",
        )

    with transaction.atomic():
        task = AgentTask.objects.create(
            organization=organization,
            agent_definition=workload,
            environment_spec=environment_spec,
            status=AgentTask.Status.DRAFT,
            timeout_seconds=effective_timeout,
            dispatch_input=trigger_payload or None,
            vnc_enabled=bool(environment_spec and environment_spec.vnc_enabled),
        )

    try:
        prepare_agent_task(task, context={"trigger": trigger})
    except Exception as exc:  # noqa: BLE001 — persist a terminal, inspectable failure
        settle_preparation_failure(task, exc)
        raise AgentDispatchError(
            "precondition",
            str(exc) or "agent package preparation failed",
        ) from exc

    task.transition_to(AgentTask.Status.QUEUED)
    try:
        start_workflow(
            "DispatchAgentTaskWorkflow",
            args=[
                DispatchAgentTaskInput(
                    agent_task_id=task.pk,
                    actor=actor,
                    trigger_payload=trigger_payload or None,
                )
            ],
            workflow_id=f"DispatchAgentTaskWorkflow-{task.guid}",
        )
    except Exception as exc:  # noqa: BLE001 — preserve the failed task for operators
        settle_dispatch_start_failure(task, exc)
        raise AgentDispatchError(
            "internal",
            str(exc) or "agent dispatch workflow failed to start",
        ) from exc
    return task


__all__ = ["AgentDispatchError", "dispatch_registered_agent"]
