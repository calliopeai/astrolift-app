"""Scope resolvers for app-keyed permission gates (#1717).

``@require_permission(Permission.APP_READ)`` with no ``scope=`` checks
the caller's bindings against the tenant context alone, which on a
request that names one app means the org. An APP- or TEAM-scoped binding
on that very app can never match, so the whole sub-org role tier is
denied its own resources.

These factories turn the app the request names into the scope the check
runs against. They resolve inside the caller's org, so a slug from
another tenant yields ``None`` and the gate falls back to the (stricter)
org check rather than silently granting.
"""

from __future__ import annotations

from typing import Any

from core.permissions import PermissionScope, ScopeKind
from core.scope_args import read_arg
from core.tenancy import get_current_tenant


def _org_id() -> int | None:
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def _app_scope(**lookup: Any) -> PermissionScope | None:
    org_id = _org_id()
    if org_id is None:
        return None
    from astrolift_registry.models import RegisteredApp

    app_id = (
        RegisteredApp.objects.filter(organization_id=org_id, **lookup).values_list("pk", flat=True).first()
    )
    return PermissionScope(kind=ScopeKind.APP, id=app_id) if app_id else None


def app_scope_by_slug(field: str = "app_slug"):
    """Scope on the app named by ``field`` (a slug argument).

    ``field`` is a dotted path, so a mutation that carries its target on
    an input object passes ``"input.app_slug"``.
    """

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        slug = read_arg(args, field)
        return _app_scope(slug=slug) if slug else None

    return _scope


def app_scope_by_guid(field: str = "app_id"):
    """Scope on the app named by ``field`` (a GUID argument)."""

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        guid = read_arg(args, field)
        return _app_scope(guid=str(guid)) if guid else None

    return _scope


def app_scope_by_workload_guid(field: str = "workload_id"):
    """Scope on the app owning the workload named by ``field``.

    A workload is not a scope of its own -- bindings stop at APP -- so a
    workload-keyed gate checks the app it belongs to.
    """

    def _scope(args: dict[str, Any]) -> PermissionScope | None:
        guid = read_arg(args, field)
        if not guid:
            return None
        org_id = _org_id()
        if org_id is None:
            return None
        from astrolift_registry.models import Workload

        app_id = (
            Workload.objects.filter(guid=str(guid), registered_app__organization_id=org_id)
            .values_list("registered_app_id", flat=True)
            .first()
        )
        return PermissionScope(kind=ScopeKind.APP, id=app_id) if app_id else None

    return _scope
