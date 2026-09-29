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


class SpecOwnedElsewhere(ValueError):
    """An agent registration named another project's or team's spec."""


def spec_for_registration(*, organization, slug: str, app):
    """The environment spec an agent registration writes under ``slug`` (#1866).

    The live spec with that slug when ``app``'s agents may run with it, with
    an explicit org write grant for an org-shared recipe; otherwise a new,
    unsaved spec owned by the app's project and team. A slug naming another
    scope's spec raises :class:`SpecOwnedElsewhere`, so registering an agent
    can never rewrite another team's image and secret bindings.
    """
    from astrolift_agents.models import AgentEnvironmentSpec
    from astrolift_agents.visibility import check_org_shared_spec_write, spec_usable_by_app
    from core.permissions import PermissionDenied

    spec = (
        AgentEnvironmentSpec.objects.filter(organization=organization, slug=slug, deleted_at__isnull=True)
        .select_related("team", "project")
        .first()
    )
    if spec is None:
        return AgentEnvironmentSpec(
            organization=organization, slug=slug, team_id=app.team_id, project_id=app.project_id
        )
    if not spec_usable_by_app(spec, app):
        raise SpecOwnedElsewhere(
            f"environment spec {slug!r} belongs to another project or team; give this agent another slug"
        )
    if spec.team_id is None and spec.project_id is None:
        try:
            check_org_shared_spec_write(spec.organization_id)
        except PermissionDenied as exc:
            raise SpecOwnedElsewhere(
                f"org-shared environment spec {slug!r} requires an org-scoped update grant; "
                "give this agent another slug to create an owned spec"
            ) from exc
    return spec
