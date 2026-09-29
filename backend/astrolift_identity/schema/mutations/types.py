"""Strawberry input and payload types for the mutation package."""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import (
    GUID,
    MutationErrorType,
)
from astrolift_identity.schema.types import (
    OrganizationType,
)


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
    # Per-org override for how far back the historical Logs surface will
    # serve lines. Bounded by the platform's log retention window rather
    # than left to the PositiveIntegerField.
    log_retention_days_default: int | None = None
    # The three siblings of the above (#1602). Each bounded by its own
    # platform window rather than the PositiveIntegerField, for the same
    # reason: above the ceiling the backend would be asked for data the
    # platform never promised to keep.
    metrics_retention_days_default: int | None = None
    metrics_rollup_retention_days_default: int | None = None
    trace_retention_days_default: int | None = None
    allow_user_profile_edit: bool | None = None
    # Operator tags stamped on every resource this org provisions (#1505).
    # A whole-map replace rather than a merge: there has to be a way to
    # remove a tag, and `{}` is the obvious one.
    default_resource_tags: strawberry.scalars.JSON | None = None
    # Per-kind managed-service isolation floor, e.g. {"postgres":
    # "dedicated"}. A whole-map replace for the same reason as the tags
    # above: lifting a floor has to be expressible, and `{}` is how.
    managed_service_isolation_policy: strawberry.scalars.JSON | None = None

    # House theme for the operator UI (#135). Whole-map replace, same reason
    # as the tags above: `{}` has to mean "no house theme".
    appearance_default: strawberry.scalars.JSON | None = None
    appearance_locked: bool | None = None
    # "show" or "hide": settings a person cannot change, for everyone who has
    # not chosen for themselves (#2154).
    restricted_settings_default: str | None = None


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


@strawberry.input
class GrantRoleInput:
    role_id: GUID
    scope_kind: str
    scope_guid: GUID  # the target Org/Team/Project/App guid
    # Exactly one principal: a member of the org by user pk, or an IdP
    # group by its external id (#2157).
    user_id: str | None = None
    group_external_id: str | None = None
    # A time-boxed grant stops granting at this instant (#2157).
    expires_at: dt.datetime | None = None


@strawberry.input
class UpdateRoleBindingInput:
    """Change a binding's role or expiry (#2157). An omitted field is left
    as it is; ``expiresAt: null`` makes the binding permanent."""

    id: GUID
    role_id: GUID | None = strawberry.UNSET
    expires_at: dt.datetime | None = strawberry.UNSET


@strawberry.input
class CreateGroupRoleMappingInput:
    group_external_id: str
    role_id: GUID
    scope_kind: str
    scope_guid: GUID


@strawberry.input
class DeleteGroupRoleMappingInput:
    id: GUID


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
class SetOrganizationModuleInput:
    key: str
    enabled: bool


@strawberry.input
class CreateRoleInput:
    slug: str
    name: str
    scope_level: str
    permissions: list[str]
    description: str = ""
    # The role this one duplicates (#2126 lineage): a system role or one of
    # this org's own. Recorded so the role page can diff against it.
    duplicated_from_id: GUID | None = None


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
class UpdateMyUiPreferencesInput:
    """A partial update of the viewer's UI preferences (#2154).

    A field left out is unchanged; a field sent as null goes back to the
    default (for ``restrictedSettings``, to following the organization).
    Values are checked against the frontend registries and a bad one is
    refused with the allowed set named.
    """

    home_layout: str | None = strawberry.UNSET
    home_layout_asked: bool | None = strawberry.UNSET
    fleet_view: str | None = strawberry.UNSET
    workflow_view: str | None = strawberry.UNSET
    app_view: str | None = strawberry.UNSET
    flow_particles: bool | None = strawberry.UNSET
    motion: str | None = strawberry.UNSET
    restricted_settings: str | None = strawberry.UNSET
    appearance: strawberry.scalars.JSON | None = strawberry.UNSET


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


@strawberry.type(name="AstroliftOrganizationModule")
class _OrganizationModulePayload:
    key: str
    enabled: bool


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
