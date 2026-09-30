"""The list contract on the identity lists (spec 44 §5.1, #2153).

Members, invitations, teams, roles, role bindings and policies take the
contract's ``filter``, ``sort`` and ``page`` / ``pageSize`` beside their
existing ``search`` / ``limit`` / ``after``. What the People, Teams, Roles,
Assignments and Policies screens used to work out in the browser over a
walked list (Mine, Admins, the role and team chips, holders of a role, the
column sorts, numbered pages) is a column, an annotation or a subquery here,
so OFFSET and ``totalCount`` are exact. See ``astrolift_graphql/README.md``.

Every filter and sort that reaches another table is confined to the caller's
org: bindings through ``_org_scope_q``, teams through ``organization_id``,
audit events through ``organization_id``. A system role is shared by every
org, so its ``bindingsCount`` counts this org's bindings only.

"Me" is the viewer, ``TenantContext.actor_user_id``. With no viewer a "me"
filter matches nothing, never everything.
"""

from __future__ import annotations

import datetime as dt
import uuid

import strawberry
from django.db import models
from django.db.models import Count, Q, Subquery
from django.db.models.functions import Cast, Coalesce, Lower
from django.utils import timezone

from astrolift_graphql import FilterField, SortKey, filter_q, filter_values
from astrolift_identity.models import Member, Role, RoleBinding, Team
from core.permissions import Permission

# ---------------------------------------------------------------------------
# Filter inputs
# ---------------------------------------------------------------------------


@strawberry.input(name="AstroliftMembersListFilter")
class MembersListFilterInput:
    """The People list's declared filters. Unset fields do not filter; set
    fields combine with AND, and the values of one list field with OR."""

    scope_kind: list[str] | None = strawberry.field(
        default=None,
        description="The row's own scope: ORG, TEAM, PROJECT, APP. ORG gives one row per person.",
    )
    lifecycle: list[str] | None = strawberry.field(
        default=None, description="active, pending_invite, pending_first_login, suspended, deactivated."
    )
    role: list[str] | None = strawberry.field(
        default=None, description="Role slugs the person holds on any scope of the organization."
    )
    team: list[str] | None = strawberry.field(default=None, description="Team slugs or ids the person is on.")
    mine: bool | None = strawberry.field(
        default=None, description="true: people who share a team with the viewer. false: everyone else."
    )
    admin: bool | None = strawberry.field(
        default=None,
        description="true: people holding, at organization scope, a role that can manage members.",
    )
    active: str | None = strawberry.field(
        default=None,
        description="Last active: 7d, 30d, 90d (within), stale (not in 90 days) or never.",
    )


@strawberry.input(name="AstroliftInvitationsListFilter")
class InvitationsListFilterInput:
    status: list[str] | None = strawberry.field(
        default=None, description="pending, accepted, expired, revoked."
    )
    role: list[str] | None = strawberry.field(
        default=None, description="Slugs of the role the invitation grants."
    )
    invited_by: list[str] | None = strawberry.field(
        default=None, description='Usernames of who sent it; "me" is the viewer.'
    )


@strawberry.input(name="AstroliftTeamsListFilter")
class TeamsListFilterInput:
    mine: bool | None = strawberry.field(
        default=None, description="true: teams the viewer is on. false: teams the viewer is not on."
    )


@strawberry.input(name="AstroliftRolesListFilter")
class RolesListFilterInput:
    is_system: bool | None = strawberry.field(
        default=None,
        description="true: the platform's system roles. false: this organization's custom roles.",
    )
    scope_level: list[str] | None = strawberry.field(default=None, description="ORG, TEAM, PROJECT, APP.")
    created_by: list[str] | None = strawberry.field(
        default=None, description='Usernames of who created the role; "me" is the viewer.'
    )


@strawberry.input(name="AstroliftRoleBindingsListFilter")
class RoleBindingsListFilterInput:
    role: list[str] | None = strawberry.field(default=None, description="Role slugs.")
    scope_kind: list[str] | None = strawberry.field(default=None, description="ORG, TEAM, PROJECT, APP.")
    kind: list[str] | None = strawberry.field(default=None, description="user or group.")
    holder: list[str] | None = strawberry.field(
        default=None,
        description=(
            'Who holds the binding: a username, "group:<external id>", or "me": the viewer\'s own '
            "bindings and those on the IdP groups the viewer is in."
        ),
    )
    subject: list[str] | None = strawberry.field(
        default=None, description='Alias of holder, including "me" and "group:<external id>".'
    )


@strawberry.input(name="AstroliftPoliciesListFilter")
class PoliciesListFilterInput:
    effect: list[str] | None = strawberry.field(default=None, description="ALLOW or DENY.")
    scope_level: list[str] | None = strawberry.field(default=None, description="ORG, TEAM, PROJECT, APP.")
    created_by: list[str] | None = strawberry.field(
        default=None, description='Usernames of who created the policy; "me" is the viewer.'
    )


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------


def wants_numbered(*, sort: str | None, page: int | None, page_size: int | None) -> bool:
    """Any of ``sort``, ``page`` or ``pageSize`` selects numbered paging,
    as on the Apps and Clusters lists; otherwise the cursor walk runs."""
    return sort is not None or page is not None or page_size is not None


def _users_q(path: str):
    """Match a user FK by the viewer's pk (what "me" becomes) or by username."""

    def q(values: list) -> Q:
        ids = [v for v in values if isinstance(v, int)]
        names = [v for v in values if isinstance(v, str)]
        out = Q(pk__in=[])
        if ids:
            out |= Q(**{f"{path}_id__in": ids})
        if names:
            out |= Q(**{f"{path}__username__in": names})
        return out

    return q


def _tri(q: Q):
    """A bool filter: true keeps the matches, false keeps the rest."""
    return lambda value: q if value else ~q


def _org_team_pks(org_id: int | None) -> list[int]:
    if org_id is None:
        return []
    return list(Team.objects.filter(organization_id=org_id).values_list("pk", flat=True))


def viewer_team_pks(org_id: int | None, viewer_id: int | None) -> list[int]:
    """The live teams of ``org_id`` the viewer is a member of."""
    if org_id is None or viewer_id is None:
        return []
    return list(
        Member.objects.filter(
            user_id=viewer_id,
            scope_kind=Member.ScopeKind.TEAM,
            scope_id__in=_org_team_pks(org_id),
        ).values_list("scope_id", flat=True)
    )


def _team_pks_named(org_id: int | None, values: list[str]) -> list[int]:
    """Team pks in ``org_id`` for slugs or guids; unknown values match nothing."""
    if org_id is None:
        return []
    guids = []
    for value in values:
        try:
            guids.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    match = Q(slug__in=values)
    if guids:
        match |= Q(guid__in=guids)
    return list(Team.objects.filter(match, organization_id=org_id).values_list("pk", flat=True))


def _org_bindings(org_id: int | None):
    """Live role bindings on any scope of ``org_id``; none without an org."""
    from astrolift_identity.schema.queries import _org_scope_q

    if org_id is None:
        return RoleBinding.objects.none()
    return RoleBinding.objects.filter(_org_scope_q(org_id, coherent=True))


def _last_active_expr(org_id: int | None, user_field: str = "user_id"):
    """The newest audited action by the row's user in ``org_id``, as a subquery.

    Same source as ``_last_active_by_user_id``; ``actor_id`` is a string
    column shared with service actors, so the user pk is cast to compare.
    """
    from django.db.models import OuterRef

    from astrolift_operations.models.audit_event import AuditEvent

    events = AuditEvent.objects.filter(
        organization_id=org_id,
        actor_kind="user",
        actor_id=Cast(OuterRef(user_field), output_field=models.CharField()),
    ).order_by("-occurred_at")
    return Subquery(events.values("occurred_at")[:1], output_field=models.DateTimeField())


def _count_expr(qs, field: str):
    """A correlated COUNT over ``qs`` grouped on ``field``, 0 when none."""
    counted = qs.order_by().values(field).annotate(_n=Count("pk")).values("_n")
    return Coalesce(Subquery(counted[:1], output_field=models.IntegerField()), 0)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

MEMBERS_DEFAULT_SORT = "name"

_ACTIVE_WINDOWS = {"7d": 7, "30d": 30, "90d": 90}


def members_sort_keys(org_id: int | None) -> dict[str, SortKey]:
    from django.db.models import OuterRef

    return {
        "name": SortKey(Lower("user__username")),
        "email": SortKey(Lower("user__email")),
        "created": SortKey("created_at"),
        "joined": SortKey(Coalesce("joined_at", "created_at")),
        "lifecycle": SortKey("lifecycle"),
        # Never active sorts below the oldest activity, as it did in the browser.
        "lastActive": SortKey(_last_active_expr(org_id), nulls_low=True),
        "roles": SortKey(_count_expr(_org_bindings(org_id).filter(user_id=OuterRef("user_id")), "user_id")),
    }


def filter_members(qs, filter_input, *, org_id: int | None, viewer_id: int | None):
    """Apply the People list's filters to an org-scoped Member queryset."""
    values = filter_values(filter_input)
    bindings = _org_bindings(org_id).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()),
        Q(role__organization_id=org_id) | Q(role__organization__isnull=True),
        user__isnull=False,
        role__deleted_at__isnull=True,
    )
    fields: dict[str, FilterField] = {
        "scope_kind": FilterField("scope_kind"),
        "lifecycle": FilterField("lifecycle"),
        "role": FilterField(
            q=lambda slugs: Q(user_id__in=bindings.filter(role__slug__in=slugs).values("user_id"))
        ),
        "team": FilterField(
            q=lambda teams: Q(
                user_id__in=Member.objects.filter(
                    scope_kind=Member.ScopeKind.TEAM, scope_id__in=_team_pks_named(org_id, teams)
                ).values("user_id")
            )
        ),
    }
    qs = qs.filter(filter_q(filter_input, fields))
    if "mine" in values:
        shares = Q(
            user_id__in=Member.objects.filter(
                scope_kind=Member.ScopeKind.TEAM, scope_id__in=viewer_team_pks(org_id, viewer_id)
            ).values("user_id")
        )
        qs = qs.filter(_tri(shares)(values["mine"]))
    if "admin" in values:
        now = timezone.now()
        admins = bindings.filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now),
            scope_kind=RoleBinding.ScopeKind.ORG,
            scope_id=org_id,
            role__permissions__contains=[Permission.ORG_MANAGE_MEMBERS.value],
        ).values("user_id")
        qs = qs.filter(_tri(Q(user_id__in=admins))(values["admin"]))
    if "active" in values:
        qs = qs.annotate(_last_active=_last_active_expr(org_id)).filter(_active_q(values["active"]))
    return qs


def _active_q(window: str) -> Q:
    if window == "never":
        return Q(_last_active__isnull=True)
    now = timezone.now()
    if window == "stale":
        return Q(_last_active__lt=now - dt.timedelta(days=90))
    days = _ACTIVE_WINDOWS.get(window)
    if days is None:
        # An unknown window matches nothing rather than being ignored.
        return Q(pk__in=[])
    return Q(_last_active__gte=now - dt.timedelta(days=days))


def member_teams(rows, org_id: int | None):
    """For a page of member rows: the team each TEAM row points at, and each
    user's teams in the org. Two queries per page, never one per row."""
    from astrolift_identity.schema.types import member_team_to_type

    user_ids = {m.user_id for m in rows}
    if org_id is None or not rows:
        return {}, {}
    teams = {t.pk: t for t in Team.objects.filter(organization_id=org_id)}
    team_rows = Member.objects.filter(
        user_id__in=user_ids, scope_kind=Member.ScopeKind.TEAM, scope_id__in=list(teams)
    ).values_list("user_id", "scope_id")
    by_user: dict[int, list] = {}
    for user_id, team_pk in team_rows:
        by_user.setdefault(user_id, []).append(teams[team_pk])
    teams_by_user = {
        user_id: [member_team_to_type(t) for t in sorted(held, key=lambda t: t.slug)]
        for user_id, held in by_user.items()
    }
    return teams, teams_by_user


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------

INVITATIONS_DEFAULT_SORT = "-created"

INVITATIONS_SORT_KEYS = {
    "name": SortKey(Lower("email")),
    "email": SortKey(Lower("email")),
    "created": SortKey("created_at"),
    "expires": SortKey("expires_at"),
    "status": SortKey("status"),
    "role": SortKey(Lower("role__slug"), nulls_low=True),
    "lastActive": SortKey("_last_active", nulls_low=True),
}


def _live_invitation_members(org_id):
    return Member.objects.filter(
        scope_kind="ORG",
        scope_id=org_id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
        user__is_active=True,
    )


def annotate_invitation_activity(qs, org_id):
    """Only an unambiguous live org member's audit activity matches an email."""
    from django.db.models import Min, OuterRef, Value

    member = (
        _live_invitation_members(org_id)
        .filter(user__email__iexact=OuterRef("email"))
        .order_by()
        .annotate(_group=Value(1))
        .values("_group")
        .annotate(_n=Count("pk"), _user=Min("user_id"))
        .filter(_n=1)
        .values("_user")
    )
    qs = qs.annotate(_invitee_id=Subquery(member, output_field=models.BigIntegerField()))
    return qs.annotate(_last_active=_last_active_expr(org_id, "_invitee_id"))


def invitation_activity(rows, org_id):
    """Batch enrichment for other sorts, without correlated work for every row."""
    from django.db.models import Max

    from astrolift_operations.models.audit_event import AuditEvent

    pending = [row for row in rows if not hasattr(row, "_last_active")]
    emails = {row.email.lower() for row in pending}
    by_email: dict[str, list[int]] = {}
    for email, user_id in (
        _live_invitation_members(org_id)
        .annotate(_email=Lower("user__email"))
        .filter(_email__in=emails)
        .values_list("_email", "user_id")
    ):
        by_email.setdefault(email, []).append(user_id)
    ids = {str(users[0]) for users in by_email.values() if len(users) == 1}
    active = {
        row["actor_id"]: row["last_at"]
        for row in AuditEvent.objects.filter(organization_id=org_id, actor_kind="user", actor_id__in=ids)
        .values("actor_id")
        .annotate(last_at=Max("occurred_at"))
    }
    for row in pending:
        users = by_email.get(row.email.lower(), [])
        row._last_active = active.get(str(users[0])) if len(users) == 1 else None


INVITATIONS_FILTERS = {
    "status": FilterField("status"),
    "role": FilterField("role__slug"),
    "invited_by": FilterField(q=_users_q("invited_by"), me=True),
}


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

TEAMS_DEFAULT_SORT = "name"

TEAMS_SORT_KEYS = {
    "name": SortKey(Lower("name")),
    "slug": SortKey("slug"),
    "created": SortKey("created_at"),
}


def filter_teams(qs, filter_input, *, org_id: int | None, viewer_id: int | None):
    values = filter_values(filter_input)
    if "mine" in values:
        qs = qs.filter(_tri(Q(pk__in=viewer_team_pks(org_id, viewer_id)))(values["mine"]))
    return qs


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

ROLES_DEFAULT_SORT = "name"

ROLES_SORT_KEYS = {
    "name": SortKey(Lower("name")),
    "slug": SortKey("slug"),
    "created": SortKey("created_at"),
    "scopeLevel": SortKey("scope_level"),
    "bindings": SortKey("_bindings_count"),
}

ROLES_FILTERS = {
    "is_system": FilterField("is_system"),
    "scope_level": FilterField("scope_level"),
    "created_by": FilterField(q=_users_q("created_by"), me=True),
}


def annotate_role_bindings_count(qs, org_id: int | None):
    """``_bindings_count``: bindings on the role inside ``org_id`` only.

    A system role is shared by every org; counting its bindings across
    tenants would tell one org how many grants the others hold.
    """
    from django.db.models import OuterRef

    return qs.annotate(_bindings_count=_count_expr(_org_bindings(org_id).filter(role=OuterRef("pk")), "role"))


def visible_role(org_id: int | None, role_id) -> Role | None:
    """A role the caller's org can see (its own or a system role), by guid."""
    if org_id is None:
        return None
    try:
        guid = uuid.UUID(str(role_id))
    except ValueError:
        return None
    return Role.objects.filter(Q(organization_id=org_id) | Q(organization__isnull=True), guid=guid).first()


# ---------------------------------------------------------------------------
# Role bindings
# ---------------------------------------------------------------------------

ROLE_BINDINGS_DEFAULT_SORT = "-created"


def role_bindings_sort_keys(org_id: int | None) -> dict[str, SortKey]:
    return {
        # The holder: a user's username, else the group's external id.
        "name": SortKey(Coalesce(Lower("user__username"), Lower("group_external_id"))),
        "role": SortKey(Lower("role__name")),
        "scope": SortKey("scope_kind"),
        # When it was granted, the column the cursor walk seeks on.
        "created": SortKey("granted_at"),
        "expires": SortKey("expires_at", nulls_low=True),
        "lastActive": SortKey(_last_active_expr(org_id), nulls_low=True),
    }


def _holder_q(values: list[str], *, org_id: int | None, viewer_id: int | None) -> Q:
    from astrolift_identity.idp_groups import member_groups

    out = Q(pk__in=[])
    names = []
    for value in values:
        if value == "me":
            if viewer_id is None or org_id is None:
                continue
            out |= Q(user_id=viewer_id)
            groups = sorted(member_groups(viewer_id, org_id))
            if groups:
                out |= Q(user__isnull=True, group_external_id__in=groups)
        elif value.startswith("group:"):
            out |= Q(user__isnull=True, group_external_id=value[len("group:") :])
        else:
            names.append(value)
    if names:
        out |= Q(user__username__in=names)
    return out


def filter_role_bindings(qs, filter_input, *, org_id: int | None, viewer_id: int | None):
    fields = {
        "role": FilterField("role__slug"),
        "scope_kind": FilterField("scope_kind"),
        "kind": FilterField(
            q=lambda kinds: (Q(user__isnull=False) if "user" in kinds else Q(pk__in=[]))
            | (Q(user__isnull=True) if "group" in kinds else Q(pk__in=[]))
        ),
        "holder": FilterField(q=lambda values: _holder_q(values, org_id=org_id, viewer_id=viewer_id)),
        "subject": FilterField(q=lambda values: _holder_q(values, org_id=org_id, viewer_id=viewer_id)),
    }
    return qs.filter(filter_q(filter_input, fields))


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------

POLICIES_DEFAULT_SORT = "-created"

POLICIES_SORT_KEYS = {
    "name": SortKey(Lower("name")),
    "slug": SortKey("slug"),
    "created": SortKey("created_at"),
    "updated": SortKey("updated_at"),
    "effect": SortKey("effect"),
    "scopeLevel": SortKey("scope_level"),
}

POLICIES_FILTERS = {
    "effect": FilterField("effect"),
    "scope_level": FilterField("scope_level"),
    "created_by": FilterField(q=_users_q("created_by"), me=True),
}
