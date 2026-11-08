"""Exact rotation reset boundaries and native-history proof; no reset RPCs here."""

from datetime import timedelta

from asgiref.sync import sync_to_async
from django.utils import timezone
from nvke.client import NvkeError
from nvke.native_workflows import validate_input
from temporalio.api.common.v1 import WorkflowExecution
from temporalio.api.enums.v1 import EventType, WorkflowTaskFailedCause
from temporalio.api.workflowservice.v1 import GetWorkflowExecutionHistoryRequest

ROTATION = "RotateEtcdEncryptionKey"
MODES = {"retry_activity", "restart_rewrite", "resume_rewrite", "refresh_survivors"}
PHASES = {"prepare", "add_secondary", "promote_primary", "rewrite", "remove_old_keys", "finalize"}
OPERATIONS = {
    "add_secondary": "etcd-encryption.add-secondary-encryption-key",
    "promote_primary": "etcd-encryption.promote-primary-encryption-key",
    "remove_old_keys": "etcd-encryption.remove-old-encryption-keys",
    "rewrite": "etcd-encryption.promote-primary-encryption-key",
}


def refused(message="Review the exact phase-specific native rotation reset boundary"):
    return NvkeError("PRECONDITION", message)


async def events(client, authority, workflow_id, run_id, checkpoint):
    token = b""
    while True:
        await sync_to_async(checkpoint, thread_sensitive=True)()
        page = await client.workflow_service.get_workflow_execution_history(
            GetWorkflowExecutionHistoryRequest(
                namespace=authority.namespace,
                execution=WorkflowExecution(workflow_id=workflow_id, run_id=run_id),
                maximum_page_size=200,
                next_page_token=token,
            ),
            retry=False,
            timeout=timedelta(seconds=authority.timeout_seconds),
        )
        await sync_to_async(checkpoint, thread_sensitive=True)()
        if page.raw_history or page.archived:
            raise refused("Retained decoded native history is required for reset review")
        for event in page.history.events:
            yield event
        if not page.next_page_token:
            return
        if page.next_page_token == token:
            raise refused("Native history did not advance during reset review")
        token = page.next_page_token


async def describe(client, authority, workflow_id, checkpoint, run_id=None):
    await sync_to_async(checkpoint, thread_sensitive=True)()
    result = await client.get_workflow_handle(workflow_id, run_id=run_id).describe(
        rpc_timeout=timedelta(seconds=authority.timeout_seconds)
    )
    await sync_to_async(checkpoint, thread_sensitive=True)()
    return result


async def first_input(client, authority, workflow_id, run_id, checkpoint, expected_type):
    stream = events(client, authority, workflow_id, run_id, checkpoint)
    try:
        first = await anext(stream)
    finally:
        await stream.aclose()
    start = first.workflow_execution_started_event_attributes
    if (
        first.event_id != 1
        or start.workflow_type.name != expected_type
        or start.task_queue.name != authority.task_queue
    ):
        raise refused("Native workflow type or queue did not match the reviewed rotation")
    parameters = await client.data_converter.decode(start.input.payloads)
    if len(parameters) != 1:
        raise refused()
    return validate_input(expected_type, parameters[0])


def target_policy(
    request, name, parameters, completed, business_failed, start_acknowledged, start_time, first_task
):
    phase, mode = request["phase"], request["mode"]
    if request["workflow_task_event_id"] <= first_task:
        raise refused("Do not reset a rotation to its first workflow task or before ownership")
    if mode == "refresh_survivors":
        if (
            phase not in {"add_secondary", "promote_primary", "rewrite", "remove_old_keys"}
            or name != "PrepareEtcdEncryptionKeyRotation"
        ):
            raise refused()
        return
    if mode == "restart_rewrite":
        if phase != "rewrite" or name != "StartEtcdEncryptionRewrite":
            raise refused()
        return
    if mode == "resume_rewrite":
        if (
            phase != "rewrite"
            or name != "GetEtcdEncryptionRewriteStatus"
            or not start_acknowledged
            or start_time is None
            or timezone.now() >= start_time + timedelta(hours=6)
        ):
            raise refused("Resume only an acknowledged rewrite within its original six-hour poll allowance")
        return
    rollout_steps = {
        "rollout.validate",
        "rollout.cluster_preflight",
        "rollout.node_preflight",
        "rollout.build_apply_to_node_payload",
        "rollout.apply_to_node",
        "rollout.check_post_condition",
    }
    allowed = {
        "prepare": {
            "PrepareEtcdEncryptionKeyRotation",
            "ValidateEtcdEncryptionHealth",
            "EnsurePendingEtcdEncryptionRotationKey",
            "rollout.load_cluster_for_rollout",
        },
        "add_secondary": rollout_steps,
        "promote_primary": rollout_steps,
        "rewrite": rollout_steps | {"ValidateEtcdRotationMembership"},
        "remove_old_keys": rollout_steps,
        "finalize": {"FinalizeEtcdRotationKey", "CompleteEtcdEncryptionKeyRotation"},
    }
    if name == "RecordEtcdKeyRotationPhase":
        next_phase = {
            "prepare": "add_secondary",
            "add_secondary": "promote_primary",
            "promote_primary": "rewrite",
            "rewrite": "remove_old_keys",
            "remove_old_keys": "finalize",
        }
        if (
            not isinstance(parameters, dict)
            or parameters.get("ClusterID") != request["clusterID"]
            or parameters.get("WorkflowID") != request["workflow_id"]
            or parameters.get("Phase") != next_phase.get(phase)
            or completed
        ):
            raise refused("Retry only the next uncommitted phase transition for the exact retained owner")
        return
    if name not in allowed[phase]:
        raise refused()
    if phase == "rewrite" and not start_acknowledged:
        raise refused("Post-rewrite recovery must preserve the acknowledged rewrite start")
    if name.startswith("rollout.") and (
        not isinstance(parameters, dict)
        or parameters.get("ClusterID") != request["clusterID"]
        or (
            name
            not in {"rollout.cluster_preflight", "rollout.node_preflight", "rollout.load_cluster_for_rollout"}
            and parameters.get("OperationName") != OPERATIONS.get(phase)
        )
    ):
        raise refused("The selected rollout activity belongs to another cluster or key phase")
    if (
        completed
        and not business_failed
        and not (phase == "prepare" and request["recorded_state"] == "running")
    ):
        raise refused("Preserve earlier successful mutations instead of replaying them")


async def review(client, authority, request, checkpoint):
    current = await describe(client, authority, request["workflow_id"], checkpoint)
    if (
        current.run_id != request["base_run_id"]
        or not current.status
        or current.status.name not in {"FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}
        or current.raw_description.pending_activities
    ):
        raise refused(
            "The exact reviewed native rotation must be closed unsuccessfully with no pending activities"
        )
    if request["phase"] == "prepare" and (
        request["recorded_state"] != "running" or current.status.name not in {"TERMINATED", "TIMED_OUT"}
    ):
        raise refused("A recorded prepare failure uses a fresh rotation ID after lock release, not reset")
    params = await first_input(
        client, authority, request["workflow_id"], current.run_id, checkpoint, ROTATION
    )
    if params != {"clusterID": request["clusterID"]}:
        raise refused("The reset owner did not prove the reviewed cluster UUID")
    first_task = None
    selected = None
    selected_task = False
    completed = business_failed = False
    starts, acknowledged, start_time = {}, False, None
    accepted, begin_inputs = False, {}
    phase_inputs, boundary_phase = {}, "prepare"
    acknowledged_input = acknowledged_operation = None
    async for event in events(client, authority, request["workflow_id"], current.run_id, checkpoint):
        if event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED:
            first_task = first_task or event.event_id
            selected_task |= event.event_id == request["workflow_task_event_id"]
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
            attr = event.activity_task_scheduled_event_attributes
            if attr.activity_type.name == "RecordEtcdKeyRotationPhase":
                phase_inputs[event.event_id] = attr.input
            if attr.activity_type.name == "BeginEtcdEncryptionKeyRotation":
                begin_inputs[event.event_id] = attr.input
            if attr.activity_type.name == "StartEtcdEncryptionRewrite":
                starts[event.event_id] = (
                    event.event_time.ToDatetime(tzinfo=timezone.get_current_timezone()),
                    attr.input,
                )
            if event.event_id == request["activity_event_id"]:
                selected = attr
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
            attr = event.activity_task_completed_event_attributes
            before_boundary = event.event_id < request["workflow_task_event_id"]
            if attr.scheduled_event_id in phase_inputs and before_boundary:
                inputs = await client.data_converter.decode(phase_inputs[attr.scheduled_event_id].payloads)
                if (
                    len(inputs) != 1
                    or not isinstance(inputs[0], dict)
                    or inputs[0].get("ClusterID") != request["clusterID"]
                    or inputs[0].get("WorkflowID") != request["workflow_id"]
                ):
                    raise refused("Native phase history did not prove the retained owner and cluster")
                boundary_phase = inputs[0].get("Phase")
            if attr.scheduled_event_id in begin_inputs and before_boundary:
                inputs = await client.data_converter.decode(begin_inputs[attr.scheduled_event_id].payloads)
                accepted = inputs == [
                    {"ClusterID": request["clusterID"], "WorkflowID": request["workflow_id"]}
                ]
            if attr.scheduled_event_id in starts and before_boundary:
                start_time, start_input = starts[attr.scheduled_event_id]
                inputs = await client.data_converter.decode(start_input.payloads)
                results = await client.data_converter.decode(attr.result.payloads)
                acknowledged_input = inputs[0] if len(inputs) == 1 else None
                acknowledged_operation = results[0] if len(results) == 1 else None
                acknowledged = (
                    isinstance(acknowledged_input, dict)
                    and isinstance(acknowledged_operation, dict)
                    and isinstance(acknowledged_operation.get("OperationID"), str)
                    and bool(acknowledged_operation["OperationID"])
                )
            if attr.scheduled_event_id == request["activity_event_id"]:
                completed = True
                values = await client.data_converter.decode(attr.result.payloads)
                business_failed = (
                    len(values) == 1
                    and isinstance(values[0], dict)
                    and any(
                        isinstance(values[0].get(key), str) and values[0][key]
                        for key in ("ApplyErr", "CheckErr", "BuildErr", "ValidateErr")
                    )
                )
    if (
        first_task is None
        or not accepted
        or not selected_task
        or selected is None
        or selected.workflow_task_completed_event_id != request["workflow_task_event_id"]
        or selected.activity_type.name != request["activity_type"]
    ):
        raise refused("The selected activity was not scheduled by the reviewed workflow task")
    if request["mode"] != "refresh_survivors" and boundary_phase != request["phase"]:
        raise refused("The reset boundary precedes the recorded key phase; preserve earlier phase work")
    values = await client.data_converter.decode(selected.input.payloads)
    parameters = values[0] if len(values) == 1 else None
    if request["mode"] == "resume_rewrite" and (
        not acknowledged
        or not isinstance(parameters, dict)
        or parameters.get("NodeID") != acknowledged_input.get("NodeID")
        or parameters.get("OperationID") != acknowledged_operation.get("OperationID")
    ):
        raise refused("Polling must retain the exact acknowledged rewrite operation and node")
    target_policy(
        request,
        selected.activity_type.name,
        parameters,
        completed,
        business_failed,
        acknowledged,
        start_time,
        first_task,
    )
    if request["mode"] == "refresh_survivors":
        retirement = await describe(client, authority, request["retirement_workflow_id"], checkpoint)
        if not retirement.status or retirement.status.name != "COMPLETED":
            raise refused("The reviewed lost-node retirement must have completed before survivor resnapshot")
        retired = await first_input(
            client,
            authority,
            request["retirement_workflow_id"],
            retirement.run_id,
            checkpoint,
            "RetireLostEtcdRotationNode",
        )
        if (
            retired["clusterID"] != request["clusterID"]
            or retired["rotationWorkflowID"] != request["workflow_id"]
        ):
            raise refused("The retirement did not prove this cluster and retained rotation owner")
    final = await describe(client, authority, request["workflow_id"], checkpoint)
    if (
        final.run_id != current.run_id
        or final.status != current.status
        or final.raw_description.pending_activities
    ):
        raise refused("The native rotation changed while reviewing reset history")
    return current


async def proof(client, authority, request, checkpoint, reason, run_id):
    params = await first_input(client, authority, request["workflow_id"], run_id, checkpoint, ROTATION)
    if params != {"clusterID": request["clusterID"]}:
        raise refused("Reset history did not match the exact cluster")
    async for event in events(client, authority, request["workflow_id"], run_id, checkpoint):
        if event.event_type != EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED:
            continue
        attr = event.workflow_task_failed_event_attributes
        if (
            attr.cause == WorkflowTaskFailedCause.WORKFLOW_TASK_FAILED_CAUSE_RESET_WORKFLOW
            and attr.base_run_id == request["base_run_id"]
            and attr.new_run_id == run_id
            and reason in attr.failure.message
        ):
            if event.event_id != request["workflow_task_event_id"]:
                raise refused("Native reset history used another workflow task boundary")
            return True
    return False
