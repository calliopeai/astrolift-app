"""One-way agent dispatch; repeated attempts observe the original job UUID only."""

from django.utils import timezone
from nvke.client import NvkeError

from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_operator_authority as authority
from astrolift_clusters import nvke_operator_jobs as jobs
from astrolift_clusters.models import NvkeOperation
from astrolift_clusters.nvke_runtime import _state
from astrolift_clusters.nvke_sites import NvkeSiteError
from core.permissions import PermissionDenied


def record_peer(row, progress, peer, *, audit_actor_id=None, observe_only=False):
    if peer["effectful"] != row.authority["tooling_effectful"]:
        raise NvkeError("OPERATOR_INVALID_RESPONSE", "The original operator effect classification changed")
    progress["operator_job"] = peer
    state = {
        "completed": "succeeded",
        "failed": "failed",
        "cancelled": "cancelled",
        "uncertain": "uncertain",
    }.get(peer["state"], "uncertain" if observe_only else "awaiting_ready")
    stage = {
        "succeeded": "operator_process_completed",
        "failed": "operator_process_failed",
        "cancelled": "operator_cancelled_before_execution",
        "uncertain": "operator_review_required",
    }.get(state, "operator_process_running")
    error = (
        NvkeError(
            "OPERATOR_REVIEW_REQUIRED",
            "Inspect the original operator receipt and private diagnostics; remote effects are not cancelled",
        )
        if state == "uncertain"
        else NvkeError(
            "OPERATOR_PROCESS_FAILED", "The native process failed; inspect its private diagnostics"
        )
        if state == "failed"
        else None
    )
    return _state(
        row,
        state,
        stage,
        progress=progress,
        error=error,
        reference={
            "kind": "operator_job",
            "id": str(row.guid),
            "instance_id": row.authority["operator_instance_id"],
        },
        audit_actor_id=audit_actor_id,
    )


def attempt(row, *, execution):
    progress = outbox.decode_result(row)

    def fence():
        current = NvkeOperation.objects.get(pk=row.pk)
        if (
            current.generation != row.generation
            or current.workflow_run_id != execution[1]
            or current.terminal
        ):
            raise NvkeError("EXECUTION_REFUSED", "Original operator execution authority changed")
        if timezone.now() >= current.deadline:
            raise NvkeError("DEADLINE_EXCEEDED", "Operator observation deadline was reached")
        return authority.checkpoint(row)

    try:
        selected = fence()
        if row.cancellation_requested_at and not progress.get("sending"):
            return _state(row, "cancelled", "operator_cancelled_before_dispatch", progress=progress)
        with authority.open_agent(selected, checkpoint=fence) as client:
            if not progress.get("sending"):
                client.identity()
                request = outbox.decode_input(row)
                fence()
                progress.update(sending=True, target={"kind": "operator_job", "id": str(row.guid)})
                _state(row, "running", "operator_sending", progress=progress)
                try:
                    peer = client.submit(str(row.guid), request, intent=row.request_digest)
                except NvkeError as error:
                    if error.code == "OPERATOR_BUSY":
                        progress["sending"] = False
                        return _state(row, "queued", "operator_waiting_for_agent", progress=progress)
                    # A lost/rejected reply never authorizes another native process.
                    peer = client.observe(
                        str(row.guid), command=jobs.command_key(row.operation_key), intent=row.request_digest
                    )
            else:
                peer = client.observe(
                    str(row.guid), command=jobs.command_key(row.operation_key), intent=row.request_digest
                )
            if peer["state"] in {"preparing", "queued", "dispatching", "running"}:
                peer = client.observe(
                    str(row.guid), command=jobs.command_key(row.operation_key), intent=row.request_digest
                )
            current = NvkeOperation.objects.get(pk=row.pk)
            if current.cancellation_requested_at and peer["state"] not in {
                "completed",
                "failed",
                "cancelled",
                "uncertain",
            }:
                peer = client.cancel(
                    str(row.guid), command=jobs.command_key(row.operation_key), intent=row.request_digest
                )
            return record_peer(row, progress, peer)
    except (NvkeError, NvkeSiteError, PermissionDenied) as error:
        return _state(
            row,
            "uncertain" if progress.get("sending") else "failed",
            "operator_review_required" if progress.get("sending") else "operator_dispatch_refused",
            progress=progress,
            error=error
            if isinstance(error, (NvkeError, NvkeSiteError))
            else NvkeError("AUTHORITY_REVOKED", "Original operator caller is no longer authorized"),
        )
    except Exception:
        return _state(
            row,
            "uncertain" if progress.get("sending") else "failed",
            "operator_review_required",
            progress=progress,
            error=NvkeError(
                "OPERATOR_UNCONFIRMED", "Retain the original operator receipt; no native replay is authorized"
            ),
        )
