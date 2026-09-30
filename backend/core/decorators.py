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

    An async-generator resolver (a subscription) is checked when it is
    first iterated, once its WebSocket identity is pinned, and a missing
    tenant ends the stream without an event rather than raising, the way
    ``@require_permission`` refuses one.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        import inspect

        def missing() -> str:
            tenant = get_current_tenant()
            if tenant is None or tenant.organization_id is None:
                return f"{fn.__qualname__} requires a resolved tenant context"
            if require_team and tenant.team_id is None:
                return f"{fn.__qualname__} requires a team in the tenant context"
            if require_project and tenant.project_id is None:
                return f"{fn.__qualname__} requires a project in the tenant context"
            return ""

        if inspect.isasyncgenfunction(fn):

            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                if missing():
                    return
                # Close the inner generator when the subscriber goes away;
                # ``async for`` alone leaves it suspended.
                inner = fn(*args, **kwargs)
                try:
                    async for item in inner:
                        yield item
                finally:
                    await inner.aclose()

        else:

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                reason = missing()
                if reason:
                    raise TenantRequired(reason)
                return fn(*args, **kwargs)

        wrapper.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
        return wrapper

    return decorator
