"""Review native owner history without publishing payloads, failures or credentials."""

import asyncio
import base64
from datetime import UTC, timedelta
from uuid import UUID

from asgiref.sync import async_to_sync, sync_to_async
from django.core import signing
from django.utils import timezone
from nvke.client import NvkeError
from nvke.contract import upstream_revision
from nvke.native_workflows import activity_names, validate_input
from temporalio.api.common.v1 import WorkflowExecution
from temporalio.api.enums.v1 import EventType
from temporalio.api.workflowservice.v1 import GetWorkflowExecutionHistoryRequest

from astrolift_clusters import nvke_native_operations as native_operations
from astrolift_clusters import nvke_operations as operations
from astrolift_clusters import nvke_sites as sites
from astrolift_clusters import nvke_temporal as native
from astrolift_clusters.models import NvkeTemporalAuthority

_SALT = "astrolift.nvke.native-history.v1"
_KINDS = {"RenewK8sCertsV2": "renewal", "RotateEtcdEncryptionKey": "etcd_key_rotation"}


def _cursor_error():
    return NvkeError("INVALID_CURSOR", "Use a current native history cursor for this exact review")


def _cursor(token, binding):
    try:
        if not isinstance(token, str) or len(token) > 65536:
            raise ValueError
        value = signing.TimestampSigner(salt=_SALT).unsign_object(token, max_age=1800)
        if not isinstance(value, dict) or value.get("binding") != binding:
            raise ValueError
        run_id = str(UUID(value["run_id"]))
        if run_id != value["run_id"]:
            raise ValueError
        page = base64.b64decode(value["page"], validate=True)
        offset = value["offset"]
        if (
            type(offset) is not int
            or not 0 <= offset <= 1000000
            or (not page and not offset)
            or len(page) > 16384
        ):
            raise ValueError
        return run_id, page, offset
    except (signing.BadSignature, ValueError, TypeError, KeyError, AttributeError):
        raise _cursor_error() from None


def project(event):
    if event.event_id < 1:
        raise NvkeError("HISTORY_UNAVAILABLE", "Native history contains an invalid event identity")
    activity = None
    scheduled = completed = None
    if event.HasField("activity_task_scheduled_event_attributes"):
        attributes = event.activity_task_scheduled_event_attributes
        name = attributes.activity_type.name
        activity = name if name in activity_names() else None
        completed = (
            str(attributes.workflow_task_completed_event_id)
            if attributes.workflow_task_completed_event_id
            else None
        )
    for field in (
        "activity_task_started_event_attributes",
        "activity_task_completed_event_attributes",
        "activity_task_failed_event_attributes",
        "activity_task_timed_out_event_attributes",
        "activity_task_canceled_event_attributes",
    ):
        if event.HasField(field):
            identity = getattr(event, field).scheduled_event_id
            scheduled = str(identity) if identity else None
    try:
        kind = EventType.Name(event.event_type).removeprefix("EVENT_TYPE_")
    except ValueError:
        kind = "UNKNOWN"
    return {
        "event_id": str(event.event_id),
        "event_type": kind,
        "occurred_at": event.event_time.ToDatetime(tzinfo=UTC) if event.HasField("event_time") else None,
        "activity_type": activity,
        "scheduled_event_id": scheduled,
        "workflow_task_completed_event_id": completed,
    }


def read(
    *,
    site_id,
    expected_site_version,
    expected_actor_id,
    expected_authority_version,
    source_operation_id,
    expected_source_version,
    workflow,
    after=None,
    limit=50,
):
    org, actor = native._actor(expected_actor_id, require_read=True)
    if workflow not in _KINDS or type(limit) is not int or not 1 <= limit <= 200:
        raise NvkeError("INVALID_INPUT", "Choose a native renewal/rotation owner and page size 1–200")
    site = sites.site_row(site_id, org)
    sites._version(site, expected_site_version)
    if not site.is_active or site.upstream_revision != upstream_revision():
        raise NvkeError("SOURCE_CHANGED", "Native history site authority changed")
    authority = (
        NvkeTemporalAuthority.objects.select_related("site").filter(site=site, is_enabled=True).first()
    )
    if authority is None:
        raise NvkeError("SURFACE_UNAVAILABLE", "Configure explicit native Temporal authority first")
    sites._version(authority, expected_authority_version)
    source, observation, controller = native_operations.cluster_observation(
        site, actor, source_operation_id, expected_source_version
    )
    owner = observation.get(_KINDS[workflow])
    if not owner or not owner.get("workflow_id"):
        raise NvkeError("SNAPSHOT_UNAVAILABLE", "The reviewed cluster does not identify this native owner")
    workflow_id = owner["workflow_id"]
    binding = [
        str(org.guid),
        actor.pk,
        str(site.guid),
        site.version,
        str(authority.guid),
        native.digest(authority),
        str(controller.guid),
        operations.connection_digest(controller),
        str(source.guid),
        source.version,
        workflow,
        workflow_id,
        observation["cluster_id"],
        limit,
    ]
    pinned_run, token, offset = _cursor(after, binding) if after is not None else (None, b"", 0)

    def checkpoint():
        current_org, current_actor = native._actor(expected_actor_id, require_read=True)
        current_site = sites.site_row(site_id, current_org)
        sites._version(current_site, expected_site_version)
        current = (
            NvkeTemporalAuthority.objects.select_related("site")
            .filter(pk=authority.pk, site=current_site, is_enabled=True)
            .first()
        )
        current_source, current_observation, current_controller = native_operations.cluster_observation(
            current_site, current_actor, source.guid, expected_source_version
        )
        if (
            not current_site.is_active
            or current_site.upstream_revision != upstream_revision()
            or current is None
            or native.digest(current) != binding[5]
            or operations.connection_digest(current_controller) != binding[7]
            or current_source.pk != source.pk
            or current_observation != observation
        ):
            raise NvkeError("SOURCE_CHANGED", "Native history authority or cluster observation changed")

    async def collect():
        client = await native.connect_native(authority, checkpoint)
        timeout = timedelta(seconds=authority.timeout_seconds)
        await sync_to_async(checkpoint, thread_sensitive=True)()
        latest = await client.get_workflow_handle(workflow_id).describe(rpc_timeout=timeout)
        if pinned_run and latest.run_id != pinned_run:
            raise NvkeError("SOURCE_CHANGED", "The native owner changed runs; review its current history")
        run_id = str(UUID(latest.run_id))
        handle = client.get_workflow_handle(workflow_id, run_id=run_id)
        await sync_to_async(checkpoint, thread_sensitive=True)()
        first = await anext(handle.fetch_history_events(page_size=1, rpc_timeout=timeout))
        start = first.workflow_execution_started_event_attributes
        values = await client.data_converter.decode(start.input.payloads)
        if (
            first.event_id != 1
            or start.workflow_type.name != workflow
            or start.task_queue.name != authority.task_queue
            or len(values) != 1
        ):
            raise NvkeError("SOURCE_CHANGED", "Native history did not prove the reviewed workflow and queue")
        parameters = validate_input(workflow, values[0])
        if parameters["clusterID"] != observation["cluster_id"]:
            raise NvkeError("SOURCE_CHANGED", "Native history did not prove the reviewed cluster")
        await sync_to_async(checkpoint, thread_sensitive=True)()
        page = await client.workflow_service.get_workflow_execution_history(
            GetWorkflowExecutionHistoryRequest(
                namespace=authority.namespace,
                execution=WorkflowExecution(workflow_id=workflow_id, run_id=run_id),
                maximum_page_size=limit,
                next_page_token=token,
                wait_new_event=False,
            ),
            retry=False,
            timeout=timeout,
        )
        if page.raw_history or page.archived:
            raise NvkeError("HISTORY_UNAVAILABLE", "Native history requires a decoded retained execution")
        await sync_to_async(checkpoint, thread_sensitive=True)()
        # Pin the current run again after paging; reset can replace it while RPCs are in flight.
        current = await client.get_workflow_handle(workflow_id).describe(rpc_timeout=timeout)
        if current.run_id != run_id:
            raise NvkeError("SOURCE_CHANGED", "The native owner changed runs while reading history")
        await sync_to_async(checkpoint, thread_sensitive=True)()
        items = page.history.events[offset : offset + limit]
        if offset > len(page.history.events):
            raise _cursor_error()
        next_cursor = None
        next_page = token if len(page.history.events) > offset + limit else page.next_page_token
        next_offset = offset + limit if len(page.history.events) > offset + limit else 0
        if next_page or next_offset:
            if len(next_page) > 16384 or (next_page == token and next_offset == offset):
                raise NvkeError("HISTORY_UNAVAILABLE", "Native history continuation exceeds the review limit")
            next_cursor = signing.TimestampSigner(salt=_SALT).sign_object(
                {
                    "binding": binding,
                    "run_id": run_id,
                    "page": base64.b64encode(next_page).decode("ascii"),
                    "offset": next_offset,
                }
            )
        return {
            "site_id": str(site.guid),
            "site_version": site.version,
            "authority_version": authority.version,
            "source_operation_id": str(source.guid),
            "source_version": source.version,
            "source_observed_at": source.finished_at,
            "cluster_id": observation["cluster_id"],
            "workflow": workflow,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "status": current.status.name if current.status else "UNKNOWN",
            "recorded_state": owner.get("state"),
            "recorded_phase": owner.get("phase"),
            "retained_mutation_lock": owner.get("retained_mutation_lock"),
            "observed_at": timezone.now(),
            "items": [project(event) for event in items],
            "next_cursor": next_cursor,
        }

    try:
        return async_to_sync(asyncio.wait_for)(collect(), timeout=authority.timeout_seconds)
    except (NvkeError, sites.NvkeSiteError):
        raise
    except Exception:
        raise NvkeError(
            "NATIVE_HISTORY_UNAVAILABLE", "The exact native owner history could not be inspected"
        ) from None
