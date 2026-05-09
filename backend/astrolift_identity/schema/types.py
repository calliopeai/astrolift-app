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
    """

    user_id: int
    username: str
    first_name: str
    last_name: str
    email: str
    locked_fields: list[str]
    org_allows_edit: bool


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


def member_to_type(member) -> MemberType:
    return MemberType(
        id=GUID(str(member.guid)),
        user=user_to_type(member.user),
        scope_kind=member.scope_kind,
        scope_id=str(member.scope_id),
        is_active=member.is_active,
        lifecycle=member.lifecycle,
        joined_at=member.joined_at,
        last_seen_at=member.last_seen_at,
        created_at=member.created_at,
        deleted_at=member.deleted_at,
    )


def role_binding_to_type(binding) -> RoleBindingType:
    return RoleBindingType(
        id=GUID(str(binding.guid)),
        user=user_to_type(binding.user) if binding.user_id else None,
        group_external_id=binding.group_external_id or "",
        role=role_to_type(binding.role),
        scope_kind=binding.scope_kind,
        scope_id=str(binding.scope_id),
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
        is_revoked=token.is_revoked,
        created_at=token.created_at,
    )


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


def identity_provider_to_type(idp, *, is_active: bool = False) -> IdentityProviderType:
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


def invitation_to_type(inv) -> InvitationType:
    return InvitationType(
        id=GUID(str(inv.guid)),
        email=inv.email,
        scope_kind=inv.scope_kind,
        scope_id=str(inv.scope_id),
        role_slug=inv.role.slug if inv.role_id else None,
        status=inv.status,
        expires_at=inv.expires_at,
        accepted_at=inv.accepted_at,
        invited_by_username=inv.invited_by.username if inv.invited_by_id else None,
        created_at=inv.created_at,
    )


def policy_to_type(policy) -> PolicyType:
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
    )
