"""
Identity mutations.

Each mutation:

1. Returns ``MutationResultType`` — never raises.
2. Is wrapped with ``@mutation_audit(...)`` (outermost) so the audit
   row captures actor + target + decision regardless of outcome.
3. Is wrapped with ``@require_permission(...)`` (innermost) so the
   permission denial surfaces as a structured error inside the
   envelope rather than a top-level GraphQL exception.

Field/input/type names use the ``Astrolift...`` prefix where they
collide with the legacy ``organization`` app's GraphQL surface.
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import (
    GUID,
    MutationResultType,
)
from astrolift_graphql import (
    failure as gql_failure,
)
from astrolift_graphql import (
    success as gql_success,
)
from astrolift_identity.models import Organization, Project, Team
from astrolift_identity.schema.types import (
    OrganizationType,
    ProjectType,
    TeamType,
    organization_to_type,
    project_to_type,
    team_to_type,
)
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

# ---- inputs ----------------------------------------------------------


@strawberry.input
class CreateOrganizationInput:
    name: str
    slug: str
    website: str | None = None


@strawberry.input
class UpdateOrganizationInput:
    id: GUID
    name: str | None = None
    website: str | None = None
    audit_log_retention_days: int | None = None


@strawberry.input
class CreateTeamInput:
    organization_id: GUID
    name: str
    slug: str
    description: str | None = None


@strawberry.input
class UpdateTeamInput:
    id: GUID
    name: str | None = None
    description: str | None = None


@strawberry.input
class CreateProjectInput:
    team_id: GUID
    name: str
    slug: str
    description: str | None = None


@strawberry.input
class UpdateProjectInput:
    id: GUID
    name: str | None = None
    description: str | None = None


@strawberry.input
class SoftDeleteByGuidInput:
    id: GUID


SoftDeleteOrganizationInput = SoftDeleteByGuidInput  # back-compat alias


@strawberry.type
class _SoftDeletePayload:
    id: GUID
    deleted: bool


# ---- helpers ---------------------------------------------------------


def _actor():
    tenant = get_current_tenant()
    actor_id = tenant.actor_user_id if tenant else None
    if actor_id is None:
        return None
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=actor_id).first()


def _resolve_org(guid: GUID) -> Organization | None:
    return Organization.objects.filter(guid=str(guid)).first()


def _resolve_team(guid: GUID) -> Team | None:
    return Team.objects.filter(guid=str(guid)).first()


def _resolve_project(guid: GUID) -> Project | None:
    return Project.objects.filter(guid=str(guid)).first()


# ---- mutations -------------------------------------------------------


@strawberry.type
class IdentityMutation:
    # ---- Organization ------------------------------------------------

    @strawberry.field
    @mutation_audit(action="org.create")
    @require_permission(Permission.ORG_UPDATE)
    def create_organization(
        self, info: Info, input: CreateOrganizationInput
    ) -> MutationResultType[OrganizationType]:
        if Organization.objects.filter(slug=input.slug).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"organization with slug {input.slug!r} already exists",
                field="slug",
            )
        org = Organization.objects.create(
            name=input.name,
            slug=input.slug,
            website=input.website or "",
        )
        return gql_success(organization_to_type(org))

    @strawberry.field
    @mutation_audit(action="org.update")
    @require_permission(Permission.ORG_UPDATE)
    def update_organization(
        self, info: Info, input: UpdateOrganizationInput
    ) -> MutationResultType[OrganizationType]:
        org = _resolve_org(input.id)
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        if input.name is not None:
            org.name = input.name
        if input.website is not None:
            org.website = input.website
        if input.audit_log_retention_days is not None:
            org.audit_log_retention_days = input.audit_log_retention_days
        org.save()
        return gql_success(organization_to_type(org))

    @strawberry.field
    @mutation_audit(action="org.delete")
    @require_permission(Permission.ORG_DELETE)
    def soft_delete_organization(
        self, info: Info, input: SoftDeleteByGuidInput
    ) -> MutationResultType[_SoftDeletePayload]:
        org = _resolve_org(input.id)
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")
        org.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    # ---- Team --------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="team.create")
    @require_permission(Permission.TEAM_CREATE)
    def create_team(
        self, info: Info, input: CreateTeamInput
    ) -> MutationResultType[TeamType]:
        org = _resolve_org(input.organization_id)
        if org is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value, "organization not found", field="organizationId"
            )
        if Team.objects.filter(organization=org, slug=input.slug).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"team with slug {input.slug!r} already exists in this org",
                field="slug",
            )
        team = Team.objects.create(
            organization=org,
            name=input.name,
            slug=input.slug,
            description=input.description or "",
        )
        return gql_success(team_to_type(team))

    @strawberry.field
    @mutation_audit(action="team.update")
    @require_permission(Permission.TEAM_UPDATE)
    def update_team(
        self, info: Info, input: UpdateTeamInput
    ) -> MutationResultType[TeamType]:
        team = _resolve_team(input.id)
        if team is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "team not found")
        if input.name is not None:
            team.name = input.name
        if input.description is not None:
            team.description = input.description
        team.save()
        return gql_success(team_to_type(team))

    @strawberry.field
    @mutation_audit(action="team.delete")
    @require_permission(Permission.TEAM_DELETE)
    def soft_delete_team(
        self, info: Info, input: SoftDeleteByGuidInput
    ) -> MutationResultType[_SoftDeletePayload]:
        team = _resolve_team(input.id)
        if team is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "team not found")
        team.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    # ---- Project -----------------------------------------------------

    @strawberry.field
    @mutation_audit(action="project.create")
    @require_permission(Permission.PROJECT_CREATE)
    def create_project(
        self, info: Info, input: CreateProjectInput
    ) -> MutationResultType[ProjectType]:
        team = _resolve_team(input.team_id)
        if team is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value, "team not found", field="teamId"
            )
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
    @require_permission(Permission.PROJECT_UPDATE)
    def update_project(
        self, info: Info, input: UpdateProjectInput
    ) -> MutationResultType[ProjectType]:
        project = _resolve_project(input.id)
        if project is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project not found")
        if input.name is not None:
            project.name = input.name
        if input.description is not None:
            project.description = input.description
        project.save()
        return gql_success(project_to_type(project))

    @strawberry.field
    @mutation_audit(action="project.delete")
    @require_permission(Permission.PROJECT_DELETE)
    def soft_delete_project(
        self, info: Info, input: SoftDeleteByGuidInput
    ) -> MutationResultType[_SoftDeletePayload]:
        project = _resolve_project(input.id)
        if project is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project not found")
        project.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))
