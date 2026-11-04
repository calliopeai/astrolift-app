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
    Project,
    Team,
)
from astrolift_identity.schema.mutations.helpers import (
    _actor,
    _listed,
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
from astrolift_identity.scopes import identity_organization_scope, team_scope_by_guid
from astrolift_services.model_retirement import retirement_guard
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
    @require_permission(Permission.TEAM_CREATE, scope=identity_organization_scope(Permission.TEAM_CREATE))
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
    @require_permission(
        Permission.TEAM_UPDATE, scope=team_scope_by_guid("input.id", permission=Permission.TEAM_UPDATE)
    )
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
    @require_permission(
        Permission.TEAM_DELETE, scope=team_scope_by_guid("input.id", permission=Permission.TEAM_DELETE)
    )
    @tenant_scoped()
    @retirement_guard(kind="team", field="input.id")
    def soft_delete_team(
        self, info: Info, input: SoftDeleteByGuidInput
    ) -> MutationResultType[_SoftDeletePayload]:
        from astrolift_registry.models import RegisteredApp
        from astrolift_services.models import ManagedService, SecretBundle

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        team = _resolve_team(input.id, org_id)
        if team is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "team not found")
        # Projects and apps keep pointing at a soft-deleted team, and the
        # routes and lists that walk live owners drop them, so they could no
        # longer be moved, deregistered or deleted (#2627). They leave first,
        # as a project's apps do (#2479), and the refusal also names what its
        # projects still own. Rows still tearing down are live. A bundle owned
        # by the team itself (no project) is legacy and no mutation deletes
        # one, so it does not block.
        team_projects = Project.objects.filter(team_id=team.pk, organization_id=org_id)
        apps = RegisteredApp.objects.filter(team_id=team.pk, organization_id=org_id).count()
        resources = ManagedService.objects.filter(project__in=team_projects).count()
        bundles = SecretBundle.objects.filter(project__in=team_projects, organization_id=org_id).count()
        projects = team_projects.count()
        if apps or resources or bundles or projects:
            held: list[str] = []
            steps: list[str] = []
            if apps:
                held.append(f"{apps} app{'' if apps == 1 else 's'}")
                steps.append("move its apps to another team or deregister them")
            if resources:
                held.append(f"{resources} managed resource{'' if resources == 1 else 's'}")
                steps.append("deprovision its managed resources")
            if bundles:
                held.append(f"{bundles} secret bundle{'' if bundles == 1 else 's'}")
                steps.append("delete its secret bundles")
            if projects:
                held.append(f"{projects} project{'' if projects == 1 else 's'}")
                steps.append("delete its projects")
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"team still has {_listed(held)}; {_listed(steps)} before deleting it",
            )
        team.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))
