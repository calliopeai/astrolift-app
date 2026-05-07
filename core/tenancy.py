"""
Request-scoped tenant context.

The control plane is multi-tenant. Every business query must be filtered
to the current request's organization. We keep the current tenant in a
context-local so the ORM managers can pick it up without each call site
threading the org through manually.

This module exposes:

* ``TenantContext`` — typed snapshot of the current tenant.
* ``set_current_tenant`` / ``get_current_tenant`` / ``clear_current_tenant``
  — primitives the middleware uses.
* ``tenant_context`` — context manager for tests and background workers.

The middleware that populates this lives in ``core.middleware.tenant``.
"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
from typing import Iterator


@dataclasses.dataclass(frozen=True, slots=True)
class TenantContext:
    organization_id: int | None = None
    team_id: int | None = None
    project_id: int | None = None
    actor_user_id: int | None = None


_current: contextvars.ContextVar[TenantContext | None] = contextvars.ContextVar(
    "astrolift_tenant_context", default=None
)


def set_current_tenant(tenant: TenantContext | None) -> contextvars.Token:
    return _current.set(tenant)


def get_current_tenant() -> TenantContext | None:
    return _current.get()


def clear_current_tenant(token: contextvars.Token | None = None) -> None:
    if token is not None:
        _current.reset(token)
    else:
        _current.set(None)


@contextlib.contextmanager
def tenant_context(tenant: TenantContext | None) -> Iterator[None]:
    token = _current.set(tenant)
    try:
        yield
    finally:
        _current.reset(token)
