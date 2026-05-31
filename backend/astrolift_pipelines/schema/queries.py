"""Read-only queries for astrolift_pipelines."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_pipelines.models import Pipeline, PipelineRun
from astrolift_pipelines.schema.types import (
    PipelineRunType,
    PipelineType,
    pipeline_run_to_type,
    pipeline_to_type,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class PipelinesQuery:
    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_pipelines(self, info: Info, limit: int = 100) -> list[PipelineType]:
        """All pipelines for the current tenant, most-recently-created first."""
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return []
        qs = (
            Pipeline.objects.filter(
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .prefetch_related("triggers")
            .order_by("name")
        )
        return [pipeline_to_type(p) for p in qs[: max(1, min(limit, 500))]]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_pipeline(self, info: Info, id: str) -> PipelineType | None:
        """Single pipeline by guid, scoped to the current tenant."""
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return None
        p = (
            Pipeline.objects.filter(
                guid=id,
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .prefetch_related("triggers")
            .first()
        )
        return pipeline_to_type(p) if p else None

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_pipeline_runs(
        self, info: Info, pipeline_id: str, limit: int = 50
    ) -> list[PipelineRunType]:
        """Pipeline runs for a given pipeline, most-recent first.

        Tenant scoping flows through the pipeline FK — only runs whose
        pipeline belongs to the current org are returned.
        """
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return []
        qs = (
            PipelineRun.objects.filter(
                pipeline__guid=pipeline_id,
                pipeline__organization_id=tenant.organization_id,
                pipeline__deleted_at__isnull=True,
                deleted_at__isnull=True,
            )
            .select_related("pipeline")
            .prefetch_related(
                "job_runs__job",
                "job_runs__step_runs__step",
            )
            .order_by("-run_number")
        )
        return [pipeline_run_to_type(pr) for pr in qs[: max(1, min(limit, 200))]]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_pipeline_run(self, info: Info, id: str) -> PipelineRunType | None:
        """Single pipeline run by guid, tenant-scoped via pipeline FK."""
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return None
        pr = (
            PipelineRun.objects.filter(
                guid=id,
                pipeline__organization_id=tenant.organization_id,
                pipeline__deleted_at__isnull=True,
                deleted_at__isnull=True,
            )
            .select_related("pipeline")
            .prefetch_related(
                "job_runs__job",
                "job_runs__step_runs__step",
            )
            .first()
        )
        return pipeline_run_to_type(pr) if pr else None
