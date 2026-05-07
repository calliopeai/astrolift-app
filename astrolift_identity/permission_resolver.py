"""
Permission resolver backed by ``RoleBinding``.

Walks up App → Project → Team → Org for the current user, unioning
every binding's permissions. ABAC policies (T5) layer on top and can
only deny — they never grant beyond the RBAC result.

Registered with :func:`core.permissions.register_permission_resolver`
in ``apps.AstroliftIdentityConfig.ready``.
"""

from __future__ import annotations

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
