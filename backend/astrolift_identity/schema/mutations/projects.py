"""ProjectMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import (
    MutationResultType,
)
from astrolift_graphql import (
    failure as gql_failure,
)
from astrolift_graphql import (
    success as gql_success,
)
from astrolift_identity.models import (
    Project,
)
from astrolift_identity.schema.mutations.helpers import (
    _actor,
    _resolve_project,
    _resolve_team,
)
from astrolift_identity.schema.mutations.types import (
    CreateProjectInput,
    SoftDeleteByGuidInput,
    UpdateProjectInput,
    _SoftDeletePayload,
)
from astrolift_identity.schema.types import (
    ProjectType,
    project_to_type,
)
from astrolift_identity.scopes import project_scope_by_guid, team_scope_by_guid
from astrolift_services.model_retirement import retirement_guard
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.naming import PROJECT_SLUG, NamingViolation
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class ProjectMutations:
    # ---- Project -----------------------------------------------------

    @strawberry.field
    @mutation_audit(action="project.create")
    @require_permission(
        Permission.PROJECT_CREATE,
        scope=team_scope_by_guid("input.team_id", permission=Permission.PROJECT_CREATE),
    )
    @tenant_scoped()
    def create_project(self, info: Info, input: CreateProjectInput) -> MutationResultType[ProjectType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        # The parent team must belong to the caller's org — a foreign
        # teamId reads as not-found.
        team = _resolve_team(input.team_id, org_id)
        if team is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "team not found", field="teamId")
        if Project.objects.filter(team=team, slug=input.slug).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"project with slug {input.slug!r} already exists in this team",
                field="slug",
            )
        project = Project.objects.create(
            team=team,
            name=input.name,
            slug=input.slug,
            description=input.description or "",
        )
        return gql_success(project_to_type(project))

    @strawberry.field
    @mutation_audit(action="project.update")
    @require_permission(
        Permission.PROJECT_UPDATE,
        scope=project_scope_by_guid("input.id", permission=Permission.PROJECT_UPDATE),
    )
    @tenant_scoped()
    def update_project(self, info: Info, input: UpdateProjectInput) -> MutationResultType[ProjectType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        project = _resolve_project(input.id, org_id)
        if project is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project not found")
        if input.slug is not None:
            # Validate the shape first, then uniqueness. A project slug is
            # unique per team, scoped to live rows (the default manager
            # already filters soft-deleted). Exclude self so a no-op
            # rename to the current slug isn't a self-collision.
            try:
                PROJECT_SLUG.validate(input.slug)
            except NamingViolation:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "slug must be lowercase letters, digits, and dashes, start "
                    "with a letter, and be at most 40 characters",
                    field="slug",
                )
            # Uniqueness is per-team; the explicit ``organization_id`` clause
            # keeps the fetch fail-closed on the caller's org (denormalized
            # and always equal to the team's org) so the tenant boundary is
            # visible on the query, not only via the org-scoped project above.
            if (
                Project.objects.filter(team_id=project.team_id, slug=input.slug, organization_id=org_id)
                .exclude(pk=project.pk)
                .exists()
            ):
                return gql_failure(
                    ErrorCode.CONFLICT.value,
                    f"a project with slug {input.slug!r} already exists in this team",
                    field="slug",
                )
            project.slug = input.slug
        if input.name is not None:
            project.name = input.name
        if input.description is not None:
            project.description = input.description
        project.save()
        return gql_success(project_to_type(project))

    @strawberry.field
    @mutation_audit(action="project.delete")
    @require_permission(
        Permission.PROJECT_DELETE,
        scope=project_scope_by_guid("input.id", permission=Permission.PROJECT_DELETE),
    )
    @tenant_scoped()
    @retirement_guard(kind="project", field="input.id")
    def soft_delete_project(
        self, info: Info, input: SoftDeleteByGuidInput
    ) -> MutationResultType[_SoftDeletePayload]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        project = _resolve_project(input.id, org_id)
        if project is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project not found")
        project.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))
