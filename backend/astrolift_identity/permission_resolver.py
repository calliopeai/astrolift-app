"""
Permission resolver backed by ``RoleBinding``.

Walks up App → Project → Team → Org for the current user, unioning
every grant's permissions, then runs the org's ABAC policies, which can
only deny: they never grant beyond the RBAC result (#2157).

A grant is any of (#2157):

* a ``RoleBinding`` on the user;
* a ``RoleBinding`` on an IdP group the user is in (``group_external_id``);
* a ``GroupRoleMapping`` of the org for an IdP group the user is in, which
  applies exactly like a group binding;
* for an app target, a ``RoleBinding`` of either kind on a team that holds
  an ``AppTeamAccess`` share on the app, at a share level that covers the
  permission (the rule ``astrolift_agents.visibility`` applies).

The user's IdP groups are the ones the IdP asserted at their last sign-in,
stored on their ORG ``Member`` row of each organization
(:mod:`astrolift_identity.idp_groups`), so they are confined to the org.

``RoleBinding.inherits=False`` grants only at the binding's own scope: it
satisfies a check whose target is that scope and nothing below it. The
target of a check is its explicit scope, or for a targetless check the
most specific scope the request selected (#1743).

Registered with :func:`core.permissions.register_permission_resolver`
in ``apps.AstroliftIdentityConfig.ready``.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import fnmatch
import functools
from collections.abc import Iterable
from typing import Any

from django.db.models import Q
from django.utils import timezone

from core.permissions import (
    ALL_SCOPES,
    NO_SCOPES,
    GrantedScopes,
    Permission,
    PermissionScope,
    ScopeKind,
)
from core.tenancy import TenantContext

# Order matters: when we match a binding at a higher scope, it grants
# down. The chain is the reverse of the ancestry walk.
_INHERITANCE_ORDER = ("ORG", "TEAM", "PROJECT", "APP")

# Share levels (``AppTeamAccess.AccessLevel``) that carry a permission on
# the shared app: a viewer share carries the read permissions only, a
# deployer or owner share carries every permission the team's grant does.
SHARE_READ_PERMISSIONS = frozenset(
    {Permission.AGENT_READ.value, Permission.APP_READ.value, Permission.AGENT_TASK_WATCH.value}
)


def share_levels(permission: str | Permission) -> list[str]:
    """The ``AppTeamAccess`` levels that delegate ``permission``."""

    value = permission.value if isinstance(permission, Permission) else str(permission)
    levels = ["deployer", "owner"]
    return ["viewer", *levels] if value in SHARE_READ_PERMISSIONS else levels


# ---------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------

SOURCE_USER = "user"
SOURCE_GROUP = "group"
SOURCE_GROUP_MAPPING = "group_mapping"


@dataclasses.dataclass(frozen=True, slots=True)
class Grant:
    """One live role grant held by the actor, whatever its source."""

    role: Any
    scope_kind: str
    scope_id: int
    inherits: bool
    source: str
    guid: str
    group_external_id: str = ""
    expires_at: dt.datetime | None = None

    @property
    def label(self) -> str:
        base = f"{self.role.slug}@{self.scope_kind}:{self.scope_id}"
        if self.source == SOURCE_GROUP:
            return f"{base} via group {self.group_external_id}"
        if self.source == SOURCE_GROUP_MAPPING:
            return f"{base} via group mapping {self.group_external_id}"
        return base

    def carries(self, permission: str) -> bool:
        return permission in (self.role.permissions or ())

    def covers(self, chain: list[tuple[str, int]]) -> bool:
        """Whether this grant reaches the target of ``chain`` (target first)."""

        if not chain:
            return False
        key = (self.scope_kind, self.scope_id)
        if key == chain[0]:
            return True
        return self.inherits and key in chain


def _memoized(fn):
    """Give a call outside any request a cache of its own for its duration.

    Inside a request the policies and the actor's groups are cached on the
    request's attributes. A call outside one (a worker, a test) would
    otherwise load them again for every app of a bulk answer; this scopes
    one cache to the call, never longer.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from astrolift_identity.abac import RequestAttributes, current_attributes, request_attributes

        if current_attributes() is not None:
            return fn(*args, **kwargs)
        with request_attributes(RequestAttributes()):
            return fn(*args, **kwargs)

    return wrapper


def _cache() -> dict | None:
    from astrolift_identity.abac import current_attributes

    attrs = current_attributes()
    return attrs.cache if attrs is not None else None


def actor_groups(tenant: TenantContext) -> frozenset[str]:
    """The IdP groups the actor is in, for the active org only."""

    if tenant.actor_user_id is None or tenant.organization_id is None:
        return frozenset()
    cache = _cache()
    key = ("groups", tenant.organization_id, tenant.actor_user_id)
    if cache is not None and key in cache:
        return cache[key]
    from astrolift_identity.idp_groups import member_groups

    groups = member_groups(tenant.actor_user_id, tenant.organization_id)
    if cache is not None:
        cache[key] = groups
    return groups


def _live_grants(tenant: TenantContext, scopes: Iterable[tuple[str, int]] | None = None) -> list[Grant]:
    """The actor's unexpired grants, optionally limited to ``scopes``.

    User bindings first, then group bindings, then group mappings, so a
    reason names the most direct grant. Group bindings carry no org
    column: their scope pins them, and callers only ever ask about scopes
    of the active org.
    """

    from astrolift_identity.models import GroupRoleMapping, RoleBinding

    if tenant.actor_user_id is None:
        return []
    scope_list = list(scopes) if scopes is not None else None
    if scope_list is not None and not scope_list:
        return []
    groups = actor_groups(tenant)
    now = timezone.now()

    principal = Q(user_id=tenant.actor_user_id)
    if groups:
        principal |= Q(user__isnull=True, group_external_id__in=sorted(groups))
    qs = RoleBinding.objects.select_related("role").filter(principal)
    if scope_list is not None:
        qs = qs.filter(_scope_filter(scope_list))
    out: list[Grant] = []
    group_rows: list[Grant] = []
    for binding in qs.order_by("granted_at", "pk"):
        if binding.expires_at is not None and binding.expires_at <= now:
            continue
        grant = Grant(
            role=binding.role,
            scope_kind=binding.scope_kind,
            scope_id=binding.scope_id,
            inherits=binding.inherits,
            source=SOURCE_USER if binding.user_id is not None else SOURCE_GROUP,
            guid=str(binding.guid),
            group_external_id=binding.group_external_id or "",
            expires_at=binding.expires_at,
        )
        (out if binding.user_id is not None else group_rows).append(grant)
    out.extend(group_rows)

    if groups and tenant.organization_id is not None:
        mappings = GroupRoleMapping.objects.select_related("role").filter(
            Q(role__organization_id=tenant.organization_id) | Q(role__organization__isnull=True),
            organization_id=tenant.organization_id,
            group_external_id__in=sorted(groups),
        )
        if scope_list is not None:
            mappings = mappings.filter(_scope_filter(scope_list))
        for mapping in mappings.order_by("created_at", "pk"):
            out.append(
                Grant(
                    role=mapping.role,
                    scope_kind=mapping.scope_kind,
                    scope_id=mapping.scope_id,
                    inherits=True,
                    source=SOURCE_GROUP_MAPPING,
                    guid=str(mapping.guid),
                    group_external_id=mapping.group_external_id,
                )
            )
    return out


@dataclasses.dataclass(frozen=True, slots=True)
class ShareGrant:
    """A team grant that reaches an app through an ``AppTeamAccess`` share."""

    grant: Grant
    share_guid: str
    team_id: int
    access_level: str
    app_id: int

    @property
    def label(self) -> str:
        return f"{self.grant.label} via team share ({self.access_level}) on APP:{self.app_id}"


def _shares_on_apps(
    organization_id: int | None, app_ids: Iterable[int]
) -> dict[int, list[tuple[str, int, str]]]:
    """``app_id -> [(share guid, team id, level)]`` for live shares whose
    app and team both belong to the org."""

    from astrolift_registry.models import AppTeamAccess

    ids = list(app_ids)
    if organization_id is None or not ids:
        return {}
    rows = AppTeamAccess.objects.filter(
        registered_app_id__in=ids,
        registered_app__organization_id=organization_id,
        registered_app__deleted_at__isnull=True,
        team__organization_id=organization_id,
        team__deleted_at__isnull=True,
    ).values_list("guid", "registered_app_id", "team_id", "access_level")
    out: dict[int, list[tuple[str, int, str]]] = {}
    for guid, app_id, team_id, level in rows:
        out.setdefault(app_id, []).append((str(guid), team_id, level))
    return out


def _share_grants(tenant: TenantContext, app_ids: Iterable[int]) -> dict[int, list[ShareGrant]]:
    """Every actor grant that reaches each app through a team share.

    Only an inheriting grant held at the sharing team counts: the share
    extends the team's grant to an app outside its tree, which a grant
    confined to its own scope does not reach.
    """

    shares = _shares_on_apps(tenant.organization_id, app_ids)
    if not shares:
        return {}
    team_ids = {team_id for rows in shares.values() for _g, team_id, _l in rows}
    by_team: dict[int, list[Grant]] = {}
    for grant in _live_grants(tenant, [("TEAM", t) for t in team_ids]):
        if grant.inherits:
            by_team.setdefault(grant.scope_id, []).append(grant)
    out: dict[int, list[ShareGrant]] = {}
    for app_id, rows in shares.items():
        for share_guid, team_id, level in rows:
            for grant in by_team.get(team_id, ()):
                out.setdefault(app_id, []).append(
                    ShareGrant(
                        grant=grant, share_guid=share_guid, team_id=team_id, access_level=level, app_id=app_id
                    )
                )
    return out


def _share_permissions(share: ShareGrant) -> set[str]:
    perms = set(share.grant.role.permissions or ())
    if share.access_level in ("deployer", "owner"):
        return perms
    if share.access_level == "viewer":
        return perms & SHARE_READ_PERMISSIONS
    return set()


# ---------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class Decision:
    """Everything one check concluded, in the order it concluded it.

    ``resolve`` returns ``(granted, reason)`` from this; the permission
    diagnosis renders the rest, so the two can never disagree.
    """

    granted: bool
    reason: str
    chain: tuple[tuple[str, int], ...] = ()
    grants: tuple[Grant, ...] = ()
    shares: tuple[ShareGrant, ...] = ()
    matched: Grant | ShareGrant | None = None
    rbac_granted: bool = False
    abac: Any = None
    superuser: bool = False
    groups: frozenset[str] = frozenset()

    def as_tuple(self) -> tuple[bool, str]:
        return self.granted, self.reason


@_memoized
def decide(
    tenant: TenantContext,
    permission: Permission,
    scope: PermissionScope | None,
) -> Decision:
    if tenant.actor_user_id is None:
        return Decision(False, "no actor")

    # Django superusers bypass the RoleBinding chain and the org's ABAC
    # policies. This matches the convention every Django app inherits from
    # auth.contrib and keeps the bootstrap admin path simple: a fresh
    # install just needs a superuser, no role plumbing required for the
    # first operator. The platform operator is not a principal of any
    # org's policies (spec 03 §6). The RoleBinding chain is still the
    # source of truth for every non-superuser, including JIT'd OIDC users.
    if _is_superuser(tenant.actor_user_id):
        return Decision(True, "django superuser", superuser=True)

    chain = _candidate_scopes(tenant, scope)
    if not chain:
        return Decision(False, "no scope")

    groups = actor_groups(tenant)
    grants = tuple(_live_grants(tenant, chain))
    covering = [g for g in grants if g.covers(chain)]
    matched: Grant | ShareGrant | None = next((g for g in covering if g.carries(permission.value)), None)
    shares: tuple[ShareGrant, ...] = ()
    if chain[0][0] == "APP":
        shares = tuple(_share_grants(tenant, [chain[0][1]]).get(chain[0][1], ()))
        if matched is None:
            matched = next((s for s in shares if permission.value in _share_permissions(s)), None)

    common = {"chain": tuple(chain), "grants": grants, "shares": shares, "groups": groups}
    if matched is None:
        return Decision(False, "no role binding grants this permission", **common)

    reason = f"role:{matched.label}"
    abac = _abac(tenant, permission.value, chain, covering, groups)
    if abac.denied:
        return Decision(False, abac.reason, matched=matched, rbac_granted=True, abac=abac, **common)
    return Decision(True, reason, matched=matched, rbac_granted=True, abac=abac, **common)


def resolve(
    tenant: TenantContext,
    permission: Permission,
    scope: PermissionScope | None,
) -> tuple[bool, str]:
    return decide(tenant, permission, scope).as_tuple()


def _abac(tenant: TenantContext, permission: str, chain, covering: Iterable[Grant], groups: frozenset[str]):
    from astrolift_identity import abac

    attrs = abac.attributes_for(tenant.actor_user_id)
    policies = abac.org_policies(tenant.organization_id, attrs)
    if not policies:
        return abac.NOT_DENIED
    subject = abac.new_subject(
        organization_id=tenant.organization_id,
        actor_user_id=tenant.actor_user_id,
        permission=permission,
        chain=list(chain),
        roles_at_target=[g.role.slug for g in covering],
        groups=groups,
        attrs=attrs,
    )
    return abac.evaluate(subject, policies)


def _abac_filter(tenant: TenantContext, slugs: set[str], chain, covering: Iterable[Grant]) -> set[str]:
    """``slugs`` minus the ones the org's policies deny at ``chain``."""

    from astrolift_identity import abac

    if not slugs or tenant.organization_id is None:
        return slugs
    attrs = abac.attributes_for(tenant.actor_user_id)
    policies = abac.org_policies(tenant.organization_id, attrs)
    if not policies:
        return slugs
    covering = list(covering)
    groups = actor_groups(tenant)
    kept = set()
    for slug in slugs:
        subject = abac.new_subject(
            organization_id=tenant.organization_id,
            actor_user_id=tenant.actor_user_id,
            permission=slug,
            chain=list(chain),
            roles_at_target=[g.role.slug for g in covering],
            groups=groups,
            attrs=attrs,
        )
        if not abac.evaluate(subject, policies).denied:
            kept.add(slug)
    return kept


def _denied_everywhere(tenant: TenantContext, slugs: Iterable[str]) -> set[str]:
    """The slugs a policy denies at every scope of the org.

    Only a policy that covers the whole org and matches on nothing a
    target decides can answer that without a target; narrower policies
    still deny at the object gate.
    """

    from astrolift_identity import abac

    if tenant.organization_id is None or tenant.actor_user_id is None:
        return set()
    attrs = abac.attributes_for(tenant.actor_user_id)
    policies = [p for p in abac.org_policies(tenant.organization_id, attrs) if abac.applies_everywhere(p)]
    if not policies:
        return set()
    groups = actor_groups(tenant)
    denied = set()
    for slug in slugs:
        subject = abac.new_subject(
            organization_id=tenant.organization_id,
            actor_user_id=tenant.actor_user_id,
            permission=slug,
            chain=[("ORG", tenant.organization_id)],
            roles_at_target=[],
            groups=groups,
            attrs=attrs,
        )
        if abac.evaluate(subject, policies).denied:
            denied.add(slug)
    return denied


def _org_confined_bindings(tenant: TenantContext) -> list[Grant]:
    """The actor's live grants whose scope lies in the active org.

    One scan, shared by the any-scope readers below. Grants are dropped
    if expired, and scope ids are confined to the active org so a TEAM
    binding in org A contributes nothing while org B is active --
    without that, cross-tenant ids would reach the row filters. Group
    bindings and group mappings are included.
    """

    from astrolift_identity.models import Project, Team
    from astrolift_registry.models import RegisteredApp

    org_id = tenant.organization_id
    live = _live_grants(tenant)

    ids_by_kind: dict[str, set[int]] = {}
    for grant in live:
        ids_by_kind.setdefault(grant.scope_kind, set()).add(grant.scope_id)

    allowed: dict[str, set[int]] = {"ORG": {org_id} & ids_by_kind.get("ORG", set())}
    for kind, model in (("TEAM", Team), ("PROJECT", Project), ("APP", RegisteredApp)):
        ids = ids_by_kind.get(kind, set())
        allowed[kind] = (
            set(model.objects.filter(pk__in=ids, organization_id=org_id).values_list("pk", flat=True))
            if ids
            else set()
        )

    return [g for g in live if g.scope_id in allowed.get(g.scope_kind, set())]


def _target_policies(tenant: TenantContext, slugs: Iterable[str]) -> bool:
    from astrolift_identity import abac

    attrs = abac.attributes_for(tenant.actor_user_id)
    return any(
        not abac.applies_everywhere(policy)
        and any(fnmatch.fnmatchcase(slug, policy.action_pattern or "*") for slug in slugs)
        for policy in abac.org_policies(tenant.organization_id, attrs)
    )


def _policy_scope_permissions(tenant: TenantContext, slugs: set[str]) -> dict[str, dict[int, set[str]]]:
    """Concrete permitted scopes; a denied parent never lends inheritance.

    Load the live scope tree and operation facts once per request. App
    navigation is usable if at least one of its environments allows the
    permission; individual operations still check their exact environment.
    """
    from astrolift_identity import abac
    from astrolift_identity.models import Project, Team
    from astrolift_identity.operation_context import OperationContext, environment_context
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp

    attrs = abac.attributes_for(tenant.actor_user_id)
    key = ("policy_scope_tree", tenant.organization_id)
    if key not in attrs.cache:
        org = ("ORG", tenant.organization_id)
        points = [(org, [org], (OperationContext(),))]
        teams = set(Team.objects.filter(organization_id=tenant.organization_id).values_list("pk", flat=True))
        points.extend((("TEAM", ident), [("TEAM", ident), org], (OperationContext(),)) for ident in teams)
        for ident, team_id, slug in Project.objects.filter(
            organization_id=tenant.organization_id
        ).values_list("pk", "team_id", "slug"):
            attrs.cache[("slug", "PROJECT", ident)] = slug
            chain = [("PROJECT", ident)]
            if team_id in teams:
                chain.append(("TEAM", team_id))
            points.append((("PROJECT", ident), [*chain, org], (OperationContext(),)))
        app_ids = []
        for ident, slug in RegisteredApp.objects.filter(organization_id=tenant.organization_id).values_list(
            "pk", "slug"
        ):
            app_ids.append(ident)
            attrs.cache[("slug", "APP", ident)] = slug
        environments = {}
        for env in AppEnvironment.objects.filter(registered_app_id__in=app_ids).select_related(
            "tenant_cluster"
        ):
            environments.setdefault(env.registered_app_id, []).append(environment_context(env))
        for ident, chain in _app_scope_chains(tenant, app_ids).items():
            points.append((("APP", ident), chain, tuple(environments.get(ident) or (OperationContext(),))))
        attrs.cache[key] = (points, app_ids)
    points, app_ids = attrs.cache[key]
    grants = _org_confined_bindings(tenant)
    shares = _share_grants(tenant, app_ids)
    allowed = {kind: {} for kind in ("ORG", "TEAM", "PROJECT", "APP")}
    for (kind, ident), chain, contexts in points:
        covering = [grant for grant in grants if grant.covers(chain)]
        candidates = {slug for grant in covering for slug in slugs if grant.carries(slug)}
        # Team shares have a permission ceiling and must be evaluated with
        # their real role at the shared app, as the object resolver does.
        extra = shares.get(ident, ()) if kind == "APP" else ()
        candidates.update(
            slug
            for share in extra
            for slug in slugs
            if share.grant.carries(slug) and share.access_level in share_levels(slug)
        )
        kept = set()
        for context in contexts:
            with abac.operation_attributes(**context.attributes()):
                for slug in candidates:
                    roles = [
                        *covering,
                        *(share.grant for share in extra if share.access_level in share_levels(slug)),
                    ]
                    kept.update(_abac_filter(tenant, {slug}, chain, roles))
        if kept:
            allowed[kind][ident] = kept
    return allowed


@_memoized
def granted_scopes(tenant: TenantContext, permission: Permission) -> GrantedScopes:
    """RoleBinding-backed :data:`core.permissions.GrantedScopesProvider`.

    Every scope in the active org where ``tenant``'s actor holds
    ``permission``. A non-inheriting TEAM or PROJECT grant lands in the
    ``exact_*`` sets (that scope and nothing below it); a non-inheriting
    ORG grant covers the org row itself and no row beneath it, so it adds
    nothing here. A policy that denies the permission everywhere empties
    the answer.
    """

    if tenant.actor_user_id is None:
        return NO_SCOPES
    if _is_superuser(tenant.actor_user_id):
        return ALL_SCOPES
    if tenant.organization_id is None:
        return NO_SCOPES
    if _denied_everywhere(tenant, [permission.value]):
        return NO_SCOPES
    if _target_policies(tenant, [permission.value]):
        allowed = _policy_scope_permissions(tenant, {permission.value})
        return GrantedScopes(
            org=False,
            team_ids=frozenset(),
            project_ids=frozenset(),
            app_ids=frozenset(allowed["APP"]),
            exact_team_ids=frozenset(allowed["TEAM"]),
            exact_project_ids=frozenset(allowed["PROJECT"]),
            org_only=bool(allowed["ORG"]),
        )

    by_kind: dict[str, set[int]] = {"ORG": set(), "TEAM": set(), "PROJECT": set(), "APP": set()}
    exact: dict[str, set[int]] = {"TEAM": set(), "PROJECT": set()}
    for grant in _org_confined_bindings(tenant):
        if not grant.carries(permission.value):
            continue
        if grant.inherits or grant.scope_kind == "APP":
            by_kind[grant.scope_kind].add(grant.scope_id)
        elif grant.scope_kind in exact:
            exact[grant.scope_kind].add(grant.scope_id)

    if by_kind["ORG"]:
        return ALL_SCOPES
    return GrantedScopes(
        org=False,
        team_ids=frozenset(by_kind["TEAM"]),
        project_ids=frozenset(by_kind["PROJECT"]),
        app_ids=frozenset(by_kind["APP"]),
        exact_team_ids=frozenset(exact["TEAM"] - by_kind["TEAM"]),
        exact_project_ids=frozenset(exact["PROJECT"] - by_kind["PROJECT"]),
    )


@_memoized
def resolve_effective_permissions_anywhere(tenant: TenantContext) -> set[str]:
    """Every permission slug the viewer holds *somewhere* in the active org.

    :func:`resolve_effective_permissions` answers for one point in the
    hierarchy, which is right for a per-app ``viewerPermissions`` field.
    It is the wrong question for the capability manifest that decides
    which modules the nav renders: a viewer whose only binding is
    TEAM-scoped resolves to the empty set at org scope, so the shell
    hides Apps, Agents and Workflows from someone who can use all three
    on her own team (#1717).

    Capability, not authority: every real action still runs its own
    scoped check, which is what stops this from being a grant. A slug a
    policy denies everywhere is left out.
    """

    if tenant.actor_user_id is None:
        return set()
    if _is_superuser(tenant.actor_user_id):
        return {p.value for p in Permission}
    if tenant.organization_id is None:
        return set()

    effective: set[str] = set()
    for grant in _org_confined_bindings(tenant):
        effective.update(grant.role.permissions or ())
    effective -= _denied_everywhere(tenant, effective)
    if _target_policies(tenant, effective):
        allowed = _policy_scope_permissions(tenant, effective)
        return {slug for scopes in allowed.values() for slugs in scopes.values() for slug in slugs}
    return effective


def _scope_ancestry(tenant: TenantContext, scope: PermissionScope) -> list[tuple[str, int]]:
    """The live target and its live ancestors, confined to the active org."""
    from astrolift_identity.models import Organization, Project, Team

    org_id = tenant.organization_id
    if org_id is None:
        return []

    if scope.kind == ScopeKind.APP:
        return _app_scope_chains(tenant, [scope.id]).get(scope.id, [])
    if scope.kind == ScopeKind.ORG:
        if scope.id != org_id or not Organization.objects.filter(pk=org_id).exists():
            return []
        return [("ORG", org_id)]

    out = [(scope.kind.value, scope.id)]
    if scope.kind == ScopeKind.PROJECT:
        row = (
            Project.objects.filter(pk=scope.id, organization_id=org_id, organization__deleted_at__isnull=True)
            .values("team_id", "team__organization_id", "team__deleted_at")
            .first()
        )
        if row is None:
            return []
        if row["team__organization_id"] == org_id and row["team__deleted_at"] is None:
            out.append(("TEAM", row["team_id"]))
    elif scope.kind == ScopeKind.TEAM:
        if not Team.objects.filter(
            pk=scope.id, organization_id=org_id, organization__deleted_at__isnull=True
        ).exists():
            return []
    else:
        return []
    out.append(("ORG", org_id))
    return out


def _app_scope_chains(tenant: TenantContext, app_ids: Iterable[int]) -> dict[int, list[tuple[str, int]]]:
    """Read current ownership in one query for both single and bulk checks.

    Joined parents need explicit validity checks: a foreign or deleted
    parent must not grant access through a stale denormalized link.
    """
    from astrolift_registry.models import RegisteredApp

    org_id = tenant.organization_id
    if org_id is None:
        return {}
    rows = RegisteredApp.objects.filter(
        pk__in=app_ids, organization_id=org_id, organization__deleted_at__isnull=True
    ).values(
        "pk",
        "project_id",
        "project__organization_id",
        "project__deleted_at",
        "team_id",
        "team__organization_id",
        "team__deleted_at",
    )
    chains = {}
    for row in rows:
        chain = [("APP", row["pk"])]
        for parent in ("project", "team"):
            if (
                row[f"{parent}_id"] is not None
                and row[f"{parent}__organization_id"] == org_id
                and row[f"{parent}__deleted_at"] is None
            ):
                chain.append((parent.upper(), row[f"{parent}_id"]))
        chain.append(("ORG", org_id))
        chains[row["pk"]] = chain
    return chains


def _candidate_scopes(
    tenant: TenantContext,
    scope: PermissionScope | None,
) -> list[tuple[str, int]]:
    if scope is not None:
        # Selected context is a default for targetless checks, never an
        # additional grant on an explicit object (#1743).
        return _scope_ancestry(tenant, scope)
    out: list[tuple[str, int]] = []
    # The selected team and project count only when they belong to the
    # active org (#1911): a binding on another org's team must never satisfy
    # a check in this org, whatever the request selected.
    if tenant.project_id is not None and _in_org("PROJECT", tenant.project_id, tenant.organization_id):
        out.append(("PROJECT", tenant.project_id))
    if tenant.team_id is not None and _in_org("TEAM", tenant.team_id, tenant.organization_id):
        out.append(("TEAM", tenant.team_id))
    if tenant.organization_id is not None:
        out.append(("ORG", tenant.organization_id))
    # Stable, dedupe-preserving order.
    seen: set[tuple[str, int]] = set()
    return [s for s in out if not (s in seen or seen.add(s))]


def _in_org(kind: str, scope_id: int, organization_id: int | None) -> bool:
    if organization_id is None:
        return False
    from astrolift_identity.models import Project, Team

    model = Project if kind == "PROJECT" else Team
    return model.objects.filter(
        pk=scope_id, organization_id=organization_id, deleted_at__isnull=True
    ).exists()


def _scope_filter(scopes: list[tuple[str, int]]):
    from django.db.models import Q

    q = Q()
    for kind, ident in scopes:
        q |= Q(scope_kind=kind, scope_id=ident)
    return q


def _is_superuser(user_id: int) -> bool:
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=user_id, is_superuser=True, is_active=True).exists()


# ---------------------------------------------------------------------
# Public helpers: full effective-permission resolution (#478)
# ---------------------------------------------------------------------
#
# ``resolve`` answers the "may this caller do X here?" question one
# permission at a time. The helpers below answer the dual: "what is the
# full set of permission slugs the caller has at this scope?". Used by
# the self-service ``viewerPermissions`` surfaces — flat list at org
# scope (``astroliftMyPermissions``), per-app at app scope
# (``astroliftAppPermissions`` and the ``AstroliftRegisteredApp``
# ``viewerPermissions`` field), and — via the bulk helper —
# the apps-list path with a bounded query count regardless of list size.


@_memoized
def resolve_effective_permissions(
    tenant: TenantContext,
    *,
    extra_scope: tuple[str, int] | None = None,
) -> set[str]:
    """Return the full permission slug set the viewer holds at ``tenant``.

    Mirrors the resolution logic in :func:`resolve` but unions instead
    of short-circuiting on a single permission: the viewer's live grants
    (user bindings, group bindings, group mappings) that reach the target
    of the explicit ``extra_scope`` (typically ``("APP", app.pk)`` for
    per-app viewerPermissions) or of the selected tenant context, plus on
    an app the team shares, minus whatever the org's policies deny there.

    Superuser bypass returns the full :class:`core.permissions.Permission`
    catalog, matching the bootstrap-admin convention in :func:`resolve`.
    Anonymous (no actor) returns an empty set.

    The result is a ``set`` so callers can union further; convert to a
    sorted list at the API boundary.
    """
    if tenant.actor_user_id is None:
        return set()

    if _is_superuser(tenant.actor_user_id):
        return {p.value for p in Permission}

    chain = _effective_scope_chain(tenant, extra_scope=extra_scope)
    if not chain:
        return set()

    covering = [g for g in _live_grants(tenant, chain) if g.covers(chain)]
    effective: set[str] = set()
    for grant in covering:
        effective.update(grant.role.permissions or ())
    if chain[0][0] == "APP":
        for share in _share_grants(tenant, [chain[0][1]]).get(chain[0][1], ()):
            effective |= _share_permissions(share)
    return _abac_filter(tenant, effective, chain, covering)


@_memoized
def resolve_effective_permissions_for_apps(
    tenant: TenantContext,
    apps: Iterable,
) -> dict[int, set[str]]:
    """Bulk variant of :func:`resolve_effective_permissions` for many apps.

    Answers "what permission slugs does the viewer have on each app in
    ``apps``?" with one grant query (plus one mapping query when the viewer
    is in IdP groups), one ancestry query, one share query and the
    superuser check, not one per app: the N+1 guard that backs
    ``viewerPermissions`` on ``astroliftApps`` / ``astroliftMyApps``.

    Returns a dict keyed by ``RegisteredApp.pk``. Apps the viewer has no
    bindings for still appear in the dict with an empty set, so callers
    can map results back uniformly without a missing-key dance.

    Scope chains use the same current, org-confined ownership as the
    object gate, regardless of the active tenant's project/team picks
    or stale app instances supplied by the caller.
    """
    app_list = list(apps)
    result: dict[int, set[str]] = {a.pk: set() for a in app_list}
    if not app_list or tenant.actor_user_id is None:
        return result

    if _is_superuser(tenant.actor_user_id):
        full = {p.value for p in Permission}
        return {pk: set(full) for pk in result}

    per_app_chain = _app_scope_chains(tenant, result)
    candidate_pairs = {link for chain in per_app_chain.values() for link in chain}
    if not candidate_pairs:
        return result

    grants = _live_grants(tenant, sorted(candidate_pairs))
    shares = _share_grants(tenant, per_app_chain)
    _prefetch_app_slugs(tenant, per_app_chain)

    for app in app_list:
        chain = per_app_chain.get(app.pk)
        if not chain:
            continue
        covering = [g for g in grants if g.covers(chain)]
        effective = result[app.pk]
        for grant in covering:
            effective.update(grant.role.permissions or ())
        for share in shares.get(app.pk, ()):
            effective |= _share_permissions(share)
        result[app.pk] = _abac_filter(tenant, effective, chain, covering)
    return result


def _prefetch_app_slugs(tenant: TenantContext, app_ids: Iterable[int]) -> None:
    """Load the slugs a policy on ``app_slug`` reads in one query, and only
    when the org has policies at all."""

    from astrolift_identity import abac
    from astrolift_registry.models import RegisteredApp

    cache = _cache()
    if cache is None or not abac.org_policies(
        tenant.organization_id, abac.attributes_for(tenant.actor_user_id)
    ):
        return
    for pk, slug in RegisteredApp.all_objects.filter(pk__in=list(app_ids)).values_list("pk", "slug"):
        cache.setdefault(("slug", "APP", pk), slug)


def _effective_scope_chain(
    tenant: TenantContext,
    *,
    extra_scope: tuple[str, int] | None,
) -> list[tuple[str, int]]:
    """Use the object gate's chain for permission projections too."""
    scope = (
        PermissionScope(kind=ScopeKind(extra_scope[0]), id=extra_scope[1])
        if extra_scope is not None
        else None
    )
    return _candidate_scopes(tenant, scope)
