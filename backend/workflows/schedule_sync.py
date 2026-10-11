"""Observed, revision-bound reconciliation of native configured workflow schedules."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta

from asgiref.sync import async_to_sync
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

log = logging.getLogger(__name__)
RPC_TIMEOUT = timedelta(seconds=8)
OPERATION_TIMEOUT = 20


@dataclasses.dataclass(frozen=True)
class ScheduleObservation:
    workflow_id: str
    schedule_id: str
    configuration_version: int
    desired_revision: str
    desired_active: bool
    observed_state: str
    confirmed: bool
    observed_at: datetime
    error_code: str = ""
    message: str = ""
    engine_created_at: datetime | None = None
    engine_updated_at: datetime | None = None
    action_count: int | None = None


def schedule_id_for(workflow) -> str:
    return f"workflow-{workflow.guid}"


def schedule_inactive_reason(workflow) -> str | None:
    if workflow.deleted_at is not None:
        return "workflow was deleted"
    if not workflow.is_enabled:
        return "workflow is disabled"
    if workflow.trigger_kind != "schedule" or not workflow.schedule_cron:
        return "workflow is no longer schedule-triggered"
    definition = workflow.definition
    if (
        definition.deleted_at is not None
        or not definition.is_enabled
        or definition.organization_id not in (None, workflow.organization_id)
    ):
        return "workflow definition is unavailable"
    project = definition.project
    if project is not None and (
        project.deleted_at is not None
        or project.organization_id != workflow.organization_id
        or project.team.deleted_at is not None
        or project.team.organization_id != workflow.organization_id
    ):
        return "workflow owner is unavailable"
    return None


def desired_revision(workflow) -> str:
    intent = {
        "workflow_guid": str(workflow.guid),
        "organization_id": workflow.organization_id,
        "active": schedule_inactive_reason(workflow) is None,
        "cron": workflow.schedule_cron or "",
        "task_queue": getattr(settings, "TEMPORAL_TASK_QUEUE", "astrolift-main"),
    }
    return hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _observation(workflow, state, *, confirmed=False, **kwargs):
    return ScheduleObservation(
        workflow_id=str(workflow.guid),
        schedule_id=schedule_id_for(workflow),
        configuration_version=workflow.version,
        desired_revision=desired_revision(workflow),
        desired_active=schedule_inactive_reason(workflow) is None,
        observed_state=state,
        confirmed=confirmed,
        observed_at=timezone.now(),
        **kwargs,
    )


def unrequested_schedule(workflow):
    return _observation(workflow, "not_requested", message="No schedule activation or cleanup was requested.")


def _action_input(workflow):
    return {
        "workflow_guid": str(workflow.guid),
        "organization_id": workflow.organization_id,
        "schedule_revision": desired_revision(workflow),
    }


def _desired_schedule(workflow):
    from temporalio.client import Schedule, ScheduleActionStartWorkflow, ScheduleSpec, ScheduleState

    return Schedule(
        action=ScheduleActionStartWorkflow(
            "ConfiguredWorkflowScheduleWorkflow",
            _action_input(workflow),
            id=f"{schedule_id_for(workflow)}-run",
            task_queue=getattr(settings, "TEMPORAL_TASK_QUEUE", "astrolift-main"),
        ),
        spec=ScheduleSpec(cron_expressions=[workflow.schedule_cron]),
        state=ScheduleState(paused=False),
    )


def _spec_hash(spec):
    def plain(value):
        if isinstance(value, dict):
            return {key: plain(item) for key, item in value.items()}
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return [plain(item) for item in value]
        return value

    encoded = json.dumps(
        plain(dataclasses.asdict(spec)), cls=DjangoJSONEncoder, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


async def _matches_action(workflow, client, schedule):
    from temporalio.api.common.v1 import Payload
    from temporalio.client import ScheduleActionStartWorkflow

    action = schedule.action
    if not isinstance(action, ScheduleActionStartWorkflow):
        return False
    args = list(action.args)
    if args and all(isinstance(arg, Payload) for arg in args):
        args = await client.data_converter.decode(args)
    return (
        action.workflow == "ConfiguredWorkflowScheduleWorkflow"
        and action.id == f"{schedule_id_for(workflow)}-run"
        and action.task_queue == getattr(settings, "TEMPORAL_TASK_QUEUE", "astrolift-main")
        and args == [_action_input(workflow)]
    )


async def _describe(handle):
    from temporalio.service import RPCError, RPCStatusCode

    try:
        return await handle.describe(rpc_timeout=RPC_TIMEOUT)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return None
        raise


async def _inspect(workflow, client, *, applied=False):
    description = await _describe(client.get_schedule_handle(schedule_id_for(workflow)))
    active = schedule_inactive_reason(workflow) is None
    if description is None:
        return _observation(workflow, "missing", confirmed=not active), None
    schedule = description.schedule
    fingerprint = _spec_hash(schedule.spec)
    action_matches = await _matches_action(workflow, client, schedule)
    configured = action_matches and not schedule.state.paused and not schedule.state.limited_actions
    receipt_matches = (
        workflow.schedule_revision == desired_revision(workflow)
        and workflow.schedule_spec_hash == fingerprint
    )
    confirmed = active and configured and (applied or receipt_matches)
    state = "paused" if schedule.state.paused else "active" if confirmed else "drifted"
    return _observation(
        workflow,
        state,
        confirmed=confirmed,
        engine_created_at=description.info.created_at,
        engine_updated_at=description.info.last_updated_at,
        action_count=description.info.num_actions,
    ), fingerprint if confirmed else None


def _failure(workflow, exc):
    from temporalio.service import RPCError, RPCStatusCode

    code = "engine_unavailable"
    if isinstance(exc, TimeoutError):
        code = "engine_timeout"
    elif isinstance(exc, RPCError):
        code = {
            RPCStatusCode.INVALID_ARGUMENT: "invalid_schedule",
            RPCStatusCode.PERMISSION_DENIED: "engine_permission_denied",
            RPCStatusCode.UNAUTHENTICATED: "engine_unauthenticated",
            RPCStatusCode.DEADLINE_EXCEEDED: "engine_timeout",
        }.get(exc.status, code)
    log.warning("schedule %s could not be confirmed (%s)", schedule_id_for(workflow), code)
    return _observation(
        workflow,
        "unknown",
        error_code=code,
        message="Schedule state is unconfirmed. Inspect this workflow ID and reconcile the reviewed configuration.",
    )


def observe_workflow_schedule(workflow, *, authorize=None):
    from astrolift_workflows.client import _get_client_async, _temporal_enabled
    from workflows.models import Workflow

    workflow = Workflow.objects.select_related("definition__project__team").get(
        pk=workflow.pk, organization_id=workflow.organization_id
    )
    if authorize is not None:
        authorize(workflow)

    if not _temporal_enabled():
        return _observation(
            workflow, "unknown", error_code="engine_disabled", message="Temporal is disabled."
        )

    @async_to_sync
    async def inspect():
        async with asyncio.timeout(OPERATION_TIMEOUT):
            observation, _ = await _inspect(workflow, await _get_client_async())
            return observation

    try:
        return inspect()
    except Exception as exc:  # noqa: BLE001 — report uncertainty, never claim success
        return _failure(workflow, exc)


def reconcile_workflow_schedule(workflow, *, expected_version, expected_active, authorize=None):
    """Serialize the engine operation with configuration writes and reject stale reviews."""
    from astrolift_workflows.client import _get_client_async, _temporal_enabled
    from core.permissions import PermissionDenied
    from workflows.models import Workflow

    with transaction.atomic():
        current = (
            Workflow.objects.select_for_update(of=("self",))
            .select_related("definition__project__team")
            .get(pk=workflow.pk, organization_id=workflow.organization_id)
        )
        if authorize is not None:
            try:
                authorize(current)
            except PermissionDenied:
                return _observation(
                    current,
                    "unknown",
                    error_code="permission_denied",
                    message="Current authority does not permit this schedule operation.",
                )
        active = schedule_inactive_reason(current) is None
        if current.version != expected_version or active != expected_active:
            return _observation(
                current,
                "unknown",
                error_code="stale_configuration",
                message="Workflow configuration changed; inspect it again before reconciliation.",
            )
        Workflow.objects.filter(pk=current.pk).update(schedule_managed=True)
        if not _temporal_enabled():
            return _observation(
                current, "unknown", error_code="engine_disabled", message="Temporal is disabled."
            )

        @async_to_sync
        async def apply():
            from temporalio.client import ScheduleUpdate
            from temporalio.service import RPCError, RPCStatusCode

            async with asyncio.timeout(OPERATION_TIMEOUT):
                client = await _get_client_async()
                handle = client.get_schedule_handle(schedule_id_for(current))
                existing = await _describe(handle)
                if not active:
                    if existing is not None:
                        try:
                            await handle.delete(rpc_timeout=RPC_TIMEOUT)
                        except RPCError as exc:
                            if exc.status != RPCStatusCode.NOT_FOUND:
                                raise
                else:
                    desired = _desired_schedule(current)
                    if existing is None:
                        await client.create_schedule(
                            schedule_id_for(current), desired, rpc_timeout=RPC_TIMEOUT
                        )
                    else:

                        def update(input):
                            return ScheduleUpdate(
                                dataclasses.replace(
                                    input.description.schedule,
                                    action=desired.action,
                                    spec=desired.spec,
                                    state=desired.state,
                                )
                            )

                        await handle.update(update, rpc_timeout=RPC_TIMEOUT)
                return await _inspect(current, client, applied=True)

        try:
            observation, fingerprint = apply()
        except Exception as exc:  # noqa: BLE001 — engine acceptance can precede a lost response
            return _failure(current, exc)
        if observation.confirmed:
            Workflow.objects.filter(pk=current.pk).update(
                schedule_managed=active,
                schedule_revision=desired_revision(current) if active else "",
                schedule_spec_hash=fingerprint if active else "",
            )
        return observation


def sync_workflow_schedule(workflow, *, authorize=None):
    return reconcile_workflow_schedule(
        workflow,
        expected_version=workflow.version,
        expected_active=schedule_inactive_reason(workflow) is None,
        authorize=authorize,
    )


def delete_workflow_schedule(workflow, *, authorize=None):
    return reconcile_workflow_schedule(
        workflow, expected_version=workflow.version, expected_active=False, authorize=authorize
    )


def write_workflow_schedule(workflow):
    result = reconcile_workflow_schedule(workflow, expected_version=workflow.version, expected_active=True)
    if not result.confirmed:
        raise RuntimeError(result.error_code or "schedule_not_confirmed")
    return result
