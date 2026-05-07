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
from astrolift_identity.models import (
    ApiToken,
    Member,
    Organization,
    Project,
    Role,
    RoleBinding,
    Team,
)
from astrolift_identity.schema.types import (
    ApiTokenPlaintextType,
    OrganizationType,
    ProjectType,
    RoleBindingType,
    TeamType,
    api_token_to_type,
    organization_to_type,
    project_to_type,
    role_binding_to_type,
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


@strawberry.input
class GrantRoleInput:
    user_id: str
    role_id: GUID
    scope_kind: str
    scope_guid: GUID  # the target Org/Team/Project/App guid


@strawberry.input
class RevokeRoleBindingInput:
    id: GUID


@strawberry.input
class CreateApiTokenInput:
    name: str
    scopes: list[str] | None = None
    expires_in_days: int | None = None
    team_slug: str | None = None


@strawberry.input
class RevokeApiTokenInput:
    id: GUID


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

    # ---- RBAC --------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="role_binding.grant")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    def grant_role(
        self, info: Info, input: GrantRoleInput
    ) -> MutationResultType[RoleBindingType]:
        from django.contrib.auth import get_user_model

        try:
            target_user_pk = int(input.user_id)
        except ValueError:
            return gql_failure(
                ErrorCode.VALIDATION.value, "userId must be a numeric pk", field="userId"
            )

        User = get_user_model()
        user = User.objects.filter(pk=target_user_pk).first()
        if user is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "user not found", field="userId")

        role = Role.objects.filter(guid=str(input.role_id)).first()
        if role is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role not found", field="roleId")

        scope_kind = input.scope_kind.upper()
        scope_id = _resolve_scope_pk(scope_kind, str(input.scope_guid))
        if scope_id is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value, "scope not found", field="scopeGuid"
            )

        if RoleBinding.objects.filter(
            user=user, role=role, scope_kind=scope_kind, scope_id=scope_id
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                "this user already has this role on this scope",
            )

        binding = RoleBinding.objects.create(
            user=user,
            role=role,
            scope_kind=scope_kind,
            scope_id=scope_id,
            granted_by=_actor(),
        )

        # Auto-add a Member row at the target scope so middleware can
        # resolve the tenant when this user logs in.
        Member.objects.get_or_create(
            user=user,
            scope_kind=scope_kind,
            scope_id=scope_id,
            defaults={"is_active": True, "lifecycle": "active"},
        )

        return gql_success(role_binding_to_type(binding))

    @strawberry.field
    @mutation_audit(action="role_binding.revoke")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    def revoke_role_binding(
        self, info: Info, input: RevokeRoleBindingInput
    ) -> MutationResultType[_SoftDeletePayload]:
        binding = RoleBinding.objects.filter(guid=str(input.id)).first()
        if binding is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role binding not found")
        binding.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    # ---- API tokens --------------------------------------------------

    @strawberry.field
    @mutation_audit(action="api_token.create")
    @require_permission(Permission.API_TOKEN_CREATE)
    def create_api_token(
        self, info: Info, input: CreateApiTokenInput
    ) -> MutationResultType[ApiTokenPlaintextType]:
        actor = _actor()
        if actor is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "no actor")

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        team = None
        if input.team_slug:
            team = Team.objects.filter(organization=org, slug=input.team_slug).first()
            if team is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value, "team not found", field="teamSlug"
                )

        plaintext, digest, last4 = _make_token_secret()

        expires_at = None
        if input.expires_in_days:
            from datetime import timedelta

            from django.utils import timezone

            expires_at = timezone.now() + timedelta(days=int(input.expires_in_days))

        token = ApiToken.objects.create(
            user=actor,
            organization=org,
            team=team,
            name=input.name.strip(),
            token_hash=digest,
            token_last_4=last4,
            scopes=list(input.scopes or []),
            expires_at=expires_at,
        )
        return gql_success(
            ApiTokenPlaintextType(
                api_token=api_token_to_type(token),
                plaintext=plaintext,
            )
        )

    @strawberry.field
    @mutation_audit(action="api_token.revoke")
    @require_permission(Permission.API_TOKEN_REVOKE)
    def revoke_api_token(
        self, info: Info, input: RevokeApiTokenInput
    ) -> MutationResultType[_SoftDeletePayload]:
        token = ApiToken.objects.filter(guid=str(input.id)).first()
        if token is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "api token not found")
        token.is_revoked = True
        token.save(update_fields=["is_revoked", "updated_at", "version"])
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))


def _resolve_scope_pk(scope_kind: str, scope_guid: str) -> int | None:
    """Map a (scope_kind, guid) pair to the corresponding integer PK."""
    if scope_kind == "ORG":
        row = Organization.objects.filter(guid=scope_guid).first()
    elif scope_kind == "TEAM":
        row = Team.objects.filter(guid=scope_guid).first()
    elif scope_kind == "PROJECT":
        row = Project.objects.filter(guid=scope_guid).first()
    else:
        return None
    return row.pk if row else None


def _make_token_secret() -> tuple[str, str, str]:
    """Generate a token; return (plaintext, hash, last4)."""
    import hashlib
    import secrets

    plaintext = "alft_" + secrets.token_urlsafe(32)
    digest = hashlib.sha256(plaintext.encode()).hexdigest()
    last4 = plaintext[-4:]
    return plaintext, digest, last4
