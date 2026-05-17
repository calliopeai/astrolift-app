"""
Astrolift permission catalog + resolver-entry decorator.

Permissions are **action verbs on resource types**, expressed as
``<resource>.<action>``. The catalog is **finite, enumerated, and
code-generated**: callers reference values from :class:`Permission`,
not strings — that way typos surface at import time, not at runtime.

The actual yes/no is delegated to a pluggable resolver
(``permission_resolver``) so this module doesn't depend on the RBAC
models that show up in P1.T2; until those land, a deny-by-default
resolver is used and tests can swap in their own.

See ``specs/03-multi-org-rbac.md`` §4.
"""

from __future__ import annotations

import enum
import functools
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from core.tenancy import TenantContext, get_current_tenant


class Permission(enum.StrEnum):
    # --- Organization ----------------------------------------------
    ORG_READ = "org.read"
    ORG_UPDATE = "org.update"
    ORG_DELETE = "org.delete"
    ORG_MANAGE_MEMBERS = "org.manage_members"

    # --- Team ------------------------------------------------------
    TEAM_READ = "team.read"
    TEAM_CREATE = "team.create"
    TEAM_UPDATE = "team.update"
    TEAM_DELETE = "team.delete"
    TEAM_MANAGE_MEMBERS = "team.manage_members"

    # --- Project ---------------------------------------------------
    PROJECT_READ = "project.read"
    PROJECT_CREATE = "project.create"
    PROJECT_UPDATE = "project.update"
    PROJECT_DELETE = "project.delete"

    # --- App -------------------------------------------------------
    APP_READ = "app.read"
    APP_CREATE = "app.create"
    APP_UPDATE = "app.update"
    APP_DELETE = "app.delete"
    APP_TRANSFER = "app.transfer"
    APP_DEPLOY = "app.deploy"
    APP_ROLLBACK = "app.rollback"
    APP_APPROVE_DEPLOY = "app.approve_deploy"
    APP_READ_LOGS = "app.read_logs"
    APP_READ_METRICS = "app.read_metrics"
    APP_EXEC_POD = "app.exec_pod"

    # --- Secrets ---------------------------------------------------
    SECRET_READ = "secret.read"
    SECRET_WRITE = "secret.write"
    SECRET_LIST = "secret.list"
    SECRET_APPROVE = "secret.approve"

    # --- Managed services -----------------------------------------
    MANAGED_SERVICE_CREATE = "managed_service.create"
    MANAGED_SERVICE_UPDATE = "managed_service.update"
    MANAGED_SERVICE_DESTROY = "managed_service.destroy"

    # --- Tokens ----------------------------------------------------
    DEPLOY_TOKEN_CREATE = "deploy_token.create"
    DEPLOY_TOKEN_ROTATE = "deploy_token.rotate"
    DEPLOY_TOKEN_REVOKE = "deploy_token.revoke"
    API_TOKEN_CREATE = "api_token.create"
    API_TOKEN_REVOKE = "api_token.revoke"

    # --- Webhooks --------------------------------------------------
    WEBHOOK_CREATE = "webhook.create"
    WEBHOOK_UPDATE = "webhook.update"
    WEBHOOK_DELETE = "webhook.delete"

    # --- Audit / Billing -------------------------------------------
    AUDIT_LOG_READ = "audit_log.read"
    AUDIT_LOG_EXPORT = "audit_log.export"
    BILLING_READ = "billing.read"
    BILLING_UPDATE = "billing.update"

    # --- Cluster / plugin -----------------------------------------
    CLUSTER_REGISTER = "cluster.register"
    CLUSTER_UPDATE = "cluster.update"
    CLUSTER_UNREGISTER = "cluster.unregister"
    CLUSTER_MANAGE = "cluster.manage"
    PROVIDER_PLUGIN_READ = "provider_plugin.read"
    PROVIDER_PLUGIN_CONFIGURE = "provider_plugin.configure"

    # --- SCM integration ------------------------------------------
    SCM_READ = "scm.read"
    SCM_CONNECT = "scm.connect"
    SCM_DISCONNECT = "scm.disconnect"
    SCM_KEY_CREATE = "scm.key_create"
    SCM_KEY_DELETE = "scm.key_delete"

    # --- Forms (form definitions + submissions) -------------------
    FORM_READ = "form.read"
    FORM_CREATE = "form.create"
    FORM_UPDATE = "form.update"
    FORM_DELETE = "form.delete"
    FORM_SUBMIT = "form.submit"
    FORM_MODERATE = "form.moderate"

    # --- Admin elevation ------------------------------------------
    ADMIN_ELEVATE = "admin.elevate"


class ScopeKind(enum.StrEnum):
    ORG = "ORG"
    TEAM = "TEAM"
    PROJECT = "PROJECT"
    APP = "APP"


@dataclass(frozen=True, slots=True)
class PermissionScope:
    kind: ScopeKind
    id: int


class PermissionDenied(Exception):
    def __init__(self, permission: Permission, scope: PermissionScope | None, reason: str = ""):
        self.permission = permission
        self.scope = scope
        self.reason = reason or "permission denied"
        super().__init__(f"{self.reason}: {permission.value}")


# ---- Resolver plug-point ---------------------------------------------

# A resolver answers the question "does this user have this permission
# on this scope?" given a TenantContext snapshot. It returns a tuple of
# (granted: bool, reason: str).
PermissionResolver = Callable[
    [TenantContext, Permission, PermissionScope | None],
    tuple[bool, str],
]


def _deny_all(_tenant, _permission, _scope) -> tuple[bool, str]:
    return False, "no permission resolver registered"


_resolver: PermissionResolver = _deny_all


def register_permission_resolver(resolver: PermissionResolver) -> None:
    global _resolver
    _resolver = resolver


def get_permission_resolver() -> PermissionResolver:
    return _resolver


def check_permission(
    permission: Permission,
    *,
    scope: PermissionScope | None = None,
) -> None:
    tenant = get_current_tenant() or TenantContext()
    granted, reason = _resolver(tenant, permission, scope)
    if not granted:
        raise PermissionDenied(permission, scope, reason)


# ---- Decorator -------------------------------------------------------


def require_permission(
    *permissions: Permission,
    scope: Callable[[Any], PermissionScope | None] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Resolver-entry permission gate.

    Usage on a Strawberry field resolver::

        @require_permission(Permission.APP_DEPLOY,
                            scope=lambda root, info, app_id: app_scope(app_id))
        def deploy_app(self, info, app_id: GUID): ...

    Multiple permissions in one call require *all* (logical AND).

    The check raises :class:`PermissionDenied`; mutation wrappers
    (``@mutation_audit``) translate that into the ``MutationResult``
    envelope so resolvers never need to catch it directly.
    """

    if not permissions:
        raise TypeError("require_permission needs at least one Permission")

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        import inspect

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            target_scope = scope(*args, **kwargs) if scope else None
            for perm in permissions:
                check_permission(perm, scope=target_scope)
            return fn(*args, **kwargs)

        # Strawberry resolver introspection follows __wrapped__ but
        # also reads __signature__ when present; set both so the
        # wrapper looks identical to the wrapped resolver.
        wrapper.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
        wrapper.__astrolift_permissions__ = tuple(permissions)
        return wrapper

    return decorator


def all_permissions() -> Iterable[Permission]:
    """Stable sorted iterator over every Permission value."""

    return tuple(sorted(Permission, key=lambda p: p.value))
