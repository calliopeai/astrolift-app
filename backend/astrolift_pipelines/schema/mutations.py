"""Mutations for astrolift_pipelines — Pipeline CRUD + Trigger management."""

from __future__ import annotations

import logging

import strawberry
from django.db import transaction
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_pipelines.commit_status import post_commit_status_for_run
from astrolift_pipelines.models import Pipeline, PipelineRun, Trigger
from astrolift_pipelines.schema.types import (
    PipelineRunType,
    PipelineType,
    TriggerType,
    pipeline_run_to_type,
    pipeline_to_type,
    trigger_to_type,
)
from astrolift_workflows.client import signal_workflow, start_workflow
from core.decorators import tenant_scoped
from core.mutations import ErrorCode
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

logger = logging.getLogger(__name__)


def _record_cancelled_run(run) -> None:
    """Count the cancellation. Swallows: a cancel is already written to the
    database and signalled to Temporal by the time this runs, so a metrics
    failure must not turn a successful mutation into an operator-visible
    error."""
    from astrolift_pipelines.metrics import record_run_completed

    try:
        record_run_completed(run)
    except Exception:  # noqa: BLE001
        logger.warning("pipeline metrics: pipeline_run=%s not recorded", run.pk, exc_info=True)


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
    def create_pipeline(self, info: Info, input: CreatePipelineInput) -> MutationResultType[PipelineType]:
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
    def create_trigger(self, info: Info, input: CreateTriggerInput) -> MutationResultType[TriggerType]:
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

        pipeline = Pipeline.objects.filter(
            guid=str(input.pipeline_id),
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
        ).first()
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
        self,
        info: Info,
        pipeline_id: GUID,
        ref: str | None = None,
    ) -> MutationResultType[PipelineRunType]:
        """Dispatch a manual PipelineRunWorkflow via Temporal (#75).

        Creates a PipelineRun with trigger_kind=manual, writes the
        Temporal workflow id onto the row, then starts the workflow.
        The actor is set from the authenticated request user.
        """
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "tenant context required",
            )

        pipeline = Pipeline.objects.filter(
            guid=str(pipeline_id),
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
        ).first()
        if pipeline is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "pipeline not found")

        trigger_ref = (ref or pipeline.default_branch or "").strip()

        # Resolve the caller identity for the trigger_actor field.
        request = (
            info.context.get("request")
            if isinstance(info.context, dict)
            else getattr(info.context, "request", None)
        )
        trigger_actor = ""
        if request is not None and hasattr(request, "user") and request.user.is_authenticated:
            trigger_actor = getattr(request.user, "email", "") or str(request.user)

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
        run_number = last_run_number + 1

        # Workflow id is deterministic so a duplicate UI click collides
        # on the same workflow id rather than spawning a parallel run.
        workflow_id = f"pipeline-run-{pipeline.pk}-{run_number}"

        with transaction.atomic():
            run = PipelineRun.objects.create(
                pipeline=pipeline,
                run_number=run_number,
                trigger_kind=PipelineRun.TriggerKind.MANUAL,
                trigger_ref=trigger_ref,
                trigger_actor=trigger_actor,
                temporal_workflow_id=workflow_id,
                status=PipelineRun.Status.PENDING,
            )

        start_workflow(
            "PipelineRunWorkflow",
            args=[run.pk],
            workflow_id=workflow_id,
        )

        return gql_success(pipeline_run_to_type(run))

    @strawberry.field
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def cancel_pipeline_run(self, info: Info, run_id: GUID) -> MutationResultType[PipelineRunType]:
        """Send a cancel signal to the running PipelineRunWorkflow (#68, #75).

        Sends the ``cancel`` signal to the Temporal workflow. The workflow
        handles cleanup (cancelling in-flight K8s Jobs, marking job runs
        cancelled) before transitioning the run to CANCELLED. If Temporal
        is disabled the status is flipped locally.
        """
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

        # Signal the Temporal workflow to cancel cleanly. When Temporal
        # is disabled, signal_workflow returns False and we fall through
        # to a local status flip so the DB stays consistent.
        if run.temporal_workflow_id:
            signal_workflow(run.temporal_workflow_id, "cancel")

        # Optimistically flip status so the UI reflects the cancel
        # immediately, even before the workflow drains.
        run.status = PipelineRun.Status.CANCELLED
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at", "updated_at", "version"])

        # This is the fourth place a run reaches a terminal status, and the
        # only one outside the spawn activity -- which is why #1585's
        # ratchet, parsing that one module, did not notice this path was
        # recording no metric. `pipeline_runs_total` undercounted every
        # cancellation.
        _record_cancelled_run(run)
        # Called here rather than from the helper above so both ratchets can
        # see their own call at the site. Burying it one level down is what
        # the commit-status ratchet flagged when this was first written: the
        # wiring existed but the transition did not declare it, and a later
        # edit to the helper could drop it silently.
        post_commit_status_for_run(run)

        return gql_success(pipeline_run_to_type(run))
