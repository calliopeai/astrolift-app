"""Resolution and safety validation for nested workflow definitions."""

from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Q

from workflows.models import WorkflowDefinition, WorkflowStage

MAX_WORKFLOW_NESTING_DEPTH = 8


class WorkflowCompositionError(ValueError):
    """A nested workflow reference is missing, invisible, cyclic, or too deep."""


def resolve_child_definition_from_candidates(
    parent: WorkflowDefinition,
    workflow_ref: str,
    candidates: Iterable[WorkflowDefinition],
) -> WorkflowDefinition | None:
    """Resolve a child from preloaded definitions with runtime visibility rules."""
    ref = (workflow_ref or "").strip()
    if not ref:
        return None

    visible = [
        candidate
        for candidate in candidates
        if candidate.slug == ref
        and candidate.is_enabled
        and candidate.deleted_at is None
        and (
            (parent.organization_id is None and candidate.organization_id is None)
            or (
                parent.organization_id is not None
                and candidate.organization_id in {parent.organization_id, None}
            )
        )
        and (
            (parent.project_id is None and candidate.project_id is None)
            or (parent.project_id is not None and candidate.project_id in {parent.project_id, None})
        )
    ]
    if parent.organization_id is not None:
        owned = next(
            (candidate for candidate in visible if candidate.organization_id == parent.organization_id),
            None,
        )
        if owned is not None:
            return owned
    return next(
        (candidate for candidate in visible if candidate.organization_id is None),
        None,
    )


def resolve_child_definition(parent: WorkflowDefinition, workflow_ref: str) -> WorkflowDefinition | None:
    """Resolve a child slug in the parent's tenant and project visibility.

    Organization-owned definitions shadow global templates. Project-owned
    parents may call definitions in the same project or reusable definitions
    with no project; a reusable parent cannot reach into a project packet.
    """
    ref = (workflow_ref or "").strip()
    if not ref:
        return None

    definitions = WorkflowDefinition.objects.filter(
        slug=ref,
        is_enabled=True,
        deleted_at__isnull=True,
    )
    if parent.organization_id is None:
        definitions = definitions.filter(organization__isnull=True)
    else:
        definitions = definitions.filter(
            Q(organization_id=parent.organization_id) | Q(organization__isnull=True)
        )

    if parent.project_id is None:
        definitions = definitions.filter(project__isnull=True)
    else:
        definitions = definitions.filter(Q(project_id=parent.project_id) | Q(project__isnull=True))

    return resolve_child_definition_from_candidates(parent, ref, definitions)


def validate_workflow_composition(
    definition: WorkflowDefinition,
    *,
    max_depth: int = MAX_WORKFLOW_NESTING_DEPTH,
) -> None:
    """Validate every nested edge reachable from ``definition``.

    ``max_depth`` counts nested child edges: a leaf is depth 0 and the
    outermost definition may wrap at most eight child levels by default.
    """
    root_label = definition.slug or str(definition.pk)

    def visit(current: WorkflowDefinition, path: list[int], labels: list[str], depth: int) -> None:
        from workflows.back_edges import validate_loop_plan

        validate_loop_plan(
            [
                {
                    "order": stage.order,
                    "kind": stage.kind,
                    "output_key": stage.output_key,
                    "back_edge": stage.back_edge,
                    "iteration": stage.iteration,
                    "max_attempts": stage.max_attempts,
                    "fan_out_count": stage.fan_out_count,
                    "fan_out_dynamic": stage.fan_out_dynamic,
                }
                for stage in current.stages.filter(deleted_at__isnull=True).order_by("order")
            ],
            pattern_kind=current.pattern_kind,
            require_review_loop=False,
        )
        invalid = (
            current.stages.filter(deleted_at__isnull=True)
            .exclude(kind=WorkflowStage.StageKind.WORKFLOW)
            .exclude(workflow_ref="")
            .first()
        )
        if invalid is not None:
            raise WorkflowCompositionError(
                f"workflow {current.slug!r} stage {invalid.order} sets workflow_ref "
                'but kind is not "workflow"'
            )
        nested_stages = current.stages.filter(
            kind=WorkflowStage.StageKind.WORKFLOW,
            deleted_at__isnull=True,
        ).order_by("order")
        for stage in nested_stages:
            ref = (stage.workflow_ref or "").strip()
            if not ref:
                raise WorkflowCompositionError(
                    f"workflow {current.slug!r} stage {stage.order} requires workflow_ref"
                )
            child = resolve_child_definition(current, ref)
            if child is None:
                raise WorkflowCompositionError(
                    f"workflow {current.slug!r} stage {stage.order} cannot resolve visible child {ref!r}"
                )
            child_label = child.slug or str(child.pk)
            if child.pk in path:
                cycle_start = path.index(child.pk)
                cycle = labels[cycle_start:] + [child_label]
                raise WorkflowCompositionError("nested workflow cycle: " + " -> ".join(cycle))
            child_depth = depth + 1
            if child_depth > max_depth:
                chain = labels + [child_label]
                raise WorkflowCompositionError(
                    f"nested workflow depth exceeds {max_depth}: " + " -> ".join(chain)
                )
            visit(child, path + [child.pk], labels + [child_label], child_depth)

    visit(definition, [definition.pk], [root_label], 0)


def workflow_parent_references(child: WorkflowDefinition) -> list[WorkflowStage]:
    """Return live stages whose visibility rules resolve to ``child``.

    Nested edges are slug based so the database cannot provide FK ``PROTECT``
    semantics. Resolve candidates through the same shadowing/project rules as
    dispatch before allowing a child definition to be disabled or deleted.
    """
    candidates = (
        WorkflowStage.objects.filter(
            kind=WorkflowStage.StageKind.WORKFLOW,
            workflow_ref=child.slug,
            deleted_at__isnull=True,
            definition__is_enabled=True,
            definition__deleted_at__isnull=True,
        )
        .exclude(definition=child)
        .select_related("definition")
        .order_by("definition__slug", "order")
    )
    return [
        stage
        for stage in candidates
        if (resolved := resolve_child_definition(stage.definition, stage.workflow_ref)) is not None
        and resolved.pk == child.pk
    ]


__all__ = [
    "MAX_WORKFLOW_NESTING_DEPTH",
    "WorkflowCompositionError",
    "resolve_child_definition",
    "resolve_child_definition_from_candidates",
    "validate_workflow_composition",
    "workflow_parent_references",
]
