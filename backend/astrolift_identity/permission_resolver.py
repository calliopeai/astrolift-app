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

from core.permissions import Permission, PermissionScope
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


def _candidate_scopes(
    tenant: TenantContext,
    scope: PermissionScope | None,
) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    if scope is not None:
        out.append((scope.kind.value, scope.id))
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
# the apps-list path with one DB hit regardless of list size.


def resolve_effective_permissions(
    tenant: TenantContext,
    *,
    extra_scope: tuple[str, int] | None = None,
) -> set[str]:
    """Return the full permission slug set the viewer holds at ``tenant``.

    Mirrors the resolution logic in :func:`resolve` but unions instead
    of short-circuiting on a single permission. The viewer's RoleBindings
    are filtered to active (non-expired, not soft-deleted) and matched
    against the scope chain projected from ``tenant`` plus an optional
    ``extra_scope`` override — typically ``("APP", app.pk)`` for the
    per-app viewerPermissions field.

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
    ``apps``?" in **one** ``RoleBinding`` query plus the superuser check,
    not one per app — the N+1 guard that backs the ``viewerPermissions``
    field on the apps-list resolvers (``astroliftApps`` /
    ``astroliftMyApps``).

    Returns a dict keyed by ``RegisteredApp.pk``. Apps the viewer has no
    bindings for still appear in the dict with an empty set, so callers
    can map results back uniformly without a missing-key dance.

    The scope chain for each app is computed from the app's own row
    (``organization_id`` / ``team_id`` / ``project_id``) so the result
    is correct regardless of the active tenant's project/team picks —
    important for the list path where the active tenant may be at org
    scope but the apps span multiple teams.
    """
    app_list = list(apps)
    result: dict[int, set[str]] = {a.pk: set() for a in app_list}
    if not app_list or tenant.actor_user_id is None:
        return result

    if _is_superuser(tenant.actor_user_id):
        full = {p.value for p in Permission}
        return {pk: set(full) for pk in result}

    from astrolift_identity.models import RoleBinding

    # Project every (scope_kind, scope_id) the viewer could conceivably
    # match against. ORG covers everything in the org; per-app TEAM /
    # PROJECT / APP add fan-out from the app row itself.
    candidate_pairs: set[tuple[str, int]] = set()
    per_app_chain: dict[int, list[tuple[str, int]]] = {}
    for app in app_list:
        chain: list[tuple[str, int]] = [("APP", app.pk)]
        if app.project_id is not None:
            chain.append(("PROJECT", app.project_id))
        if app.team_id is not None:
            chain.append(("TEAM", app.team_id))
        if app.organization_id is not None:
            chain.append(("ORG", app.organization_id))
        per_app_chain[app.pk] = chain
        candidate_pairs.update(chain)

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
    """Build the (scope_kind, scope_id) chain we'll union bindings over.

    Order doesn't matter for the union (a binding either matches a chain
    link or it doesn't), but stable dedupe makes the helper easier to
    reason about under repeated calls with overlapping scopes.
    """
    out: list[tuple[str, int]] = []
    if extra_scope is not None:
        out.append(extra_scope)
    if tenant.project_id is not None:
        out.append(("PROJECT", tenant.project_id))
    if tenant.team_id is not None:
        out.append(("TEAM", tenant.team_id))
    if tenant.organization_id is not None:
        out.append(("ORG", tenant.organization_id))
    seen: set[tuple[str, int]] = set()
    return [s for s in out if not (s in seen or seen.add(s))]
