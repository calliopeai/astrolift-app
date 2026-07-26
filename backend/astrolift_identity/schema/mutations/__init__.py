"""IdentityMutation — assembled from per-feature mixin modules."""

from __future__ import annotations

import strawberry

from astrolift_identity.schema.mutations.allowlist import AllowlistMutations
from astrolift_identity.schema.mutations.helpers import (  # noqa: F401
    SoftDeleteOrganizationInput,
    _actor,
    _idp_locked_fields,
    _make_token_secret,
    _my_profile_payload,
    _resolve_org,
    _resolve_project,
    _resolve_scope_pk_in_org,
    _resolve_team,
    _validate_idp_config,
)
from astrolift_identity.schema.mutations.identity_providers import IdentityProviderMutations
from astrolift_identity.schema.mutations.invitations import InvitationMutations
from astrolift_identity.schema.mutations.organizations import OrganizationMutations
from astrolift_identity.schema.mutations.policies import PolicyMutations
from astrolift_identity.schema.mutations.profile import ProfileMutations
from astrolift_identity.schema.mutations.projects import ProjectMutations
from astrolift_identity.schema.mutations.role_bindings import RoleBindingMutations
from astrolift_identity.schema.mutations.roles import RoleMutations
from astrolift_identity.schema.mutations.sessions import SessionMutations
from astrolift_identity.schema.mutations.step_up import StepUpMutations
from astrolift_identity.schema.mutations.teams import TeamMutations
from astrolift_identity.schema.mutations.tokens import ApiTokenMutations

# Re-exported for the public import surface (tests / cross-app importers).
from astrolift_identity.schema.mutations.types import (  # noqa: F401
    AcceptInvitationInput,
    AddOrganizationAllowlistDomainInput,
    AssertSessionInput,
    AttestSessionInput,
    BulkAssignTeamMemberRolesInput,
    BulkRevokeRoleBindingsInput,
    CreateApiTokenInput,
    CreateIdentityProviderInput,
    CreateInvitationInput,
    CreateOrganizationInput,
    CreatePolicyInput,
    CreateProjectInput,
    CreateRoleInput,
    CreateTeamInput,
    DeleteRoleInput,
    ElevateAdminSessionInput,
    GenerateInstallEnrollmentQrInput,
    GrantRoleInput,
    LogoutAllSessionsInput,
    MarkOnboardingCompleteInput,
    RemoveOrganizationAllowlistDomainInput,
    RequestAttestationChallengeInput,
    ResendInvitationInput,
    RevokeApiTokenInput,
    RevokeAstroliftSessionInput,
    RevokeInvitationInput,
    RevokeRoleBindingInput,
    SetActiveIdentityProviderInput,
    SoftDeleteByGuidInput,
    UpdateIdentityProviderInput,
    UpdateMyProfileInput,
    UpdateOrganizationInput,
    UpdatePolicyInput,
    UpdateProjectInput,
    UpdateRoleInput,
    UpdateTeamInput,
    _AttestationChallengePayload,
    _AttestationResult,
    _BulkAssignTeamMemberRolesPayload,
    _BulkOpItemResult,
    _BulkRevokeRoleBindingsPayload,
    _DeelevatePayload,
    _ElevatePayload,
    _HeartbeatSessionPayload,
    _LogoutAllSessionsPayload,
    _MarkOnboardingCompletePayload,
    _RevokeAstroliftSessionPayload,
    _SoftDeletePayload,
)


@strawberry.type
class IdentityMutation(
    OrganizationMutations,
    TeamMutations,
    ProjectMutations,
    RoleBindingMutations,
    RoleMutations,
    InvitationMutations,
    AllowlistMutations,
    ApiTokenMutations,
    PolicyMutations,
    IdentityProviderMutations,
    ProfileMutations,
    SessionMutations,
    StepUpMutations,
):
    """Root mutation type — inherits fields from each domain mixin."""
