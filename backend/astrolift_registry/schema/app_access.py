"""Who may enter an app behind central auth, over GraphQL (#2132).

The grant is enforced at the Envoy edge (``core.edge_access``). An app whose
``astrolift.toml`` declares ``[ingress.access]`` is managed there: the UI and
CLI show it and refuse to change it, so the repo stays the source of truth.
"""

from __future__ import annotations

import datetime as dt
import enum
from dataclasses import dataclass
from typing import Any

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import app_scope_by_slug
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, PermissionDenied, require_permission
from core.tenancy import get_current_tenant


@strawberry.type(name="AstroliftAppAccess")
class AppAccessType:
    app_slug: str
    groups: list[str]
    users: list[str]
    restricted: bool
    """False: every signed-in user of the cluster's identity provider may enter."""
    managed_by_manifest: bool
    enforced_on: list[str]
    """Slugs of the Envoy-edge clusters this app runs on, where the rule applies.
    On any other edge the app is gated by login only."""


@strawberry.type(name="AstroliftAppAccessPreview")
class AppAccessPreviewType:
    allowed: int | None
    total: int | None
    losing: list[str]
    """Emails that can enter today and could not under the proposed rule."""


@strawberry.input
class SetAppAccessInput:
    app_slug: str
    groups: list[str] = strawberry.field(default_factory=list)
    users: list[str] = strawberry.field(default_factory=list)


@strawberry.enum(name="AstroliftEdgeAccessStatus")
class EdgeAccessStatus(enum.Enum):
    """Whether the edge carries an environment's rule (#2534); ``core.edge_access``."""

    ENFORCED = "enforced"
    OPEN = "open"
    APPLYING = "applying"
    UNVERIFIED = "unverified"
    PENDING_DEPLOY = "pending_deploy"
    DRIFT = "drift"
    NOT_ON_EDGE = "not_on_edge"


@strawberry.type(name="AstroliftEdgeAccessRow")
class EdgeAccessRowType:
    """One environment of one app: who may enter, and whether the edge holds it."""

    id: str
    """``<app guid>:<environment guid>``."""
    app_slug: str
    app_name: str
    team_slug: str | None
    project_slug: str | None
    environment: str
    environment_id: str
    cluster_slug: str
    cluster_name: str
    hosts: list[str]
    """The hostnames the edge holds the rule for; empty until a deploy records them."""
    restricted: bool
    groups: list[str]
    users: list[str]
    source: str
    """``ui`` (the console or CLI), ``manifest`` (``[ingress.access]``), or empty when open."""
    set_by: str | None
    """Who last saved the rule in the console or CLI, from the audit log."""
    set_at: dt.datetime | None
    status: EdgeAccessStatus
    edge_changed_at: dt.datetime | None
    """When the cluster's recorded rules last changed."""
    edge_applied_at: dt.datetime | None
    """When the last edge apply that read the rules began; null when none is on record."""


@strawberry.input(name="AstroliftEdgeAccessFilter")
class EdgeAccessFilterInput:
    team: str | None = None
    project: str | None = None
    cluster: str | None = None
    status: list[EdgeAccessStatus] | None = None


@strawberry.type(name="AstroliftEdgeAccessPage")
class EdgeAccessPageType:
    items: list[EdgeAccessRowType]
    next_cursor: str | None
    total_count: int


@strawberry.type(name="AstroliftEdgeAccessPersonLogin")
class EdgeAccessPersonLoginType:
    """The person in one cluster's identity provider."""

    cluster_slug: str
    resolved: bool
    """False when the provider could not be read; ``reason`` says why."""
    reason: str
    has_login: bool | None
    enabled: bool | None
    groups: list[str]


@strawberry.type(name="AstroliftEdgeAccessPersonRow")
class EdgeAccessPersonRowType:
    rule: EdgeAccessRowType
    enters: bool | None
    """Null when it turns on groups or a login the provider could not be read for."""
    via: str
    """``user``, ``group`` or ``open``; ``unknown`` when groups could not be read."""
    via_groups: list[str]


@strawberry.type(name="AstroliftEdgeAccessPersonPage")
class EdgeAccessPersonPageType:
    email: str
    logins: list[EdgeAccessPersonLoginType]
    items: list[EdgeAccessPersonRowType]
    """The environments the person may enter, or may and it could not be told."""
    next_cursor: str | None
    total_count: int
    refused_count: int
    """Environments, under the same filters, that refuse the person."""


def _app(app_slug: str) -> RegisteredApp | None:
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if org_id is None:
        return None
    return RegisteredApp.objects.filter(
        organization_id=org_id, slug=app_slug, deleted_at__isnull=True
    ).first()


def _edge_clusters(app: RegisteredApp):
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.models import AppEnvironment

    ids = AppEnvironment.objects.filter(registered_app=app, deleted_at__isnull=True).values_list(
        "tenant_cluster_id", flat=True
    )
    return list(
        TenantCluster.objects.filter(
            pk__in=list(ids), ingress_class="envoy", deleted_at__isnull=True
        ).order_by("slug")
    )


def _to_type(app: RegisteredApp) -> AppAccessType:
    from core.edge_access import app_access, is_restricted

    access = app_access(app)
    return AppAccessType(
        app_slug=app.slug,
        groups=access["groups"],
        users=access["users"],
        restricted=is_restricted(access),
        managed_by_manifest=(app.edge_access or {}).get("source") == "manifest",
        enforced_on=[c.slug for c in _edge_clusters(app)],
    )


_EDGE_PAGE_DEFAULT = 25
_EDGE_PAGE_MAX = 200


@dataclass
class _EdgeRow:
    sort_key: tuple[str, str, str]
    row: EdgeAccessRowType
    grant: dict[str, list[str]]
    cluster: Any


def _edge_rows(filter: EdgeAccessFilterInput | None, search: str | None) -> list[_EdgeRow]:
    """Every environment of every app the caller may read, in this org only.

    The rows are computed, not stored, so filters past the database ones
    (status, and search, which matches hosts, users and groups too) run here
    over the org's environments; an org has hundreds, not millions.
    """
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.visibility import visible_registry_apps
    from core.app_deploy import namespace_for_environment
    from core.edge_access import app_access, environment_edge_state, is_restricted

    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if org_id is None:
        return []
    apps = visible_registry_apps(
        RegisteredApp.objects.filter(organization_id=org_id, deleted_at__isnull=True), Permission.APP_READ
    )
    envs = AppEnvironment.objects.filter(
        registered_app__in=apps,
        registered_app__organization_id=org_id,
        deleted_at__isnull=True,
    ).select_related("registered_app__organization", "registered_app__team", "registered_app__project")
    if filter is not None and filter.team:
        envs = envs.filter(registered_app__team__slug=filter.team)
    if filter is not None and filter.project:
        envs = envs.filter(registered_app__project__slug=filter.project)
    if filter is not None and filter.cluster:
        envs = envs.filter(tenant_cluster__slug=filter.cluster)
    envs = list(envs)
    # One read per cluster: each row carries the whole shared rule table.
    clusters = {c.pk: c for c in TenantCluster.objects.filter(pk__in={e.tenant_cluster_id for e in envs})}
    set_by = _last_set_by(org_id, [e.registered_app for e in envs])

    wanted = {s.value for s in filter.status} if filter is not None and filter.status else None
    term = (search or "").strip().lower()
    out: list[_EdgeRow] = []
    for env in envs:
        app = env.registered_app
        cluster = clusters.get(env.tenant_cluster_id)
        if cluster is None:
            continue
        status, hosts = environment_edge_state(app, cluster, namespace_for_environment(env))
        if wanted is not None and status not in wanted:
            continue
        grant = app_access(app)
        if term and not any(
            term in str(v).lower()
            for v in (app.slug, app.name, env.name, cluster.slug, *hosts, *grant["groups"], *grant["users"])
        ):
            continue
        source = (app.edge_access or {}).get("source") or ""
        who, when = set_by.get(app.pk, (None, None)) if source == "ui" else (None, None)
        out.append(
            _EdgeRow(
                sort_key=(app.slug, env.name, str(env.guid)),
                grant=grant,
                cluster=cluster,
                row=EdgeAccessRowType(
                    id=f"{app.guid}:{env.guid}",
                    app_slug=app.slug,
                    app_name=app.name,
                    team_slug=app.team.slug if app.team_id else None,
                    project_slug=app.project.slug if app.project_id else None,
                    environment=env.name,
                    environment_id=str(env.guid),
                    cluster_slug=cluster.slug,
                    cluster_name=cluster.name,
                    hosts=hosts,
                    restricted=is_restricted(grant),
                    groups=grant["groups"],
                    users=grant["users"],
                    source=source if is_restricted(grant) else "",
                    set_by=who,
                    set_at=when,
                    status=EdgeAccessStatus(status),
                    edge_changed_at=cluster.edge_access_changed_at,
                    edge_applied_at=cluster.edge_access_applied_at,
                ),
            )
        )
    out.sort(key=lambda r: r.sort_key)
    return out


def _last_set_by(org_id: int, apps: list[RegisteredApp]) -> dict[int, tuple[str | None, dt.datetime]]:
    """Who last saved each app's rule in the console or CLI, and when.

    ``setAppAccess`` names the app in its ``input`` variable; only this org's
    rows, and none older than the app (a slug can be reused).
    """
    from core.schema.audit import MutationAuditLog

    by_slug = {a.slug: a for a in apps if (a.edge_access or {}).get("source") == "ui"}
    if not by_slug:
        return {}
    out: dict[int, tuple[str | None, dt.datetime]] = {}
    logs = (
        MutationAuditLog.objects.filter(
            organization_id=org_id,
            operation="app.access.set",
            success=True,
            variables__input__appSlug__in=list(by_slug),
        )
        .select_related("user")
        .order_by("-timestamp", "-pk")
    )
    for log in logs:
        app = by_slug.get(str((log.variables.get("input") or {}).get("appSlug")))
        if app is None or app.pk in out or log.timestamp < app.created_at:
            continue
        user = log.user
        out[app.pk] = ((user.email or user.username) if user is not None else None, log.timestamp)
    return out


def _page_after(rows: list[Any], key, after: str | None, first: int) -> tuple[list[Any], str | None]:
    from astrolift_graphql.pagination import clamp_limit, decode_cursor, encode_cursor

    size = clamp_limit(first, default=_EDGE_PAGE_DEFAULT, maximum=_EDGE_PAGE_MAX)
    position = decode_cursor(after, arity=3) if after else None
    if position is not None:
        rows = [r for r in rows if key(r) > position]
    page = rows[:size]
    return page, encode_cursor(*key(page[-1])) if len(rows) > size else None


def _person_logins(
    info: Info, email: str, clusters: list[Any]
) -> dict[int, tuple[Any, EdgeAccessPersonLoginType]]:
    """The person in each cluster's identity provider, one read per cluster.

    Reading a provider is ``cluster.users``, as listing its users is; a
    shared cluster's provider holds every org's logins, so it is the
    platform operator's. Without that, groups stay unresolved.
    """
    from _sdk.identity_users import IdentityUsersError

    from astrolift_clusters.schema.auth_users import identity_users_driver
    from astrolift_clusters.schema.mutations import _require_operator_for_shared
    from core.permissions import PermissionScope, ScopeKind, check_permission

    tenant = get_current_tenant()
    tenant_org_id = tenant.organization_id if tenant else None
    out: dict[int, tuple[Any, EdgeAccessPersonLoginType]] = {}
    for cluster in clusters:

        def unresolved(reason: str, cluster=cluster) -> tuple[Any, EdgeAccessPersonLoginType]:
            return None, EdgeAccessPersonLoginType(
                cluster_slug=cluster.slug,
                resolved=False,
                reason=reason,
                has_login=None,
                enabled=None,
                groups=[],
            )

        if cluster.ingress_class != "envoy":
            continue
        try:
            org_id = cluster.organization_id or tenant_org_id or 0
            check_permission(Permission.CLUSTER_USERS, scope=PermissionScope(kind=ScopeKind.ORG, id=org_id))
            _require_operator_for_shared(info, cluster, Permission.CLUSTER_USERS)
        except PermissionDenied:
            out[cluster.pk] = unresolved("Reading this cluster's identity provider needs cluster.users.")
            continue
        driver, reason = identity_users_driver(cluster)
        if driver is None:
            out[cluster.pk] = unresolved(reason or "Astrolift cannot list this identity provider.")
            continue
        try:
            found = [u for u in driver.list_users(search=email, limit=60) if u.email.lower() == email]
        except IdentityUsersError as exc:
            out[cluster.pk] = unresolved(str(exc))
            continue
        person = found[0] if found else False
        out[cluster.pk] = (
            person,
            EdgeAccessPersonLoginType(
                cluster_slug=cluster.slug,
                resolved=True,
                reason="",
                has_login=bool(found),
                enabled=found[0].enabled if found else None,
                groups=sorted(found[0].groups) if found else [],
            ),
        )
    return out


@strawberry.type
class AppAccessQuery:
    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def astrolift_app_access(self, info: Info, app_slug: str) -> AppAccessType | None:
        app = _app(app_slug)
        return _to_type(app) if app is not None else None

    @strawberry.field
    @require_permission(
        Permission.APP_ACCESS, scope=app_scope_by_slug("app_slug", permission=Permission.APP_ACCESS)
    )
    @tenant_scoped()
    def astrolift_app_access_preview(
        self, info: Info, app_slug: str, groups: list[str], users: list[str]
    ) -> AppAccessPreviewType | None:
        """What a proposed rule would do, before it is saved."""
        from _sdk.identity_users import IdentityUsersError

        from core.edge_access import access_preview

        app = _app(app_slug)
        if app is None:
            return None
        clusters = _edge_clusters(app)
        if not clusters:
            return AppAccessPreviewType(allowed=None, total=None, losing=[])
        try:
            preview = access_preview(app, clusters[0], groups=groups, users=users)
        except IdentityUsersError:
            return AppAccessPreviewType(allowed=None, total=None, losing=[])
        return AppAccessPreviewType(**preview)

    @strawberry.field
    @require_permission(Permission.APP_READ, any_scope=True)
    @tenant_scoped()
    def astrolift_edge_access(
        self,
        info: Info,
        filter: EdgeAccessFilterInput | None = None,
        search: str | None = None,
        first: int = _EDGE_PAGE_DEFAULT,
        after: str | None = None,
    ) -> EdgeAccessPageType:
        """Who may enter each environment of every app the caller can read,
        and whether the edge holds it (#2534). Ordered by app, then environment.

        ``status`` is computed from what the control plane recorded, not read
        from the cluster: see ``core.edge_access.environment_edge_state``.
        """
        rows = _edge_rows(filter, search)
        page, next_cursor = _page_after(rows, lambda r: r.sort_key, after, first)
        return EdgeAccessPageType(items=[r.row for r in page], next_cursor=next_cursor, total_count=len(rows))

    @strawberry.field
    @require_permission(Permission.APP_READ, any_scope=True)
    @tenant_scoped()
    def astrolift_edge_access_for_person(
        self,
        info: Info,
        email: str,
        filter: EdgeAccessFilterInput | None = None,
        search: str | None = None,
        first: int = _EDGE_PAGE_DEFAULT,
        after: str | None = None,
    ) -> EdgeAccessPersonPageType:
        """The environments one person may enter, and through which grant (#2534).

        Their groups come from each cluster's identity provider; where it
        cannot be read, a group rule is answered ``enters: null``. ``me`` is
        the caller's own email.
        """
        from astrolift_clusters.schema.mutations import _caller
        from core.edge_access import person_reach

        email = email.strip().lower()
        if email == "me":
            email = str(getattr(_caller(info), "email", "") or "").lower()
        rows = _edge_rows(filter, search) if "@" in email else []
        clusters = list({r.cluster.pk: r.cluster for r in rows}.values())
        logins = _person_logins(info, email, clusters)
        reached: list[EdgeAccessPersonRowType] = []
        refused = 0
        for r in rows:
            if r.row.status == EdgeAccessStatus.NOT_ON_EDGE:
                continue
            person, _login = logins.get(r.cluster.pk, (None, None))
            enters, via, groups = person_reach(r.grant, email, person)
            if enters is False:
                refused += 1
                continue
            reached.append(EdgeAccessPersonRowType(rule=r.row, enters=enters, via=via, via_groups=groups))
        page, next_cursor = _page_after(
            reached,
            lambda p: (p.rule.app_slug, p.rule.environment, p.rule.environment_id),
            after,
            first,
        )
        return EdgeAccessPersonPageType(
            email=email,
            logins=[login for _person, login in logins.values()],
            items=page,
            next_cursor=next_cursor,
            total_count=len(reached),
            refused_count=refused,
        )


@strawberry.type
class AppAccessMutation:
    @strawberry.field
    @mutation_audit(action="app.access.set")
    @require_permission(
        Permission.APP_ACCESS, scope=app_scope_by_slug("input.app_slug", permission=Permission.APP_ACCESS)
    )
    @tenant_scoped()
    def set_app_access(self, info: Info, input: SetAppAccessInput) -> MutationResultType[AppAccessType]:
        from core.edge_access import set_app_access

        app = _app(input.app_slug)
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appSlug")
        if (app.edge_access or {}).get("source") == "manifest":
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "this app's access is set by [ingress.access] in its astrolift.toml; change it there",
            )
        if any("@" not in u for u in input.users):
            return gql_failure(ErrorCode.VALIDATION.value, "users must be email addresses", field="users")
        set_app_access(app, groups=input.groups, users=input.users, source="ui")
        app.refresh_from_db()
        return gql_success(_to_type(app))
