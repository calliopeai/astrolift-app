"""Project ownership checks shared by agent resource surfaces."""

from __future__ import annotations


def agent_spec_belongs_to_project(spec, project) -> bool:
    from astrolift_registry.models import Workload
    from workflows.models import WorkflowStage

    if Workload.objects.filter(
        slug=spec.slug,
        kind=Workload.Kind.AGENT,
        registered_app__project=project,
        registered_app__deleted_at__isnull=True,
        deleted_at__isnull=True,
    ).exists():
        return True
    return WorkflowStage.objects.filter(
        definition__project=project,
        definition__organization_id=spec.organization_id,
        definition__deleted_at__isnull=True,
        environment_spec_slug=spec.slug,
        deleted_at__isnull=True,
    ).exists()
