"""Bounded reconciliation of definition runs against their exact Temporal execution."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import datetime, timedelta

from temporalio import activity

log = logging.getLogger(__name__)
_BATCH_SIZE = 40
_AUDIT_BATCH_SIZE = 10
_CONCURRENCY = 10
_RPC_TIMEOUT = timedelta(seconds=3)
_CURSOR_KEY = "astrolift:workflow-run-reconcile:cursor:v1"
_WORKFLOW_KIND = "WorkflowDefinitionRunWorkflow"
_STATUSES = {
    "RUNNING": "running",
    "COMPLETED": "completed",
    "FAILED": "failed",
    "CANCELED": "cancelled",
    "TERMINATED": "terminated",
    "TIMED_OUT": "timed_out",
}


@dataclasses.dataclass(frozen=True)
class RunIdentity:
    pk: int
    organization_id: int | None
    workflow_id: str
    run_id: str
    version: int


@dataclasses.dataclass
class WorkflowRunReconcileSummary:
    evaluated: int = 0
    repaired: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: int = 0


def _next_batch() -> list[RunIdentity]:
    from django.core.cache import cache
    from django.db.models import Exists, OuterRef, Q

    from astrolift_operations.models import WorkflowRun
    from workflows.models import WorkflowStageExecution

    candidates = WorkflowRun.objects.filter(workflow_kind=_WORKFLOW_KIND, deleted_at__isnull=True)
    candidates = candidates.alias(
        open_stages=Exists(
            WorkflowStageExecution.objects.filter(
                workflow_run_id=OuterRef("pk"), status__in=["pending", "running"], deleted_at__isnull=True
            )
        )
    )
    attention = Q(status="running") | Q(ended_at__isnull=True) | Q(open_stages=True)
    state = cache.get(_CURSOR_KEY) or {}
    fields = ("pk", "organization_id", "workflow_id", "run_id", "version")
    rows = []
    # Reserve capacity for active runs, while auditing older terminal mirrors
    # that fan-out workers may have closed incorrectly. Freeze each cycle's
    # upper bound so continuous inserts cannot starve earlier rows.
    for lane, queryset, size in (
        ("active", candidates.filter(attention), _BATCH_SIZE),
        ("audit", candidates.exclude(attention), _AUDIT_BATCH_SIZE),
    ):
        cursor, upper = state.get(lane, (0, 0))
        if not upper:
            upper = queryset.order_by("-pk").values_list("pk", flat=True).first() or 0
        batch = list(queryset.filter(pk__gt=cursor, pk__lte=upper).order_by("pk").values_list(*fields)[:size])
        if not batch and cursor:
            upper = queryset.order_by("-pk").values_list("pk", flat=True).first() or 0
            batch = list(queryset.filter(pk__lte=upper).order_by("pk").values_list(*fields)[:size])
        state[lane] = (batch[-1][0], upper) if batch else (0, 0)
        rows.extend(batch)
    cache.set(_CURSOR_KEY, state, timeout=None)
    return [RunIdentity(*row) for row in rows]


def _apply_observation(identity: RunIdentity, status: str, closed_at: datetime | None) -> bool | None:
    from django.db import connection, transaction
    from django.db.models import F
    from django.utils import timezone

    from astrolift_operations.models import WorkflowRun
    from workflows.models import WorkflowStageExecution
    from workflows.run_status import synchronize_workflow_instances

    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '2s'")
                cursor.execute("SET LOCAL statement_timeout = '5s'")
        run = WorkflowRun.objects.select_for_update().filter(pk=identity.pk, deleted_at__isnull=True).first()
        if run is None or (run.organization_id, run.workflow_id, run.run_id, run.workflow_kind) != (
            identity.organization_id,
            identity.workflow_id,
            identity.run_id,
            _WORKFLOW_KIND,
        ):
            return None
        # A finalizer or dispatch can commit while describe is in flight.
        # Retry on a later sweep instead of undoing that newer observation.
        if run.version != identity.version:
            return None
        changed = (
            run.status != status
            or run.ended_at != closed_at
            or (closed_at is not None and run.current_stage_execution_id is not None)
        )
        if changed:
            run.status = status
            run.ended_at = closed_at
            if closed_at is not None:
                run.current_stage_execution = None
            run.save(update_fields=["status", "ended_at", "current_stage_execution", "updated_at", "version"])
        if status in {"cancelled", "failed", "terminated", "timed_out"}:
            updated = WorkflowStageExecution.objects.filter(
                workflow_run=run, status__in=["pending", "running"], deleted_at__isnull=True
            ).update(
                status="failed" if status == "failed" else "cancelled",
                ended_at=closed_at,
                updated_at=timezone.now(),
                version=F("version") + 1,
            )
            changed = bool(updated) or changed
        return synchronize_workflow_instances(run, authoritative=True) or changed


async def reconcile_workflow_runs() -> WorkflowRunReconcileSummary:
    from asgiref.sync import sync_to_async

    from astrolift_workflows.client import _get_client_async, _temporal_enabled

    summary = WorkflowRunReconcileSummary()
    if not await sync_to_async(_temporal_enabled, thread_sensitive=False)():
        return summary
    rows = await sync_to_async(_next_batch, thread_sensitive=False)()
    if not rows:
        return summary
    # Connection failure fails the activity visibly; no mirror is changed.
    client = await asyncio.wait_for(_get_client_async(), timeout=5)
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def reconcile(identity: RunIdentity) -> None:
        async with semaphore:
            summary.evaluated += 1
            if not identity.workflow_id or not identity.run_id or identity.organization_id is None:
                summary.skipped += 1
                return
            try:
                handle = client.get_workflow_handle(identity.workflow_id, run_id=identity.run_id)
                desc = await asyncio.wait_for(handle.describe(rpc_timeout=_RPC_TIMEOUT), timeout=4)
                if (desc.id, desc.run_id, desc.workflow_type) != (
                    identity.workflow_id,
                    identity.run_id,
                    _WORKFLOW_KIND,
                ):
                    summary.errors += 1
                    log.warning("workflow reconcile identity mismatch for mirror %s", identity.pk)
                    return
                status = _STATUSES.get(desc.status.name if desc.status else "")
                if status is None or (status != "running" and desc.close_time is None):
                    summary.skipped += 1
                    return
                changed = await sync_to_async(_apply_observation, thread_sensitive=False)(
                    identity, status, desc.close_time if status != "running" else None
                )
                if changed is None:
                    summary.skipped += 1
                elif changed:
                    summary.repaired += 1
                else:
                    summary.unchanged += 1
            except Exception:
                summary.errors += 1
                log.exception("workflow reconcile failed for mirror %s", identity.pk)

    await asyncio.wait_for(asyncio.gather(*(reconcile(row) for row in rows)), timeout=40)
    log.info("workflow run reconciliation: %s", dataclasses.asdict(summary))
    return summary


@activity.defn(name="astrolift.workflows.reconcile_runs_tick")
async def reconcile_workflow_runs_tick() -> WorkflowRunReconcileSummary:
    return await reconcile_workflow_runs()
