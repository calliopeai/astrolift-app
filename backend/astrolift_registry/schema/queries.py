"""Read-only queries for the registry app."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_registry.models import Container, RegisteredApp, Workload
from astrolift_registry.schema.types import (
    ContainerType,
    RegisteredAppType,
    WorkloadType,
    app_to_type,
    container_to_type,
    workload_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission


@strawberry.type
class RegistryQuery:
    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_apps(self, info: Info) -> list[RegisteredAppType]:
        qs = RegisteredApp.objects.select_related("organization", "team", "project").order_by("-created_at")[
            :200
        ]
        return [app_to_type(a) for a in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app(self, info: Info, slug: str) -> RegisteredAppType | None:
        app = (
            RegisteredApp.objects.select_related("organization", "team", "project").filter(slug=slug).first()
        )
        return app_to_type(app) if app else None

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_workloads(self, info: Info, app_slug: str | None = None) -> list[WorkloadType]:
        qs = Workload.objects.select_related("registered_app")
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        return [workload_to_type(w) for w in qs[:200]]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_containers(self, info: Info, workload_slug: str | None = None) -> list[ContainerType]:
        qs = Container.objects.select_related("workload")
        if workload_slug:
            qs = qs.filter(workload__slug=workload_slug)
        return [container_to_type(c) for c in qs[:500]]
