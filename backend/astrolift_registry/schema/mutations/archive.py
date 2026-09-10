"""ArchiveMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_registry.schema.mutations.types import (
    ArchiveAppInput,
    RestoreAppInput,
)
from astrolift_registry.schema.types import (
    RegisteredAppType,
    app_to_type,
)
from astrolift_registry.scopes import app_scope_by_slug
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class ArchiveMutations:
    @strawberry.mutation
    @mutation_audit(action="app.archive")
    @require_permission(Permission.APP_UPDATE, scope=app_scope_by_slug("input.app_slug"))
    @tenant_scoped()
    def archive_app(self, info: Info, input: ArchiveAppInput) -> MutationResultType[RegisteredAppType]:
        """Scale all workloads to zero and suppress deploys. Idempotent."""
        tenant = get_current_tenant()
        app = RegisteredApp.objects.filter(
            organization_id=tenant.organization_id,
            slug=input.app_slug,
            deleted_at__isnull=True,
        ).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appSlug")

        if app.archived_at is not None:
            return gql_success(app_to_type(app))

        actor = info.context.request.user
        app.archived_at = timezone.now()
        app.archived_by = actor
        app.updated_by = actor
        app.save(update_fields=["archived_at", "archived_by", "updated_by", "updated_at", "version"])

        workloads = Workload.objects.filter(registered_app=app, deleted_at__isnull=True)
        for wl in workloads:
            wl.pre_archive_replicas = wl.replicas
            wl.replicas = 0
            wl.updated_by = actor
            wl.save(update_fields=["replicas", "pre_archive_replicas", "updated_by", "updated_at", "version"])

        return gql_success(app_to_type(app))

    @strawberry.mutation
    @mutation_audit(action="app.restore")
    @require_permission(Permission.APP_UPDATE, scope=app_scope_by_slug("input.app_slug"))
    @tenant_scoped()
    def restore_app(self, info: Info, input: RestoreAppInput) -> MutationResultType[RegisteredAppType]:
        """Restore archived app: un-archive and return workloads to pre-archive replicas."""
        tenant = get_current_tenant()
        app = RegisteredApp.objects.filter(
            organization_id=tenant.organization_id,
            slug=input.app_slug,
            deleted_at__isnull=True,
        ).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appSlug")

        if app.archived_at is None:
            return gql_success(app_to_type(app))

        actor = info.context.request.user
        app.archived_at = None
        app.archived_by = None
        app.updated_by = actor
        app.save(update_fields=["archived_at", "archived_by", "updated_by", "updated_at", "version"])

        workloads = Workload.objects.filter(
            registered_app=app,
            deleted_at__isnull=True,
            pre_archive_replicas__isnull=False,
        )
        for wl in workloads:
            wl.replicas = wl.pre_archive_replicas
            wl.pre_archive_replicas = None
            wl.updated_by = actor
            wl.save(update_fields=["replicas", "pre_archive_replicas", "updated_by", "updated_at", "version"])

        return gql_success(app_to_type(app))
