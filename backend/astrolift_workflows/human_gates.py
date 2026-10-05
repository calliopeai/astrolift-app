"""Exact, authorized human decisions with durable Temporal update recovery."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID

from asgiref.sync import async_to_sync

from astrolift_workflows import client as engine
from astrolift_workflows.schema.stage_temporal_types import stage_temporal_execution
from core.permissions import Permission
from core.tenancy import get_current_tenant
from workflows.human_gate_protocol import GATE_UPDATE, decision_update_id, validate_gate_request
from workflows.scopes import may_decide_human_gate, visible_runs


def find_gate(user, execution_id, stage_execution_id):
    from astrolift_operations.models import WorkflowRun
    from workflows.models import WorkflowStageExecution

    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None:
        return None
    try:
        run_guid, stage_guid = UUID(str(execution_id)), UUID(str(stage_execution_id))
    except (ValueError, TypeError):
        return None
    runs = visible_runs(
        WorkflowRun.objects.filter(
            guid=run_guid,
            organization_id=tenant.organization_id,
            deleted_at__isnull=True,
            workflow_kind="WorkflowDefinitionRunWorkflow",
        ),
        tenant.organization_id,
        Permission.WORKFLOW_TRIGGER,
    )
    row = (
        WorkflowStageExecution.objects.filter(
            guid=stage_guid,
            workflow_run_id__in=runs.values("pk"),
            deleted_at__isnull=True,
            stage__kind="human_gate",
            stage__deleted_at__isnull=True,
        )
        .select_related("stage", "workflow_run")
        .first()
    )
    return row if row is not None and may_decide_human_gate(user, row.stage.approvers) else None


async def _engine_request(identity, update_id, payload=None):
    from temporalio.client import WorkflowUpdateFailedError
    from temporalio.service import RPCError, RPCStatusCode

    client = await asyncio.wait_for(engine._get_client_async(), 5)
    if client.namespace != identity.namespace:
        return None, "unknown", "The stage belongs to a different Temporal namespace"
    handle = client.get_workflow_handle(identity.workflow_id, run_id=identity.run_id)
    description = await handle.describe(rpc_timeout=timedelta(seconds=5))
    if description.run_id != identity.run_id or description.workflow_type != "WorkflowDefinitionRunWorkflow":
        return None, "unknown", "The engine returned a different execution"
    try:
        if payload is None:
            result = await handle.get_update_handle(update_id).result(rpc_timeout=timedelta(seconds=5))
        else:
            result = await handle.execute_update(
                GATE_UPDATE, payload, id=update_id, rpc_timeout=timedelta(seconds=10)
            )
    except WorkflowUpdateFailedError:
        return None, "refused", "The worker refused the decision; inspect the gate before retrying"
    except RPCError as exc:
        if payload is None and exc.status == RPCStatusCode.NOT_FOUND:
            return None, "not_requested", ""
        raise
    if not isinstance(result, dict) or result.get("state") != "requested":
        return None, "unknown", "The engine returned an unsupported decision receipt"
    request = {key: value for key, value in result.items() if key != "state"}
    validate_gate_request(request)
    if decision_update_id(request["execution_guid"]) != update_id:
        return None, "unknown", "The engine returned a different decision receipt"
    return request, "requested", ""


def request_engine(row, payload=None):
    identity = stage_temporal_execution(row)
    if identity is None:
        return None, "unbound", "This stage has no captured Temporal incarnation"
    if not engine._temporal_enabled():
        return None, "unknown", "Temporal is disabled; no decision was submitted"
    try:
        request, state, error = async_to_sync(_engine_request)(
            identity, decision_update_id(str(row.guid)), payload
        )
        if request is not None and request["execution_id"] != str(row.pk):
            return None, "unknown", "The engine returned a different stage receipt"
        return request, state, error
    except Exception:
        return (
            None,
            "unknown",
            (
                "Decision delivery is unknown; recover this exact gate before retrying"
                if payload is not None
                else "The decision receipt could not be observed"
            ),
        )


def gate_state(row, user, observation=None):
    from astrolift_workflows.schema.human_gate_types import HumanGateDecisionState

    request, state, error = observation if observation is not None else request_engine(row)
    recorded = (row.output or {}).get("human_gate")
    recorded = recorded if isinstance(recorded, dict) else {}
    decision = recorded.get("decision")
    decision = decision if decision in ("approved", "rejected") else None
    if recorded.get("request_id") == str(row.guid) and decision:
        state = "recorded"
        request = {
            "execution_id": str(row.pk),
            "execution_guid": str(row.guid),
            "decision": decision,
            "decided_by_user_id": recorded.get("decided_by_user_id"),
            "note": recorded.get("note", ""),
        }
    elif row.is_terminal or row.workflow_run.status != "running" or row.workflow_run.ended_at is not None:
        state = "closed"
    return HumanGateDecisionState(
        execution_guid=str(row.workflow_run.guid),
        stage_execution_guid=str(row.guid),
        stage_guid=str(row.stage.guid),
        temporal_execution=stage_temporal_execution(row),
        stage_status=row.status,
        run_status=row.workflow_run.status,
        request_state=state,
        requested_decision=request["decision"] if request else None,
        recorded_decision=decision,
        note=request["note"] if request else str(recorded.get("note", "")),
        decided_by_me=(request["decided_by_user_id"] == user.pk)
        if request
        else (recorded.get("decided_by_user_id") == user.pk if decision else None),
        observation_error=error,
    ), request


def decide_gate(row, user, *, decision, note, confirmed, temporal_run_id):
    if confirmed is not True:
        raise ValueError("An explicit user decision must be confirmed")
    payload = {
        "execution_id": str(row.pk),
        "execution_guid": str(row.guid),
        "decision": decision,
        "decided_by_user_id": user.pk,
        "note": note,
    }
    validate_gate_request(payload)
    identity = stage_temporal_execution(row)
    if identity is None or temporal_run_id != identity.run_id:
        raise ValueError("The reviewed Temporal incarnation does not match this stage")
    state, prior = gate_state(row, user)
    if prior is not None:
        return (
            state,
            "" if prior == payload else "A different decision has already been accepted for this gate",
        )
    if state.request_state != "not_requested":
        return state, state.observation_error or "This gate is no longer awaiting a new decision"
    # Re-read live run/approver authority immediately before admission. The
    # worker validator independently refuses gates that have already moved on.
    current = find_gate(user, str(row.workflow_run.guid), str(row.guid))
    if current is None:
        raise ValueError("Human gate not found or no longer permitted")
    if (
        current.status != "running"
        or current.workflow_run.status != "running"
        or current.workflow_run.ended_at
    ):
        return gate_state(current, user)[0], "This gate is no longer awaiting a new decision"
    if stage_temporal_execution(current) != identity:
        raise ValueError("The stage identity changed; inspect the gate again")
    observed = request_engine(current, payload)
    current.refresh_from_db()
    state, admitted = gate_state(current, user, observed)
    if admitted is None:
        return state, state.observation_error or "Decision was not accepted"
    return (
        state,
        "" if admitted == payload else "A different decision has already been accepted for this gate",
    )
