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

import datetime as dt

import strawberry
from django.db.models import Q
from strawberry.types import Info

from astrolift_graphql import (
    GUID,
    MutationErrorType,
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
    OrganizationAllowlistedDomain,
    Policy,
    Project,
    Role,
    RoleBinding,
    Team,
)
from astrolift_identity.schema.types import (
    ApiTokenPlaintextType,
    EnrollmentQrPayloadType,
    IdentityProviderType,
    InvitationCreatedType,
    InvitationType,
    MyProfileType,
    OrganizationAllowlistedDomainType,
    OrganizationType,
    PolicyType,
    ProjectType,
    RoleBindingType,
    RoleType,
    TeamType,
    api_token_to_type,
    identity_provider_to_type,
    invitation_to_type,
    organization_allowlisted_domain_to_type,
    organization_to_type,
    policy_to_type,
    project_to_type,
    role_binding_to_type,
    role_to_type,
    team_to_type,
)
from astrolift_identity.step_up import requires_elevation
from core.decorators import tenant_scoped
from core.mutations import AuditEntry, ErrorCode, emit_audit, mutation_audit
from core.naming import PROJECT_SLUG, TEAM_SLUG, NamingViolation
from core.optimistic import check_version_match as _check_version_match
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
    slug: str | None = None
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
    slug: str | None = None
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
class BulkRevokeRoleBindingsInput:
    """Input for bulk role-binding revocation on the members list.

    Operator selects N bindings in the UI and submits the whole list
    in one mutation; the resolver still does a per-id permission +
    not-found check so a partial failure (one binding already gone,
    one in a sibling org) doesn't kill the whole batch.
    """

    binding_ids: list[GUID]


@strawberry.input
class BulkAssignTeamMemberRolesInput:
    """Input for bulk role assignment on the team-members panel.

    The role is granted on the *team* scope to each member's user.
    Idempotent: a member that already has this role at this scope is
    reported in the per-id results as already-assigned, not as a
    failure — re-running with the same input is a no-op.
    """

    team_id: GUID
    role_id: GUID
    member_ids: list[GUID]


@strawberry.input
class CreateInvitationInput:
    email: str
    role_slug: str | None = None
    expires_in_days: int | None = None


@strawberry.input
class RevokeInvitationInput:
    id: GUID


@strawberry.input
class ResendInvitationInput:
    id: GUID


@strawberry.input
class AcceptInvitationInput:
    token: str


@strawberry.input
class AddOrganizationAllowlistDomainInput:
    domain: str
    default_role_slug: str | None = None
    requires_review: bool = False


@strawberry.input
class RemoveOrganizationAllowlistDomainInput:
    id: GUID


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
    timezone: str | None = None
    """IANA timezone name to save as the user's explicit zone override.
    Pass an empty string to clear the override (fall back to
    browser-detected zone). Pass ``None`` to leave the current value
    unchanged. Validated against ``zoneinfo.available_timezones()``."""


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
class GenerateInstallEnrollmentQrInput:
    """Input for ``generateInstallEnrollmentQr`` (#494).

    ``label`` is the human-friendly name surfaced on the enrolled
    device's listing ("Sarah's iPhone"). Operator-facing only; mobile
    sends its own User-Agent at redeem time, which overrides this on
    the resulting session row.

    ``ttl_seconds`` is clamped server-side to
    ``ENROLLMENT_TTL_MIN..ENROLLMENT_TTL_MAX``; ``None`` resolves to
    the default (5 min per the issue spec).
    """

    label: str | None = None
    ttl_seconds: int | None = None


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
    # Optimistic-concurrency gate (#497) — null skips the check.
    if_match_version: int | None = None


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
    # Optimistic-concurrency gate (#497) — null skips the check.
    if_match_version: int | None = None


@strawberry.input
class SetActiveIdentityProviderInput:
    id: GUID


@strawberry.input
class LogoutAllSessionsInput:
    keep_current: bool = True


@strawberry.input
class RevokeAstroliftSessionInput:
    """Revoke a single session by its GUID (#480).

    ``reason`` is an optional free-form note (truncated to 48 chars)
    appended to the canonical ``RevocationReason`` tag on the
    audit row — operators use it for "lost device", "fired
    employee", etc. Required arg is ``session_id`` only.
    """

    session_id: GUID
    reason: str | None = None


@strawberry.type(name="AstroliftRevokeAstroliftSessionPayload")
class _RevokeAstroliftSessionPayload:
    id: GUID
    revoked: bool


@strawberry.type(name="AstroliftHeartbeatSessionPayload")
class _HeartbeatSessionPayload:
    id: GUID
    last_seen_at: dt.datetime | None


@strawberry.input
class ElevateAdminSessionInput:
    """Step-up auth input (#487, spec 27 §4.1).

    ``method`` is one of ``password | otp | webauthn | magic_link``.
    ``credential`` is the raw value the operator presented (password
    string, OTP code, WebAuthn assertion serialized as JSON, magic-
    link token). Per-call ``ttl_seconds`` is clamped server-side at
    ``STEP_UP_AUTH_MAX_TTL_SECONDS``; omit to use the operator-
    configurable default.
    """

    method: str
    credential: str
    ttl_seconds: int | None = None


@strawberry.input
class MarkOnboardingCompleteInput:
    """Flip the active org's ``onboarding_completed_at`` to now.

    ``skip`` distinguishes "operator explicitly skipped the wizard"
    from "operator completed it" so the audit log can carry the
    intent — the persisted timestamp is the same either way.
    Idempotent: a re-run on an already-complete org is a no-op and
    returns the existing payload rather than failing.
    """

    skip: bool = False


@strawberry.type(name="AstroliftMarkOnboardingCompletePayload")
class _MarkOnboardingCompletePayload:
    organization: OrganizationType
    already_completed: bool


@strawberry.type(name="AstroliftLogoutAllSessionsPayload")
class _LogoutAllSessionsPayload:
    revoked_count: int
    kept_current: bool


@strawberry.type(name="AstroliftElevatePayload")
class _ElevatePayload:
    """Result of a successful elevateAdminSession call.

    ``elevated_until`` is the absolute UTC timestamp at which the
    elevation lapses; the FE renders ``seconds_remaining`` as a
    countdown next to the "Admin elevated" nav indicator.
    ``method`` echoes back which credential was accepted so the FE
    can show "elevated via WebAuthn" on the session info popover.
    """

    elevated_until: dt.datetime
    seconds_remaining: int
    method: str


@strawberry.type(name="AstroliftDeelevatePayload")
class _DeelevatePayload:
    """Result of deelevateAdminSession — the session is now un-elevated.

    Carries ``previously_elevated`` so the audit row + UI can
    distinguish "operator clicked log-me-out-of-admin while elevated"
    from "operator clicked it while already un-elevated" (a no-op).
    """

    previously_elevated: bool


# ---- Device attestation (#496) ----------------------------------------


@strawberry.input
class RequestAttestationChallengeInput:
    """Ask the platform for a one-shot attestation nonce.

    ``kind`` is ``ios_appattest | android_play_integrity``. The nonce
    that comes back is bound to the (viewer, kind) pair — re-using
    it on the wrong kind or for a different user fails the
    challenge-consume check at submission time.
    """

    kind: str


@strawberry.type(name="AstroliftAttestationChallengePayload")
class _AttestationChallengePayload:
    """Server-issued attestation challenge.

    ``challenge`` is the nonce the client must include in its
    attestation/integrity blob. ``expires_at`` is the absolute UTC
    deadline after which the verifier will reject submissions.
    """

    challenge: str
    expires_at: dt.datetime
    kind: str


@strawberry.input
class AttestSessionInput:
    """Submit an iOS App Attest / Android Play Integrity blob (#496).

    Exactly one of ``attestation_object`` (iOS) and ``integrity_token``
    (Android) is required; ``key_id`` is iOS-only.
    """

    kind: str
    challenge: str
    attestation_object: str | None = None
    key_id: str | None = None
    integrity_token: str | None = None


@strawberry.type(name="AstroliftAttestationResult")
class _AttestationResult:
    """Outcome of an attestSession / assertSession call.

    ``trust_level`` is the value persisted on the session row
    (``genuine`` on success; ``failed`` on a rejected verification).
    ``attested_at`` is the timestamp of the latest verification.
    """

    trust_level: str
    kind: str
    attested_at: dt.datetime | None
    reason: str | None = None


@strawberry.input
class AssertSessionInput:
    """Submit a periodic iOS App Attest assertion against an attested session.

    iOS-only — Android Play Integrity is per-token + has no long-lived
    signing key, so re-attestation goes through ``attestSession``
    again.
    """

    assertion: str
    challenge: str


@strawberry.type
class _SoftDeletePayload:
    id: GUID
    deleted: bool


@strawberry.type(name="AstroliftBulkOpItemResult")
class _BulkOpItemResult:
    """Per-id outcome of a bulk mutation.

    ``id`` is the GUID the operator submitted (binding id, member id,
    etc.). ``ok`` is the per-id success bit; on failure ``errors``
    carries the same shape as the top-level MutationResult envelope.
    The ``already_existed`` flag distinguishes "idempotent no-op"
    from "fresh action" so the UI can render a soft "already had this
    role" line instead of a hard error.
    """

    id: GUID
    ok: bool
    already_existed: bool = False
    errors: list[MutationErrorType] = strawberry.field(default_factory=list)


@strawberry.type(name="AstroliftBulkRevokeRoleBindingsPayload")
class _BulkRevokeRoleBindingsPayload:
    results: list[_BulkOpItemResult]
    revoked_count: int
    failed_count: int


@strawberry.type(name="AstroliftBulkAssignTeamMemberRolesPayload")
class _BulkAssignTeamMemberRolesPayload:
    results: list[_BulkOpItemResult]
    assigned_count: int
    already_assigned_count: int
    failed_count: int


# ---- helpers ---------------------------------------------------------


def _actor():
    tenant = get_current_tenant()
    actor_id = tenant.actor_user_id if tenant else None
    if actor_id is None:
        return None
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=actor_id).first()


def _resolve_org(guid: GUID, org_id: int | None) -> Organization | None:
    """Resolve an org by guid, but only when it is the caller's own org.

    A guid for any other org — or a missing tenant (``org_id is None``)
    — resolves to None so the caller can't reach across tenants.
    """
    return Organization.objects.filter(guid=str(guid), pk=org_id).first()


def _resolve_team(guid: GUID, org_id: int | None) -> Team | None:
    """Resolve a team by guid, scoped to the caller's org."""
    return Team.objects.filter(guid=str(guid), organization_id=org_id).first()


def _resolve_project(guid: GUID, org_id: int | None) -> Project | None:
    """Resolve a project by guid, scoped to the caller's org.

    ``organization_id`` is denormalized onto Project, so no team join is
    needed to enforce the boundary.
    """
    return Project.objects.filter(guid=str(guid), organization_id=org_id).first()


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
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        org = _resolve_org(input.id, org_id)
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        if input.name is not None:
            org.name = input.name
        if input.website is not None:
            org.website = input.website
        if input.audit_log_retention_days is not None:
            days = int(input.audit_log_retention_days)
            # Sane bound (spec ceiling ~7 years). The DB column is a
            # PositiveIntegerField, which still admits 0 and absurdly
            # large values; both the /administration/organization and
            # /administration/audit surfaces write this field, so guard
            # it here rather than trusting client-side min/max alone.
            if days < 1 or days > 2557:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "auditLogRetentionDays must be between 1 and 2557 (about 7 years)",
                    field="auditLogRetentionDays",
                )
            org.audit_log_retention_days = days
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
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        org = _resolve_org(input.id, org_id)
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

    # ---- Project -----------------------------------------------------

    @strawberry.field
    @mutation_audit(action="project.create")
    @require_permission(Permission.PROJECT_CREATE)
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
    @require_permission(Permission.PROJECT_UPDATE)
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
                Project.objects.filter(
                    team_id=project.team_id, slug=input.slug, organization_id=org_id
                )
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
    @require_permission(Permission.PROJECT_DELETE)
    @tenant_scoped()
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

    # ---- RBAC --------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="role_binding.grant")
    @requires_elevation(action_label="role_binding.grant")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def grant_role(self, info: Info, input: GrantRoleInput) -> MutationResultType[RoleBindingType]:
        from django.contrib.auth import get_user_model

        # #1183 privilege-escalation fix. Without a caller-org constraint
        # on the scope, role, and target user, a caller who holds
        # ORG_MANAGE_MEMBERS in their own org could grant any role, on any
        # scope, to any user in any other org — cross-tenant account
        # takeover. Every lookup below is bound to the caller's org and
        # fails closed.
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "scope not found", field="scopeGuid")

        try:
            target_user_pk = int(input.user_id)
        except ValueError:
            return gql_failure(ErrorCode.VALIDATION.value, "userId must be a numeric pk", field="userId")

        # The target must already be a member of the caller's org.
        # Checking org membership (rather than global user existence)
        # both enforces the tenant boundary and avoids leaking whether an
        # arbitrary user pk exists anywhere on the install.
        if not Member.objects.filter(
            user_id=target_user_pk,
            scope_kind=Member.ScopeKind.ORG,
            scope_id=org_id,
            deleted_at__isnull=True,
        ).exists():
            return gql_failure(ErrorCode.NOT_FOUND.value, "user not found", field="userId")

        User = get_user_model()
        user = User.objects.filter(pk=target_user_pk).first()
        if user is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "user not found", field="userId")

        # Restrict the role to the caller's own custom roles or a
        # system/null-org role — another org's custom role reads as
        # not-found.
        role = (
            Role.objects.filter(guid=str(input.role_id))
            .filter(Q(organization_id=org_id) | Q(organization__isnull=True))
            .first()
        )
        if role is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role not found", field="roleId")

        # Resolve the scope guid WITHIN the caller org — a scope owned by
        # another org resolves to None and reads as not-found.
        scope_kind = input.scope_kind.upper()
        scope_id = _resolve_scope_pk_in_org(scope_kind, str(input.scope_guid), org_id)
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

        # Resolve a single source-scope label inline so the FE can show
        # the role-source tooltip on the freshly-granted binding without
        # an extra refetch.
        from astrolift_identity.schema.queries import _resolve_source_scope_labels

        label = _resolve_source_scope_labels([binding]).get((binding.scope_kind, binding.scope_id), "")
        return gql_success(role_binding_to_type(binding, source_scope_label=label))

    @strawberry.field
    @mutation_audit(action="role_binding.revoke")
    @requires_elevation(action_label="role_binding.revoke")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def revoke_role_binding(
        self, info: Info, input: RevokeRoleBindingInput
    ) -> MutationResultType[_SoftDeletePayload]:
        from astrolift_identity.schema.queries import _org_scope_q

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role binding not found")
        # Only bindings on the caller org's own scopes are revocable; a
        # foreign-org binding guid reads as not-found.
        binding = RoleBinding.objects.filter(guid=str(input.id)).filter(_org_scope_q(org_id)).first()
        if binding is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role binding not found")
        binding.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    # ---- Bulk RBAC ---------------------------------------------------
    #
    # Bulk operations stay in the same MutationResult envelope as their
    # single-item counterparts. The top-level ``ok`` flips false only on
    # input-shape errors (empty list, missing team, etc.); per-id outcomes
    # (already-revoked, not-found, partial denies) live in
    # ``data.results`` so the UI can render row-level state without
    # losing the rows that succeeded. Each item also emits an individual
    # audit row via ``emit_audit`` so an operator who revokes 50
    # bindings at once still gets 50 audit entries — one per affected
    # subject — to satisfy compliance review.

    @strawberry.field
    @mutation_audit(action="role_binding.bulk_revoke")
    @requires_elevation(action_label="role_binding.bulk_revoke")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def bulk_revoke_astrolift_role_bindings(
        self, info: Info, input: BulkRevokeRoleBindingsInput
    ) -> MutationResultType[_BulkRevokeRoleBindingsPayload]:
        from core.mutations import AuditEntry, emit_audit

        ids = list(input.binding_ids or [])
        if not ids:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "bindingIds must contain at least one id",
                field="bindingIds",
            )
        # Cap the batch size so a runaway operator can't lock the table
        # for minutes; matches the largest realistic offboarding wave.
        if len(ids) > 500:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "bindingIds may not exceed 500 entries per call",
                field="bindingIds",
            )

        # De-duplicate while preserving submission order so duplicate
        # selections don't double-count in the audit log.
        seen: set[str] = set()
        ordered_unique: list[str] = []
        for gid in ids:
            gid_s = str(gid)
            if gid_s in seen:
                continue
            seen.add(gid_s)
            ordered_unique.append(gid_s)

        actor = _actor()
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None

        from astrolift_identity.schema.queries import _org_scope_q

        # Pre-fetch in one query so a 500-id batch is one trip, not 500.
        # Key by ``str(guid)`` because ``binding.guid`` is a ``UUID``
        # instance, not a string — a dict lookup with the operator-
        # supplied string would otherwise miss every row. Scoped to the
        # caller org's own scopes so a foreign-org binding guid reads as
        # not-found (fail closed) instead of being revocable cross-tenant.
        bindings_by_guid = {
            str(b.guid): b
            for b in RoleBinding.objects.select_related("user", "role")
            .filter(guid__in=ordered_unique, deleted_at__isnull=True)
            .filter(_org_scope_q(org_id))
        }

        results: list[_BulkOpItemResult] = []
        revoked = 0
        failed = 0

        for gid_s in ordered_unique:
            binding = bindings_by_guid.get(gid_s)
            if binding is None:
                failed += 1
                results.append(
                    _BulkOpItemResult(
                        id=GUID(gid_s),
                        ok=False,
                        errors=[
                            MutationErrorType(
                                code=ErrorCode.NOT_FOUND.value,
                                message="role binding not found or already revoked",
                                field=None,
                            )
                        ],
                    )
                )
                continue

            binding.soft_delete(by=actor)
            revoked += 1
            results.append(_BulkOpItemResult(id=GUID(gid_s), ok=True, errors=[]))

            # Per-binding audit row so the trail names *which* subject /
            # role lost access, not just "50 bindings revoked".
            emit_audit(
                AuditEntry(
                    actor_user_id=actor.pk if actor else None,
                    organization_id=org_id,
                    action="role_binding.revoke",
                    decision="ALLOW",
                    target_kind="role_binding",
                    target_id=str(binding.guid),
                    duration_ms=0,
                    permissions=(Permission.ORG_MANAGE_MEMBERS.value,),
                    error_code=None,
                    error_message=None,
                    extra={
                        "bulk": True,
                        "user_id": binding.user_id,
                        "role_id": binding.role_id,
                        "scope_kind": binding.scope_kind,
                        "scope_id": binding.scope_id,
                    },
                )
            )

        return gql_success(
            _BulkRevokeRoleBindingsPayload(
                results=results,
                revoked_count=revoked,
                failed_count=failed,
            )
        )

    @strawberry.field
    @mutation_audit(action="role_binding.bulk_assign_team")
    @requires_elevation(action_label="role_binding.bulk_assign_team")
    @require_permission(Permission.TEAM_MANAGE_MEMBERS)
    @tenant_scoped()
    def bulk_assign_astrolift_team_member_roles(
        self, info: Info, input: BulkAssignTeamMemberRolesInput
    ) -> MutationResultType[_BulkAssignTeamMemberRolesPayload]:
        from core.mutations import AuditEntry, emit_audit

        member_ids = list(input.member_ids or [])
        if not member_ids:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "memberIds must contain at least one id",
                field="memberIds",
            )
        if len(member_ids) > 500:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "memberIds may not exceed 500 entries per call",
                field="memberIds",
            )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        # The team must belong to the caller's org — a foreign team guid
        # reads as not-found so a caller can't bulk-assign roles into
        # another tenant's team.
        team = _resolve_team(input.team_id, org_id)
        if team is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "team not found", field="teamId")

        # Restrict the role to the caller's own custom roles or a
        # system/null-org role — another org's custom role reads as
        # not-found. Without this a caller could attach a foreign org's
        # custom role to their own team's members (same cross-tenant grant
        # class grant_role guards against). Mirrors the grant_role fix (#1183).
        role = (
            Role.objects.filter(guid=str(input.role_id), deleted_at__isnull=True)
            .filter(Q(organization_id=org_id) | Q(organization__isnull=True))
            .first()
        )
        if role is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "role not found", field="roleId")

        # A team-scoped binding only makes sense with a role whose
        # scope_level is TEAM (or ORG-level promoted down). We accept
        # ORG/TEAM here because some installs treat their ORG-level
        # role catalog as the union of all scopes; PROJECT/APP scoped
        # roles are deliberately rejected so we don't create bindings
        # the resolver chain can't ever evaluate.
        if role.scope_level not in {"ORG", "TEAM"}:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"role scope_level {role.scope_level!r} cannot be granted on a team scope",
                field="roleId",
            )

        seen: set[str] = set()
        ordered_unique: list[str] = []
        for gid in member_ids:
            gid_s = str(gid)
            if gid_s in seen:
                continue
            seen.add(gid_s)
            ordered_unique.append(gid_s)

        # Pull members in one query. Restrict to TEAM-scope members of
        # *this* team so the operator can't slip in a member guid from a
        # sibling team and grant a role into a scope they didn't intend.
        members_by_guid = {
            str(m.guid): m
            for m in Member.objects.select_related("user").filter(
                guid__in=ordered_unique,
                scope_kind=Member.ScopeKind.TEAM,
                scope_id=team.pk,
                deleted_at__isnull=True,
            )
        }

        # Existing bindings on this team for this role keyed by user_id
        # so the per-id loop can answer "already had it?" in O(1).
        existing_user_ids = set(
            RoleBinding.objects.filter(
                role=role,
                scope_kind=RoleBinding.ScopeKind.TEAM,
                scope_id=team.pk,
                deleted_at__isnull=True,
            ).values_list("user_id", flat=True)
        )

        actor = _actor()

        results: list[_BulkOpItemResult] = []
        assigned = 0
        already = 0
        failed = 0

        for gid_s in ordered_unique:
            member = members_by_guid.get(gid_s)
            if member is None:
                failed += 1
                results.append(
                    _BulkOpItemResult(
                        id=GUID(gid_s),
                        ok=False,
                        errors=[
                            MutationErrorType(
                                code=ErrorCode.NOT_FOUND.value,
                                message="team member not found",
                                field=None,
                            )
                        ],
                    )
                )
                continue
            if member.user_id is None:
                failed += 1
                results.append(
                    _BulkOpItemResult(
                        id=GUID(gid_s),
                        ok=False,
                        errors=[
                            MutationErrorType(
                                code=ErrorCode.PRECONDITION.value,
                                message="member is not bound to a user",
                                field=None,
                            )
                        ],
                    )
                )
                continue

            if member.user_id in existing_user_ids:
                already += 1
                results.append(
                    _BulkOpItemResult(
                        id=GUID(gid_s),
                        ok=True,
                        already_existed=True,
                        errors=[],
                    )
                )
                continue

            binding = RoleBinding.objects.create(
                user_id=member.user_id,
                role=role,
                scope_kind=RoleBinding.ScopeKind.TEAM,
                scope_id=team.pk,
                granted_by=actor,
            )
            existing_user_ids.add(member.user_id)
            assigned += 1
            results.append(_BulkOpItemResult(id=GUID(gid_s), ok=True, errors=[]))

            emit_audit(
                AuditEntry(
                    actor_user_id=actor.pk if actor else None,
                    organization_id=org_id,
                    action="role_binding.grant",
                    decision="ALLOW",
                    target_kind="role_binding",
                    target_id=str(binding.guid),
                    duration_ms=0,
                    permissions=(Permission.TEAM_MANAGE_MEMBERS.value,),
                    error_code=None,
                    error_message=None,
                    extra={
                        "bulk": True,
                        "user_id": member.user_id,
                        "role_id": role.pk,
                        "team_id": team.pk,
                        "scope_kind": "TEAM",
                    },
                )
            )

        return gql_success(
            _BulkAssignTeamMemberRolesPayload(
                results=results,
                assigned_count=assigned,
                already_assigned_count=already,
                failed_count=failed,
            )
        )

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
        SHA-256 hash. An invitation email is dispatched to ``email``
        with the accept link; email delivery is best-effort and the
        copy-link affordance in the UI is the durable channel — a
        send failure logs but does not fail the mutation.
        """
        from datetime import timedelta

        from django.utils import timezone

        from astrolift_identity.emails import (
            build_invitation_accept_url,
            send_invitation_email,
        )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        email = input.email.strip().lower()
        if not email or "@" not in email:
            return gql_failure(ErrorCode.VALIDATION.value, "valid email required", field="email")

        role = None
        if input.role_slug:
            # Only the caller org's custom roles or a system/null-org role
            # may be attached — a foreign org's custom role slug reads as
            # not-found.
            role = (
                Role.objects.filter(slug=input.role_slug)
                .filter(Q(organization_id=org_id) | Q(organization__isnull=True))
                .first()
            )
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

        # Best-effort delivery. The accept_url_path returned in the
        # payload remains the durable copy-link affordance regardless
        # of whether the email actually goes out.
        org = Organization.objects.filter(pk=org_id).only("name").first()
        org_name = org.name if org is not None else "Astrolift"
        send_invitation_email(
            to_email=email,
            org_name=org_name,
            inviter=_actor(),
            accept_url=build_invitation_accept_url(plaintext),
            expires_at=expires_at,
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

    @strawberry.field
    @mutation_audit(action="invitation.resend")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def resend_invitation(
        self, info: Info, input: ResendInvitationInput
    ) -> MutationResultType[InvitationCreatedType]:
        """Re-send a pending invitation with a freshly-rotated token.

        Only a PENDING invitation in the caller's own org can be
        resent; an accepted / revoked / already-expired-status
        invitation — or a guid owned by another org — reads as
        not-found or precondition and never mutates.

        The plaintext token is never persisted (only its SHA-256 hash
        is), so the original link cannot be re-sent: resend *always*
        mints a fresh token and refreshes ``expires_at`` to a new
        window. That invalidates any previously-issued link for this
        invitation, which is also the desired security property — a
        resend supersedes the old link. The new plaintext + accept URL
        come back in the ``InvitationCreatedType`` payload exactly
        once, preserving the copy-link durable channel exactly as the
        create path does. Email delivery stays best-effort and never
        fails the mutation.
        """
        from datetime import timedelta

        from django.utils import timezone

        from astrolift_identity.emails import (
            build_invitation_accept_url,
            send_invitation_email,
        )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        # Scope the lookup to the caller's own org — a foreign-org guid
        # (or a missing tenant) resolves to None and reads as not-found,
        # so a resend can never reach across tenants.
        inv = Invitation.objects.filter(
            guid=str(input.id),
            scope_kind=Invitation.ScopeKind.ORG,
            scope_id=org_id,
            deleted_at__isnull=True,
        ).first()
        if inv is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "invitation not found")
        if inv.status != Invitation.Status.PENDING:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"invitation is {inv.status}; only a pending invitation can be resent",
            )

        # Rotate the token (the old link dies) and refresh the window so
        # the freshly-sent link is always valid.
        plaintext, digest, _last4 = _make_token_secret()
        expires_at = timezone.now() + timedelta(days=7)
        inv.token_hash = digest
        inv.expires_at = expires_at
        inv.save(update_fields=["token_hash", "expires_at", "updated_at", "version"])

        # Best-effort delivery. The accept_url_path returned in the
        # payload is the durable copy-link affordance regardless of
        # whether the email actually goes out.
        org = Organization.objects.filter(pk=org_id).only("name").first()
        org_name = org.name if org is not None else "Astrolift"
        send_invitation_email(
            to_email=inv.email,
            org_name=org_name,
            inviter=_actor(),
            accept_url=build_invitation_accept_url(plaintext),
            expires_at=expires_at,
        )

        return gql_success(
            InvitationCreatedType(
                invitation=invitation_to_type(inv),
                plaintext_token=plaintext,
                accept_url_path=f"/auth/invitation/{plaintext}",
            )
        )

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

    # ---- Domain allowlist --------------------------------------------

    @strawberry.field
    @mutation_audit(action="organization_allowlist_domain.add")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def add_organization_allowlist_domain(
        self, info: Info, input: AddOrganizationAllowlistDomainInput
    ) -> MutationResultType[OrganizationAllowlistedDomainType]:
        """Allowlist an email domain so SSO users from it auto-join
        this org on first sign-in. Optionally grants ``default_role``
        and/or routes the auto-join through admin review.
        """
        import re

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        domain = (input.domain or "").strip().lower()
        if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "domain must look like 'example.com' — letters, digits, dots, hyphens",
                field="domain",
            )

        role = None
        if input.default_role_slug:
            # Only the caller org's custom roles or a system/null-org role
            # may be the auto-join default — a foreign org's custom role
            # slug reads as not-found.
            role = (
                Role.objects.filter(slug=input.default_role_slug)
                .filter(Q(organization_id=org_id) | Q(organization__isnull=True))
                .first()
            )
            if role is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    "role not found",
                    field="defaultRoleSlug",
                )

        if OrganizationAllowlistedDomain.objects.filter(
            organization_id=org_id,
            domain=domain,
            deleted_at__isnull=True,
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"domain {domain!r} is already on the allowlist",
                field="domain",
            )

        rule = OrganizationAllowlistedDomain.objects.create(
            organization_id=org_id,
            domain=domain,
            default_role=role,
            requires_review=bool(input.requires_review),
        )
        return gql_success(organization_allowlisted_domain_to_type(rule))

    @strawberry.field
    @mutation_audit(action="organization_allowlist_domain.remove")
    @require_permission(Permission.ORG_MANAGE_MEMBERS)
    @tenant_scoped()
    def remove_organization_allowlist_domain(
        self, info: Info, input: RemoveOrganizationAllowlistDomainInput
    ) -> MutationResultType[_SoftDeletePayload]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        rule = OrganizationAllowlistedDomain.objects.filter(
            guid=str(input.id),
            organization_id=org_id,
            deleted_at__isnull=True,
        ).first()
        if rule is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "allowlist entry not found")
        rule.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

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

        from astrolift_identity.api_tokens import (
            deadline_from_days,
            mint_token,
            normalize_scopes,
            validate_scopes,
        )

        normalized = normalize_scopes(input.scopes)
        unknown = validate_scopes(normalized)
        if unknown:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown scope(s): {', '.join(unknown)}",
                field="scopes",
            )

        minted = mint_token()
        token = ApiToken.objects.create(
            user=actor,
            organization=org,
            team=team,
            name=input.name.strip(),
            token_hash=minted.token_hash,
            token_last_4=minted.last4,
            scopes=normalized,
            expires_at=deadline_from_days(input.expires_in_days),
        )
        return gql_success(
            ApiTokenPlaintextType(
                api_token=api_token_to_type(token),
                plaintext=minted.plaintext,
            )
        )

    @strawberry.field
    @mutation_audit(action="api_token.revoke")
    @require_permission(Permission.API_TOKEN_REVOKE)
    @tenant_scoped()
    def revoke_api_token(
        self, info: Info, input: RevokeApiTokenInput
    ) -> MutationResultType[_SoftDeletePayload]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        token = ApiToken.objects.filter(guid=str(input.id), organization_id=org_id).first()
        if token is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "api token not found")
        token.is_revoked = True
        token.save(update_fields=["is_revoked", "updated_at", "version"])
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    # ---- Install enrollment QR (#494) --------------------------------

    @strawberry.field
    @mutation_audit(action="auth.enrollment.generated")
    def generate_install_enrollment_qr(
        self, info: Info, input: GenerateInstallEnrollmentQrInput
    ) -> MutationResultType[EnrollmentQrPayloadType]:
        # Self-service: any authed user can enroll their own mobile
        # device. The operator's web session IS the proof — same
        # pattern as ``update_my_profile``. EXEMPT in
        # ``test_tenancy_guardrail`` for the same reason.
        actor = _actor()
        if actor is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "authentication required")

        # Bind the enrollment to the operator's active tenant when
        # one is set; the redeemed access token inherits the same
        # organization. Tenants without an active org (e.g. a fresh
        # operator who hasn't picked one yet) get a null binding —
        # the resulting API token is unscoped, same as the existing
        # api_token.create path when no org is picked.
        tenant = get_current_tenant()
        org = None
        if tenant and tenant.organization_id:
            org = Organization.objects.filter(pk=tenant.organization_id).first()

        from astrolift_identity import device_flow as df

        result = df.create_enrollment(
            user=actor,
            organization=org,
            label=input.label or "",
            ttl_seconds=input.ttl_seconds,
        )
        if isinstance(result, str):
            if result == "rate_limited":
                return gql_failure(
                    ErrorCode.RATE_LIMITED.value,
                    f"too many active enrollments (max {df.ENROLLMENT_MAX_ACTIVE_PER_USER});"
                    " wait for one to expire or use it first",
                )
            if result == "no_organization":
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    "no active organization — pick one before pairing a mobile device",
                )
            return gql_failure(ErrorCode.PRECONDITION.value, f"enrollment failed: {result}")

        # ---- build the QR payload + render the SVG -------------------
        import base64
        import json

        from django.conf import settings as dj_settings

        from astrolift_identity import _qr

        install_url = (dj_settings.APP_BASE_URL or "").rstrip("/") or "http://localhost:3000"
        install_label = org.name if org else (dj_settings.APP_BASE_URL or "Astrolift")
        payload_dict = {
            "v": 1,
            "install_url": install_url,
            "install_label": install_label,
            "enrollment_token": result.token_plaintext,
            "expires_at": result.expires_at.isoformat(),
            "session_id": result.session_id,
        }
        payload_json = json.dumps(payload_dict, separators=(",", ":"))
        qr_payload = base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("ascii")

        # The QR encodes the deep-link URL (per the issue task
        # context); the encoded JSON travels as a query param so
        # mobile scanners that pop a browser still hand off the
        # full payload. Falls back to plain ``install_url`` for
        # operators typing manually.
        deep_link = f"astrolift://enroll?payload={qr_payload}"
        qr = _qr.encode_text(deep_link, _qr.Ecc.M)
        qr_svg = _qr.to_svg(qr)

        return gql_success(
            EnrollmentQrPayloadType(
                qr_payload=qr_payload,
                qr_svg=qr_svg,
                verification_uri=install_url,
                session_id=result.session_id,
                session_guid=GUID(result.session_guid),
                expires_at=result.expires_at,
            )
        )

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

        # Stamp the creator / updater off the active tenant context so
        # the policies table (#415) can render a Created-by column
        # without a follow-up audit-log join. Both columns are set to
        # the same actor at create time — first edit will rotate
        # ``updated_by`` only.
        actor = _actor()
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
            created_by=actor,
            updated_by=actor,
        )
        return gql_success(policy_to_type(policy))

    @strawberry.field
    @mutation_audit(action="policy.update")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def update_policy(self, info: Info, input: UpdatePolicyInput) -> MutationResultType[PolicyType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        policy = Policy.objects.filter(guid=str(input.id), organization_id=org_id).first()
        if policy is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "policy not found")

        # #497 — optimistic-concurrency gate. Detect concurrent edits
        # before we overwrite a peer admin's policy change.
        mismatch = _check_version_match(policy, if_match_version=input.if_match_version, kind="Policy")
        if mismatch is not None:
            return mismatch

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
        # Rotate the updater so the "last modified by" column on the
        # policies table reflects this edit even if the creator was
        # someone else (#466).
        policy.updated_by = _actor()
        policy.save()
        return gql_success(policy_to_type(policy))

    @strawberry.field
    @mutation_audit(action="policy.delete")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def soft_delete_policy(
        self, info: Info, input: SoftDeleteByGuidInput
    ) -> MutationResultType[_SoftDeletePayload]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        policy = Policy.objects.filter(guid=str(input.id), organization_id=org_id).first()
        if policy is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "policy not found")
        policy.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    # ---- Identity providers ------------------------------------------

    @strawberry.field
    @mutation_audit(action="identity_provider.create")
    @requires_elevation(action_label="identity_provider.create")
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

        from django.db import transaction
        from django.utils import timezone

        actor = _actor()
        is_active = False
        with transaction.atomic():
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

            if input.set_active:
                # Stamp the audit columns atomically with the org
                # binding flip — if either side fails the whole switch
                # rolls back. ``activated_at`` is distinct from
                # ``updated_at`` so subsequent config edits don't
                # tick the FE's "active since" caption forward (#467).
                idp.activated_at = timezone.now()
                idp.last_switched_by = actor
                idp.save(
                    update_fields=[
                        "activated_at",
                        "last_switched_by",
                        "updated_at",
                        "version",
                    ]
                )
                org.identity_provider_id = idp.pk
                org.save(update_fields=["identity_provider", "updated_at", "version"])
                is_active = True

        return gql_success(identity_provider_to_type(idp, is_active=is_active))

    @strawberry.field
    @mutation_audit(action="identity_provider.update")
    @requires_elevation(action_label="identity_provider.update")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def update_identity_provider(
        self, info: Info, input: UpdateIdentityProviderInput
    ) -> MutationResultType[IdentityProviderType]:
        # #1183 SSO-hijack fix: this mutation overwrites client_id /
        # client_secret_ref / discovery URL. Scoped to the caller org so
        # a foreign IdP guid reads as not-found and can't be tampered.
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        idp = (
            IdentityProvider.objects.select_related("organization")
            .filter(guid=str(input.id), organization_id=org_id)
            .first()
        )
        if idp is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "identity provider not found")

        # #497 — optimistic-concurrency gate.
        mismatch = _check_version_match(idp, if_match_version=input.if_match_version, kind="IdentityProvider")
        if mismatch is not None:
            return mismatch

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
    @requires_elevation(action_label="identity_provider.set_active")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def set_active_identity_provider(
        self, info: Info, input: SetActiveIdentityProviderInput
    ) -> MutationResultType[IdentityProviderType]:
        from django.db import transaction
        from django.utils import timezone

        # #1183: setting the active IdP flips the org's login provider.
        # Scoped to the caller org so a foreign IdP guid reads as
        # not-found and can't be used to hijack another org's SSO.
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        idp = (
            IdentityProvider.objects.select_related("organization")
            .filter(guid=str(input.id), organization_id=org_id)
            .first()
        )
        if idp is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "identity provider not found")
        org = idp.organization
        actor = _actor()

        # Stamp the audit fields and the org binding inside one
        # transaction so the "active since {date}" + "by {user}"
        # caption on the settings page (#415) never desyncs from the
        # actual binding. ``activated_at`` is set unconditionally so
        # re-promoting a previously-active IdP rolls the caption
        # forward to the new activation moment, not the original one.
        with transaction.atomic():
            idp.activated_at = timezone.now()
            idp.last_switched_by = actor
            idp.save(
                update_fields=[
                    "activated_at",
                    "last_switched_by",
                    "updated_at",
                    "version",
                ]
            )
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
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        idp = (
            IdentityProvider.objects.select_related("organization")
            .filter(guid=str(input.id), organization_id=org_id)
            .first()
        )
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

        # ── Timezone preference (#775) ──────────────────────────────────
        # Stored in UserPreferences (lazy one-to-one off auth.User).
        # Validated against zoneinfo.available_timezones(); an empty
        # string clears the override so the UI reverts to browser-detected.
        if input.timezone is not None:
            from zoneinfo import available_timezones

            from astrolift_identity.models import UserPreferences

            tz_val = input.timezone.strip()
            if tz_val and tz_val not in available_timezones():
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"{tz_val!r} is not a valid IANA timezone name",
                    field="timezone",
                )
            prefs = UserPreferences.for_user(viewer)
            prefs.timezone = tz_val
            prefs.save(update_fields=["timezone"])
            # ``for_user`` returns a fresh row via get_or_create; keep the
            # reverse one-to-one cache on ``viewer`` in sync so the payload
            # below reflects this write instead of a stale cached row.
            viewer.preferences = prefs

        return gql_success(_my_profile_payload(viewer, org, locked))

    @strawberry.field
    @mutation_audit(action="organization.mark_onboarding_complete")
    @require_permission(Permission.ORG_UPDATE)
    @tenant_scoped()
    def mark_onboarding_complete(
        self, info: Info, input: MarkOnboardingCompleteInput
    ) -> MutationResultType[_MarkOnboardingCompletePayload]:
        """Mark the active organization's first-run wizard as done.

        Idempotent on purpose: the FE may race a manual re-run with
        the auto-open path, and we want both calls to succeed so the
        wizard reliably closes. ``already_completed`` lets the FE
        suppress the "great, you're set up!" toast on the re-entry
        path. ``skip`` only affects the audit row's ``extra`` payload —
        the persisted timestamp is the same.
        """
        from django.utils import timezone

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        already = org.onboarding_completed_at is not None
        if not already:
            org.onboarding_completed_at = timezone.now()
            org.save(update_fields=["onboarding_completed_at", "updated_at", "version"])

        return gql_success(
            _MarkOnboardingCompletePayload(
                organization=organization_to_type(org),
                already_completed=already,
            )
        )

    @strawberry.field
    @mutation_audit(action="session.logout_all")
    def logout_all_sessions(
        self, info: Info, input: LogoutAllSessionsInput
    ) -> MutationResultType[_LogoutAllSessionsPayload]:
        """Revoke every active session bound to the signed-in viewer.

        Soft-revokes the matching ``AstroliftSession`` sidecars AND
        deletes the underlying ``django_session`` rows so the cookies
        fail auth on the next request. Defaults to keeping the
        current session active so the caller doesn't immediately
        bounce to /login.

        Self-only — every authenticated user can sign themselves
        out of their other devices; no permission gate.
        """
        from astrolift_identity.models import AstroliftSession, RevocationReason
        from astrolift_identity.sessions import revoke_session

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        current_key = getattr(getattr(request, "session", None), "session_key", None)
        kept_current = False
        revoked_count = 0
        qs = AstroliftSession.objects.filter(user=viewer)
        for row in qs:
            if input.keep_current and current_key and row.session_key == current_key:
                kept_current = True
                continue
            revoke_session(row=row, actor_user_id=viewer.pk, reason=RevocationReason.LOGOUT)
            revoked_count += 1

        return gql_success(
            _LogoutAllSessionsPayload(
                revoked_count=revoked_count,
                kept_current=bool(kept_current),
            )
        )

    @strawberry.field
    @mutation_audit(
        action="session.revoke",
        target=lambda self, info, input: ("astrolift_session", str(input.session_id)),
    )
    def revoke_astrolift_session(
        self, info: Info, input: RevokeAstroliftSessionInput
    ) -> MutationResultType[_RevokeAstroliftSessionPayload]:
        """Revoke a single AstroliftSession by GUID.

        Permission gate:

        * Caller owns the session → allowed.
        * Caller belongs to the active org AND holds ``ORG_MANAGE_MEMBERS``
          AND the target session belongs to a user in that org → allowed.
        * Otherwise denied.

        Refuses to revoke the caller's *own current* session — that
        path is ``logoutAllSessions`` with ``keep_current=False`` or
        a plain ``/logout``. Revoking your own current request
        would race the response that's holding the cookie.

        Idempotent: revoking an already-revoked row returns
        ``ok: true`` with ``revoked=False``.
        """
        from astrolift_identity.models import AstroliftSession, Member, RevocationReason
        from astrolift_identity.sessions import revoke_session

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        row = AstroliftSession.all_objects.filter(guid=str(input.session_id)).first()
        if row is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "session not found")

        is_owner = row.user_id == viewer.pk
        is_org_admin = False
        if not is_owner:
            tenant = get_current_tenant()
            org_id = tenant.organization_id if tenant else None
            if org_id is not None:
                # The active-org check: caller must be a Member of the
                # active org AND hold ORG_MANAGE_MEMBERS AND the target
                # session must belong to a user in that same org. Member
                # rows are (scope_kind, scope_id) — the ORG-scoped row
                # is the one we want for cross-tenant gating.
                target_in_org = Member.objects.filter(
                    user_id=row.user_id,
                    scope_kind=Member.ScopeKind.ORG.value,
                    scope_id=org_id,
                    deleted_at__isnull=True,
                ).exists()
                caller_in_org = Member.objects.filter(
                    user_id=viewer.pk,
                    scope_kind=Member.ScopeKind.ORG.value,
                    scope_id=org_id,
                    deleted_at__isnull=True,
                ).exists()
                if target_in_org and caller_in_org:
                    from core.permissions import check_permission as _check

                    try:
                        _check(Permission.ORG_MANAGE_MEMBERS)
                        is_org_admin = True
                    except Exception:  # noqa: BLE001 — gate is best-effort
                        is_org_admin = False

        if not (is_owner or is_org_admin):
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "cannot revoke this session")

        if is_owner:
            current_key = getattr(getattr(request, "session", None), "session_key", None)
            if current_key and row.session_key == current_key:
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    "cannot revoke the current session via this mutation; use logout",
                )

        already_revoked = row.revoked_at is not None or row.deleted_at is not None
        reason = (input.reason or "").strip()
        revoke_session(
            row=row,
            actor_user_id=viewer.pk,
            reason=(RevocationReason.ADMIN_REVOKE if is_org_admin else RevocationReason.USER_REVOKE),
        )
        if reason and not already_revoked:
            # Operator-supplied reason — stash it as a free-form note
            # so the audit row can carry it without expanding the
            # vocabulary of RevocationReason tags.
            row.revocation_reason = (row.revocation_reason + ":" + reason[:48])[:64]
            row.save(update_fields=["revocation_reason", "updated_at", "version"])

        return gql_success(
            _RevokeAstroliftSessionPayload(
                id=GUID(str(row.guid)),
                revoked=not already_revoked,
            )
        )

    @strawberry.field
    @mutation_audit(action="session.heartbeat")
    def heartbeat_session(self, info: Info) -> MutationResultType[_HeartbeatSessionPayload]:
        """Refresh the current session's ``last_seen_at`` immediately.

        Bypasses the middleware's per-minute write throttle — the
        client is explicitly asserting liveness, so we honor the
        ping. Used by CLIs on periodic ticks and the mobile app on
        foreground events.

        Self-only — no permission gate. Returns the updated
        ``last_seen_at`` so the caller can confirm the write took.
        """
        from astrolift_identity.models import AstroliftSession
        from astrolift_identity.sessions import record_session, touch_last_seen

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        # record_session() find-or-creates the row; touch_last_seen
        # then bypasses the throttle.
        row = record_session(request)
        if row is None:
            current_key = getattr(getattr(request, "session", None), "session_key", None)
            if current_key:
                row = AstroliftSession.objects.filter(session_key=current_key, user=viewer).first()
        if row is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "no active session to heartbeat")
        touch_last_seen(row)
        return gql_success(
            _HeartbeatSessionPayload(
                id=GUID(str(row.guid)),
                last_seen_at=row.last_seen_at,
            )
        )

    # ---- Step-up auth (#487, spec 27 §4.1) ---------------------------
    #
    # ``elevateAdminSession`` flips a server-side timer on the current
    # session so sensitive mutations (secret writes, role grants,
    # force-redeploys, app deregistration) can run. The decorator
    # ``@requires_elevation`` on those mutations re-checks the timer
    # on every call; lapsed → deny envelope → FE re-prompts.
    #
    # Self-only — every authed user can elevate their own session;
    # no permission gate. The credential verifier (default: password
    # against the Django User row, swappable via
    # ``register_credential_verifier``) is what gates access. A
    # session with no underlying user (token-auth path) cannot
    # elevate and gets the same VALIDATION envelope as wrong creds.

    @strawberry.field
    @mutation_audit(action="auth.elevate_admin")
    def elevate_admin_session(
        self, info: Info, input: ElevateAdminSessionInput
    ) -> MutationResultType[_ElevatePayload]:
        from astrolift_identity.session_elevation import (
            KNOWN_METHODS,
            elevate,
            verify_credential,
        )

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        session = getattr(request, "session", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")
        if session is None:
            # Token-authed callers (API tokens) don't carry a session
            # bag; step-up is browser-session only by design. They
            # surface a typed error so the FE can hide the modal for
            # those callers.
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "step-up auth requires a browser session",
            )

        method = (input.method or "").strip().lower()
        if method not in KNOWN_METHODS:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown method {input.method!r}",
                field="method",
            )
        if not input.credential:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "credential is required",
                field="credential",
            )
        if not verify_credential(viewer, method, input.credential):
            # Audit the failed attempt so security review can spot
            # brute-force probes; bare-bones AuditEntry beats relying
            # on @mutation_audit's success/failure split because we
            # want a distinct ``auth.elevate_admin.failed`` row.
            tenant = get_current_tenant()
            try:
                emit_audit(
                    AuditEntry(
                        actor_user_id=tenant.actor_user_id if tenant else viewer.pk,
                        organization_id=tenant.organization_id if tenant else None,
                        action="auth.elevate_admin.failed",
                        decision="DENY",
                        target_kind="user",
                        target_id=viewer.pk,
                        duration_ms=0,
                        permissions=(),
                        error_code=ErrorCode.PERMISSION_DENIED.value,
                        error_message="invalid credential for step-up",
                        extra={"method": method},
                    )
                )
            except Exception:  # noqa: BLE001
                import logging as _log

                _log.getLogger(__name__).exception("elevate_admin_session: failed-attempt audit emit raised")
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "invalid credential",
                field="credential",
            )

        status = elevate(session, method=method, ttl_seconds=input.ttl_seconds)
        return gql_success(
            _ElevatePayload(
                elevated_until=status.elevated_until,  # type: ignore[arg-type]
                seconds_remaining=status.seconds_remaining,
                method=method,
            )
        )

    @strawberry.field
    @mutation_audit(action="auth.deelevate_admin")
    def deelevate_admin_session(self, info: Info) -> MutationResultType[_DeelevatePayload]:
        from astrolift_identity.session_elevation import deelevate, get_status

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        session = getattr(request, "session", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")
        if session is None:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "step-up auth requires a browser session",
            )

        was_elevated = get_status(session).elevated
        deelevate(session)
        return gql_success(_DeelevatePayload(previously_elevated=was_elevated))

    # ---- Device attestation (#496) ---------------------------------
    #
    # ``requestAttestationChallenge`` issues a server-side nonce the
    # mobile SDK incorporates into its App Attest / Play Integrity
    # blob. ``attestSession`` consumes the nonce + verifies the blob
    # against Apple / Google and persists the outcome on the session
    # sidecar. ``assertSession`` (iOS only) periodically re-checks the
    # attested key + bumps the monotonic counter so a replayed
    # assertion is rejected.
    #
    # All three are self-only — every authed user attests their own
    # sessions; no permission gate. The verifier rejects on its own
    # if the install hasn't configured IOS_APP_ID / ANDROID_PACKAGE_NAME.

    @strawberry.field
    @mutation_audit(action="auth.attestation.challenge_issued")
    def request_attestation_challenge(
        self, info: Info, input: RequestAttestationChallengeInput
    ) -> MutationResultType[_AttestationChallengePayload]:
        from astrolift_identity.attestation import AttestationError
        from astrolift_identity.attestation.service import issue_challenge

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        try:
            row = issue_challenge(user=viewer, kind=input.kind)
        except AttestationError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, exc.message, field="kind")
        return gql_success(
            _AttestationChallengePayload(
                challenge=row.nonce,
                expires_at=row.expires_at,
                kind=row.kind,
            )
        )

    @strawberry.field
    @mutation_audit(action="auth.attestation.submitted")
    def attest_session(self, info: Info, input: AttestSessionInput) -> MutationResultType[_AttestationResult]:
        from astrolift_identity.attestation import AttestationError
        from astrolift_identity.attestation.service import attest_session
        from astrolift_identity.models import AstroliftSession
        from astrolift_identity.sessions import record_session

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        # Bind to the current session sidecar. record_session() find-
        # or-creates the row if the middleware hasn't yet — happens on
        # the first request of a newly issued session.
        row = record_session(request)
        if row is None:
            current_key = getattr(getattr(request, "session", None), "session_key", None)
            if current_key:
                row = AstroliftSession.objects.filter(session_key=current_key, user=viewer).first()
        if row is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "no active session to attest",
            )

        try:
            attest_session(
                session=row,
                kind=input.kind,
                challenge=input.challenge,
                attestation_object=input.attestation_object,
                key_id=input.key_id,
                integrity_token=input.integrity_token,
            )
        except AttestationError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"{exc.reason_code}: {exc.message}",
                field="attestationObject",
            )

        row.refresh_from_db()
        return gql_success(
            _AttestationResult(
                trust_level=row.attestation_trust_level,
                kind=row.attestation_kind,
                attested_at=row.attestation_verified_at,
                reason=None,
            )
        )

    @strawberry.field
    @mutation_audit(action="auth.attestation.asserted")
    def assert_session(self, info: Info, input: AssertSessionInput) -> MutationResultType[_AttestationResult]:
        from astrolift_identity.attestation import AttestationError
        from astrolift_identity.attestation.service import assert_session
        from astrolift_identity.models import AstroliftSession
        from astrolift_identity.sessions import record_session

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        row = record_session(request)
        if row is None:
            current_key = getattr(getattr(request, "session", None), "session_key", None)
            if current_key:
                row = AstroliftSession.objects.filter(session_key=current_key, user=viewer).first()
        if row is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "no active session to assert",
            )

        try:
            assert_session(
                session=row,
                challenge=input.challenge,
                assertion=input.assertion,
            )
        except AttestationError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"{exc.reason_code}: {exc.message}",
                field="assertion",
            )

        row.refresh_from_db()
        return gql_success(
            _AttestationResult(
                trust_level=row.attestation_trust_level,
                kind=row.attestation_kind,
                attested_at=row.attestation_verified_at,
                reason=None,
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


def _resolve_scope_pk_in_org(scope_kind: str, scope_guid: str, org_id: int | None) -> int | None:
    """Map a ``(scope_kind, guid)`` pair to its integer PK, but only when
    the referenced row belongs to ``org_id``.

    A scope owned by another org — or an unsupported scope kind (APP is
    not grantable here, matching the prior behaviour) — resolves to None
    so a caller can't grant a role into a foreign tenant. ORG resolves
    only when the guid *is* the caller's own org.
    """
    if scope_kind == "ORG":
        row = Organization.objects.filter(guid=scope_guid, pk=org_id).first()
    elif scope_kind == "TEAM":
        row = Team.objects.filter(guid=scope_guid, organization_id=org_id).first()
    elif scope_kind == "PROJECT":
        row = Project.objects.filter(guid=scope_guid, organization_id=org_id).first()
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
    from astrolift_identity.models import UserPreferences

    try:
        tz = user.preferences.timezone or None
    except UserPreferences.DoesNotExist:
        tz = None

    return MyProfileType(
        user_id=user.pk,
        username=user.username or "",
        first_name=user.first_name or "",
        last_name=user.last_name or "",
        email=user.email or "",
        locked_fields=list(locked),
        org_allows_edit=org.allow_user_profile_edit,
        timezone=tz,
    )
