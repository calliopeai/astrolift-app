"""Read-only queries for the lifecycle app."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_lifecycle.models import (
    AppEnvironment,
    Deployment,
    DeploymentLog,
    PreviewEnvironment,
)
from astrolift_lifecycle.schema.types import (
    AppEnvironmentType,
    DeploymentLogEntryType,
    DeploymentType,
    PreviewEnvironmentType,
    app_env_to_type,
    deployment_log_to_type,
    deployment_to_type,
    preview_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission


@strawberry.type
class LifecycleQuery:
    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_environments(
        self, info: Info, app_slug: str | None = None
    ) -> list[AppEnvironmentType]:
        qs = (
            AppEnvironment.objects.select_related(
                "registered_app", "tenant_cluster", "managed_domain"
            )
            .order_by("registered_app__slug", "name")
        )
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        return [app_env_to_type(e) for e in qs[:300]]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_deployments(
        self,
        info: Info,
        app_slug: str | None = None,
        environment_name: str | None = None,
        limit: int = 50,
    ) -> list[DeploymentType]:
        qs = (
            Deployment.objects.select_related(
                "registered_app", "app_environment", "workload"
            )
            .order_by("-created_at")
        )
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        if environment_name:
            qs = qs.filter(app_environment__name=environment_name)
        return [deployment_to_type(d) for d in qs[: max(1, min(limit, 200))]]

    @strawberry.field
    @require_permission(Permission.APP_READ_LOGS)
    @tenant_scoped()
    def astrolift_deployment_log(
        self, info: Info, deployment_id: str
    ) -> list[DeploymentLogEntryType]:
        # Look up the deployment by guid then return its log entries.
        deployment = Deployment.objects.filter(guid=deployment_id).first()
        if deployment is None:
            return []
        qs = DeploymentLog.objects.filter(deployment=deployment).order_by("occurred_at")
        return [deployment_log_to_type(e) for e in qs[:1000]]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_preview_environments(
        self, info: Info, app_slug: str | None = None
    ) -> list[PreviewEnvironmentType]:
        qs = PreviewEnvironment.objects.select_related("registered_app").order_by(
            "-created_at"
        )
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        return [preview_to_type(p) for p in qs[:200]]
