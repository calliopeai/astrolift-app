"""Mutations for astrolift_pipelines — Pipeline CRUD + Trigger management."""

from __future__ import annotations

import strawberry
from django.db import transaction
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_pipelines.models import Pipeline, PipelineRun, Trigger
from astrolift_pipelines.schema.types import (
    PipelineRunType,
    PipelineType,
    TriggerType,
    pipeline_run_to_type,
    pipeline_to_type,
    trigger_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

# ---------------------------------------------------------------------------
# Input types
# ---------------------------------------------------------------------------


@strawberry.input
class CreatePipelineInput:
    name: str
    repo_url: str
    default_branch: str = "main"
    toml_path: str = ""


@strawberry.input
class UpdatePipelineInput:
    name: str | None = None
    repo_url: str | None = None
    default_branch: str | None = None
    toml_path: str | None = None


@strawberry.input
class CreateTriggerInput:
    pipeline_id: GUID
    kind: str
    config: str = "{}"


# ---------------------------------------------------------------------------
# Root mutation type
# ---------------------------------------------------------------------------

_VALID_TRIGGER_KINDS = {k.value for k in Trigger.Kind}


@strawberry.type
class PipelinesMutation:
    @strawberry.field
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def create_pipeline(
        self, info: Info, input: CreatePipelineInput
    ) -> MutationResultType[PipelineType]:
        name = (input.name or "").strip()
        if not name:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "name is required",
                field="name",
            )

        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "tenant context required",
            )

        if Pipeline.objects.filter(
            organization_id=tenant.organization_id,
            name=name,
            deleted_at__isnull=True,
        ).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"a pipeline named {name!r} already exists in this organization",
                field="name",
            )

        with transaction.atomic():
            pipeline = Pipeline.objects.create(
                organization_id=tenant.organization_id,
                name=name,
                repo_url=(input.repo_url or "").strip(),
                default_branch=(input.default_branch or "main").strip() or "main",
                toml_path=(input.toml_path or "").strip(),
            )

        return gql_success(pipeline_to_type(pipeline))

    @strawberry.field
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def update_pipeline(
        self, info: Info, id: GUID, input: UpdatePipelineInput
    ) -> MutationResultType[PipelineType]:
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "tenant context required",
            )

        pipeline = (
            Pipeline.objects.filter(
                guid=str(id),
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .prefetch_related("triggers")
            .first()
        )
        if pipeline is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "pipeline not found")

        update_fields: list[str] = ["updated_at", "version"]

        if input.name is not None:
            name = input.name.strip()
            if not name:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "name must not be empty",
                    field="name",
                )
            if (
                Pipeline.objects.filter(
                    organization_id=tenant.organization_id,
                    name=name,
                    deleted_at__isnull=True,
                )
                .exclude(pk=pipeline.pk)
                .exists()
            ):
                return gql_failure(
                    ErrorCode.CONFLICT.value,
                    f"a pipeline named {name!r} already exists in this organization",
                    field="name",
                )
            pipeline.name = name
            update_fields.append("name")

        if input.repo_url is not None:
            pipeline.repo_url = input.repo_url.strip()
            update_fields.append("repo_url")

        if input.default_branch is not None:
            branch = input.default_branch.strip() or "main"
            pipeline.default_branch = branch
            update_fields.append("default_branch")

        if input.toml_path is not None:
            pipeline.toml_path = input.toml_path.strip()
            update_fields.append("toml_path")

        pipeline.save(update_fields=update_fields)
        return gql_success(pipeline_to_type(pipeline))

    @strawberry.field
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def delete_pipeline(self, info: Info, id: GUID) -> MutationResultType[PipelineType]:
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "tenant context required",
            )

        pipeline = (
            Pipeline.objects.filter(
                guid=str(id),
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .prefetch_related("triggers")
            .first()
        )
        if pipeline is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "pipeline not found")

        snapshot = pipeline_to_type(pipeline)
        pipeline.deleted_at = timezone.now()
        pipeline.save(update_fields=["deleted_at", "updated_at", "version"])
        return gql_success(snapshot)

    @strawberry.field
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def create_trigger(
        self, info: Info, input: CreateTriggerInput
    ) -> MutationResultType[TriggerType]:
        kind = (input.kind or "").strip().lower()
        if kind not in _VALID_TRIGGER_KINDS:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown trigger kind {kind!r}; valid kinds: {sorted(_VALID_TRIGGER_KINDS)}",
                field="kind",
            )

        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "tenant context required",
            )

        pipeline = (
            Pipeline.objects.filter(
                guid=str(input.pipeline_id),
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .first()
        )
        if pipeline is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "pipeline not found")

        import json

        try:
            config = json.loads(input.config or "{}")
        except json.JSONDecodeError:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "config must be valid JSON",
                field="config",
            )

        with transaction.atomic():
            trigger = Trigger.objects.create(
                pipeline=pipeline,
                kind=kind,
                config=config,
            )

        return gql_success(trigger_to_type(trigger))

    @strawberry.field
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def trigger_pipeline_run(
        self, info: Info, pipeline_id: GUID, ref: str | None = None
    ) -> MutationResultType[PipelineRunType]:
        """Stub — execution triggers land in M3. Returns a pending run row."""
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "tenant context required",
            )

        pipeline = (
            Pipeline.objects.filter(
                guid=str(pipeline_id),
                organization_id=tenant.organization_id,
                deleted_at__isnull=True,
            )
            .first()
        )
        if pipeline is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "pipeline not found")

        last_run_number = (
            PipelineRun.objects.filter(
                pipeline=pipeline,
                deleted_at__isnull=True,
            )
            .order_by("-run_number")
            .values_list("run_number", flat=True)
            .first()
            or 0
        )

        with transaction.atomic():
            run = PipelineRun.objects.create(
                pipeline=pipeline,
                run_number=last_run_number + 1,
                trigger_kind=PipelineRun.TriggerKind.MANUAL,
                trigger_ref=(ref or "").strip(),
                trigger_actor="",
                status=PipelineRun.Status.PENDING,
            )

        return gql_success(pipeline_run_to_type(run))

    @strawberry.field
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def cancel_pipeline_run(
        self, info: Info, run_id: GUID
    ) -> MutationResultType[PipelineRunType]:
        """Stub — cancellation wires to the Temporal workflow in M3."""
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "tenant context required",
            )

        run = (
            PipelineRun.objects.filter(
                guid=str(run_id),
                pipeline__organization_id=tenant.organization_id,
                pipeline__deleted_at__isnull=True,
                deleted_at__isnull=True,
            )
            .select_related("pipeline")
            .prefetch_related("job_runs__job", "job_runs__step_runs__step")
            .first()
        )
        if run is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "pipeline run not found")

        cancellable = {PipelineRun.Status.PENDING, PipelineRun.Status.RUNNING}
        if run.status not in cancellable:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"run is in status {run.status!r} — only pending/running runs can be cancelled",
            )

        run.status = PipelineRun.Status.CANCELLED
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at", "updated_at", "version"])

        return gql_success(pipeline_run_to_type(run))
