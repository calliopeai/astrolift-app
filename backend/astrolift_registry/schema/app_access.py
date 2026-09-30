"""Who may enter an app behind central auth, over GraphQL (#2132).

The grant is enforced at the Envoy edge (``core.edge_access``). An app whose
``astrolift.toml`` declares ``[ingress.access]`` is managed there: the UI and
CLI show it and refuse to change it, so the repo stays the source of truth.
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import app_scope_by_slug
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
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
