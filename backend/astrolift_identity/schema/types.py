"""
GraphQL types for the identity / tenant-hierarchy models.

Each Strawberry type maps a model row to its public shape:
``id`` is the GUID (string), never the integer PK; tracking columns
are exposed for clients that build activity feeds; the FK chain is
expressed as nested types.
"""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID


@strawberry.type(name="AstroliftOrganization")
class OrganizationType:
    id: GUID
    slug: str
    name: str
    website: str
    scim_enabled: bool
    audit_log_retention_days: int
    preview_max_active_default: int
    log_retention_days_default: int
    allow_user_profile_edit: bool
    # Non-null once the first-run wizard has been completed or
    # explicitly skipped. The FE opens the onboarding wizard when
    # this is null AND the operator has zero team memberships.
    onboarding_completed_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime
    deleted_at: dt.datetime | None


@strawberry.type(name="AstroliftMyProfile")
class MyProfileType:
    """The signed-in viewer's editable profile.

    ``locked_fields`` lists field names whose values came from the
    IdP at last login. Local edits to a locked field would be
    overwritten on the next sync, so the UI surfaces them
    read-only with a tooltip. ``org_allows_edit`` is the outer
    org-policy gate; when False, the entire form is read-only.

    ``timezone`` is the user's saved IANA timezone override (e.g.
    ``"America/New_York"``). Empty string / None means "no override
    — fall back to the browser-detected zone."
    """

    user_id: int
    username: str
    first_name: str
    last_name: str
    email: str
    locked_fields: list[str]
    org_allows_edit: bool
    timezone: str | None


@strawberry.type(name="AstroliftTeam")
class TeamType:
    id: GUID
    slug: str
    name: str
    organization: OrganizationType
    created_at: dt.datetime
    updated_at: dt.datetime
    deleted_at: dt.datetime | None


@strawberry.type(name="AstroliftProject")
class ProjectType:
    id: GUID
    slug: str
    name: str
    organization: OrganizationType
    team: TeamType
    created_at: dt.datetime
    updated_at: dt.datetime
    deleted_at: dt.datetime | None


def organization_to_type(org) -> OrganizationType:
    return OrganizationType(
        id=GUID(str(org.guid)),
        slug=org.slug,
        name=org.name,
        website=org.website,
        scim_enabled=org.scim_enabled,
        audit_log_retention_days=org.audit_log_retention_days,
        preview_max_active_default=org.preview_max_active_default,
        log_retention_days_default=org.log_retention_days_default,
        allow_user_profile_edit=org.allow_user_profile_edit,
        onboarding_completed_at=org.onboarding_completed_at,
        created_at=org.created_at,
        updated_at=org.updated_at,
        deleted_at=org.deleted_at,
    )


def team_to_type(team) -> TeamType:
    return TeamType(
        id=GUID(str(team.guid)),
        slug=team.slug,
        name=team.name,
        organization=organization_to_type(team.organization),
        created_at=team.created_at,
        updated_at=team.updated_at,
        deleted_at=team.deleted_at,
    )


def project_to_type(project) -> ProjectType:
    return ProjectType(
        id=GUID(str(project.guid)),
        slug=project.slug,
        name=project.name,
        organization=organization_to_type(project.organization),
        team=team_to_type(project.team),
        created_at=project.created_at,
        updated_at=project.updated_at,
        deleted_at=project.deleted_at,
    )


# ---- RBAC types ------------------------------------------------------


@strawberry.type(name="AstroliftUser")
class UserType:
    id: str  # Django auth user pk (int rendered as string)
    username: str
    email: str
    is_active: bool


@strawberry.type(name="AstroliftRole")
class RoleType:
    id: GUID
    slug: str
    name: str
    description: str
    scope_level: str
    permissions: list[str]
    is_system: bool


@strawberry.type(name="AstroliftMember")
class MemberType:
    id: GUID
    user: UserType
    scope_kind: str
    scope_id: str
    is_active: bool
    lifecycle: str
    joined_at: dt.datetime | None
    last_seen_at: dt.datetime | None
    # Most recent recorded activity for this user — derived from the
    # AuditEvent stream (max occurred_at where actor_id == user.pk).
    # Distinct from ``last_seen_at`` which only flips on session login;
    # ``last_active_at`` covers every audited action (mutations, deploy
    # commands, API-token use). Null when the user has no audit record
    # in the current org yet — most often a fresh invite acceptance.
    last_active_at: dt.datetime | None
    created_at: dt.datetime
    deleted_at: dt.datetime | None


@strawberry.type(name="AstroliftRoleBinding")
class RoleBindingType:
    id: GUID
    user: UserType | None
    group_external_id: str
    role: RoleType
    scope_kind: str
    scope_id: str
    # Human-readable description of where this binding was granted —
    # e.g. "team payments", "project frontend/web", "app web-api",
    # or "organization". Resolved server-side so the FE doesn't need
    # to parallel-load teams / projects / apps to render the tooltip.
    source_scope_label: str
    granted_at: dt.datetime
    expires_at: dt.datetime | None
    inherits: bool


def user_to_type(user) -> UserType:
    return UserType(
        id=str(user.pk),
        username=user.get_username(),
        email=user.email or "",
        is_active=user.is_active,
    )


def role_to_type(role) -> RoleType:
    return RoleType(
        id=GUID(str(role.guid)),
        slug=role.slug,
        name=role.name,
        description=role.description or "",
        scope_level=role.scope_level,
        permissions=list(role.permissions or []),
        is_system=role.is_system,
    )


def member_to_type(member, *, last_active_at: dt.datetime | None = None) -> MemberType:
    return MemberType(
        id=GUID(str(member.guid)),
        user=user_to_type(member.user),
        scope_kind=member.scope_kind,
        scope_id=str(member.scope_id),
        is_active=member.is_active,
        lifecycle=member.lifecycle,
        joined_at=member.joined_at,
        last_seen_at=member.last_seen_at,
        last_active_at=last_active_at,
        created_at=member.created_at,
        deleted_at=member.deleted_at,
    )


def role_binding_to_type(binding, *, source_scope_label: str = "") -> RoleBindingType:
    return RoleBindingType(
        id=GUID(str(binding.guid)),
        user=user_to_type(binding.user) if binding.user_id else None,
        group_external_id=binding.group_external_id or "",
        role=role_to_type(binding.role),
        scope_kind=binding.scope_kind,
        scope_id=str(binding.scope_id),
        source_scope_label=source_scope_label,
        granted_at=binding.granted_at,
        expires_at=binding.expires_at,
        inherits=binding.inherits,
    )


@strawberry.type(name="AstroliftApiToken")
class ApiTokenType:
    id: GUID
    name: str
    user: UserType
    team_slug: str | None
    token_last_4: str
    scopes: list[str]
    expires_at: dt.datetime | None
    last_used_at: dt.datetime | None
    last_used_ip: str | None
    last_used_agent: str | None
    is_revoked: bool
    created_at: dt.datetime


@strawberry.type(name="AstroliftApiTokenPlaintext")
class ApiTokenPlaintextType:
    """Returned exactly once on creation — the raw token never lives in the DB."""

    api_token: ApiTokenType
    plaintext: str


def api_token_to_type(token) -> ApiTokenType:
    return ApiTokenType(
        id=GUID(str(token.guid)),
        name=token.name,
        user=user_to_type(token.user),
        team_slug=token.team.slug if token.team_id else None,
        token_last_4=token.token_last_4,
        scopes=list(token.scopes or []),
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
        last_used_ip=token.last_used_ip or None,
        last_used_agent=token.last_used_agent or None,
        is_revoked=token.is_revoked,
        created_at=token.created_at,
    )


@strawberry.type(name="AstroliftEnrollmentQrPayload")
class EnrollmentQrPayloadType:
    """Return shape for ``generateInstallEnrollmentQr`` (#494).

    The mutation hands the operator everything needed to render the
    QR + a fallback URL:

    * ``qr_payload`` — opaque base64-encoded JSON the mobile QR
      scanner decodes. Contains the install URL, label, enrollment
      token, and expiry per the spec.
    * ``qr_svg`` — server-rendered SVG markup. The FE renders this
      directly so we don't need a client-side QR library (which
      would mean a new npm dep on every install). Contains no
      JavaScript; safe to embed via ``dangerouslySetInnerHTML``.
    * ``verification_uri`` — human-readable URL the operator can
      paste on the device as a fallback if the QR is unreadable.
    * ``session_id`` + ``session_guid`` — identifies the row that
      will be flipped to ``consumed`` when the mobile redeems the
      QR, so the FE's listing UI can refetch on a successful pair.
    * ``expires_at`` — when the enrollment token lapses; FE drives
      a countdown + auto-refresh from this.
    """

    qr_payload: str
    qr_svg: str
    verification_uri: str
    session_id: str
    session_guid: GUID
    expires_at: dt.datetime


@strawberry.type(name="AstroliftIdentityProvider")
class IdentityProviderType:
    """One configured identity provider (Auth0 / OIDC / Cognito / local / …).

    The org binds exactly one as primary via Organization.identity_provider_id;
    the ``is_active`` flag mirrors that binding so the UI can render a
    "currently used" indicator without a second query.
    """

    id: GUID
    organization_slug: str
    kind: str
    name: str
    config: strawberry.scalars.JSON
    metadata_url: str
    oidc_discovery_url: str
    client_id: str
    is_default: bool
    is_active: bool
    created_at: dt.datetime
    updated_at: dt.datetime
    # When this IdP was last bound as the org's active provider. Distinct
    # from ``updated_at`` so the FE's "active since" caption doesn't tick
    # forward every time the discovery URL is edited. Null on legacy rows
    # that were active before the field was introduced (#467).
    activated_at: dt.datetime | None
    # Username of the operator who last flipped this IdP to active (#467).
    # Null when the IdP has never been switched (e.g. created with
    # ``set_active=False`` and never promoted) or when the actor user
    # has since been deleted (FK on_delete=SET_NULL).
    last_switched_by_username: str | None
    # Optimistic-concurrency version (#497) — pass back as
    # ``ifMatchVersion`` on ``updateAstroliftIdentityProvider`` to
    # detect concurrent edits.
    version: int


def identity_provider_to_type(idp, *, is_active: bool = False) -> IdentityProviderType:
    switcher = idp.last_switched_by if idp.last_switched_by_id else None
    return IdentityProviderType(
        id=GUID(str(idp.guid)),
        organization_slug=idp.organization.slug,
        kind=idp.kind,
        name=idp.name or idp.kind,
        config=idp.config or {},
        metadata_url=idp.metadata_url or "",
        oidc_discovery_url=idp.oidc_discovery_url or "",
        client_id=idp.client_id or "",
        is_default=idp.is_default,
        is_active=is_active,
        created_at=idp.created_at,
        updated_at=idp.updated_at,
        activated_at=idp.activated_at,
        last_switched_by_username=switcher.get_username() if switcher is not None else None,
        version=int(idp.version or 0),
    )


@strawberry.type(name="AstroliftPolicy")
class PolicyType:
    id: GUID
    slug: str
    name: str
    description: str
    scope_level: str
    scope_id: str | None
    effect: str
    action_pattern: str
    resource_pattern: strawberry.scalars.JSON
    conditions: strawberry.scalars.JSON
    actor_pattern: strawberry.scalars.JSON
    created_at: dt.datetime
    updated_at: dt.datetime
    deleted_at: dt.datetime | None
    # Lifecycle attribution sourced from ``Tracking.created_by`` /
    # ``updated_by`` (#466). Null when the FK is null (legacy rows
    # written before the mutation started stamping the actor) or when
    # the referenced user has since been deleted (FK SET_NULL).
    created_by_username: str | None
    updated_by_username: str | None
    # Optimistic-concurrency version (#497) — pass back as
    # ``ifMatchVersion`` on ``updateAstroliftPolicy`` to detect a
    # concurrent edit.
    version: int


@strawberry.type(name="AstroliftInvitation")
class InvitationType:
    id: GUID
    email: str
    scope_kind: str
    scope_id: str
    role_slug: str | None
    status: str
    expires_at: dt.datetime
    accepted_at: dt.datetime | None
    invited_by_username: str | None
    # Richer inviter projection (#418). The username field is retained
    # for back-compat with the existing FE column; new surfaces should
    # prefer the trio below so the invitation row can render the same
    # avatar + display name shape used everywhere else identities show
    # up (member list, approval picker). ``invited_by_user_id`` stays a
    # plain string mirror of ``UserType.id`` so the FE can deep-link to
    # the inviter's profile without an extra round-trip.
    invited_by_user_id: str | None
    invited_by_display_name: str | None
    invited_by_email: str | None
    invited_by_avatar_url: str | None
    created_at: dt.datetime


@strawberry.type(name="AstroliftInvitationCreated")
class InvitationCreatedType:
    """Returned once at create time. The plaintext token must be shared
    with the recipient through a side channel (email, link); the
    backend stores only the hash, so this is the *only* moment the
    plaintext exists."""

    invitation: InvitationType
    plaintext_token: str
    accept_url_path: str


def invitation_to_type(inv, *, userinfo_by_user_id: dict[int, object] | None = None) -> InvitationType:
    """Map an Invitation row to its GraphQL projection.

    ``userinfo_by_user_id`` is an optional pre-fetched mapping of
    Auth0 ``UserInfo`` rows keyed by ``internal_user_id``. Pass it in
    from a list resolver to avoid N+1; for one-off calls (mutations
    returning a single invitation) it can be omitted and the avatar
    field falls back to empty — the inviter is still rendered by
    name, just without the picture.
    """
    inviter = inv.invited_by if inv.invited_by_id else None
    inviter_userinfo = (
        userinfo_by_user_id.get(inviter.pk)
        if (inviter is not None and userinfo_by_user_id is not None)
        else None
    )
    return InvitationType(
        id=GUID(str(inv.guid)),
        email=inv.email,
        scope_kind=inv.scope_kind,
        scope_id=str(inv.scope_id),
        role_slug=inv.role.slug if inv.role_id else None,
        status=inv.status,
        expires_at=inv.expires_at,
        accepted_at=inv.accepted_at,
        invited_by_username=inviter.username if inviter is not None else None,
        invited_by_user_id=str(inviter.pk) if inviter is not None else None,
        invited_by_display_name=(
            _resolve_display_name(inviter, userinfo=inviter_userinfo) if inviter is not None else None
        ),
        invited_by_email=(inviter.email or "") if inviter is not None else None,
        invited_by_avatar_url=(
            (getattr(inviter_userinfo, "picture", "") or "") if inviter_userinfo is not None else ""
        )
        if inviter is not None
        else None,
        created_at=inv.created_at,
    )


@strawberry.type(name="AstroliftOrganizationAllowlistedDomain")
class OrganizationAllowlistedDomainType:
    """One trusted email domain on an organization's auto-join allowlist."""

    id: GUID
    domain: str
    default_role_slug: str | None
    requires_review: bool
    created_at: dt.datetime
    updated_at: dt.datetime


def organization_allowlisted_domain_to_type(rule) -> OrganizationAllowlistedDomainType:
    return OrganizationAllowlistedDomainType(
        id=GUID(str(rule.guid)),
        domain=rule.domain,
        default_role_slug=rule.default_role.slug if rule.default_role_id else None,
        requires_review=rule.requires_review,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


@strawberry.type(name="AstroliftAppSummary")
class AppSummaryType:
    """A registered app reduced to what the sidebar needs.

    The full ``AstroliftRegisteredApp`` type carries manifest text,
    deploy tokens, retention counts -- payload the nav tree never
    renders. ``AppSummaryType`` is the slim projection so the nav
    query stays cheap on orgs with hundreds of apps.
    """

    id: GUID
    slug: str
    name: str
    status: str
    # True when the app is agent-backed (has a ``kind=agent`` workload). The
    # sidebar renders agents with a bot icon rather than the app rocket.
    is_agent: bool = False


@strawberry.type(name="AstroliftNavTreeProject")
class NavTreeProjectType:
    project: ProjectType
    apps: list[AppSummaryType]


@strawberry.type(name="AstroliftNavTreeTeam")
class NavTreeTeamType:
    team: TeamType
    projects: list[NavTreeProjectType]
    unassigned_apps: list[AppSummaryType]


@strawberry.type(name="AstroliftNavTree")
class NavTreeType:
    organization: OrganizationType
    teams: list[NavTreeTeamType]
    unassigned_apps: list[AppSummaryType]


def app_to_summary(app, *, is_agent: bool = False) -> AppSummaryType:
    return AppSummaryType(
        id=GUID(str(app.guid)),
        slug=app.slug,
        name=app.name,
        status=app.provisioning_status,
        is_agent=is_agent,
    )


@strawberry.type(name="AstroliftActiveSession")
class ActiveSessionType:
    """One AstroliftSession sidecar for the current viewer.

    ``id`` is the GUID of the AstroliftSession row (NOT the
    underlying django_session key — that never leaves the cookie
    jar). Operators pass this id to ``revokeAstroliftSession`` to
    drop a single device.

    ``client_kind`` is one of the lowercase ``ClientKind`` values
    (``web``, ``cli``, ``mobile``, ``browser_extension``,
    ``api_token``). FE narrows it into a union.

    ``last_seen_at`` is the heartbeat timestamp the
    SessionTrackingMiddleware stamps on every authed request
    (rate-limited to once per minute) plus explicit ``heartbeatSession``
    pings — drives the "stale CLI" hint on the operator-facing list.
    v1 uses the Django default session store, so ``created_at`` /
    ``last_seen_at`` / ``ip_address`` / ``user_agent`` are null —
    the django_session table doesn't track them. They become
    populated once a ``SessionMetadata`` model + middleware lands.

    #487 adds ``elevated_until`` so the FE can render the
    "Admin elevated for N more minutes" nav indicator without a
    second round-trip.
    """

    id: str
    expires_at: dt.datetime | None
    is_current: bool
    client_kind: str
    label: str
    created_at: dt.datetime | None
    last_seen_at: dt.datetime | None
    ip_address: str | None
    user_agent: str | None
    elevated_until: dt.datetime | None = None
    elevation_method: str | None = None
    # #496 — device attestation state. ``attestation_kind`` is one of
    # ``none | ios_appattest | android_play_integrity``;
    # ``attestation_trust_level`` is one of
    # ``not_attested | genuine | unknown | failed``. ``attested_at``
    # is the last successful verification timestamp.
    attestation_kind: str = "none"
    attestation_trust_level: str = "not_attested"
    attested_at: dt.datetime | None = None


@strawberry.type(name="AstroliftElevationStatus")
class ElevationStatusType:
    """Snapshot of the current session's step-up elevation (#487).

    ``required_for`` enumerates the gql operation names that the
    backend will gate behind a fresh elevation — the FE uses it to
    pre-prompt instead of waiting for the first STEP_UP_REQUIRED
    envelope after the user already clicked Save.
    """

    elevated: bool
    elevated_until: dt.datetime | None
    seconds_remaining: int
    method: str | None
    required_for: list[str]


def active_session_to_type(row, *, is_current: bool) -> ActiveSessionType:
    """Adapt an ``AstroliftSession`` row to the GraphQL type."""
    return ActiveSessionType(
        id=str(row.guid),
        expires_at=row.expires_at,
        is_current=is_current,
        client_kind=row.client_kind,
        label=row.label or "",
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        ip_address=str(row.last_seen_ip) if row.last_seen_ip else None,
        user_agent=row.last_seen_agent or None,
        attestation_kind=row.attestation_kind,
        attestation_trust_level=row.attestation_trust_level,
        attested_at=row.attestation_verified_at,
    )


def policy_to_type(policy) -> PolicyType:
    creator = policy.created_by if policy.created_by_id else None
    updater = policy.updated_by if policy.updated_by_id else None
    return PolicyType(
        id=GUID(str(policy.guid)),
        slug=policy.slug,
        name=policy.name,
        description=policy.description or "",
        scope_level=policy.scope_level,
        scope_id=str(policy.scope_id) if policy.scope_id else None,
        effect=policy.effect,
        action_pattern=policy.action_pattern,
        resource_pattern=policy.resource_pattern or {},
        conditions=policy.conditions or [],
        actor_pattern=policy.actor_pattern or {},
        created_at=policy.created_at,
        updated_at=policy.updated_at,
        deleted_at=policy.deleted_at,
        created_by_username=creator.get_username() if creator is not None else None,
        updated_by_username=updater.get_username() if updater is not None else None,
        version=int(policy.version or 0),
    )


# ---- Approver picker types (#410) --------------------------------------
#
# A shape stable enough to drive an approver multi-select on the
# register-app wizard *and* any later surface that needs to render a
# user identity (settings approval policy, env approval policy, etc.).
# Distinct from ``UserType`` because the picker shows a display name and
# avatar URL that ``UserType`` doesn't carry — the existing UserType is
# used in admin views where the auth username is enough.


@strawberry.type(name="AstroliftApproverUser")
class ApproverUserType:
    id: str  # Django auth user pk rendered as string (matches UserType convention)
    email: str
    display_name: str  # Best-available human label: full name / nickname / username / email-local
    avatar_url: str  # Best-available avatar URL (from Auth0 UserInfo.picture); empty when none


def _resolve_display_name(user, *, userinfo=None) -> str:
    """Best-effort human label for an approver picker entry.

    Priority: full name (first + last) → nickname → username → email-local.
    Falls back to an empty string when the user has no usable identity
    fields at all (shouldn't happen for active members, but the picker
    rendering needs to be defensive).
    """
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    full = f"{first} {last}".strip()
    if full:
        return full
    if userinfo is not None:
        for attr in ("name", "nickname"):
            value = (getattr(userinfo, attr, "") or "").strip()
            if value:
                return value
    username = (user.get_username() or "").strip()
    if username and "@" not in username:
        return username
    email = (getattr(user, "email", "") or "").strip()
    if email:
        local = email.split("@", 1)[0]
        if local:
            return local
    return username or email or ""


def approver_user_to_type(user, *, userinfo=None) -> ApproverUserType:
    return ApproverUserType(
        id=str(user.pk),
        email=(getattr(user, "email", "") or ""),
        display_name=_resolve_display_name(user, userinfo=userinfo),
        avatar_url=((getattr(userinfo, "picture", "") or "") if userinfo is not None else ""),
    )


# ---- Invite-flow polish types (#418) -----------------------------------
#
# Search rows the InviteDialog uses to detect a duplicate before letting
# the operator dispatch the create_invitation mutation. The two match
# kinds share one type because the UI needs to render them together in
# one combobox (with a small badge differentiating); a single resolver
# returning one shape keeps the FE rendering simple.


@strawberry.type(name="AstroliftSearchableUser")
class SearchableUserType:
    """One row in the invite-dialog's de-dupe search (#418).

    ``match_kind`` is ``MEMBER`` for an existing active org member or
    ``INVITATION`` for a pending invitation already issued at the org
    scope. The two cases share this shape so the FE renders them in a
    single combobox. ``user_id`` is populated only for MEMBER rows
    (invitations don't yet have a user account). ``invitation_id`` /
    ``expires_at`` / ``status`` are populated only for INVITATION rows;
    the FE narrows by ``match_kind`` to pick which CTA to show
    ('Grant role to this user' vs. 'Resend / Cancel and re-invite').
    """

    match_kind: str  # "MEMBER" | "INVITATION"
    email: str
    display_label: str
    avatar_url: str
    # Member-only
    user_id: str | None
    # Invitation-only
    invitation_id: GUID | None
    invitation_status: str | None
    expires_at: dt.datetime | None
