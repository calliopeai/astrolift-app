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

    # Take an existing cloud resource the platform cannot prove it owns and
    # stamp the platform's identity onto it (#1365). Its own grant, not
    # ``managed_service.create``: provisioning creates a resource whose whole
    # history the platform knows, while adoption reaches into a resource
    # somebody else built -- possibly another Astrolift managed service --
    # and asserts ownership of it. Following the precedent ``agent_box.attach``
    # set, a distinct capability gets a distinct grant. Seeded only to the
    # roles holding the full enum (``org_owner``, ``org_admin``): the blast
    # radius of a wrong adoption is a live resource pointed at the wrong
    # service, so it stays above the app-deploy tier until an install asks
    # otherwise via a custom role.
    MANAGED_SERVICE_ADOPT = "managed_service.adopt"

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
    APP_LOG_EXPORT = "app.log_export"
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

    # --- Agent skills + tool defs (skill registry) ----------------
    SKILL_READ = "skill.read"  # list/read Skill + ToolDef catalog
    SKILL_WRITE = "skill.write"  # create/update/delete org skills + tool defs
    SKILL_IMPORT = "skill.import"  # import skills/tools from a github config repo

    # --- Agent environment specs (agent platform foundation) ------
    # CRUD on the reusable, org-scoped container-environment recipe an
    # agent task launches into (image, runtime, tool preset, secret
    # refs). Scoped per-org; no platform-shared rows.
    AGENT_ENV_SPEC_READ = "agent_env_spec.read"
    AGENT_ENV_SPEC_CREATE = "agent_env_spec.create"
    AGENT_ENV_SPEC_UPDATE = "agent_env_spec.update"
    AGENT_ENV_SPEC_DELETE = "agent_env_spec.delete"

    # --- Agent tasks (#877) ---------------------------------------
    # Operator-grade live access into a RUNNING agent task's pod via
    # the noVNC relay (the GUI equivalent of ``app.exec_pod``).
    # Deny-by-default; granted to org owner/admin via the system-role
    # comprehensions over the full enum.
    AGENT_TASK_WATCH = "agent_task.watch"
    # Queue a follow-up prompt into a RUNNING agent task's next turn
    # (#1390 — the steering channel). Operator-grade and a *write* into a
    # live agent, so it is deliberately separate from the passive
    # ``agent_task.watch`` and from ``agent.dispatch`` (which authorizes
    # starting a run, not steering one mid-flight). Deny-by-default;
    # granted to org owner/admin only, via the system-role comprehensions
    # over the full enum — no granular role carries it.
    AGENT_TASK_SEND_INPUT = "agent_task.send_input"

    # --- Agent boxes (#129) ---------------------------------------
    # Open an interactive session inside a running ``AgentBox`` pod
    # through the exec relay. Its own grant rather than ``app.exec_pod``
    # for the same reason ``agent_task.watch`` is not ``app.exec_pod``:
    # a box is an agent, not an app, and an install must be able to let
    # a role reach the agents it may start without also handing it a
    # shell in every application pod on the cluster. Seeded alongside
    # ``agent.dispatch`` so "may start a box" implies "may reach it".
    AGENT_BOX_ATTACH = "agent_box.attach"

    # --- Agent dispatch (spec 33, PR-1) ---------------------------
    # Dispatch a run of a registered agent ``Workload(kind=agent)`` —
    # the ``runAstroliftAgent`` mutation (Once-mode in PR-1; later modes
    # gate on the same grant). Distinct from ``app.deploy`` so an org can
    # let a role run agents without granting full app-deploy rights;
    # granted alongside the deploy ops on the same roles for now (see
    # ``astrolift_identity.system_roles._DEPLOY_OPS``).
    AGENT_DISPATCH = "agent.dispatch"

    # --- Agents module (spec 34/36 Phase 0) -----------------------
    # Standalone CRUD verbs for the Agents entity module so an org can
    # grant the Agents surface mix-and-match, independent of ``app.*``.
    # The agent-instance (workload/run) read resolvers re-gate from
    # ``app.read``/``app.read_logs`` to ``AGENT_READ``; ``registerAgentRepo``
    # re-gates from ``app.create`` to ``AGENT_CREATE``. Dispatch stays on
    # the dedicated ``AGENT_DISPATCH`` grant above.
    AGENT_READ = "agent.read"
    AGENT_CREATE = "agent.create"
    AGENT_UPDATE = "agent.update"
    AGENT_DELETE = "agent.delete"

    # --- Workflows module (spec 34/36 Phase 0) --------------------
    # Standalone verbs for the Workflows entity module. Phase 0 ships
    # the perms + role grants + the ``workflows`` entry in ``me.modules``
    # only; the workflow resolvers are consolidated + RBAC-gated in
    # Phase 3 (the definition CRUD currently lives staff-gated in the
    # un-prefixed ``workflows`` app).
    WORKFLOW_READ = "workflow.read"
    WORKFLOW_CREATE = "workflow.create"
    WORKFLOW_UPDATE = "workflow.update"
    WORKFLOW_DELETE = "workflow.delete"
    WORKFLOW_TRIGGER = "workflow.trigger"

    # --- Pipelines (#86) ------------------------------------------
    PIPELINE_READ = "pipeline.read"
    PIPELINE_CREATE = "pipeline.create"
    PIPELINE_UPDATE = "pipeline.update"
    PIPELINE_DELETE = "pipeline.delete"
    PIPELINE_TRIGGER = "pipeline.trigger"  # dispatch a manual run
    PIPELINE_CANCEL = "pipeline.cancel"  # cancel a running run
    PIPELINE_SECRET_MANAGE = "pipeline.secret_manage"  # add/rotate pipeline secrets

    # --- Admin elevation ------------------------------------------
    ADMIN_ELEVATE = "admin.elevate"


@dataclass(frozen=True, slots=True)
class ModuleEntitlement:
    """One row of the ``me.modules`` capability manifest (spec 34/36 §0.3).

    A coarse, server-computed view of what the viewer can do with an
    entity *module* (apps / agents / workflows / admin), derived from
    the fine-grained permission slugs. The frontend shell reads these to
    decide which top-level modules to show and which in-module actions to
    surface — it never re-derives capability from raw slugs.

    ``dashboard`` is intentionally NOT a module here: the shell always
    renders it, so it carries no entitlement row.
    """

    key: str
    can_view: bool
    can_create: bool
    can_manage: bool
    can_run: bool


# Permission slugs the ``admin`` module's view/manage capability keys off
# (spec 34/36 §0.3). Any one of these (or is_staff / superuser) lights up
# the Admin module. Kept as a module-level constant so the mapping has a
# single, greppable source.
_ADMIN_VIEW_SLUGS = (
    "cluster.register",
    "cluster.manage",
    "org.manage_members",
    "billing.read",
    "audit_log.read",
)


def module_entitlements(
    perms: Iterable[str],
    *,
    is_superuser: bool = False,
    is_staff: bool = False,
) -> list[ModuleEntitlement]:
    """The single source-of-truth mapping from permission slugs to the
    ``me.modules`` capability manifest (spec 34/36 §0.3).

    ``perms`` is the viewer's effective permission slug set for the
    active tenant (typically the result of
    :func:`astrolift_identity.permission_resolver.resolve_effective_permissions`).
    Returns one :class:`ModuleEntitlement` per entity module in a stable
    order: ``apps``, ``agents``, ``workflows``, ``admin``.

    The mapping table is fixed (do not re-derive capability anywhere
    else):

    ===========  ===============  =================  ============================  ================
    key          can_view         can_create         can_manage                    can_run
    ===========  ===============  =================  ============================  ================
    ``apps``     app.read         app.create         app.update | app.delete       app.deploy
    ``agents``   agent.read       agent.create       agent.update | agent.delete   agent.dispatch
    ``workflows``workflow.read    workflow.create    workflow.update|.delete       workflow.trigger
    ``admin``    any admin slug   cluster.register   same as can_view              (always false)
                 OR staff/super   | org.manage_members
    ===========  ===============  =================  ============================  ================

    ``dashboard`` is always visible and is **not** returned here.

    Superuser short-circuits to every capability ``true`` on every
    module, matching the bootstrap-admin bypass elsewhere in this module.
    The ``admin`` module additionally lights its view/manage capability
    for staff (``is_staff``) even without an explicit admin slug.
    """
    held = set(perms)

    if is_superuser:
        return [
            ModuleEntitlement(key=key, can_view=True, can_create=True, can_manage=True, can_run=True)
            for key in ("apps", "agents", "workflows", "admin")
        ]

    def has(slug: str) -> bool:
        return slug in held

    apps = ModuleEntitlement(
        key="apps",
        can_view=has("app.read"),
        can_create=has("app.create"),
        can_manage=has("app.update") or has("app.delete"),
        can_run=has("app.deploy"),
    )
    agents = ModuleEntitlement(
        key="agents",
        can_view=has("agent.read"),
        can_create=has("agent.create"),
        can_manage=has("agent.update") or has("agent.delete"),
        can_run=has("agent.dispatch"),
    )
    workflows = ModuleEntitlement(
        key="workflows",
        can_view=has("workflow.read"),
        can_create=has("workflow.create"),
        can_manage=has("workflow.update") or has("workflow.delete"),
        can_run=has("workflow.trigger"),
    )
    admin_view = is_staff or any(has(slug) for slug in _ADMIN_VIEW_SLUGS)
    admin = ModuleEntitlement(
        key="admin",
        can_view=admin_view,
        can_create=has("cluster.register") or has("org.manage_members"),
        can_manage=admin_view,
        can_run=False,
    )
    return [apps, agents, workflows, admin]


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
    # API bearer scopes are a ceiling over the user's normal RBAC grants.
    # Session-authenticated callers have no current API token and are
    # unaffected. Imported lazily to keep this core module identity-agnostic
    # during Django app initialization.
    from astrolift_identity.api_tokens import (
        get_current_api_token,
        token_scope_allows_permission,
    )

    api_token = get_current_api_token()
    if api_token is not None and not token_scope_allows_permission(api_token, permission.value):
        raise PermissionDenied(permission, scope, "api token scope does not allow this permission")
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
