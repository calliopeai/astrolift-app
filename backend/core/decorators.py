"""
Decorators that wire tenant scoping and permission checks into views and
GraphQL resolvers.

``@tenant_scoped`` ensures the current request has a resolved tenant
before the view body runs. It is the *minimum* gate — it asserts a tenant
context EXISTS but does NOT filter any queryset; resolvers must still add
an explicit ``organization_id=`` filter (see the decorator docstring).
Permission checks (``@require_permission``) come on top in P0.4.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from core.tenancy import get_current_tenant


class TenantRequired(Exception):
    """Raised when ``@tenant_scoped`` cannot resolve a tenant."""


def tenant_scoped(
    *,
    require_team: bool = False,
    require_project: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Wrap a view/resolver so it sees a resolved tenant context.

    Raises :class:`TenantRequired` (rather than returning a fake empty
    queryset) so callers can decide whether to translate to a 401, a
    GraphQL error, or a domain-specific response.

    This decorator does NOT filter querysets. It only guarantees a tenant
    context EXISTS; it does not scope any query written in the resolver
    body. Resolvers MUST add their own explicit ``organization_id=``
    constraint (and fail closed) — the presence of ``@tenant_scoped`` is
    not isolation. That false assumption is the #1183 leak class.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        import inspect

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            tenant = get_current_tenant()
            if tenant is None or tenant.organization_id is None:
                raise TenantRequired(f"{fn.__qualname__} requires a resolved tenant context")
            if require_team and tenant.team_id is None:
                raise TenantRequired(f"{fn.__qualname__} requires a team in the tenant context")
            if require_project and tenant.project_id is None:
                raise TenantRequired(f"{fn.__qualname__} requires a project in the tenant context")
            return fn(*args, **kwargs)

        wrapper.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
        return wrapper

    return decorator
