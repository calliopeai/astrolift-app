"""The grant ceiling: a caller hands out only permissions they hold (#1964).

``org.manage_members`` lets a caller grant roles. On its own it would let
them grant any role, ``org_owner`` included, to anyone, themselves too. So
every path that hands out access checks the permissions it would hand out
against the caller's own at the scope where they land: the caller's bindings
on that scope and its ancestors, the chain the permission resolver walks.
The platform operator holds everything everywhere and has no ceiling.

The role picker filters with the same ceiling, so what it offers and what the
mutations accept cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from django.contrib.auth import get_user_model

from astrolift_identity.permission_resolver import resolve_effective_permissions
from core.permissions import (
    Permission,
    PermissionDenied,
    PermissionScope,
    ScopeKind,
    is_platform_operator,
)
from core.tenancy import TenantContext, get_current_tenant

_CATALOG = frozenset(p.value for p in Permission)

REFUSAL = "you can only hand out permissions you hold at this scope"


@dataclass(frozen=True, slots=True)
class GrantCeiling:
    """What one caller may hand out at one scope."""

    unrestricted: bool
    held: frozenset[str]

    def allows(self, permissions: Iterable[str] | None) -> bool:
        return self.unrestricted or set(permissions or ()) <= self.held


def grant_ceiling(tenant: TenantContext, *, scope_kind: str, scope_id: int) -> GrantCeiling:
    """The ceiling for ``tenant``'s actor at one scope of the active org.

    A scope outside the active org has no ancestry there, so nothing is
    grantable on it.
    """

    actor = None
    if tenant.actor_user_id is not None:
        actor = get_user_model().objects.filter(pk=tenant.actor_user_id).first()
    if actor is None:
        return GrantCeiling(unrestricted=False, held=frozenset())
    if is_platform_operator(actor):
        return GrantCeiling(unrestricted=True, held=frozenset())
    # A slug that has left the catalogue grants nothing, so holding it must
    # not let a role carrying the same stale slug through.
    held = resolve_effective_permissions(tenant, extra_scope=(scope_kind, scope_id)) & _CATALOG
    return GrantCeiling(unrestricted=False, held=frozenset(held))


def require_grantable(
    permissions: Iterable[str] | None,
    *,
    scope_kind: str,
    scope_id: int,
    gate: Permission,
) -> None:
    """Refuse the current caller handing out ``permissions`` at the scope.

    Raises :class:`PermissionDenied` naming ``gate``, the permission the
    calling mutation is gated on, so ``@mutation_audit`` records the refusal
    as a DENY.
    """

    tenant = get_current_tenant() or TenantContext()
    if not grant_ceiling(tenant, scope_kind=scope_kind, scope_id=scope_id).allows(permissions):
        raise PermissionDenied(gate, PermissionScope(kind=ScopeKind(scope_kind), id=scope_id), REFUSAL)
