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
    IdentityProvider,
    Invitation,
    Member,
    Organization,
    Policy,
    Project,
    Role,
    RoleBinding,
    Team,
)
from astrolift_identity.schema.types import (
    ApiTokenPlaintextType,
    IdentityProviderType,
    InvitationCreatedType,
    InvitationType,
    MyProfileType,
    OrganizationType,
    PolicyType,
    ProjectType,
    RoleBindingType,
    RoleType,
    TeamType,
    api_token_to_type,
    identity_provider_to_type,
    invitation_to_type,
    organization_to_type,
    policy_to_type,
    project_to_type,
    role_binding_to_type,
    role_to_type,
    team_to_type,
)
from core.decorators import tenant_scoped
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
    allow_user_profile_edit: bool | None = None


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
class CreateInvitationInput:
    email: str
    role_slug: str | None = None
    expires_in_days: int | None = None


@strawberry.input
class RevokeInvitationInput:
    id: GUID


@strawberry.input
class AcceptInvitationInput:
    token: str


@strawberry.input
class CreateRoleInput:
    slug: str
    name: str
    scope_level: str
    permissions: list[str]
    description: str = ""


@strawberry.input
class UpdateRoleInput:
    id: GUID
    name: str | None = None
    permissions: list[str] | None = None
    description: str | None = None


@strawberry.input
class DeleteRoleInput:
    id: GUID


@strawberry.input
class UpdateMyProfileInput:
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None


@strawberry.input
class CreateApiTokenInput:
    name: str
    scopes: list[str] | None = None
    expires_in_days: int | None = None
    team_slug: str | None = None


@strawberry.input
class RevokeApiTokenInput:
    id: GUID


@strawberry.input
class CreatePolicyInput:
    name: str
    slug: str
    description: str | None = None
    scope_level: str = "ORG"
    scope_id: int | None = None
    effect: str = "DENY"
    action_pattern: str = "*"
    resource_pattern: strawberry.scalars.JSON | None = None
    conditions: strawberry.scalars.JSON | None = None
    actor_pattern: strawberry.scalars.JSON | None = None


@strawberry.input
class UpdatePolicyInput:
    id: GUID
    name: str | None = None
    description: str | None = None
    effect: str | None = None
    action_pattern: str | None = None
    resource_pattern: strawberry.scalars.JSON | None = None
    conditions: strawberry.scalars.JSON | None = None
    actor_pattern: strawberry.scalars.JSON | None = None


@strawberry.input
class CreateIdentityProviderInput:
    """Per-kind config validated server-side; see IdentityProviderKindValidator."""

    kind: str  # oidc | saml | cognito | auth0 | okta | azure_ad | google | github | local
    display_name: str | None = None
    config: strawberry.scalars.JSON | None = None
    metadata_url: str | None = None
    oidc_discovery_url: str | None = None
    client_id: str | None = None
    client_secret_ref: str | None = None
    set_active: bool = False


@strawberry.input
class UpdateIdentityProviderInput:
    id: GUID
    display_name: str | None = None
    config: strawberry.scalars.JSON | None = None
    metadata_url: str | None = None
    oidc_discovery_url: str | None = None
    client_id: str | None = None
    client_secret_ref: str | None = None


@strawberry.input
class SetActiveIdentityProviderInput:
    id: GUID


@strawberry.input
class LogoutAllSessionsInput:
    keep_current: bool = True


@strawberry.type(name="AstroliftLogoutAllSessionsPayload")
class _LogoutAllSessionsPayload:
    revoked_count: int
    kept_current: bool


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
        if input.allow_user_profile_edit is not None:
            org.allow_user_profile_edit = input.allow_user_profile_edit
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
    @tenant_scoped()
    def create_team(self, info: Info, input: CreateTeamInput) -> MutationResultType[TeamType]:
        org = _resolve_org(input.organization_id)
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
    @tenant_scoped()
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
    @tenant_scoped()
    def create_project(self, info: Info, input: CreateProjectInput) -> MutationResultType[ProjectType]:
        team = _resolve_team(input.team_id)
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
    @require_permission(Permission.PROJECT_UPDATE)
    @tenant_scoped()
    def update_project(self, info: Info, input: UpdateProjectInput) -> MutationResultType[ProjectType]:
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
    @tenant_scoped()
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
    @tenant_scoped()
    def grant_role(self, info: Info, input: GrantRoleInput) -> MutationResultType[RoleBindingType]:
        from django.contrib.auth import get_user_model

        try:
            target_user_pk = int(input.user_id)
        except ValueError:
            return gql_failure(ErrorCode.VALIDATION.value, "userId must be a numeric pk", field="userId")

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
            return gql_failure(ErrorCode.NOT_FOUND.value, "scope not found", field="scopeGuid")

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
    @tenant_scoped()
    def revoke_role_binding(
        self, info: Info, input: RevokeRoleBindingInput
    ) -> MutationResultType[_SoftDeletePayload]:
        binding = RoleBinding.objects.filter(guid=str(input.id)).first()
        if binding is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role binding not found")
        binding.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    # ---- Custom roles ----------------------------------------------------

    @strawberry.field
    @mutation_audit(action="role.create")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def create_role(self, info: Info, input: CreateRoleInput) -> MutationResultType[RoleType]:
        """Define a custom role for the current org.

        System roles ship with the platform (``is_system=True``) and
        are not editable here — operators clone-and-prune by creating
        a new custom role with the subset of permissions they want.
        Permissions are validated against the catalog at the model
        level (``Role.clean()``); a typo surfaces as VALIDATION.
        """
        from django.core.exceptions import ValidationError

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        scope = input.scope_level.upper()
        if scope not in {"ORG", "TEAM", "PROJECT", "APP"}:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"scope_level must be ORG/TEAM/PROJECT/APP, got {scope!r}",
                field="scopeLevel",
            )

        slug = input.slug.strip().lower()
        if not slug:
            return gql_failure(ErrorCode.VALIDATION.value, "slug required", field="slug")

        if Role.objects.filter(organization_id=org_id, slug=slug, deleted_at__isnull=True).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"a role with slug {slug!r} already exists in this org",
                field="slug",
            )

        role = Role(
            organization_id=org_id,
            slug=slug,
            name=input.name.strip(),
            description=input.description or "",
            scope_level=scope,
            permissions=list(input.permissions or []),
            is_system=False,
        )
        try:
            role.full_clean()
        except ValidationError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "; ".join(f"{k}: {v[0]}" for k, v in exc.message_dict.items()),
                field="permissions" if "permissions" in exc.message_dict else None,
            )
        role.save()
        return gql_success(role_to_type(role))

    @strawberry.field
    @mutation_audit(action="role.update")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def update_role(self, info: Info, input: UpdateRoleInput) -> MutationResultType[RoleType]:
        """Update a custom role. System roles are read-only here.

        Lookup is by guid first so we can return PRECONDITION for the
        is_system case explicitly. Cross-org access then falls through
        to NOT_FOUND.
        """
        from django.core.exceptions import ValidationError

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        role = Role.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
        if role is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role not found")
        if role.is_system:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "system roles cannot be edited; clone into a custom role instead",
            )
        if role.organization_id != org_id:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role not found")

        fields_to_update = ["updated_at", "version"]
        if input.name is not None:
            role.name = input.name.strip()
            fields_to_update.append("name")
        if input.description is not None:
            role.description = input.description
            fields_to_update.append("description")
        if input.permissions is not None:
            role.permissions = list(input.permissions)
            fields_to_update.append("permissions")

        try:
            role.full_clean()
        except ValidationError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "; ".join(f"{k}: {v[0]}" for k, v in exc.message_dict.items()),
                field="permissions" if "permissions" in exc.message_dict else None,
            )
        role.save(update_fields=fields_to_update)
        return gql_success(role_to_type(role))

    @strawberry.field
    @mutation_audit(action="role.delete")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def soft_delete_role(self, info: Info, input: DeleteRoleInput) -> MutationResultType[_SoftDeletePayload]:
        """Soft-delete a custom role. Bindings to it stay in place
        until separately revoked — clearing them in the same call
        would silently kick users out of access; deliberate.

        Same lookup pattern as update_role: by guid first so the
        is_system case returns PRECONDITION explicitly.
        """
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        role = Role.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
        if role is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role not found")
        if role.is_system:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "system roles cannot be deleted",
            )
        if role.organization_id != org_id:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role not found")
        role.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    # ---- Invitations -----------------------------------------------------

    @strawberry.field
    @mutation_audit(action="invitation.create")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def create_invitation(
        self, info: Info, input: CreateInvitationInput
    ) -> MutationResultType[InvitationCreatedType]:
        """Issue an invitation token for an email address.

        The plaintext token is returned exactly once via the
        InvitationCreatedType payload — the DB stores only its
        SHA-256 hash. Operators are responsible for getting the
        plaintext to the recipient out-of-band (UI exposes a
        copy-link affordance with the accept URL).
        """
        from datetime import timedelta

        from django.utils import timezone

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        email = input.email.strip().lower()
        if not email or "@" not in email:
            return gql_failure(ErrorCode.VALIDATION.value, "valid email required", field="email")

        role = None
        if input.role_slug:
            role = Role.objects.filter(slug=input.role_slug).first()
            if role is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "role not found", field="roleSlug")

        # Reject if there's already an active pending invite for the
        # same email at this scope — re-sending should go through a
        # separate "rotate token" flow rather than silently doubling
        # up rows.
        if Invitation.objects.filter(
            email=email,
            scope_kind=Invitation.ScopeKind.ORG,
            scope_id=org_id,
            status=Invitation.Status.PENDING,
            deleted_at__isnull=True,
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"a pending invitation already exists for {email}",
                field="email",
            )

        plaintext, digest, _last4 = _make_token_secret()
        expires_at = timezone.now() + timedelta(
            days=int(input.expires_in_days) if input.expires_in_days else 7
        )
        inv = Invitation.objects.create(
            email=email,
            scope_kind=Invitation.ScopeKind.ORG,
            scope_id=org_id,
            role=role,
            token_hash=digest,
            expires_at=expires_at,
            invited_by=_actor(),
            status=Invitation.Status.PENDING,
        )
        return gql_success(
            InvitationCreatedType(
                invitation=invitation_to_type(inv),
                plaintext_token=plaintext,
                accept_url_path=f"/auth/invitation/{plaintext}",
            )
        )

    @strawberry.field
    @mutation_audit(action="invitation.revoke")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def revoke_invitation(
        self, info: Info, input: RevokeInvitationInput
    ) -> MutationResultType[InvitationType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        inv = Invitation.objects.filter(
            guid=str(input.id),
            scope_kind=Invitation.ScopeKind.ORG,
            scope_id=org_id,
            deleted_at__isnull=True,
        ).first()
        if inv is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "invitation not found")
        if inv.status not in (Invitation.Status.PENDING,):
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"invitation is already {inv.status}",
            )
        inv.status = Invitation.Status.REVOKED
        inv.save(update_fields=["status", "updated_at", "version"])
        return gql_success(invitation_to_type(inv))

    # By definition the accepting user has no tenant context yet at
    # the moment they click the link. The token *is* the auth check.
    # Listed in the tenancy guardrail's EXEMPT set with this rationale.
    @strawberry.field
    @mutation_audit(action="invitation.accept")
    def accept_invitation(
        self, info: Info, input: AcceptInvitationInput
    ) -> MutationResultType[InvitationType]:
        import hashlib

        from django.utils import timezone

        request = getattr(info.context, "request", None)
        user = getattr(request, "user", None) if request else None
        if user is None or not getattr(user, "is_authenticated", False):
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "sign in before accepting an invitation",
            )

        digest = hashlib.sha256(input.token.encode()).hexdigest()
        inv = Invitation.objects.filter(token_hash=digest, deleted_at__isnull=True).first()
        if inv is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "invitation not found")
        if inv.status != Invitation.Status.PENDING:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"invitation is {inv.status}; nothing to accept",
            )
        if inv.is_expired:
            inv.status = Invitation.Status.EXPIRED
            inv.save(update_fields=["status", "updated_at", "version"])
            return gql_failure(ErrorCode.PRECONDITION.value, "invitation has expired")

        # Match the invite to a user. We require the invited email to
        # match the caller's email so an invite leaked to another user
        # can't be redeemed against a different account.
        if (user.email or "").lower() != inv.email.lower():
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "invitation email does not match your account",
            )

        # Idempotency: a concurrent accept would insert a duplicate
        # Member row; the unique constraint on (user, scope_kind,
        # scope_id) catches that case at the DB level. We swallow the
        # IntegrityError to a clean precondition error.
        from django.db import IntegrityError, transaction

        try:
            with transaction.atomic():
                Member.objects.create(
                    user=user,
                    scope_kind=inv.scope_kind,
                    scope_id=inv.scope_id,
                    is_active=True,
                    lifecycle=Member.Lifecycle.ACTIVE,
                    joined_at=timezone.now(),
                )
                if inv.role_id and inv.scope_kind == Invitation.ScopeKind.ORG:
                    RoleBinding.objects.create(
                        user=user,
                        role_id=inv.role_id,
                        scope_kind=RoleBinding.ScopeKind.ORG,
                        scope_id=inv.scope_id,
                    )
                inv.status = Invitation.Status.ACCEPTED
                inv.accepted_at = timezone.now()
                inv.save(update_fields=["status", "accepted_at", "updated_at", "version"])
        except IntegrityError:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "you are already a member of this scope",
            )

        return gql_success(invitation_to_type(inv))

    # ---- API tokens --------------------------------------------------

    @strawberry.field
    @mutation_audit(action="api_token.create")
    @require_permission(Permission.API_TOKEN_CREATE)
    @tenant_scoped()
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
                return gql_failure(ErrorCode.NOT_FOUND.value, "team not found", field="teamSlug")

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
    @tenant_scoped()
    def revoke_api_token(
        self, info: Info, input: RevokeApiTokenInput
    ) -> MutationResultType[_SoftDeletePayload]:
        token = ApiToken.objects.filter(guid=str(input.id)).first()
        if token is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "api token not found")
        token.is_revoked = True
        token.save(update_fields=["is_revoked", "updated_at", "version"])
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    # ---- ABAC Policy -------------------------------------------------

    @strawberry.field
    @mutation_audit(action="policy.create")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def create_policy(self, info: Info, input: CreatePolicyInput) -> MutationResultType[PolicyType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        if input.scope_level not in {"ORG", "TEAM", "PROJECT", "APP"}:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "scopeLevel must be ORG / TEAM / PROJECT / APP",
                field="scopeLevel",
            )
        if input.effect not in {"ALLOW", "DENY"}:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "effect must be ALLOW or DENY",
                field="effect",
            )
        if Policy.objects.filter(organization=org, slug=input.slug).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"policy with slug {input.slug!r} already exists",
                field="slug",
            )

        policy = Policy.objects.create(
            organization=org,
            name=input.name.strip(),
            slug=input.slug,
            description=input.description or "",
            scope_level=input.scope_level,
            scope_id=input.scope_id,
            effect=input.effect,
            action_pattern=input.action_pattern or "*",
            resource_pattern=input.resource_pattern or {},
            conditions=input.conditions or [],
            actor_pattern=input.actor_pattern or {},
        )
        return gql_success(policy_to_type(policy))

    @strawberry.field
    @mutation_audit(action="policy.update")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def update_policy(self, info: Info, input: UpdatePolicyInput) -> MutationResultType[PolicyType]:
        policy = Policy.objects.filter(guid=str(input.id)).first()
        if policy is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "policy not found")

        for field in (
            "name",
            "description",
            "effect",
            "action_pattern",
            "resource_pattern",
            "conditions",
            "actor_pattern",
        ):
            new_value = getattr(input, field)
            if new_value is not None:
                setattr(policy, field, new_value)
        policy.save()
        return gql_success(policy_to_type(policy))

    @strawberry.field
    @mutation_audit(action="policy.delete")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def soft_delete_policy(
        self, info: Info, input: SoftDeleteByGuidInput
    ) -> MutationResultType[_SoftDeletePayload]:
        policy = Policy.objects.filter(guid=str(input.id)).first()
        if policy is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "policy not found")
        policy.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    # ---- Identity providers ------------------------------------------

    @strawberry.field
    @mutation_audit(action="identity_provider.create")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def create_identity_provider(
        self, info: Info, input: CreateIdentityProviderInput
    ) -> MutationResultType[IdentityProviderType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        valid_kinds = {k.value for k in IdentityProvider.Kind}
        if input.kind not in valid_kinds:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"kind must be one of {sorted(valid_kinds)}",
                field="kind",
            )

        validated = _validate_idp_config(input)
        if validated is not None:
            return validated

        idp = IdentityProvider.objects.create(
            organization=org,
            kind=input.kind,
            display_name=(input.display_name or "").strip(),
            config=input.config or {},
            metadata_url=input.metadata_url or "",
            oidc_discovery_url=input.oidc_discovery_url or "",
            client_id=input.client_id or "",
            client_secret_ref=input.client_secret_ref or "",
            is_default=False,
        )

        is_active = False
        if input.set_active:
            org.identity_provider_id = idp.pk
            org.save(update_fields=["identity_provider", "updated_at", "version"])
            is_active = True

        return gql_success(identity_provider_to_type(idp, is_active=is_active))

    @strawberry.field
    @mutation_audit(action="identity_provider.update")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def update_identity_provider(
        self, info: Info, input: UpdateIdentityProviderInput
    ) -> MutationResultType[IdentityProviderType]:
        idp = IdentityProvider.objects.select_related("organization").filter(guid=str(input.id)).first()
        if idp is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "identity provider not found")

        for field in (
            "display_name",
            "config",
            "metadata_url",
            "oidc_discovery_url",
            "client_id",
            "client_secret_ref",
        ):
            new_value = getattr(input, field)
            if new_value is not None:
                setattr(idp, field, new_value)
        idp.save()
        active_id = (
            Organization.objects.filter(pk=idp.organization_id)
            .values_list("identity_provider_id", flat=True)
            .first()
        )
        return gql_success(identity_provider_to_type(idp, is_active=(idp.pk == active_id)))

    @strawberry.field
    @mutation_audit(action="identity_provider.set_active")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def set_active_identity_provider(
        self, info: Info, input: SetActiveIdentityProviderInput
    ) -> MutationResultType[IdentityProviderType]:
        idp = IdentityProvider.objects.select_related("organization").filter(guid=str(input.id)).first()
        if idp is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "identity provider not found")
        org = idp.organization
        org.identity_provider_id = idp.pk
        org.save(update_fields=["identity_provider", "updated_at", "version"])
        return gql_success(identity_provider_to_type(idp, is_active=True))

    @strawberry.field
    @mutation_audit(action="identity_provider.delete")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def soft_delete_identity_provider(
        self, info: Info, input: SoftDeleteByGuidInput
    ) -> MutationResultType[_SoftDeletePayload]:
        idp = IdentityProvider.objects.select_related("organization").filter(guid=str(input.id)).first()
        if idp is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "identity provider not found")
        # Refuse to delete the IdP that's currently active — the operator
        # must set_active to a different one first, or this install would
        # be left without a way to log in.
        if idp.organization.identity_provider_id == idp.pk:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "this provider is currently active; pick a different one before deleting",
            )
        idp.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    @strawberry.field
    @mutation_audit(action="profile.update_self")
    def update_my_profile(self, info: Info, input: UpdateMyProfileInput) -> MutationResultType[MyProfileType]:
        """Self-service profile edit. Gated by the org-level
        ``allow_user_profile_edit`` toggle and per-field IdP locks
        (a field claimed by the IdP at last login can't be edited
        locally because the next sync would overwrite it).

        Intentionally not gated by ``@require_permission`` — every
        authenticated user is allowed to edit *their own* profile.
        Admins can disable the entire feature org-wide via the
        toggle, which the resolver enforces here.
        """

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        org = Organization.objects.filter(pk=org_id).first() if org_id is not None else None
        if org is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no organization")

        if not org.allow_user_profile_edit:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "profile editing is disabled by your organization administrator",
            )

        session = getattr(request, "session", None) if request else None
        locked = _idp_locked_fields(viewer, session=session)

        if input.first_name is not None and "first_name" in locked:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "first_name is managed by your identity provider",
                field="firstName",
            )
        if input.last_name is not None and "last_name" in locked:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "last_name is managed by your identity provider",
                field="lastName",
            )
        if input.email is not None and "email" in locked:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "email is managed by your identity provider",
                field="email",
            )

        if input.first_name is not None:
            viewer.first_name = input.first_name.strip()[:150]
        if input.last_name is not None:
            viewer.last_name = input.last_name.strip()[:150]
        if input.email is not None:
            email = input.email.strip()[:254]
            if email and "@" not in email:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "email must be a valid address",
                    field="email",
                )
            viewer.email = email
        viewer.save(update_fields=["first_name", "last_name", "email"])

        return gql_success(_my_profile_payload(viewer, org, locked))

    @strawberry.field
    @mutation_audit(action="session.logout_all")
    def logout_all_sessions(
        self, info: Info, input: LogoutAllSessionsInput
    ) -> MutationResultType[_LogoutAllSessionsPayload]:
        """Revoke every active django_session row bound to the
        signed-in viewer. Defaults to keeping the current session
        active so the caller doesn't immediately bounce to /login.

        Self-only — every authenticated user can sign themselves
        out of their other devices; no permission gate.
        """
        from django.contrib.sessions.models import Session

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        current_key = getattr(getattr(request, "session", None), "session_key", None)
        viewer_pk = str(viewer.pk)
        keys_to_delete: list[str] = []
        for s in Session.objects.iterator():
            try:
                data = s.get_decoded()
            except Exception:
                continue
            if str(data.get("_auth_user_id", "")) != viewer_pk:
                continue
            if input.keep_current and s.session_key == current_key:
                continue
            keys_to_delete.append(s.session_key)

        if keys_to_delete:
            Session.objects.filter(session_key__in=keys_to_delete).delete()

        return gql_success(
            _LogoutAllSessionsPayload(
                revoked_count=len(keys_to_delete),
                kept_current=bool(input.keep_current and current_key is not None),
            )
        )


def _validate_idp_config(input) -> MutationResultType | None:
    """Per-kind config validation. Returns a failure envelope or None."""
    kind = input.kind
    if kind == "local":
        return None
    if kind in {"oidc", "okta", "azure_ad", "auth0", "google", "github"}:
        if not (input.oidc_discovery_url or input.metadata_url):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"{kind} requires oidcDiscoveryUrl",
                field="oidcDiscoveryUrl",
            )
        if not input.client_id:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"{kind} requires clientId",
                field="clientId",
            )
    if kind == "cognito":
        # Cognito needs user_pool_id + region in addition to OIDC fields
        cfg = input.config or {}
        for required in ("user_pool_id", "region"):
            if required not in cfg:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"cognito config requires {required!r}",
                    field=f"config.{required}",
                )
    if kind == "saml":
        if not input.metadata_url:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "saml requires metadataUrl",
                field="metadataUrl",
            )
    return None


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


def _idp_locked_fields(user, session=None) -> list[str]:
    """Fields whose value came from the IdP at last login.

    Phase 1 logic: when the user signed in via local accounts (the
    default ``django.contrib.auth.backends.ModelBackend``), nothing
    is locked — they typed their own credentials, they own the data.
    When they came through OIDC / SAML / Cognito, ``email`` is
    locked because it's the IdP's primary identity claim and
    overwriting locally would just get clobbered on next login.

    The auth backend is read from ``session['_auth_user_backend']``
    (set by Django's ``login()``); ``user.backend`` is only present
    during the login request itself, not on subsequent requests.

    Future: per-IdP claim mapping would move this to
    IdentityProvider.locked_fields so installs that don't get email
    from their IdP can still let users edit it.
    """
    backend = ""
    if session is not None:
        backend = session.get("_auth_user_backend", "") or ""
    if not backend:
        backend = getattr(user, "backend", "") or ""
    if not backend:
        # No backend tag → can't tell, assume local accounts so the
        # user gets edit access. SSO installs will set the backend.
        return []
    if "ModelBackend" in backend:
        return []
    return ["email"]


def _my_profile_payload(user, org, locked: list[str]) -> MyProfileType:
    return MyProfileType(
        user_id=user.pk,
        username=user.username or "",
        first_name=user.first_name or "",
        last_name=user.last_name or "",
        email=user.email or "",
        locked_fields=list(locked),
        org_allows_edit=org.allow_user_profile_edit,
    )
