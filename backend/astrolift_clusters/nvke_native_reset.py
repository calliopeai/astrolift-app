"""Durable rotation reset intent; sending is one-way even when the reply is lost."""

import asyncio
import hashlib
import hmac
import logging
from datetime import timedelta
from uuid import UUID

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from nvke.client import NvkeError
from nvke.contract import upstream_revision
from nvke.operations import action, execute_action
from temporalio.api.common.v1 import WorkflowExecution
from temporalio.api.enums.v1 import ResetReapplyType
from temporalio.api.workflowservice.v1 import ResetWorkflowExecutionRequest

from astrolift_clusters import nvke_maintenance as maintenance
from astrolift_clusters import nvke_native_operations as native_operations
from astrolift_clusters import nvke_native_reset_review as review
from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_sites as sites
from astrolift_clusters import nvke_temporal as native
from astrolift_clusters.models import NvkeOperation, NvkeTemporalAuthority
from astrolift_clusters.nvke_connections import open_connection
from astrolift_clusters.nvke_runtime import _state
from astrolift_identity.api_tokens import get_current_api_token
from core.permissions import PermissionDenied
from core.secrets import encrypt_at_rest

KEY = "temporal.reset.RotateEtcdEncryptionKey"
logger = logging.getLogger(__name__)


def _uuid(value):
    try:
        result = UUID(str(value))
        if not result.int or str(result) != value:
            raise ValueError
        return str(result)
    except (ValueError, TypeError, AttributeError):
        raise NvkeError("INVALID_INPUT", "Use an exact nonzero reset request/run/source UUID") from None


def _event(value):
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith("0")
        or len(value) > 19
        or int(value) > 9223372036854775807
    ):
        raise NvkeError("INVALID_INPUT", "Use exact positive decimal-string native event IDs")
    return int(value)


def _text(value, maximum, *, multiline=False):
    try:
        if (
            isinstance(value, str)
            and value.strip()
            and (value.isprintable() or (multiline and all(c.isprintable() or c in "\n\r\t" for c in value)))
            and len(value.encode("utf-8")) <= maximum
        ):
            return value
    except UnicodeError:
        pass
    raise NvkeError(
        "INVALID_INPUT", "Provide bounded reviewed reset rationale and node-work evidence references"
    )


def _owner(observation, request):
    owner = observation.get("etcd_key_rotation") or {}
    if (
        observation.get("mutation_lock_reported") is not True
        or observation.get("mutation_lock_owner") != "etcd_key_rotation"
        or owner.get("workflow_id") != request["workflow_id"]
        or owner.get("phase") != request["phase"]
        or owner.get("state") not in {"failed", "running"}
    ):
        raise review.refused("The exact rotation owner, phase and mutation lock must still be recorded")
    return owner


def reserve(
    *,
    site_id,
    expected_site_version,
    expected_actor_id,
    expected_authority_version,
    source_operation_id,
    expected_source_version,
    request_id,
    workflow,
    workflow_id,
    base_run_id,
    workflow_task_event_id,
    activity_event_id,
    activity_type,
    phase,
    mode,
    rationale,
    quiescence_evidence,
    acknowledge_boundary_reviewed=False,
    acknowledge_conflicting_work_stopped=False,
    retirement_workflow_id="",
    timeout_seconds=7200,
):
    org, actor = native._actor(expected_actor_id, require_read=True)
    if workflow != review.ROTATION:
        raise NvkeError(
            "UNSUPPORTED",
            "Only the documented etcd rotation reset procedure is supported; renewal reset is not recovery",
        )
    if (
        phase not in review.PHASES
        or mode not in review.MODES
        or type(timeout_seconds) is not int
        or not 30 <= timeout_seconds <= 86400
    ):
        raise NvkeError(
            "INVALID_INPUT", "Choose a declared native rotation phase/mode and observation deadline 30–86400"
        )
    if acknowledge_boundary_reviewed is not True or acknowledge_conflicting_work_stopped is not True:
        raise review.refused("Explicitly review the phase boundary and confirm conflicting node work stopped")
    identity, base, source_id = _uuid(request_id), _uuid(base_run_id), _uuid(source_operation_id)
    parameters = {
        "workflow_id": _text(workflow_id, 1000),
        "base_run_id": base,
        "workflow_task_event_id": _event(workflow_task_event_id),
        "activity_event_id": _event(activity_event_id),
        "activity_type": _text(activity_type, 128),
        "phase": phase,
        "mode": mode,
        "rationale": _text(rationale, 4000, multiline=True),
        "quiescence_evidence": _text(quiescence_evidence, 4000, multiline=True),
        "acknowledge_boundary_reviewed": True,
        "acknowledge_conflicting_work_stopped": True,
        "retirement_workflow_id": _text(retirement_workflow_id, 1000) if retirement_workflow_id else "",
    }
    if mode == "refresh_survivors" and not parameters["retirement_workflow_id"]:
        raise review.refused("Review the completed lost-node retirement before survivor resnapshot")
    with transaction.atomic():
        site = sites.site_row(site_id, org, locked=True)
        digest = hmac.new(
            str(settings.SECRET_KEY).encode(),
            outbox.canonical(
                {
                    "site": str(site.guid),
                    "site_version": expected_site_version,
                    "authority_version": expected_authority_version,
                    "source": source_id,
                    "source_version": expected_source_version,
                    "parameters": parameters,
                    "timeout": timeout_seconds,
                    "key": KEY,
                }
            ),
            hashlib.sha256,
        ).hexdigest()
        existing = (
            NvkeOperation.all_objects.select_for_update()
            .filter(site=site, actor=actor, request_id=identity)
            .first()
        )
        if existing is not None:
            if existing.deleted_at is not None or not hmac.compare_digest(existing.request_digest, digest):
                raise NvkeError("CONFLICT", "This request ID identifies a different reset review")
            if not existing.terminal:
                transaction.on_commit(lambda: outbox.dispatch(existing.pk))
            return existing
        sites._version(site, expected_site_version)
        if not site.is_active or site.upstream_revision != upstream_revision():
            raise NvkeError("SOURCE_CHANGED", "Native reset site authority changed")
        authority = (
            NvkeTemporalAuthority.objects.select_related("site").filter(site=site, is_enabled=True).first()
        )
        if authority is None:
            raise NvkeError("SURFACE_UNAVAILABLE", "Configure explicit native Temporal authority first")
        sites._version(authority, expected_authority_version)
        source, observation, controller = native_operations.cluster_observation(
            site, actor, source_id, expected_source_version
        )
        owner = _owner(observation, parameters)
        parameters.update(clusterID=observation["cluster_id"], recorded_state=owner["state"])
        if (
            NvkeOperation.all_objects.filter(
                site=site,
                operation_key__startswith="temporal.",
                authority__native_cluster_id=parameters["clusterID"],
            )
            .exclude(status__in=["succeeded", "failed", "cancelled"])
            .exists()
        ):
            raise NvkeError("RESOURCE_BUSY", "Reconcile existing native maintenance before reviewing a reset")
        token = get_current_api_token()
        sealed = encrypt_at_rest(outbox.canonical(parameters))
        row = NvkeOperation.objects.create(
            site=site,
            organization=org,
            actor=actor,
            request_id=identity,
            request_digest=digest,
            operation_key=KEY,
            expected_site_version=site.version,
            authority={
                "native_temporal_id": str(authority.guid),
                "native_temporal_digest": native.digest(authority),
                "native_cluster_id": parameters["clusterID"],
                "connection_id": str(controller.guid),
                "connection_digest": outbox.connection_digest(controller),
                "source_id": str(source.guid),
                "source_version": source.version,
                "token_id": str(token.guid) if token else None,
                "revision": upstream_revision(),
                "organization_namespace": site.organization_namespace,
            },
            input_backend_kind=sealed.backend_kind,
            input_ciphertext=sealed.backend_ref,
            deadline=timezone.now() + timedelta(seconds=timeout_seconds),
            created_by=actor,
            updated_by=actor,
        )
        transaction.on_commit(lambda: outbox.dispatch(row.pk))
        return row


def _live(row, request, fence):
    with outbox.original_actor(row):
        org, actor = native._actor(row.actor_id, require_read=True)
        site = sites.site_row(row.site.guid, org)
        _source, observation, controller = native_operations.cluster_observation(
            site, actor, row.authority["source_id"], row.authority["source_version"]
        )
        _owner(observation, request)
        with open_connection(controller, namespace=site.organization_namespace, checkpoint=fence) as client:
            response = execute_action(
                client, action("getCluster"), {"path": {"clusterID": request["clusterID"]}}
            )
        if (
            not isinstance(response.data, dict)
            or response.data.get("id") != request["clusterID"]
            or not 200 <= response.status_code < 300
        ):
            raise review.refused("A live controller read must prove the exact reset cluster")
        owner = _owner(maintenance.project(response.data, timezone.now()), request)
        if owner.get("state") != request["recorded_state"]:
            raise review.refused("Native rotation state changed after the reviewed observation")


async def send_reset(client, authority, request, request_id, reason):
    return await client.workflow_service.reset_workflow_execution(
        ResetWorkflowExecutionRequest(
            namespace=authority.namespace,
            workflow_execution=WorkflowExecution(
                workflow_id=request["workflow_id"], run_id=request["base_run_id"]
            ),
            workflow_task_finish_event_id=request["workflow_task_event_id"],
            request_id=request_id,
            reason=reason,
            reset_reapply_type=ResetReapplyType.RESET_REAPPLY_TYPE_NONE,
        ),
        retry=False,
        timeout=timedelta(seconds=authority.timeout_seconds),
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
            raise NvkeError("EXECUTION_REFUSED", "Native reset execution authority changed")
        if timezone.now() >= current.deadline:
            raise NvkeError("DEADLINE_EXCEEDED", "Native reset observation deadline was reached")
        with outbox.original_actor(row):
            native._actor(row.actor_id, require_read=True)
            return native_operations.checkpoint(row)

    def mark_sending():
        fence()
        if NvkeOperation.objects.get(pk=row.pk).cancellation_requested_at:
            raise NvkeError("OPERATION_CANCELLED", "Native reset cancelled before sending")
        progress.update(
            sending=True,
            target={"workflow_id": request["workflow_id"], "base_run_id": request["base_run_id"]},
        )
        _state(row, "running", "native_reset_sending", progress=progress)

    async def execute():
        authority = await sync_to_async(fence, thread_sensitive=True)()
        client = await native.connect_native(authority, fence)
        reason = f"Astro NVKE reset {row.guid} {row.request_digest}"
        if not progress.get("sending"):
            await sync_to_async(_live, thread_sensitive=True)(row, request, fence)
            await review.review(client, authority, request, fence)
            await sync_to_async(_live, thread_sensitive=True)(row, request, fence)
            current = await review.describe(client, authority, request["workflow_id"], fence)
            if (
                current.run_id != request["base_run_id"]
                or not current.status
                or current.status.name not in {"FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}
            ):
                raise review.refused("Native rotation changed immediately before reset")
            await sync_to_async(mark_sending, thread_sensitive=True)()
            await sync_to_async(fence, thread_sensitive=True)()
            try:
                response = await send_reset(client, authority, request, str(row.guid), reason)
                progress["native_run_id"] = response.run_id
                await sync_to_async(_state, thread_sensitive=True)(
                    row, "awaiting_ready", "native_reset_acknowledged", progress=progress
                )
            except Exception:
                pass
        current = await review.describe(client, authority, request["workflow_id"], fence)
        if current.run_id == request["base_run_id"] or (
            progress.get("native_run_id") and progress["native_run_id"] != current.run_id
        ):
            raise NvkeError(
                "RESET_UNCONFIRMED", "Native reset identity is unconfirmed; do not send another reset RPC"
            )
        if not await review.proof(client, authority, request, fence, reason, current.run_id):
            raise NvkeError(
                "RESET_UNCONFIRMED", "Native history did not prove this exact reset intent and boundary"
            )
        latest = await review.describe(client, authority, request["workflow_id"], fence)
        if latest.run_id != current.run_id:
            raise NvkeError("SOURCE_CHANGED", "Native reset owner changed runs during observation")
        progress.update(
            native_run_id=latest.run_id, native_status=latest.status.name if latest.status else "UNKNOWN"
        )
        reference = {
            "kind": "native_reset",
            "workflow_id": request["workflow_id"],
            "base_run_id": request["base_run_id"],
            "run_id": latest.run_id,
            "workflow_task_event_id": str(request["workflow_task_event_id"]),
        }
        if progress["native_status"] == "COMPLETED":
            return await sync_to_async(_state, thread_sensitive=True)(
                row, "succeeded", "native_reset_workflow_completed", progress=progress, reference=reference
            )
        if progress["native_status"] in {"FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
            return await sync_to_async(_state, thread_sensitive=True)(
                row,
                "failed",
                "native_reset_workflow_closed",
                progress=progress,
                reference=reference,
                error=NvkeError(
                    "NATIVE_WORKFLOW_FAILED",
                    "Reset execution closed unsuccessfully; retain ownership and review its new run",
                ),
            )
        if progress["native_status"] != "RUNNING":
            raise NvkeError("RESET_UNCONFIRMED", "Native reset execution state is not recognized")
        return await sync_to_async(_state, thread_sensitive=True)(
            row, "awaiting_ready", "native_reset_workflow_running", progress=progress, reference=reference
        )

    try:
        authority = fence()
        request = outbox.decode_input(row)
        return async_to_sync(asyncio.wait_for)(execute(), timeout=authority.timeout_seconds)
    except (NvkeError, sites.NvkeSiteError) as exc:
        status = (
            "uncertain"
            if progress.get("sending")
            else "cancelled"
            if exc.code == "OPERATION_CANCELLED"
            else "failed"
        )
        return _state(
            row,
            status,
            "native_reset_review_required",
            progress=progress,
            error=NvkeError(exc.code, str(exc)),
        )
    except PermissionDenied:
        return _state(
            row,
            "uncertain" if progress.get("sending") else "failed",
            "native_reset_review_required",
            progress=progress,
            error=NvkeError("AUTHORITY_REVOKED", "The original native reset caller is no longer authorized"),
        )
    except Exception as exc:
        logger.warning("Native reset execution failed (%s); original intent retained", type(exc).__name__)
        return _state(
            row,
            "uncertain" if progress.get("sending") else "failed",
            "native_reset_review_required",
            progress=progress,
            error=NvkeError(
                "RESET_UNCONFIRMED", "Native reset requires history inspection; no reset RPC will be replayed"
            ),
        )
