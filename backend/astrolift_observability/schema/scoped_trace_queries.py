"""Scoped trace envelopes with explicit source availability."""

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID
from astrolift_observability import scoped_traces
from astrolift_observability.schema.types import AppTracePage, TraceSpansResult
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import app_scope_by_slug, live_app_owners
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.schema.enums import ObservabilityPanelReason as Reason
from core.tenancy import get_current_tenant


def scoped_app(app_slug):
    return live_app_owners(
        RegisteredApp.objects.select_related("organization").filter(
            slug=app_slug,
            organization_id=get_current_tenant().organization_id,
            organization__deleted_at__isnull=True,
            deleted_at__isnull=True,
        )
    ).first()


@strawberry.type
class ScopedTracesQuery:
    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def astrolift_app_trace_page(
        self,
        info: Info,
        app_slug: str,
        since: str,
        until: str,
        environment_name: str | None = None,
        environment_id: GUID | None = None,
        service: str | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> AppTracePage:
        app = scoped_app(app_slug)
        if app is None:
            return AppTracePage(reason=Reason.NOT_CONFIGURED, items=[], scope=None)
        return scoped_traces.trace_page(
            app,
            environment_name=environment_name,
            environment_id=environment_id,
            since=since,
            until=until,
            service=service,
            status=status,
            limit=limit,
        )

    @strawberry.field
    @require_permission(
        Permission.APP_READ, scope=app_scope_by_slug("app_slug", permission=Permission.APP_READ)
    )
    @tenant_scoped()
    def astrolift_trace_spans_result(
        self,
        info: Info,
        app_slug: str,
        trace_id: str,
        since: str,
        until: str,
        environment_name: str | None = None,
        environment_id: GUID | None = None,
    ) -> TraceSpansResult:
        app = scoped_app(app_slug)
        if app is None:
            return TraceSpansResult(reason=Reason.NOT_CONFIGURED, items=[], scope=None)
        return scoped_traces.trace_spans(
            app,
            environment_name=environment_name,
            environment_id=environment_id,
            trace_id=trace_id,
            since=since,
            until=until,
        )
