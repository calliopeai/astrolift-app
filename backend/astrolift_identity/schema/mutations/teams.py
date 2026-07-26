"""TeamMutations — split from the monolithic mutations module."""

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
    Team,
)
from astrolift_identity.schema.mutations.helpers import (
    _actor,
    _resolve_org,
    _resolve_team,
)
from astrolift_identity.schema.mutations.types import (
    CreateTeamInput,
    SoftDeleteByGuidInput,
    UpdateTeamInput,
    _SoftDeletePayload,
)
from astrolift_identity.schema.types import (
    TeamType,
    team_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.naming import TEAM_SLUG, NamingViolation
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class TeamMutations:
    # ---- Team --------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="team.create")
    @require_permission(Permission.TEAM_CREATE)
    @tenant_scoped()
    def create_team(self, info: Info, input: CreateTeamInput) -> MutationResultType[TeamType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        # A team may only be created inside the caller's own org — a
        # foreign organizationId reads as not-found.
        org = _resolve_org(input.organization_id, org_id)
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found", field="organizationId")
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
    @tenant_scoped()
    def update_team(self, info: Info, input: UpdateTeamInput) -> MutationResultType[TeamType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        team = _resolve_team(input.id, org_id)
        if team is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "team not found")
        if input.slug is not None:
            # Validate the shape first, then uniqueness. A team slug is
            # unique per organization, scoped to live rows (the default
            # manager already filters soft-deleted). Exclude self so a
            # no-op rename to the current slug isn't a self-collision.
            try:
                TEAM_SLUG.validate(input.slug)
            except NamingViolation:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "slug must be lowercase letters, digits, and dashes, start "
                    "with a letter, and be at most 40 characters",
                    field="slug",
                )
            if (
                Team.objects.filter(organization_id=team.organization_id, slug=input.slug)
                .exclude(pk=team.pk)
                .exists()
            ):
                return gql_failure(
                    ErrorCode.CONFLICT.value,
                    f"a team with slug {input.slug!r} already exists in this org",
                    field="slug",
                )
            team.slug = input.slug
        if input.name is not None:
            team.name = input.name
        if input.description is not None:
            team.description = input.description
        team.save()
        return gql_success(team_to_type(team))

    @strawberry.field
    @mutation_audit(action="team.delete")
    @require_permission(Permission.TEAM_DELETE)
    @tenant_scoped()
    def soft_delete_team(
        self, info: Info, input: SoftDeleteByGuidInput
    ) -> MutationResultType[_SoftDeletePayload]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        team = _resolve_team(input.id, org_id)
        if team is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "team not found")
        team.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))
