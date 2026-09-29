"""
Who has access on one object, and the target scopes the access questions
take (#2157).

``resolve_target`` turns a ``(scopeKind, scopeId)`` pair from the API into
a scope of the caller's org, or ``None``: a scope of another org, a deleted
one, or an unknown kind all read as not found. ``access_on`` lists every
grant that reaches a target, from the same rules the permission resolver
applies: bindings on the target and on its ancestors (an ancestor binding
only when it inherits), user and group bindings, the org's group mappings,
and on an app the team shares.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from django.core.exceptions import ValidationError
from django.utils import timezone

TARGET_KINDS = ("ORG", "TEAM", "PROJECT", "APP", "AGENT")


@dataclasses.dataclass(frozen=True, slots=True)
class Target:
    kind: str  # ORG / TEAM / PROJECT / APP, an AGENT resolves to its APP
    pk: int
    guid: str
    requested_kind: str


def resolve_target(kind: str | None, ident: str | None, organization_id: int | None) -> Target | None:
    """The org's scope named by ``kind`` and a guid (or a numeric id)."""

    from astrolift_identity.models import Organization, Project, Team
    from astrolift_registry.models import RegisteredApp, Workload

    kind = (kind or "").strip().upper()
    ident = (ident or "").strip()
    if organization_id is None or kind not in TARGET_KINDS or not ident:
        return None
    lookup: dict[str, Any] = {"pk": int(ident)} if ident.isdigit() else {"guid": ident}
    try:
        if kind == "ORG":
            row = Organization.objects.filter(pk=organization_id, **lookup).values_list("pk", "guid").first()
        elif kind == "TEAM":
            row = (
                Team.objects.filter(organization_id=organization_id, **lookup)
                .values_list("pk", "guid")
                .first()
            )
        elif kind == "PROJECT":
            row = (
                Project.objects.filter(organization_id=organization_id, **lookup)
                .values_list("pk", "guid")
                .first()
            )
        elif kind == "APP":
            row = (
                RegisteredApp.objects.filter(organization_id=organization_id, **lookup)
                .values_list("pk", "guid")
                .first()
            )
        else:
            row = (
                Workload.objects.filter(
                    kind=Workload.Kind.AGENT,
                    registered_app__organization_id=organization_id,
                    registered_app__deleted_at__isnull=True,
                    **lookup,
                )
                .values_list("registered_app_id", "registered_app__guid")
                .first()
            )
    except (ValueError, TypeError, ValidationError):
        # A malformed guid is simply not found.
        return None
    if row is None:
        return None
    return Target(kind="APP" if kind == "AGENT" else kind, pk=row[0], guid=str(row[1]), requested_kind=kind)


@dataclasses.dataclass(frozen=True, slots=True)
class AccessRow:
    principal_kind: str  # USER / GROUP / TEAM
    source: str  # USER_BINDING / GROUP_BINDING / GROUP_MAPPING / TEAM_SHARE
    binding_guid: str
    scope_kind: str
    scope_id: int
    inherited: bool
    inherits: bool
    user: Any = None
    group_external_id: str = ""
    team: Any = None
    role: Any = None
    access_level: str = ""
    share_guid: str = ""
    expires_at: dt.datetime | None = None
    member_guid: str = ""
    group_member_count: int | None = None

    @property
    def sort_key(self) -> tuple:
        kind_rank = {"USER": 0, "GROUP": 1, "TEAM": 2}.get(self.principal_kind, 3)
        if self.user is not None:
            name = (self.user.get_username() or "").lower()
        elif self.team is not None:
            name = (self.team.slug or "").lower()
        else:
            name = self.group_external_id.lower()
        return (kind_rank, name, self.inherited, self.source, self.binding_guid)

    def matches(self, term: str) -> bool:
        t = term.lower()
        haystack = [self.group_external_id, self.access_level, self.source]
        if self.user is not None:
            haystack += [
                self.user.get_username(),
                self.user.email or "",
                self.user.first_name,
                self.user.last_name,
            ]
        if self.team is not None:
            haystack += [self.team.slug, self.team.name]
        if self.role is not None:
            haystack += [self.role.slug, self.role.name]
        return any(t in (h or "").lower() for h in haystack)


def access_on(organization_id: int, target: Target) -> list[AccessRow]:
    """Every grant that reaches ``target``, one row per grant."""

    from astrolift_identity.idp_groups import group_member_counts
    from astrolift_identity.models import GroupRoleMapping, Member, RoleBinding, Team
    from astrolift_identity.permission_resolver import _scope_ancestry, _scope_filter, _shares_on_apps
    from core.permissions import PermissionScope, ScopeKind
    from core.tenancy import TenantContext

    tenant = TenantContext(organization_id=organization_id)
    chain = _scope_ancestry(tenant, PermissionScope(kind=ScopeKind(target.kind), id=target.pk))
    if not chain:
        return []
    own = chain[0]
    now = timezone.now()
    rows: list[AccessRow] = []

    def live(binding) -> bool:
        return binding.expires_at is None or binding.expires_at > now

    for binding in (
        RoleBinding.objects.select_related("user", "role")
        .filter(_scope_filter(chain))
        .order_by("granted_at", "pk")
    ):
        key = (binding.scope_kind, binding.scope_id)
        if not live(binding) or (key != own and not binding.inherits):
            continue
        rows.append(_binding_row(binding, inherited=key != own))

    for mapping in (
        GroupRoleMapping.objects.select_related("role")
        .filter(organization_id=organization_id)
        .filter(_scope_filter(chain))
        .order_by("created_at", "pk")
    ):
        rows.append(_mapping_row(mapping, inherited=(mapping.scope_kind, mapping.scope_id) != own))

    if target.kind == "APP":
        shares = _shares_on_apps(organization_id, [target.pk]).get(target.pk, [])
        teams = {
            t.pk: t
            for t in Team.objects.filter(pk__in={s[1] for s in shares}, organization_id=organization_id)
        }
        team_bindings: dict[int, list] = {}
        if teams:
            for binding in RoleBinding.objects.select_related("user", "role").filter(
                scope_kind="TEAM", scope_id__in=list(teams), inherits=True
            ):
                if live(binding):
                    team_bindings.setdefault(binding.scope_id, []).append(
                        _binding_row(binding, inherited=True)
                    )
            for mapping in GroupRoleMapping.objects.select_related("role").filter(
                organization_id=organization_id, scope_kind="TEAM", scope_id__in=list(teams)
            ):
                team_bindings.setdefault(mapping.scope_id, []).append(_mapping_row(mapping, inherited=True))
        for share_guid, team_id, level in shares:
            team = teams.get(team_id)
            if team is None:
                continue
            rows.append(
                AccessRow(
                    principal_kind="TEAM",
                    source="TEAM_SHARE",
                    binding_guid=share_guid,
                    scope_kind="APP",
                    scope_id=target.pk,
                    inherited=False,
                    inherits=True,
                    team=team,
                    access_level=level,
                    share_guid=share_guid,
                )
            )
            for row in team_bindings.get(team_id, ()):
                rows.append(
                    dataclasses.replace(
                        row,
                        source="TEAM_SHARE",
                        team=team,
                        access_level=level,
                        share_guid=share_guid,
                    )
                )

    # Member ids and group sizes, one query per kind for the whole set.
    user_ids = {r.user.pk for r in rows if r.user is not None}
    member_guids = dict(
        Member.objects.filter(
            scope_kind=Member.ScopeKind.ORG, scope_id=organization_id, user_id__in=user_ids
        ).values_list("user_id", "guid")
    )
    counts = group_member_counts(
        organization_id, sorted({r.group_external_id for r in rows if r.group_external_id})
    )
    return [
        dataclasses.replace(
            r,
            member_guid=str(member_guids.get(r.user.pk, "")) if r.user is not None else "",
            group_member_count=counts.get(r.group_external_id) if r.group_external_id else None,
        )
        for r in rows
    ]


def _binding_row(binding, *, inherited: bool) -> AccessRow:
    is_user = binding.user_id is not None
    return AccessRow(
        principal_kind="USER" if is_user else "GROUP",
        source="USER_BINDING" if is_user else "GROUP_BINDING",
        binding_guid=str(binding.guid),
        scope_kind=binding.scope_kind,
        scope_id=binding.scope_id,
        inherited=inherited,
        inherits=binding.inherits,
        user=binding.user if is_user else None,
        group_external_id="" if is_user else binding.group_external_id,
        role=binding.role,
        expires_at=binding.expires_at,
    )


def _mapping_row(mapping, *, inherited: bool) -> AccessRow:
    return AccessRow(
        principal_kind="GROUP",
        source="GROUP_MAPPING",
        binding_guid=str(mapping.guid),
        scope_kind=mapping.scope_kind,
        scope_id=mapping.scope_id,
        inherited=inherited,
        inherits=True,
        group_external_id=mapping.group_external_id,
        role=mapping.role,
    )
