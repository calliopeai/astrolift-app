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

import contextvars
import enum
import functools
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied

from core.tenancy import TenantContext, get_current_tenant

if TYPE_CHECKING:
    from astrolift_identity.operation_context import OperationContext


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
    # Who may enter an app behind central auth (#2132).
    APP_ACCESS = "app.access"

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
    # The users of the cluster's edge identity provider: list, create,
    # disable, delete, reset password, groups (#2131). Not ``cluster.manage``:
    # adding a login is a people decision, not an infrastructure one.
    CLUSTER_USERS = "cluster.users"
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

    # --- Zentinelle integration (#1887, #1888) --------------------
    # Connect / disconnect the org to a Zentinelle deployment and rotate
    # the credentials it issued.
    ZENTINELLE_CONNECT = "zentinelle.connect"
    # Register a cluster with Zentinelle and deploy / remove its gateway.
    ZENTINELLE_GATEWAY_MANAGE = "zentinelle.gateway_manage"
    # The rest of the #1888 RBAC table, declared ahead of the screens that
    # check them so the stock roles can carry their defaults (#1864). Each
    # notes the levels #1888 scopes it to; a stock role carries it only when
    # the role's own level is one of them.
    # Capability toggles and the Zentinelle settings held in Astrolift (org).
    ZENTINELLE_CONFIGURE = "zentinelle.configure"
    # Policies in effect (org, team, project).
    ZENTINELLE_POLICY_VIEW = "zentinelle.policy_view"
    ZENTINELLE_POLICY_EDIT = "zentinelle.policy_edit"
    # Model usage and cost (org, team, project).
    ZENTINELLE_USAGE_VIEW = "zentinelle.usage_view"
    # The evidence and audit stream (org, team, project).
    ZENTINELLE_AUDIT_VIEW = "zentinelle.audit_view"
    ZENTINELLE_AUDIT_EXPORT = "zentinelle.audit_export"
    # The health card and per-cluster gateway status (org).
    ZENTINELLE_STATUS_VIEW = "zentinelle.status_view"
    # Declared-versus-observed verdicts per agent, task and fleet (org, team, project).
    ZENTINELLE_CONFORMANCE_VIEW = "zentinelle.conformance_view"

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

    ``enabled`` says whether the module is switched on for the active org
    at all (#1859). The entity modules are always on; the per-org modules
    in :data:`ORG_MODULE_KEYS` are on only where an org admin turned them
    on and the install has not forced them off. It is independent of the
    ``can_*`` fields: a superuser can hold every capability of a module
    that is still off for the org.
    """

    key: str
    can_view: bool
    can_create: bool
    can_manage: bool
    can_run: bool
    enabled: bool


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

# Modules an org admin switches on per organization (#1859). The keys match
# ``astrolift_identity.models.OrganizationModule.Key``; the org-side state
# lives there because this module sits below the identity app.
ORG_MODULE_KEYS = ("chat_studio_integration", "agent_live_attach", "chat_studio_agent_runs")


def module_entitlements(
    perms: Iterable[str],
    *,
    is_superuser: bool = False,
    is_staff: bool = False,
    org_modules_enabled: Iterable[str] = (),
) -> list[ModuleEntitlement]:
    """The single source-of-truth mapping from permission slugs to the
    ``me.modules`` capability manifest (spec 34/36 §0.3).

    ``perms`` is the viewer's effective permission slug set for the
    active tenant (typically the result of
    :func:`astrolift_identity.permission_resolver.resolve_effective_permissions`).
    ``org_modules_enabled`` is the set of :data:`ORG_MODULE_KEYS` that are
    on for the active org (``astrolift_identity.org_modules.enabled_modules``).
    Returns one :class:`ModuleEntitlement` per module in a stable order:
    ``apps``, ``agents``, ``workflows``, ``admin``,
    ``chat_studio_integration``, ``agent_live_attach``, ``chat_studio_agent_runs``.

    The mapping table is fixed (do not re-derive capability anywhere
    else):

    ============================  ===============  =================  ============================  ================
    key                           can_view         can_create         can_manage                    can_run
    ============================  ===============  =================  ============================  ================
    ``apps``                      app.read         app.create         app.update | app.delete       app.deploy
    ``agents``                    agent.read       agent.create       agent.update | agent.delete   agent.dispatch
    ``workflows``                 workflow.read    workflow.create    workflow.update|.delete       workflow.trigger
    ``admin``                     any admin slug   cluster.register   same as can_view              (always false)
                                  OR staff/super   | org.manage_members
    ``chat_studio_integration``   app.read         app.create         app.update | app.delete       app.deploy
    ``agent_live_attach``         agent.read       (always false)     (always false)                agent_box.attach
    ``chat_studio_agent_runs``    agent.read       (always false)     (always false)                agent.dispatch
    ============================  ===============  =================  ============================  ================

    ``chat_studio_integration`` mirrors ``apps`` because shipping from
    Chat Studio creates and deploys apps. ``chat_studio_agent_runs`` mirrors
    ``agent_live_attach`` -- both are Chat Studio entry points onto the
    Agents module that never create or manage, only view + run -- it is
    the capability calliope-chat-studio#694's ``astrolift_capability(...,
    "chat_studio_agent_runs", can="run")`` reads. ``enabled`` is ``True``
    for the first four (install-wide) and, for the three per-org modules,
    whether the key is in ``org_modules_enabled``.

    One deliberate exception to "the mapping table is fixed": the
    ``AstroliftMe.modules`` resolver recomputes ``chat_studio_integration``'s
    ``can_create`` from :func:`granted_scopes` instead of taking the row
    returned here (#1919). The Builder API's create only ever checks
    ``app.create`` at a TEAM or the ORG scope, so the flat "held anywhere"
    ``perms`` set this function reads -- which also lights up for a bare
    project- or app-scoped grant -- cannot answer whether create will
    actually succeed.

    ``dashboard`` is always visible and is **not** returned here.

    Superuser short-circuits every ``can_*`` to ``true`` on every module,
    matching the bootstrap-admin bypass elsewhere in this module, but not
    ``enabled``: a superuser cannot use a module that is off for the org.
    The ``admin`` module additionally lights its view/manage capability
    for staff (``is_staff``) even without an explicit admin slug.
    """
    held = set(perms)
    org_on = set(org_modules_enabled)

    if is_superuser:
        return [
            ModuleEntitlement(
                key=key,
                can_view=True,
                can_create=True,
                can_manage=True,
                can_run=True,
                enabled=key not in ORG_MODULE_KEYS or key in org_on,
            )
            for key in ("apps", "agents", "workflows", "admin", *ORG_MODULE_KEYS)
        ]

    def has(slug: str) -> bool:
        return slug in held

    apps = ModuleEntitlement(
        key="apps",
        can_view=has("app.read"),
        can_create=has("app.create"),
        can_manage=has("app.update") or has("app.delete"),
        can_run=has("app.deploy"),
        enabled=True,
    )
    agents = ModuleEntitlement(
        key="agents",
        can_view=has("agent.read"),
        can_create=has("agent.create"),
        can_manage=has("agent.update") or has("agent.delete"),
        can_run=has("agent.dispatch"),
        enabled=True,
    )
    workflows = ModuleEntitlement(
        key="workflows",
        can_view=has("workflow.read"),
        can_create=has("workflow.create"),
        can_manage=has("workflow.update") or has("workflow.delete"),
        can_run=has("workflow.trigger"),
        enabled=True,
    )
    admin_view = is_staff or any(has(slug) for slug in _ADMIN_VIEW_SLUGS)
    admin = ModuleEntitlement(
        key="admin",
        can_view=admin_view,
        can_create=has("cluster.register") or has("org.manage_members"),
        can_manage=admin_view,
        can_run=False,
        enabled=True,
    )
    chat_studio_integration = ModuleEntitlement(
        key="chat_studio_integration",
        can_view=apps.can_view,
        can_create=apps.can_create,
        can_manage=apps.can_manage,
        can_run=apps.can_run,
        enabled="chat_studio_integration" in org_on,
    )
    agent_live_attach = ModuleEntitlement(
        key="agent_live_attach",
        can_view=has("agent.read"),
        can_create=False,
        can_manage=False,
        can_run=has("agent_box.attach"),
        enabled="agent_live_attach" in org_on,
    )
    chat_studio_agent_runs = ModuleEntitlement(
        key="chat_studio_agent_runs",
        can_view=has("agent.read"),
        can_create=False,
        can_manage=False,
        can_run=has("agent.dispatch"),
        enabled="chat_studio_agent_runs" in org_on,
    )
    return [
        apps,
        agents,
        workflows,
        admin,
        chat_studio_integration,
        agent_live_attach,
        chat_studio_agent_runs,
    ]


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


@dataclass(frozen=True, slots=True)
class GrantedScopes:
    """Where in the active org a caller holds one permission.

    :func:`check_permission` answers "may the caller do X *here*", which
    needs a *here*. A collection resolver has no *here* -- finding out
    which rows exist is the whole request -- and asking anyway collapses
    the candidate scopes to the org alone (#1717), so a TEAM-scoped
    grant can never satisfy a teams list and only an org-wide binding
    works.

    This is the dual: the scopes a permission is held *at*, so a list can
    gate on "held anywhere" and then narrow its rows to the scopes that
    actually cover them. ``org=True`` means the grant covers the whole
    org and the per-kind sets carry no further information.
    """

    org: bool
    team_ids: frozenset[int]
    project_ids: frozenset[int]
    app_ids: frozenset[int]
    # Scopes held by a non-inheriting grant (``RoleBinding.inherits=False``,
    # #2157): the team or project row itself, never its projects or apps.
    # A reader that ignores these under-grants, which is the safe side.
    exact_team_ids: frozenset[int] = frozenset()
    exact_project_ids: frozenset[int] = frozenset()

    def __bool__(self) -> bool:
        return (
            self.org
            or bool(self.team_ids)
            or bool(self.project_ids)
            or bool(self.app_ids)
            or bool(self.exact_team_ids)
            or bool(self.exact_project_ids)
        )


NO_SCOPES = GrantedScopes(org=False, team_ids=frozenset(), project_ids=frozenset(), app_ids=frozenset())
ALL_SCOPES = GrantedScopes(org=True, team_ids=frozenset(), project_ids=frozenset(), app_ids=frozenset())

# A granted-scopes provider answers the dual of the resolver question:
# not "may the caller act here" but "where may the caller act". It backs
# every ``any_scope=True`` gate and the row filters that pair with one.
GrantedScopesProvider = Callable[[TenantContext, Permission], GrantedScopes]


def scopes_from_resolver(tenant: TenantContext, permission: Permission) -> GrantedScopes:
    """Fallback provider: ask the scoped resolver with no scope.

    Reproduces exactly what an unscoped ``check_permission`` would have
    concluded, so an install (or a test) that plugs in only a resolver
    keeps its existing behaviour rather than silently losing rows.
    Public so a test swapping the resolver can pair it with this and
    keep the two plug-points answering from one place.
    """

    granted, _reason = _resolver(tenant, permission, None)
    return ALL_SCOPES if granted else NO_SCOPES


_scopes_provider: GrantedScopesProvider = scopes_from_resolver


def register_granted_scopes_provider(provider: GrantedScopesProvider) -> None:
    global _scopes_provider
    _scopes_provider = provider


def get_granted_scopes_provider() -> GrantedScopesProvider:
    return _scopes_provider


# An ``any_scope=True`` resolver asks this question twice: once at the
# gate, once again when it narrows its rows to the same scopes. The
# answer cannot change in between, so the decorator opens a memo for the
# duration of the call and both reads land on one lookup. Outside a
# gated call the var is ``None`` and every read hits the provider.
_scopes_memo: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "astrolift_granted_scopes_memo", default=None
)


def granted_scopes(tenant: TenantContext | None, permission: Permission) -> GrantedScopes:
    """Every scope in the active org where the caller holds ``permission``."""

    tenant = tenant or TenantContext()
    memo = _scopes_memo.get()
    if memo is None:
        return _scopes_provider(tenant, permission)
    key = (tenant.actor_user_id, tenant.organization_id, permission)
    if key not in memo:
        memo[key] = _scopes_provider(tenant, permission)
    return memo[key]


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


def check_permission_any_scope(permission: Permission) -> None:
    """Gate a collection resolver on holding ``permission`` at any scope.

    Weaker than :func:`check_permission` by construction: it accepts a
    binding on any team / project / app in the active org, not just an
    org-wide one. That is only safe when the resolver then narrows its
    rows to the scopes the caller's bindings actually cover -- see
    ``astrolift_identity.permission_resolver.granted_scopes``. Never use
    it to gate a resolver that reads or mutates one named object; that
    one has a scope, so pass it via ``scope=``.
    """

    tenant = get_current_tenant() or TenantContext()
    from astrolift_identity.api_tokens import (
        get_current_api_token,
        token_scope_allows_permission,
    )

    api_token = get_current_api_token()
    if api_token is not None and not token_scope_allows_permission(api_token, permission.value):
        raise PermissionDenied(permission, None, "api token scope does not allow this permission")
    if not granted_scopes(tenant, permission):
        raise PermissionDenied(permission, None, "no grant for this permission at any scope")


# ---- Decorator -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PermissionGate:
    """What one ``@require_permission`` layer checks, kept on its wrapper.

    ``scope`` is the factory that names the target, ``any_scope`` the
    collection form. Neither means the targetless check, where the
    selected team or project stands in for the target (#1743). The
    surface guardrail (#1866) reads this off every resolver.
    """

    permissions: tuple[Permission, ...]
    scope: Callable[[dict[str, Any]], PermissionScope | None] | None
    any_scope: bool


def require_permission(
    *permissions: Permission,
    scope: Callable[[dict[str, Any]], PermissionScope | None] | None = None,
    any_scope: bool = False,
    operation: Callable[[dict[str, Any]], Iterable[OperationContext]] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Resolver-entry permission gate.

    Usage on a Strawberry field resolver::

        @require_permission(Permission.APP_DEPLOY,
                            scope=app_scope_by_slug("app_slug"))
        def deploy_app(self, info, app_slug: str): ...

    ``scope`` receives the resolver's arguments already bound to its
    signature and defaults applied, so it can read them by name whether
    the caller passed them positionally or by keyword. Returning ``None``
    falls back to the plain tenant-context check.

    Multiple permissions in one call require *all* (logical AND).

    ``operation`` supplies authoritative ABAC facts for the targeted
    environment(s). Every target must allow every permission. One target's
    context stays bound through the resolver's nested checks; app-wide
    operations check all affected environments before any side effect.

    ``any_scope=True`` switches the gate to "holds this permission at
    any scope in the active org" -- the only correct gate for a
    collection resolver, which has no single target to check against.
    It is deliberately weaker than the scoped check, so a resolver using
    it MUST filter its rows down to the caller's granted scopes; see
    ``check_permission_any_scope``. Mutually exclusive with ``scope``.

    The check raises :class:`PermissionDenied`; mutation wrappers
    (``@mutation_audit``) translate that into the ``MutationResult``
    envelope so resolvers never need to catch it directly.

    An async-generator resolver (a subscription) is checked when it is
    first iterated, after the WebSocket identity is pinned, and a refusal
    ends the stream without an event: a subscription has no envelope to
    carry the error, and its contract is to complete silently.
    """

    if not permissions:
        raise TypeError("require_permission needs at least one Permission")
    if any_scope and scope is not None:
        raise TypeError("require_permission takes scope= or any_scope=, not both")

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        import inspect

        signature = inspect.signature(fn)

        from contextlib import contextmanager

        @contextmanager
        def authorization_context(context=None):
            memo = _scopes_memo.set({}) if any_scope or operation is not None else None
            try:
                if operation is None:
                    yield
                else:
                    from astrolift_identity.abac import operation_attributes

                    facts = (
                        context.attributes()
                        if context is not None
                        else {"environment": None, "region": None, "approvals": None}
                    )
                    with operation_attributes(**facts):
                        yield
            finally:
                if memo is not None:
                    _scopes_memo.reset(memo)

        def check(args, kwargs) -> None:
            if any_scope:
                for perm in permissions:
                    check_permission_any_scope(perm)
                return
            target_scope = None
            if scope is not None:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                target_scope = scope(bound.arguments)
            for perm in permissions:
                check_permission(perm, scope=target_scope)

        def admitted_context(args, kwargs):
            contexts = (None,)
            if operation is not None:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                contexts = tuple(operation(bound.arguments))
                if not contexts:
                    raise PermissionDenied(permissions[0], None, "operation target could not be resolved")
            for context in contexts:
                with authorization_context(context):
                    check(args, kwargs)
            return contexts[0] if len(contexts) == 1 else None

        if inspect.isasyncgenfunction(fn):

            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                from asgiref.sync import sync_to_async

                try:
                    context = await sync_to_async(admitted_context)(args, kwargs)
                except PermissionDenied:
                    return
                with authorization_context(context):
                    # Closing the outer subscription also closes the resource
                    # held by its inner generator, including on cancellation.
                    inner = fn(*args, **kwargs)
                    try:
                        async for item in inner:
                            yield item
                    finally:
                        await inner.aclose()

        elif inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                from asgiref.sync import sync_to_async

                context = await sync_to_async(admitted_context)(args, kwargs)
                with authorization_context(context):
                    return await fn(*args, **kwargs)

        else:

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                context = admitted_context(args, kwargs)
                with authorization_context(context):
                    return fn(*args, **kwargs)

        # Strawberry resolver introspection follows __wrapped__ but
        # also reads __signature__ when present; set both so the
        # wrapper looks identical to the wrapped resolver.
        wrapper.__signature__ = signature  # type: ignore[attr-defined]
        wrapper.__astrolift_permissions__ = tuple(permissions)
        wrapper.__astrolift_permission_gate__ = PermissionGate(  # type: ignore[attr-defined]
            permissions=tuple(permissions), scope=scope, any_scope=any_scope
        )
        return wrapper

    return decorator


@dataclass(frozen=True, slots=True)
class RouteAuth:
    """How a route outside GraphQL authorizes, declared on its view (#1866).

    A REST view or WebSocket relay has no resolver decorator to read, so it
    states its contract here: ``credential`` is what authenticates the
    caller, ``permissions`` what is then checked (empty when the credential
    alone is the grant, as for a machine key bound to one object), and
    ``scope`` the target the check runs against. The surface guardrail
    requires one on every route that is not on its allowlist. It records
    the contract and enforces nothing: the view's own checks, and the tests
    that pin them, are what hold it to what it says.
    """

    credential: str
    scope: str
    permissions: tuple[Permission, ...] = ()


def route_auth(
    *, credential: str, scope: str, permissions: Iterable[Permission] = ()
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare a route's :class:`RouteAuth` on its view.

    Apply it innermost, directly on the view function: Django's view
    decorators copy the function's attributes onto their wrappers, so the
    declaration stays readable from the callback the URL resolver holds.
    """

    declared = RouteAuth(credential=credential, scope=scope, permissions=tuple(permissions))

    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        view.__astrolift_route_auth__ = declared  # type: ignore[attr-defined]
        return view

    return decorator


def all_permissions() -> Iterable[Permission]:
    """Stable sorted iterator over every Permission value."""

    return tuple(sorted(Permission, key=lambda p: p.value))


# ---- Platform operator ------------------------------------------------


def is_platform_operator(user) -> bool:
    """Whether ``user`` is the install's platform operator: an active Django superuser.

    The resolver treats the same person as holding every permission at every
    scope. This is the authority for the legacy boilerworks surfaces that
    used to check Django model permissions (#1864): generic delete, editing
    another user's profile, Django group membership. They have no
    ``Permission`` of their own because they are not Astrolift features.
    Astrolift never grants Django model permissions, so a superuser was the
    only caller those checks admitted.
    """

    return bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and getattr(user, "is_superuser", False)
    )


def require_platform_operator(user) -> None:
    """Refuse anyone but the platform operator.

    Raises Django's ``PermissionDenied``, as the Django permission checks it
    replaces did, so callers and error handling see the same failure.

    A bearer token additionally needs the ``admin`` scope (#1949). These
    legacy surfaces (generic hard delete, editing another user's profile,
    Django group membership) have no ``Permission`` of their own, so
    ``check_permission``'s api-token ceiling never applies to them; without
    this, a read-only token minted for the operator (e.g. a mobile
    enrollment token) could still reach them. Session-authenticated callers
    carry no API token and are unaffected.
    """

    if not is_platform_operator(user):
        raise DjangoPermissionDenied("Only the platform operator may do this.")

    from astrolift_identity.api_tokens import SCOPE_ADMIN, get_current_api_token, has_scope

    api_token = get_current_api_token()
    if api_token is not None and not has_scope(api_token, SCOPE_ADMIN):
        raise DjangoPermissionDenied("Only the platform operator may do this.")


def check_platform_operator(user, *, gate: Permission) -> None:
    """Refuse anyone but the platform operator at a resolver gate.

    For operations whose reach is the whole install: install-wide settings,
    fleet-wide sweeps, rows that belong to no organization or to every one.
    A permission alone cannot gate those. The stock ``org_owner`` and
    ``org_admin`` roles carry the whole catalogue, ``admin.elevate``
    included, so every organization's admins hold any permission such a gate
    could name (#1978).

    Same test as :func:`require_platform_operator`, bearer admin scope
    included. It raises this module's :class:`PermissionDenied` naming
    ``gate``, the permission the resolver is declared with, so
    ``@mutation_audit`` answers PERMISSION_DENIED and records a DENY. Django's
    exception would surface there as an INTERNAL error.
    """

    try:
        require_platform_operator(user)
    except DjangoPermissionDenied as exc:
        raise PermissionDenied(gate, None, "only the platform operator may do this") from exc
