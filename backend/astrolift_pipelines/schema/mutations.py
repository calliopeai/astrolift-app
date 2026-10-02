"""Mutations for astrolift_pipelines — Pipeline CRUD + Trigger management."""

from __future__ import annotations

import strawberry
from django.db import IntegrityError, transaction
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.step_up import requires_elevation
from astrolift_pipelines.models import Pipeline, PipelineRun, Trigger
from astrolift_pipelines.run_contracts import (
    PipelineContractError,
    dispatch_pipeline_run,
    request_pipeline_cancellation,
    reserve_pipeline_run,
)
from astrolift_pipelines.schema.types import (
    PipelineRunType,
    PipelineSecretChangeType,
    PipelineType,
    TriggerType,
    pipeline_run_to_type,
    pipeline_to_type,
    trigger_to_type,
)
from astrolift_pipelines.scopes import (
    live_secret_pipelines,
    pipeline_app_scope,
    pipeline_creation_scope,
    pipeline_run_app_scope,
    pipeline_secret_scope,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, PermissionDenied, require_permission
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


@strawberry.input
class SetPipelineSecretInput:
    pipeline_id: GUID
    name: str
    value: str


@strawberry.input
class DeletePipelineSecretInput:
    pipeline_id: GUID
    name: str


@strawberry.input
class StartPipelineRunInput:
    pipeline_id: GUID
    expected_version: int
    request_id: str
    ref: str | None = None
    confirmed: bool = False


_SECRET_WRITE_PERMISSIONS = (Permission.PIPELINE_SECRET_MANAGE, Permission.SECRET_WRITE)
_SECRET_WRITE_SCOPE = pipeline_secret_scope("input.pipeline_id", permissions=_SECRET_WRITE_PERMISSIONS)


def _change_secret(info, input, *, delete=False):
    from astrolift_pipelines.pipeline_secrets import (
        delete_pipeline_secret,
        set_pipeline_secret,
        validate_secret_name,
    )

    try:
        with transaction.atomic():
            tenant = get_current_tenant()
            pipeline = (
                live_secret_pipelines(
                    Pipeline.objects.select_for_update(of=("self",)),
                    organization_id=tenant.organization_id,
                )
                .filter(guid=str(input.pipeline_id))
                .first()
            )
            if pipeline is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "Pipeline is unavailable")
            # The entry gate ran before acquiring the row lock. Recheck the
            # current owner so an intervening app association cannot widen it.
            require_permission(*_SECRET_WRITE_PERMISSIONS, scope=_SECRET_WRITE_SCOPE)(
                lambda info, input: None
            )(info, input=input)
            try:
                name = validate_secret_name(pipeline, input.name)
            except ValueError as exc:
                return gql_failure(ErrorCode.VALIDATION.value, str(exc), field="name")
            if delete:
                delete_pipeline_secret(pipeline, name)
            else:
                set_pipeline_secret(pipeline, name, input.value)
            return gql_success(PipelineSecretChangeType(pipeline_id=GUID(str(pipeline.guid)), name=name))
    except PermissionDenied:
        raise
    except Exception:  # noqa: BLE001 — backend errors may contain the submitted secret
        return gql_failure(ErrorCode.INTERNAL.value, "Could not update pipeline secret")


# ---------------------------------------------------------------------------
# Root mutation type
# ---------------------------------------------------------------------------

_VALID_TRIGGER_KINDS = {k.value for k in Trigger.Kind}


def _start_pipeline(info, input):
    user = info.context.get("user") if isinstance(info.context, dict) else getattr(info.context, "user", None)
    try:
        run = reserve_pipeline_run(
            pipeline_id=input.pipeline_id,
            expected_version=input.expected_version,
            request_id=input.request_id,
            ref=input.ref,
            user=user,
        )
        run = dispatch_pipeline_run(run)
    except Pipeline.DoesNotExist:
        return gql_failure(ErrorCode.NOT_FOUND.value, "Pipeline not found")
    except PermissionDenied:
        return gql_failure(ErrorCode.PERMISSION_DENIED.value, "Pipeline permission denied")
    except PipelineContractError as exc:
        return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
    except IntegrityError:
        return gql_failure(
            ErrorCode.CONFLICT.value,
            "A concurrent start reserved this request; reconcile with the same requestId",
        )
    except Exception:
        return gql_failure(ErrorCode.INTERNAL.value, "Pipeline start could not be prepared")
    if run.dispatch_status == "uncertain":
        result = gql_failure(ErrorCode.PRECONDITION.value, run.dispatch_last_error)
        result.data = pipeline_run_to_type(run)
        return result
    return gql_success(pipeline_run_to_type(run))


@strawberry.type
class PipelinesMutation:
    @strawberry.field
    @mutation_audit(action="pipeline.secret.set")
    @require_permission(*_SECRET_WRITE_PERMISSIONS, scope=_SECRET_WRITE_SCOPE)
    @tenant_scoped()
    def set_pipeline_secret(
        self, info: Info, input: SetPipelineSecretInput
    ) -> MutationResultType[PipelineSecretChangeType]:
        return _change_secret(info, input)

    @strawberry.field
    @mutation_audit(action="pipeline.secret.delete")
    @require_permission(*_SECRET_WRITE_PERMISSIONS, scope=_SECRET_WRITE_SCOPE)
    @tenant_scoped()
    def delete_pipeline_secret(
        self, info: Info, input: DeletePipelineSecretInput
    ) -> MutationResultType[PipelineSecretChangeType]:
        return _change_secret(info, input, delete=True)

    @strawberry.field
    @require_permission(Permission.APP_UPDATE, scope=pipeline_creation_scope)
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
    @require_permission(
        Permission.APP_UPDATE, scope=pipeline_app_scope("id", permission=Permission.APP_UPDATE)
    )
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
    @require_permission(
        Permission.APP_UPDATE, scope=pipeline_app_scope("id", permission=Permission.APP_UPDATE)
    )
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
    @require_permission(
        Permission.APP_UPDATE, scope=pipeline_app_scope("input.pipeline_id", permission=Permission.APP_UPDATE)
    )
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
    @mutation_audit(action="pipeline.run.start")
    @require_permission(
        Permission.APP_UPDATE, scope=pipeline_app_scope("input.pipeline_id", permission=Permission.APP_UPDATE)
    )
    @requires_elevation(action_label="Start this pipeline")
    @tenant_scoped()
    def start_pipeline_run(
        self, info: Info, input: StartPipelineRunInput
    ) -> MutationResultType[PipelineRunType]:
        if not input.confirmed:
            return gql_failure(ErrorCode.PRECONDITION.value, "Explicit confirmation is required")
        return _start_pipeline(info, input)

    @strawberry.field
    @mutation_audit(action="pipeline.run.start")
    @require_permission(
        Permission.APP_UPDATE, scope=pipeline_app_scope("pipeline_id", permission=Permission.APP_UPDATE)
    )
    @requires_elevation(action_label="Start this pipeline")
    @tenant_scoped()
    def trigger_pipeline_run(
        self,
        info: Info,
        pipeline_id: GUID,
        ref: str | None = None,
        request_id: str | None = None,
        expected_version: int | None = None,
        confirmed: bool = False,
    ) -> MutationResultType[PipelineRunType]:
        """Compatibility entry; old clients receive an explicit review prerequisite."""
        if not request_id or expected_version is None or not confirmed:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "Review the pipeline and supply requestId, expectedVersion and confirmed; use startPipelineRun",
            )
        return _start_pipeline(
            info,
            StartPipelineRunInput(
                pipeline_id=pipeline_id,
                ref=ref,
                request_id=request_id,
                expected_version=expected_version,
                confirmed=confirmed,
            ),
        )

    @strawberry.field
    @mutation_audit(action="pipeline.run.cancel.request")
    @require_permission(
        Permission.APP_UPDATE, scope=pipeline_run_app_scope("run_id", permission=Permission.APP_UPDATE)
    )
    @requires_elevation(action_label="Cancel this pipeline run")
    @tenant_scoped()
    def cancel_pipeline_run(
        self,
        info: Info,
        run_id: GUID,
        expected_version: int | None = None,
        temporal_workflow_id: str | None = None,
        temporal_run_id: str | None = None,
        confirmed: bool = False,
    ) -> MutationResultType[PipelineRunType]:
        if expected_version is None or not temporal_workflow_id or not temporal_run_id or not confirmed:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "Review the exact execution and supply expectedVersion, temporalWorkflowId, temporalRunId and confirmed",
            )
        tenant = get_current_tenant()
        run = (
            PipelineRun.objects.filter(
                guid=str(run_id), organization_id=tenant.organization_id, pipeline__deleted_at__isnull=True
            )
            .select_related("pipeline")
            .first()
        )
        if run is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "Pipeline run not found")
        try:
            run = request_pipeline_cancellation(
                run,
                expected_version=expected_version,
                workflow_id=temporal_workflow_id,
                temporal_run_id=temporal_run_id,
            )
        except PermissionDenied:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "Pipeline permission denied")
        except PipelineContractError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        if run.cancellation_status == "uncertain":
            result = gql_failure(ErrorCode.PRECONDITION.value, run.cancellation_last_error)
            result.data = pipeline_run_to_type(run)
            return result
        return gql_success(pipeline_run_to_type(run))
