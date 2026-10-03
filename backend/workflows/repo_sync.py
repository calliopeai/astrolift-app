"""Declarative workflow discovery and reconciliation for agent repositories."""

from __future__ import annotations

import dataclasses
from pathlib import PurePosixPath

from django.db import transaction

from astrolift_manifest.parser import ManifestError
from workflows.manifest import ParsedWorkflowManifest, _fan_out_columns, parse_workflow_manifest
from workflows.models import WorkflowDefinition, WorkflowStage
from workflows.target_references import references_filter


@dataclasses.dataclass(frozen=True, slots=True)
class RepositoryWorkflowManifest:
    path: str
    parsed: ParsedWorkflowManifest


@dataclasses.dataclass(frozen=True, slots=True)
class ReconciledWorkflow:
    path: str
    slug: str
    created: bool


def discover_repository_workflows(files: dict[str, str]) -> list[RepositoryWorkflowManifest]:
    """Parse every ``workflows/**/*.toml`` file in deterministic order."""
    manifests: list[RepositoryWorkflowManifest] = []
    for path, body in sorted(files.items()):
        repo_path = PurePosixPath(path)
        normalized = str(repo_path)
        parts = repo_path.parts
        if ".." in parts:
            continue
        if len(parts) < 2 or parts[0] != "workflows" or not normalized.endswith(".toml"):
            continue
        try:
            parsed = parse_workflow_manifest(body)
        except ManifestError as exc:
            location = f" at {exc.path}" if exc.path else ""
            raise ManifestError(
                f"{normalized}{location}: {exc}",
                path=exc.path,
                line=exc.line,
                column=exc.column,
            ) from exc
        manifests.append(RepositoryWorkflowManifest(path=normalized, parsed=parsed))
    return manifests


@transaction.atomic
def reconcile_repository_workflows(
    *,
    organization,
    project,
    source_repo: str,
    source_ref: str,
    manifests: list[RepositoryWorkflowManifest],
) -> list[ReconciledWorkflow]:
    """Converge source-owned definitions and ordered stages by repo path."""
    from django.utils import timezone

    from astrolift_registry.models import Workload

    if project.organization_id != organization.pk:
        raise ValueError("workflow project must belong to the repository organization")

    results: list[ReconciledWorkflow] = []
    retained_paths: set[str] = set()
    for item in manifests:
        retained_paths.add(item.path)
        parsed = item.parsed
        definition = WorkflowDefinition.objects.filter(
            organization=organization,
            source_repo=source_repo,
            source_path=item.path,
        ).first()
        created = definition is None
        if definition is None:
            collision = WorkflowDefinition.objects.filter(
                organization=organization,
                slug=parsed.definition.slug,
                deleted_at__isnull=True,
            ).exists()
            if collision:
                raise ValueError(
                    f"workflow slug {parsed.definition.slug!r} from {item.path} is already owned "
                    "by another definition"
                )
            definition = WorkflowDefinition(
                organization=organization,
                project=project,
                source_repo=source_repo,
                source_path=item.path,
            )
        elif (
            WorkflowDefinition.objects.filter(
                organization=organization,
                slug=parsed.definition.slug,
                deleted_at__isnull=True,
            )
            .exclude(pk=definition.pk)
            .exists()
        ):
            raise ValueError(
                f"workflow slug {parsed.definition.slug!r} from {item.path} collides with another definition"
            )

        definition.name = parsed.definition.name
        definition.slug = parsed.definition.slug
        definition.description = parsed.definition.description
        definition.pattern_kind = parsed.definition.pattern
        definition.model_label = ""
        definition.states = []
        definition.transitions = []
        definition.is_enabled = True
        definition.project = project
        definition.source_ref = source_ref
        definition.deleted_at = None
        definition.deleted_by = None
        definition.save()

        retained_orders: set[int] = set()
        for spec in parsed.stages:
            retained_orders.add(spec.order)
            stage = WorkflowStage.objects.filter(
                definition=definition,
                order=spec.order,
            ).first()
            if stage is None:
                stage = WorkflowStage(
                    definition=definition,
                    order=spec.order,
                    slug=f"{definition.slug}-stage-{spec.order}",
                )
            workload = None
            if spec.agent:
                workload = (
                    Workload.objects.filter(
                        registered_app__organization=organization,
                        kind=Workload.Kind.AGENT,
                        deleted_at__isnull=True,
                    )
                    .filter(references_filter([spec.agent]))
                    .first()
                )
            fan_out_count, fan_out_dynamic = _fan_out_columns(spec.fan_out)
            stage.kind = spec.kind
            stage.role = spec.role
            stage.agent_definition = workload
            stage.agent_ref = spec.agent or ""
            stage.workflow_ref = spec.workflow or ""
            stage.environment_spec_slug = spec.environment_spec_slug or ""
            stage.skill_refs = list(spec.skills)
            stage.iteration = dict(spec.iteration)
            stage.back_edge = dict(spec.back_edge)
            stage.max_attempts = spec.max_attempts
            stage.on_failure = spec.on_failure
            stage.timeout_seconds = spec.timeout
            stage.fan_out_count = fan_out_count
            stage.fan_out_dynamic = fan_out_dynamic
            stage.prompt = spec.prompt or ""
            stage.output_key = spec.output_key or ""
            stage.approvers = list(spec.approvers)
            stage.deleted_at = None
            stage.deleted_by = None
            stage.save()

        for stale in WorkflowStage.objects.filter(
            definition=definition,
            deleted_at__isnull=True,
        ).exclude(order__in=retained_orders):
            stale.deleted_at = timezone.now()
            stale.save(update_fields=["deleted_at", "updated_at", "version"])

        results.append(
            ReconciledWorkflow(
                path=item.path,
                slug=definition.slug,
                created=created,
            )
        )

    # The repo tree is a complete source snapshot, so absence is declarative:
    # retire definitions previously owned by this repo that were removed from
    # ``workflows/``.  Keep the rows for audit/FK integrity while making them
    # undiscoverable and unrunnable.
    retired_at = timezone.now()
    stale_definitions = WorkflowDefinition.objects.filter(
        organization=organization,
        source_repo=source_repo,
        deleted_at__isnull=True,
    ).exclude(source_path__in=retained_paths)
    for stale in stale_definitions:
        stale.is_enabled = False
        stale.source_ref = source_ref
        stale.deleted_at = retired_at
        stale.save(
            update_fields=[
                "is_enabled",
                "source_ref",
                "deleted_at",
                "updated_at",
                "version",
            ]
        )

    # Validate after the entire repository has converged so references are
    # order-independent and forward references work. The surrounding atomic
    # transaction rolls back every definition when one edge is invalid.
    from workflows.composition import validate_workflow_composition

    definitions = WorkflowDefinition.objects.filter(
        organization=organization,
        source_repo=source_repo,
        deleted_at__isnull=True,
    )
    for definition in definitions:
        validate_workflow_composition(definition)
    return results


__all__ = [
    "ReconciledWorkflow",
    "RepositoryWorkflowManifest",
    "discover_repository_workflows",
    "reconcile_repository_workflows",
]
