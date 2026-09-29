"""GraphQL types for the access UX queries (#2126, after #2157).

The principal search, the grant preview, the policy simulation and the
condition catalog. The resolvers live on ``IdentityQuery``; the work is in
``astrolift_identity.principals``, ``grant_preview`` and
``policy_simulation``, and the catalog is read from ``abac`` itself.
"""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID
from astrolift_identity.schema.types import UserType

# ---------------------------------------------------------------------
# Principal search
# ---------------------------------------------------------------------


@strawberry.input(name="AstroliftPrincipalSearchFilter")
class PrincipalSearchFilterInput:
    kind: list[str] | None = strawberry.field(
        default=None, description="USER, GROUP, TEAM, INVITATION. Unset searches every kind."
    )


@strawberry.type(name="AstroliftPrincipal")
class PrincipalType:
    """A user, an IdP group, a team or a pending invitation of the org."""

    kind: str = strawberry.field(description="USER, GROUP, TEAM or INVITATION.")
    key: str = strawberry.field(
        description=(
            "A stable id for the row: user:<userId>, group:<externalId>, team:<teamId> or "
            "invitation:<invitationId>."
        )
    )
    name: str = strawberry.field(description="What to show: display name, group id, team or email.")
    secondary: str = strawberry.field(description="Email for a user, slug for a team, else empty.")
    user: UserType | None = None
    user_id: str | None = strawberry.field(default=None, description="The value grantRole takes as userId.")
    member_id: GUID | None = strawberry.field(default=None, description="The user's ORG membership row.")
    lifecycle: str | None = None
    avatar_url: str | None = None
    group_external_id: str | None = None
    member_count: int | None = strawberry.field(
        default=None, description="Members of a group (active, carrying it) or of a team."
    )
    bindings_count: int | None = strawberry.field(default=None, description="A group's role bindings here.")
    mappings_count: int | None = strawberry.field(default=None, description="A group's role mappings here.")
    team_id: GUID | None = None
    team_slug: str | None = None
    invitation_id: GUID | None = None
    invitation_status: str | None = None
    expires_at: dt.datetime | None = None


@strawberry.type(name="AstroliftPrincipalKindCount")
class PrincipalKindCountType:
    kind: str
    count: int


@strawberry.type(name="AstroliftPrincipalPage")
class PrincipalPageType:
    """A numbered page of principals, with the match count of each kind."""

    items: list[PrincipalType]
    total_count: int
    page: int
    page_size: int
    next_cursor: str | None = None
    counts: list[PrincipalKindCountType] = strawberry.field(
        default_factory=list, description="Matches per kind searched, for the view tabs."
    )


# ---------------------------------------------------------------------
# Grant preview
# ---------------------------------------------------------------------


@strawberry.input(name="AstroliftPrincipalRef")
class PrincipalRefInput:
    kind: str = strawberry.field(description="USER, GROUP or TEAM.")
    id: str = strawberry.field(
        description="USER: the user id grantRole takes. GROUP: the external id. TEAM: the team id."
    )


@strawberry.input(name="AstroliftGrantPreviewInput")
class GrantPreviewInput:
    """A draft grant, change or removal. Nothing is written.

    GRANT takes ``principals``, ``roleId``, ``scopeKind`` and ``scopeId``
    (and ``expiresAt``); CHANGE takes ``bindingId`` and ``roleId`` and/or
    ``expiresAt``; REMOVE takes ``bindingId``.
    """

    action: str = strawberry.field(description="GRANT, CHANGE or REMOVE.")
    principals: list[PrincipalRefInput] | None = None
    role_id: GUID | None = None
    scope_kind: str | None = strawberry.field(default=None, description="ORG, TEAM, PROJECT, APP or AGENT.")
    scope_id: str | None = strawberry.field(default=None, description="The scope's id.")
    binding_id: GUID | None = strawberry.field(
        default=None, description="The RoleBinding to change or remove."
    )
    expires_at: dt.datetime | None = strawberry.UNSET


@strawberry.type(name="AstroliftGrantPreviewSource")
class GrantPreviewSourceType:
    """One grant a person holds that carries some of the permissions in play."""

    source: str = strawberry.field(
        description="USER_BINDING, GROUP_BINDING, GROUP_MAPPING, TEAM_SHARE, or SUPERUSER."
    )
    binding_id: GUID | None
    role_slug: str | None
    role_name: str | None
    scope_kind: str | None
    scope_guid: GUID | None
    source_scope_label: str
    group_external_id: str | None
    team_slug: str | None
    inherited: bool = strawberry.field(description="Held on an ancestor of the scope, or through a share.")
    permissions: list[str] = strawberry.field(description="The permissions in play it carries.")


@strawberry.type(name="AstroliftGrantPreviewPerson")
class GrantPreviewPersonType:
    user: UserType
    member_id: GUID | None
    through: list[str] = strawberry.field(
        description='How the change reaches them: "direct", "group <id>" or "team <slug>".'
    )
    gained: list[str] = strawberry.field(description="Permissions in play they would get.")
    lost: list[str] = strawberry.field(description="Permissions in play they would no longer hold.")
    kept: list[str] = strawberry.field(description="Permissions in play they hold through another grant.")
    via: list[GrantPreviewSourceType] = strawberry.field(
        description="Their other grants that carry permissions in play, excluding the one being changed."
    )


@strawberry.type(name="AstroliftGrantPreviewGroup")
class GrantPreviewGroupType:
    group_external_id: str
    member_count: int


@strawberry.type(name="AstroliftGrantPreview")
class GrantPreviewType:
    """Who a draft grant, change or removal affects, read-only (#2126).

    Computed from the same grants the permission resolver reads, at the
    draft's scope, now. A person who both gains and loses (a role change)
    is in both lists. People lists are capped at ``limit``; the counts are
    exact.
    """

    ok: bool
    errors: list[str]
    action: str
    permissions: list[str] = strawberry.field(description="The permissions in play.")
    scope_kind: str | None
    scope_guid: GUID | None
    source_scope_label: str
    summary: str
    gaining_count: int
    losing_count: int
    unchanged_count: int
    gaining: list[GrantPreviewPersonType]
    losing: list[GrantPreviewPersonType]
    unchanged: list[GrantPreviewPersonType]
    groups: list[GrantPreviewGroupType]
    allowed: bool = strawberry.field(description="Whether the caller's grant ceiling allows it.")
    refusal: str | None
    notes: list[str]


# ---------------------------------------------------------------------
# Policy simulation
# ---------------------------------------------------------------------


@strawberry.input(name="AstroliftPolicyDraftInput")
class PolicyDraftInput:
    """A policy as the editor holds it, before saving. Same fields as createPolicy."""

    effect: str = "DENY"
    action_pattern: str = "*"
    scope_level: str = "ORG"
    scope_id: str | None = strawberry.field(default=None, description="The scope's id, or its numeric id.")
    resource_pattern: strawberry.scalars.JSON | None = None
    conditions: strawberry.scalars.JSON | None = None
    actor_pattern: strawberry.scalars.JSON | None = None


@strawberry.type(name="AstroliftPolicySimulationHolder")
class PolicySimulationHolderType:
    """A current holder of an action the draft touches, and what it would do."""

    user: UserType
    member_id: GUID | None
    outcome: str = strawberry.field(description="DENIED, UNKNOWN (fail-closed guess) or NOT_DENIED.")
    denied: list[str] = strawberry.field(description="Actions it would definitely deny them.")
    unknown: list[str] = strawberry.field(
        description="Actions it would deny for want of an attribute a simulation cannot know."
    )
    allowed: list[str] = strawberry.field(description="Actions it would not deny.")
    scope_kind: str
    scope_guid: GUID | None
    source_scope_label: str
    detail: str


@strawberry.type(name="AstroliftPolicySimulationDecision")
class PolicySimulationDecisionType:
    """A recorded, allowed decision the draft would have changed."""

    id: GUID
    occurred_at: dt.datetime
    action: str
    actor_id: str
    actor_display: str
    outcome: str = strawberry.field(description="DENIED or UNKNOWN.")
    permissions: list[str]
    detail: str


@strawberry.type(name="AstroliftPolicySimulation")
class PolicySimulationType:
    """What a draft policy would deny (#2126), read-only.

    ``sources`` says what was evaluated: HOLDERS (the org's current holders
    of the actions the draft touches, now) and AUDIT (recorded decisions in
    the window). Lists are capped at ``limit``; the counts are exact.
    """

    ok: bool
    errors: list[str]
    sources: list[str]
    actions: list[str] = strawberry.field(description="Catalog permissions the action pattern matches.")
    holders_count: int
    holders_denied_count: int
    holders_unknown_count: int
    holders: list[PolicySimulationHolderType]
    audit_recorded: bool = strawberry.field(
        description="Whether the org has recorded decisions in the window."
    )
    window_days: int
    decisions_evaluated: int
    decisions_denied_count: int
    decisions_unknown_count: int
    decisions: list[PolicySimulationDecisionType]
    notes: list[str]


# ---------------------------------------------------------------------
# Condition catalog
# ---------------------------------------------------------------------


@strawberry.type(name="AstroliftPolicyConditionField")
class ConditionFieldType:
    name: str
    type: str = strawberry.field(description="weekdays, time_ranges, time_zone, cidrs, strings or integer.")
    label: str
    description: str
    required: bool
    default: strawberry.scalars.JSON | None
    options: list[str]
    minimum: int | None


@strawberry.type(name="AstroliftPolicyConditionKind")
class ConditionKindType:
    kind: str
    label: str
    description: str
    needs: str = strawberry.field(
        description=(
            "What a check must carry to evaluate it: clock, client_ip, session (the request's own "
            "user only) or operation (supplied by the call site)."
        )
    )
    fields: list[ConditionFieldType]
    example: strawberry.scalars.JSON


@strawberry.type(name="AstroliftPolicyPatternKey")
class PatternKeyType:
    key: str
    label: str
    description: str


@strawberry.type(name="AstroliftPolicyConditionCatalog")
class ConditionCatalogType:
    """What the ABAC evaluator understands, read from the evaluator itself."""

    conditions: list[ConditionKindType]
    resource_keys: list[PatternKeyType]
    actor_keys: list[PatternKeyType]
    effects: list[str]
    scope_levels: list[str]


def condition_catalog() -> ConditionCatalogType:
    from astrolift_identity import abac
    from astrolift_identity.models import Policy

    return ConditionCatalogType(
        conditions=[
            ConditionKindType(
                kind=k.kind,
                label=k.label,
                description=k.description,
                needs=k.needs,
                fields=[
                    ConditionFieldType(
                        name=f.name,
                        type=f.type,
                        label=f.label,
                        description=f.description,
                        required=f.required,
                        default=f.default,
                        options=list(f.options),
                        minimum=f.minimum,
                    )
                    for f in k.fields
                ],
                example=dict(k.example),
            )
            for k in abac.CONDITION_KINDS
        ],
        resource_keys=[
            PatternKeyType(key=k.key, label=k.label, description=k.description) for k in abac.RESOURCE_KEYS
        ],
        actor_keys=[
            PatternKeyType(key=k.key, label=k.label, description=k.description) for k in abac.ACTOR_KEYS
        ],
        effects=[e.value for e in Policy.Effect],
        scope_levels=[s.value for s in Policy.ScopeLevel],
    )
