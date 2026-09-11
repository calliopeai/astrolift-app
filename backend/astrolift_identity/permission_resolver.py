"""
Permission resolver backed by ``RoleBinding``.

Walks up App → Project → Team → Org for the current user, unioning
every binding's permissions. ABAC policies (T5) layer on top and can
only deny — they never grant beyond the RBAC result.

Registered with :func:`core.permissions.register_permission_resolver`
in ``apps.AstroliftIdentityConfig.ready``.
"""

from __future__ import annotations

from collections.abc import Iterable

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


def resolve(
    tenant: TenantContext,
    permission: Permission,
    scope: PermissionScope | None,
) -> tuple[bool, str]:
    if tenant.actor_user_id is None:
        return False, "no actor"

    # Django superusers bypass the RoleBinding chain. This matches the
    # convention every Django app inherits from auth.contrib and keeps
    # the bootstrap admin path simple: a fresh install just needs a
    # superuser, no role plumbing required for the first operator. The
    # RoleBinding chain is still the source of truth for every
    # non-superuser, including JIT'd OIDC users.
    if _is_superuser(tenant.actor_user_id):
        return True, "django superuser"

    # Lazy import — this resolver is registered before all model apps
    # are guaranteed to be ready in some import paths.
    from astrolift_identity.models import RoleBinding

    candidate_scopes = _candidate_scopes(tenant, scope)
    if not candidate_scopes:
        return False, "no scope"

    bindings = (
        RoleBinding.objects.select_related("role")
        .filter(
            user_id=tenant.actor_user_id,
        )
        .filter(
            _scope_filter(candidate_scopes),
        )
    )

    now = timezone.now()
    for binding in bindings:
        if binding.expires_at is not None and binding.expires_at <= now:
            continue
        if permission.value in (binding.role.permissions or ()):
            return True, f"role:{binding.role.slug}@{binding.scope_kind}:{binding.scope_id}"

    return False, "no role binding grants this permission"


def _org_confined_bindings(tenant: TenantContext) -> list:
    """The actor's live RoleBindings whose scope lies in the active org.

    One scan, shared by both any-scope readers below. Bindings are
    dropped if expired, and scope ids are confined to the active org so a
    TEAM binding in org A contributes nothing while org B is active --
    without that, cross-tenant ids would reach the row filters.
    """

    from astrolift_identity.models import Project, RoleBinding, Team
    from astrolift_registry.models import RegisteredApp

    org_id = tenant.organization_id
    now = timezone.now()
    live = [
        b
        for b in RoleBinding.objects.select_related("role").filter(user_id=tenant.actor_user_id)
        if b.expires_at is None or b.expires_at > now
    ]

    ids_by_kind: dict[str, set[int]] = {}
    for binding in live:
        ids_by_kind.setdefault(binding.scope_kind, set()).add(binding.scope_id)

    allowed: dict[str, set[int]] = {"ORG": {org_id} & ids_by_kind.get("ORG", set())}
    for kind, model in (("TEAM", Team), ("PROJECT", Project), ("APP", RegisteredApp)):
        ids = ids_by_kind.get(kind, set())
        allowed[kind] = (
            set(model.objects.filter(pk__in=ids, organization_id=org_id).values_list("pk", flat=True))
            if ids
            else set()
        )

    return [b for b in live if b.scope_id in allowed.get(b.scope_kind, set())]


def granted_scopes(tenant: TenantContext, permission: Permission) -> GrantedScopes:
    """RoleBinding-backed :data:`core.permissions.GrantedScopesProvider`.

    Every scope in the active org where ``tenant``'s actor holds
    ``permission``.
    """

    if tenant.actor_user_id is None:
        return NO_SCOPES
    if _is_superuser(tenant.actor_user_id):
        return ALL_SCOPES
    if tenant.organization_id is None:
        return NO_SCOPES

    by_kind: dict[str, set[int]] = {"ORG": set(), "TEAM": set(), "PROJECT": set(), "APP": set()}
    for binding in _org_confined_bindings(tenant):
        if permission.value in (binding.role.permissions or ()):
            by_kind[binding.scope_kind].add(binding.scope_id)

    if by_kind["ORG"]:
        return ALL_SCOPES
    return GrantedScopes(
        org=False,
        team_ids=frozenset(by_kind["TEAM"]),
        project_ids=frozenset(by_kind["PROJECT"]),
        app_ids=frozenset(by_kind["APP"]),
    )


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
    scoped check, which is what stops this from being a grant.
    """

    if tenant.actor_user_id is None:
        return set()
    if _is_superuser(tenant.actor_user_id):
        return {p.value for p in Permission}
    if tenant.organization_id is None:
        return set()

    effective: set[str] = set()
    for binding in _org_confined_bindings(tenant):
        effective.update(binding.role.permissions or ())
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
    if tenant.project_id is not None:
        out.append(("PROJECT", tenant.project_id))
    if tenant.team_id is not None:
        out.append(("TEAM", tenant.team_id))
    if tenant.organization_id is not None:
        out.append(("ORG", tenant.organization_id))
    # Stable, dedupe-preserving order.
    seen: set[tuple[str, int]] = set()
    return [s for s in out if not (s in seen or seen.add(s))]


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


def resolve_effective_permissions(
    tenant: TenantContext,
    *,
    extra_scope: tuple[str, int] | None = None,
) -> set[str]:
    """Return the full permission slug set the viewer holds at ``tenant``.

    Mirrors the resolution logic in :func:`resolve` but unions instead
    of short-circuiting on a single permission. The viewer's RoleBindings
    are filtered to active (non-expired, not soft-deleted) and matched
    against the explicit target's ancestry when ``extra_scope`` is set
    (typically ``("APP", app.pk)`` for per-app viewerPermissions), or the
    selected tenant context when no target is supplied.

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

    from astrolift_identity.models import RoleBinding

    candidate_scopes = _effective_scope_chain(tenant, extra_scope=extra_scope)
    if not candidate_scopes:
        return set()

    scope_ids_by_kind: dict[str, set[int]] = {}
    for kind, ident in candidate_scopes:
        scope_ids_by_kind.setdefault(kind, set()).add(ident)

    bindings = RoleBinding.objects.select_related("role").filter(
        user_id=tenant.actor_user_id,
        deleted_at__isnull=True,
        scope_kind__in=scope_ids_by_kind.keys(),
    )
    now = timezone.now()
    effective: set[str] = set()
    for binding in bindings:
        if binding.expires_at is not None and binding.expires_at <= now:
            continue
        if binding.scope_id not in scope_ids_by_kind.get(binding.scope_kind, set()):
            continue
        for slug in binding.role.permissions or ():
            effective.add(slug)
    return effective


def resolve_effective_permissions_for_apps(
    tenant: TenantContext,
    apps: Iterable,
) -> dict[int, set[str]]:
    """Bulk variant of :func:`resolve_effective_permissions` for many apps.

    Answers "what permission slugs does the viewer have on each app in
    ``apps``?" in **one** ``RoleBinding`` query plus one ancestry query
    and the superuser check, not one per app — the N+1 guard that backs
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

    from astrolift_identity.models import RoleBinding

    per_app_chain = _app_scope_chains(tenant, result)
    candidate_pairs = {link for chain in per_app_chain.values() for link in chain}
    if not candidate_pairs:
        return result

    scope_kinds = {k for k, _ in candidate_pairs}
    bindings = list(
        RoleBinding.objects.select_related("role").filter(
            user_id=tenant.actor_user_id,
            deleted_at__isnull=True,
            scope_kind__in=scope_kinds,
        )
    )
    if not bindings:
        return result

    now = timezone.now()
    # Bucket the viewer's active bindings by (scope_kind, scope_id) so
    # the per-app pass is a constant-time lookup per scope link.
    perms_by_scope: dict[tuple[str, int], set[str]] = {}
    for binding in bindings:
        if binding.expires_at is not None and binding.expires_at <= now:
            continue
        key = (binding.scope_kind, binding.scope_id)
        bucket = perms_by_scope.setdefault(key, set())
        for slug in binding.role.permissions or ():
            bucket.add(slug)

    for app in app_list:
        effective = result[app.pk]
        for link in per_app_chain.get(app.pk, ()):
            grants = perms_by_scope.get(link)
            if grants:
                effective.update(grants)
    return result


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
