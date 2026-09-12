"""Read-only queries for astrolift_pipelines."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import PageType, keyset_page, search_q
from astrolift_identity.scope_visibility import visible_apps
from astrolift_pipelines.models import Pipeline, PipelineRun
from astrolift_pipelines.schema.types import (
    PipelineRunType,
    PipelineType,
    pipeline_run_to_type,
    pipeline_to_type,
)
from astrolift_pipelines.scopes import pipeline_app_scope, pipeline_run_app_scope
from astrolift_registry.models import RegisteredApp
from core.decorators import tenant_scoped
from core.permissions import Permission, granted_scopes, require_permission
from core.tenancy import get_current_tenant


def _pipelines_qs(*, search: str | None = None):
    """Filtered, unordered pipeline catalogue for the caller's org.

    Shared by the list field and its paginated sibling so the two can
    never disagree about what a pipeline row is. Ordering is
    deliberately not applied here — ``keyset_page`` imposes it from
    the seek key.
    """
    # Org-scope to the caller's tenant. Pipeline carries a direct
    # organization FK but its manager is not tenant-aware, and
    # @tenant_scoped only asserts a tenant exists — it does not
    # filter. ``organization_id=None`` matches no rows (the column is
    # NOT NULL), so this fails closed (#1183).
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    qs = Pipeline.objects.filter(
        organization_id=org_id,
        deleted_at__isnull=True,
    ).prefetch_related("triggers")
    scopes = granted_scopes(tenant, Permission.APP_READ)
    if not scopes.org:
        visible_app_ids = visible_apps(
            RegisteredApp.objects.filter(organization_id=org_id, deleted_at__isnull=True),
            Permission.APP_READ,
        ).values_list("id", flat=True)
        qs = qs.filter(registered_app_id__in=visible_app_ids)
    if search:
        # ``registered_app`` is a nullable many-to-one, so OR-ing over
        # it widens the join without duplicating rows (which would
        # corrupt both the page and total_count).
        qs = qs.filter(
            search_q(
                search,
                "name",
                "repo_url",
                "registered_app__slug",
                "registered_app__name",
            )
        )
    return qs


def _pipeline_runs_qs(*, pipeline_id: str, search: str | None = None):
    """Filtered, unordered run stream for one pipeline.

    Tenant scoping flows through the pipeline FK — only runs whose
    pipeline belongs to the current org are returned, and an org id of
    ``None`` matches nothing (the column is NOT NULL). Shared by the
    list field and its paginated sibling; ordering stays out so
    ``keyset_page`` can impose it from the seek key.
    """
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    qs = (
        PipelineRun.objects.filter(
            pipeline__guid=pipeline_id,
            pipeline__organization_id=org_id,
            pipeline__deleted_at__isnull=True,
            deleted_at__isnull=True,
        )
        .select_related("pipeline")
        .prefetch_related(
            "job_runs__job",
            "job_runs__step_runs__step",
        )
    )
    if search:
        qs = qs.filter(search_q(search, "trigger_ref", "trigger_actor", "trigger_kind"))
    return qs


@strawberry.type
class PipelinesQuery:
    @strawberry.field(
        deprecation_reason="Caps at 500 rows with no way to reach the 501st. Use astroliftPipelinesPage."
    )
    @require_permission(Permission.APP_READ, any_scope=True)
    @tenant_scoped()
    def astrolift_pipelines(self, info: Info, limit: int = 100) -> list[PipelineType]:
        """All pipelines for the current tenant, ordered by name."""
        qs = _pipelines_qs().order_by("name")
        return [pipeline_to_type(p) for p in qs[: max(1, min(limit, 500))]]

    @strawberry.field
    @require_permission(Permission.APP_READ, any_scope=True)
    @tenant_scoped()
    def astrolift_pipelines_page(
        self,
        info: Info,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[PipelineType]:
        """Cursor-paginated pipeline catalogue (#1235).

        Replaces ``astroliftPipelines``, whose 500-row cap made the 501st
        pipeline unreachable from the UI rather than merely slow to reach.

        Seek key is ``(name, guid)`` ASCENDING — the alphabetical
        catalogue order the list field already serves, kept so the page is
        a drop-in rather than a silent re-sort. ``name`` is a non-null
        SlugField; the ``guid`` tiebreak is load-bearing because name
        uniqueness within an org is a mutation-level check, not a DB
        constraint. ``search`` matches the pipeline name, its repo URL,
        and the app it deploys.
        """
        page = keyset_page(
            _pipelines_qs(search=search),
            cursor=after,
            limit=limit,
            sort_field="name",
            tiebreak_field="guid",
            descending=False,
        )
        return page.map(pipeline_to_type)

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=pipeline_app_scope("id"))
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

    @strawberry.field(
        deprecation_reason=("Caps at 200 rows with no way to reach the 201st. Use astroliftPipelineRunsPage.")
    )
    @require_permission(Permission.APP_READ, scope=pipeline_app_scope("pipeline_id"))
    @tenant_scoped()
    def astrolift_pipeline_runs(self, info: Info, pipeline_id: str, limit: int = 50) -> list[PipelineRunType]:
        """Pipeline runs for a given pipeline, most-recent first.

        Tenant scoping flows through the pipeline FK — only runs whose
        pipeline belongs to the current org are returned.
        """
        qs = _pipeline_runs_qs(pipeline_id=pipeline_id).order_by("-run_number")
        return [pipeline_run_to_type(pr) for pr in qs[: max(1, min(limit, 200))]]

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=pipeline_app_scope("pipeline_id"))
    @tenant_scoped()
    def astrolift_pipeline_runs_page(
        self,
        info: Info,
        pipeline_id: str,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[PipelineRunType]:
        """Cursor-paginated run history for one pipeline (#1235).

        Replaces ``astroliftPipelineRuns``, whose 200-row cap put a
        pipeline's own history out of reach within weeks on anything that
        runs per-commit.

        Seek key is ``(-run_number, -guid)``, not the helper's default
        ``created_at``: ``run_number`` is the monotonic per-pipeline
        counter the list field already ordered on, it is NOT NULL, and it
        keeps the newest run first even when a backfill writes rows out of
        wall-clock order. The stream is single-pipeline (``pipeline_id``
        stays required), which is what makes a per-pipeline counter a
        valid sort key. ``search`` matches the triggering ref, the actor,
        and the trigger kind.
        """
        page = keyset_page(
            _pipeline_runs_qs(pipeline_id=pipeline_id, search=search),
            cursor=after,
            limit=limit,
            sort_field="run_number",
            tiebreak_field="guid",
        )
        return page.map(pipeline_run_to_type)

    @strawberry.field
    @require_permission(Permission.APP_READ, scope=pipeline_run_app_scope("id"))
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
