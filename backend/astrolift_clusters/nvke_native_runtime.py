"""Recover the exact native execution; a missing start reply never permits replay."""

from datetime import timedelta

from asgiref.sync import async_to_sync, sync_to_async
from django.utils import timezone
from nvke.client import NvkeError
from nvke.native_workflows import validate_input
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

from astrolift_clusters import nvke_native_operations as admission
from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_temporal as native
from astrolift_clusters.models import NvkeOperation
from astrolift_clusters.nvke_runtime import _state
from astrolift_clusters.nvke_sites import NvkeSiteError
from core.permissions import PermissionDenied


def native_workflow_id(row):
    return f"astro-nvke-{row.guid}"


def attempt(row, *, execution):
    progress = outbox.decode_result(row)

    def fence():
        current = NvkeOperation.objects.get(pk=row.pk)
        if (
            current.generation != row.generation
            or current.workflow_run_id != execution[1]
            or current.terminal
        ):
            raise NvkeError("EXECUTION_REFUSED", "Native NVKE execution authority changed")
        if timezone.now() >= current.deadline:
            raise NvkeError("DEADLINE_EXCEEDED", "Native NVKE observation deadline was reached")
        return admission.checkpoint(row)

    def prepare_start():
        authority = fence()
        current = NvkeOperation.objects.get(pk=row.pk)
        if current.cancellation_requested_at:
            raise NvkeError("OPERATION_CANCELLED", "Native workflow cancelled before dispatch")
        if not NvkeOperation.objects.filter(
            guid=row.authority["source_id"],
            site_id=row.site_id,
            organization_id=row.organization_id,
            version=row.authority["source_version"],
            status="succeeded",
            operation_key="getCluster",
        ).exists():
            raise NvkeError("SOURCE_CHANGED", "The reviewed cluster observation changed before dispatch")
        progress.update(sending=True, target={"workflow_id": native_workflow_id(row)})
        _state(row, "running", "native_sending", progress=progress)
        return authority

    async def inspect():
        authority = await sync_to_async(fence, thread_sensitive=True)()
        client = await native.connect_native(authority, fence)
        timeout = timedelta(seconds=authority.timeout_seconds)
        identity = native_workflow_id(row)
        if not progress.get("sending"):
            if name == "RetireLostEtcdRotationNode":
                owner = client.get_workflow_handle(parameters["rotationWorkflowID"])
                owner_description = await owner.describe(rpc_timeout=timeout)
                if not owner_description.status or owner_description.status.name not in {
                    "FAILED",
                    "CANCELED",
                    "TERMINATED",
                    "TIMED_OUT",
                }:
                    raise NvkeError(
                        "PRECONDITION", "The reviewed rotation owner must be closed without success"
                    )
                owner = client.get_workflow_handle(
                    parameters["rotationWorkflowID"], run_id=owner_description.run_id
                )
                first = await anext(owner.fetch_history_events(page_size=1, rpc_timeout=timeout))
                started = first.workflow_execution_started_event_attributes
                values = await client.data_converter.decode(started.input.payloads)
                if (
                    first.event_id != 1
                    or started.workflow_type.name != "RotateEtcdEncryptionKey"
                    or started.task_queue.name != authority.task_queue
                    or outbox.canonical(values) != outbox.canonical([{"clusterID": parameters["clusterID"]}])
                ):
                    raise NvkeError(
                        "PRECONDITION", "The retained owner does not match this cluster's native rotation"
                    )
            await sync_to_async(prepare_start, thread_sensitive=True)()
            await sync_to_async(fence, thread_sensitive=True)()
            try:
                await client.start_workflow(
                    name,
                    parameters,
                    id=identity,
                    task_queue=authority.task_queue,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
                    request_id=str(row.guid),
                    rpc_timeout=timeout,
                    memo={"astro_nvke_request": row.request_digest},
                )
            except Exception:
                # Confirm from server history, including when the start succeeded
                # but its acknowledgement was lost. Never start this ID again.
                pass
        await sync_to_async(fence, thread_sensitive=True)()
        handle = client.get_workflow_handle(identity, run_id=progress.get("native_run_id"))
        description = await handle.describe(rpc_timeout=timeout)
        run_id = description.run_id
        handle = client.get_workflow_handle(identity, run_id=run_id)
        first = await anext(handle.fetch_history_events(page_size=1, rpc_timeout=timeout))
        start = first.workflow_execution_started_event_attributes
        values = await client.data_converter.decode(start.input.payloads)
        memo = start.memo.fields.get("astro_nvke_request")
        proof = await client.data_converter.decode([memo]) if memo is not None else []
        if (
            first.event_id != 1
            or start.workflow_type.name != name
            or start.task_queue.name != authority.task_queue
            or outbox.canonical(values) != outbox.canonical([parameters])
            or proof != [row.request_digest]
            or start.original_execution_run_id != run_id
            or start.continued_execution_run_id
            or start.attempt != 1
            or (progress.get("native_run_id") and progress["native_run_id"] != run_id)
        ):
            raise NvkeError("SOURCE_CHANGED", "Native workflow history does not match the admitted request")
        await sync_to_async(fence, thread_sensitive=True)()
        progress["native_run_id"] = run_id
        progress["native_status"] = description.status.name if description.status else "UNKNOWN"
        reference = {
            "kind": "native_workflow",
            "workflow_id": identity,
            "run_id": run_id,
            "workflow_type": name,
        }
        if progress["native_status"] == "COMPLETED":
            return await sync_to_async(_state, thread_sensitive=True)(
                row,
                "succeeded",
                "native_workflow_completed",
                progress=progress,
                reference=reference,
            )
        if progress["native_status"] in {"FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
            return await sync_to_async(_state, thread_sensitive=True)(
                row,
                "failed",
                "native_workflow_closed",
                progress=progress,
                reference=reference,
                error=NvkeError(
                    "NATIVE_WORKFLOW_FAILED",
                    "Native workflow closed without successful completion; review retained ownership",
                ),
            )
        if progress["native_status"] != "RUNNING":
            raise NvkeError("SOURCE_CHANGED", "Native workflow has an unsupported execution state")
        return await sync_to_async(_state, thread_sensitive=True)(
            row,
            "awaiting_ready",
            "native_workflow_running",
            progress=progress,
            reference=reference,
        )

    try:
        admission.checkpoint(row)
        name = admission.workflow_name(row.operation_key)
        parameters = validate_input(name, outbox.decode_input(row))
        if row.cancellation_requested_at and not progress.get("sending"):
            return _state(row, "cancelled", "cancelled_before_dispatch")
        return async_to_sync(inspect)()
    except PermissionDenied:
        return _state(
            row,
            "uncertain" if progress.get("sending") else "failed",
            "native_review_required",
            progress=progress,
            error=NvkeError("AUTHORITY_REVOKED", "The original native NVKE caller is no longer authorized"),
        )
    except (NvkeError, NvkeSiteError) as exc:
        safe = NvkeError(exc.code, str(exc))
        if exc.code == "OPERATION_CANCELLED" and not progress.get("sending"):
            return _state(row, "cancelled", "cancelled_before_dispatch", error=safe)
        return _state(
            row,
            "uncertain" if progress.get("sending") else "failed",
            "native_review_required",
            error=safe,
            progress=progress,
        )
    except Exception:
        return _state(
            row,
            "uncertain" if progress.get("sending") else "failed",
            "native_review_required",
            progress=progress,
            error=NvkeError(
                "NATIVE_TEMPORAL_UNAVAILABLE",
                "Native execution could not be confirmed; retain its original workflow identity",
            ),
        )
