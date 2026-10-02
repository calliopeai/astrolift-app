"""Durable pipeline reservation, exact engine binding and cancellation requests."""

from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from astrolift_pipelines.models import Pipeline, PipelineRun
from core.current_credential import current_dispatch_credential
from core.permissions import Permission, PermissionScope, ScopeKind, check_permission
from core.run_input_contract import canonical_bytes
from core.tenancy import get_current_tenant


class PipelineContractError(ValueError):
    pass


def pipeline_scope(pipeline):
    return (
        PermissionScope(kind=ScopeKind.APP, id=pipeline.registered_app_id)
        if pipeline.registered_app_id
        else PermissionScope(kind=ScopeKind.ORG, id=pipeline.organization_id)
    )


def authorize_pipeline(pipeline, *, permission=Permission.APP_UPDATE):
    from astrolift_pipelines.scopes import live_secret_pipelines

    tenant = get_current_tenant()
    if (
        tenant is None
        or tenant.organization_id != pipeline.organization_id
        or not live_secret_pipelines(
            Pipeline.objects.filter(pk=pipeline.pk), organization_id=tenant.organization_id
        ).exists()
    ):
        raise PipelineContractError("The pipeline or its owner is unavailable")
    from astrolift_pipelines.scopes import pipeline_secret_scope

    scope = pipeline_secret_scope("pipeline_id", permissions=(permission,))({"pipeline_id": pipeline.guid})
    check_permission(permission, scope=scope)


def reserve_pipeline_run(
    *,
    pipeline_id,
    expected_version,
    request_id,
    ref=None,
    user=None,
    actor_key=None,
    trigger_kind="manual",
    commit_sha="",
    skip_secrets=False,
    trigger_actor="",
    trusted_webhook=False,
):
    """Unique numbers include deleted history; request keys are never recycled."""
    tenant = get_current_tenant()
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise PipelineContractError("requestId must contain 1 to 128 characters")
    with transaction.atomic(), current_dispatch_credential(Permission.APP_UPDATE):
        query = Pipeline.objects.select_for_update(of=("self",)).select_related(
            "organization", "registered_app"
        )
        if trusted_webhook:
            pipeline = query.get(pk=pipeline_id)
            from astrolift_pipelines.scopes import live_secret_pipelines

            if not live_secret_pipelines(
                Pipeline.objects.filter(pk=pipeline.pk), organization_id=pipeline.organization_id
            ).exists():
                raise PipelineContractError("The pipeline or its owner is unavailable")
            if not actor_key or not actor_key.startswith("webhook:"):
                raise PipelineContractError("A webhook actor is required")
        else:
            if tenant is None or tenant.actor_user_id is None:
                raise PipelineContractError("An authenticated organization actor is required")
            pipeline = query.get(guid=str(pipeline_id), organization_id=tenant.organization_id)
            authorize_pipeline(pipeline)
            actor_key = f"user:{tenant.actor_user_id}"
        normalized_ref = (ref if ref is not None else pipeline.default_branch or "").strip()
        payload = {
            "pipeline": str(pipeline.guid),
            "version": None if trusted_webhook else expected_version,
            "ref": ref,
            "kind": trigger_kind,
            "commit": commit_sha,
            "skip_secrets": bool(skip_secrets),
        }
        request_digest = hmac.new(
            settings.SECRET_KEY.encode(), canonical_bytes(payload), hashlib.sha256
        ).hexdigest()
        existing = (
            PipelineRun._unscoped.select_related("pipeline", "organization", "registered_app")
            .filter(organization_id=pipeline.organization_id, actor_key=actor_key, request_id=request_id)
            .first()
        )
        if existing is not None:
            if existing.deleted_at is not None or existing.request_digest != request_digest:
                raise PipelineContractError("requestId already belongs to a different or deleted run")
            return existing
        if expected_version != pipeline.version:
            raise PipelineContractError("The pipeline changed; review its current version")
        number = (
            PipelineRun._unscoped.filter(pipeline=pipeline).aggregate(value=Max("run_number"))["value"] or 0
        ) + 1
        run = PipelineRun.objects.create(
            pipeline=pipeline,
            organization=pipeline.organization,
            registered_app=pipeline.registered_app,
            pipeline_version=pipeline.version,
            run_number=number,
            actor_key=actor_key,
            request_id=request_id,
            request_digest=request_digest,
            trigger_kind=trigger_kind,
            trigger_ref=normalized_ref,
            commit_sha=commit_sha,
            skip_secrets=skip_secrets,
            trigger_actor=trigger_actor,
            created_by=user,
            updated_by=user,
        )
        run.temporal_workflow_id = f"pipeline-run-{run.guid}"
        run.save(update_fields=["temporal_workflow_id", "updated_at", "version"])
        return run


def dispatch_pipeline_run(run, *, trusted_webhook=False):
    from astrolift_workflows.client import recover_workflow_once, start_workflow_once

    with transaction.atomic(), current_dispatch_credential(Permission.APP_UPDATE):
        current = (
            PipelineRun.objects.select_for_update(of=("self",))
            .select_related("pipeline", "organization", "registered_app")
            .get(pk=run.pk)
        )
        from astrolift_pipelines.scopes import live_secret_pipelines

        if (
            not live_secret_pipelines(
                Pipeline.objects.filter(pk=current.pipeline_id), organization_id=current.organization_id
            ).exists()
            or current.organization_id != current.pipeline.organization_id
            or current.registered_app_id != current.pipeline.registered_app_id
        ):
            raise PipelineContractError(
                "The pipeline owner changed or is unavailable; reconcile without dispatching"
            )
        if not trusted_webhook:
            authorize_pipeline(current.pipeline)
            tenant = get_current_tenant()
            assert tenant is not None
            if current.actor_key != f"user:{tenant.actor_user_id}":
                raise PipelineContractError("The start belongs to another actor")
        if current.temporal_run_id:
            return current
        if timezone.now() - current.created_at > timedelta(hours=24):
            raise PipelineContractError(
                "Uncertain start is older than 24 hours; reconcile without resubmitting"
            )
        try:
            if (
                current.status not in {"pending", "running"}
                or current.pipeline.version != current.pipeline_version
            ):
                handle = recover_workflow_once(
                    "PipelineRunWorkflow", [current.pk], workflow_id=current.temporal_workflow_id
                )
                if handle is None:
                    raise PipelineContractError("This terminal run cannot be submitted")
            else:
                handle = start_workflow_once(
                    "PipelineRunWorkflow", [current.pk], workflow_id=current.temporal_workflow_id
                )
        except PipelineContractError:
            raise
        except Exception:
            current.dispatch_status = "uncertain"
            current.dispatch_last_error = (
                "Submission unavailable or response uncertain; retry with the same requestId"
            )
            current.save(update_fields=["dispatch_status", "dispatch_last_error", "updated_at", "version"])
            return current
        current.temporal_run_id = handle.run_id
        current.dispatch_status = "submitted"
        current.dispatch_last_error = ""
        current.save(
            update_fields=[
                "temporal_run_id",
                "dispatch_status",
                "dispatch_last_error",
                "updated_at",
                "version",
            ]
        )
        return current


def request_pipeline_cancellation(
    run, *, expected_version, workflow_id, temporal_run_id, trusted_internal=False
):
    from astrolift_workflows.client import describe_workflow_instance, signal_pipeline_execution

    with transaction.atomic(), current_dispatch_credential(Permission.APP_UPDATE):
        current = (
            PipelineRun.objects.select_for_update(of=("self",))
            .select_related("pipeline", "organization", "registered_app")
            .get(pk=run.pk)
        )
        if not trusted_internal:
            authorize_pipeline(current.pipeline)
        if (
            current.organization_id != current.pipeline.organization_id
            or current.registered_app_id != current.pipeline.registered_app_id
        ):
            raise PipelineContractError("The pipeline owner changed")
        if (
            not temporal_run_id
            or current.temporal_run_id != temporal_run_id
            or current.temporal_workflow_id != workflow_id
        ):
            raise PipelineContractError("The reviewed engine execution changed or is unavailable")
        if current.version != expected_version:
            raise PipelineContractError("The run changed; review its current version")
        if current.cancellation_status in {"acknowledged", "observed"}:
            return current
        if current.status not in {"pending", "running"}:
            raise PipelineContractError("The run is already terminal")
        current.cancellation_requested_at = timezone.now()
        current.cancellation_status = "requesting"
        current.cleanup_status = "pending"
        try:
            description = describe_workflow_instance(workflow_id, run_id=temporal_run_id)
            if (
                description is None
                or description["run_id"] != temporal_run_id
                or description["status"] != "RUNNING"
            ):
                raise PipelineContractError("The reviewed execution is unavailable or already closed")
            signal_pipeline_execution(workflow_id, temporal_run_id)
        except Exception:
            current.cancellation_status = "uncertain"
            current.cancellation_last_error = (
                "Cancellation not acknowledged; review the same exact run before retrying"
            )
            current.save(
                update_fields=[
                    "cancellation_requested_at",
                    "cancellation_status",
                    "cancellation_last_error",
                    "cleanup_status",
                    "updated_at",
                    "version",
                ]
            )
            return current
        current.cancellation_status = "acknowledged"
        current.cancellation_last_error = ""
        current.save(
            update_fields=[
                "cancellation_requested_at",
                "cancellation_status",
                "cancellation_last_error",
                "cleanup_status",
                "updated_at",
                "version",
            ]
        )
        return current


def observe_pipeline_cancellation(run):
    from astrolift_pipelines.models import JobRun
    from astrolift_workflows.client import describe_workflow_instance

    if not run.temporal_run_id or run.cancellation_status not in {"acknowledged", "uncertain", "observed"}:
        return run
    description = describe_workflow_instance(run.temporal_workflow_id, run_id=run.temporal_run_id)
    if (
        description is None
        or description["run_id"] != run.temporal_run_id
        or description["status"] == "RUNNING"
    ):
        return run
    with transaction.atomic(), current_dispatch_credential(Permission.APP_UPDATE):
        current = PipelineRun.objects.select_for_update().get(pk=run.pk)
        if current.temporal_run_id != run.temporal_run_id:
            return current
        changed = current.cancellation_status != "observed"
        current.cancellation_status = "observed"
        if current.cancellation_observed_at is None:
            current.cancellation_observed_at = timezone.now()
            changed = True
        jobs = JobRun.objects.filter(pipeline_run=current)
        statuses = set(jobs.values_list("cleanup_status", flat=True))
        cleanup_status = (
            "failed"
            if "failed" in statuses
            else "complete"
            if statuses <= {"complete", "not_required"}
            else "pending"
        )
        changed = changed or current.cleanup_status != cleanup_status
        current.cleanup_status = cleanup_status
        if not changed:
            return current
        current.save(
            update_fields=[
                "cancellation_status",
                "cancellation_observed_at",
                "cleanup_status",
                "updated_at",
                "version",
            ]
        )
        return current


def recover_pipeline_start(run):
    """Read the exact reserved engine identity; never submit an execution."""
    from astrolift_workflows.client import recover_workflow_once

    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None or tenant.actor_user_id is None:
        raise PipelineContractError("An authenticated organization actor is required for recovery")
    with transaction.atomic(), current_dispatch_credential(Permission.APP_READ):
        current = (
            PipelineRun.objects.select_for_update(of=("self",))
            .select_related("pipeline", "organization", "registered_app")
            .get(pk=run.pk, organization_id=tenant.organization_id, actor_key=f"user:{tenant.actor_user_id}")
        )
        authorize_pipeline(current.pipeline, permission=Permission.APP_READ)
        if (
            current.organization_id != current.pipeline.organization_id
            or current.registered_app_id != current.pipeline.registered_app_id
        ):
            raise PipelineContractError("The pipeline owner changed")
        if current.temporal_run_id:
            return current
        try:
            handle = recover_workflow_once(
                "PipelineRunWorkflow", [current.pk], workflow_id=current.temporal_workflow_id
            )
        except Exception:
            return current
        if handle is None:
            return current
        current.temporal_run_id = handle.run_id
        current.dispatch_status = "submitted"
        current.dispatch_last_error = ""
        current.save(
            update_fields=[
                "temporal_run_id",
                "dispatch_status",
                "dispatch_last_error",
                "updated_at",
                "version",
            ]
        )
        return current
