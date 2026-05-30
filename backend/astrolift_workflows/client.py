"""
Sync wrappers around the Temporal client for use from request handlers.

GraphQL mutations live in sync code paths (Strawberry-Django default,
WSGI). The Temporal SDK is async-only. Rather than convert every
resolver to async, this module exposes a small synchronous surface
that:

  * connects on demand using ``settings.TEMPORAL_*``
  * returns ``WorkflowHandle``-shaped tuples (``workflow_id``, ``run_id``)
  * signals/terminates by id

If Temporal is disabled (``settings.ASTROLIFT_TEMPORAL_ENABLED`` is
False, default ``not DEBUG``), the helpers return a synthetic handle
so dev/test environments can still drive the state machine without
running a Temporal server.

The reason we expose a sync facade and not just ``async_to_sync`` at
each call site is that the Temporal client is expensive to construct
(grpc channel + namespace handshake). Cache it once per process.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from asgiref.sync import async_to_sync
from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class WorkflowHandle:
    workflow_id: str
    run_id: str
    enqueued: bool  # False when running in disabled mode


_client_lock = threading.Lock()
_client: Any = None  # temporalio.client.Client | None


def _temporal_enabled() -> bool:
    # Default ON. Two override knobs, in priority order:
    #   1. ``constance.config.TEMPORAL_ENABLED`` — admin-flippable at
    #      runtime via /app/admin/constance/. Wins when set so an
    #      operator can disable the runtime without a redeploy.
    #   2. ``settings.ASTROLIFT_TEMPORAL_ENABLED`` — env-var-backed
    #      deploy-time default (also seeds the Constance row on first
    #      boot). Defaults True.
    # The DEBUG-derived legacy fallback was removed because
    # DJANGO_CONFIGURATION=Dev (the only config the published image
    # boots cleanly with) leaves DEBUG truthy, which silently turned
    # the entire workflow runtime into a no-op in prd.
    # Explicit ``settings.ASTROLIFT_TEMPORAL_ENABLED = False`` is a
    # hard kill switch — tests set it via the django ``settings``
    # fixture; ops can set it via env for emergency shutdown without
    # touching constance. True or unset falls through to the
    # constance-then-settings precedence below.
    settings_override = getattr(settings, "ASTROLIFT_TEMPORAL_ENABLED", None)
    if settings_override is False:
        return False
    try:
        from constance import config as constance_config

        return bool(getattr(constance_config, "TEMPORAL_ENABLED", True))
    except Exception:
        # Constance unavailable (no DB, migrations not applied, plugin
        # disabled) — fall through to the env-backed default.
        pass
    return bool(settings_override) if settings_override is not None else True


async def _get_client_async() -> Any:
    global _client
    if _client is not None:
        return _client
    from temporalio.client import Client

    address = getattr(settings, "TEMPORAL_ADDRESS", "localhost:7233")
    namespace = getattr(settings, "TEMPORAL_NAMESPACE", "default")
    client = await Client.connect(address, namespace=namespace)
    with _client_lock:
        if _client is None:
            _client = client
    return _client


def _task_queue() -> str:
    return getattr(settings, "TEMPORAL_TASK_QUEUE", "astrolift-main")


@async_to_sync
async def _start(workflow: str, *, args: list[Any], workflow_id: str, task_queue: str) -> tuple[str, str]:
    client = await _get_client_async()
    from temporalio.common import WorkflowIDReusePolicy

    handle = await client.start_workflow(
        workflow,
        *args,
        id=workflow_id,
        task_queue=task_queue,
        # Supersede any still-running workflow with the same ID so a
        # new deploy can always start even if the previous one is mid-
        # cancel (e.g. operator aborted but Temporal hasn't drained yet).
        id_reuse_policy=WorkflowIDReusePolicy.TERMINATE_IF_RUNNING,
    )
    return handle.id, handle.result_run_id or handle.first_execution_run_id or ""


@async_to_sync
async def _signal(workflow_id: str, signal_name: str, *args: Any) -> None:
    client = await _get_client_async()
    handle = client.get_workflow_handle(workflow_id)
    await handle.signal(signal_name, *args)


@async_to_sync
async def _terminate(workflow_id: str, reason: str) -> None:
    client = await _get_client_async()
    handle = client.get_workflow_handle(workflow_id)
    await handle.terminate(reason=reason)


def start_workflow(
    workflow_name: str,
    args: list[Any],
    *,
    workflow_id: str,
    task_queue: str | None = None,
) -> WorkflowHandle:
    """Submit a workflow start. No-op (logs only) when Temporal is disabled."""
    if not _temporal_enabled():
        logger.info(
            "temporal disabled; would-have-started workflow=%s id=%s",
            workflow_name,
            workflow_id,
        )
        return WorkflowHandle(workflow_id=workflow_id, run_id="", enqueued=False)

    queue = task_queue or _task_queue()
    wf_id, run_id = _start(workflow_name, args=args, workflow_id=workflow_id, task_queue=queue)
    return WorkflowHandle(workflow_id=wf_id, run_id=run_id, enqueued=True)


def signal_workflow(workflow_id: str, signal_name: str, *args: Any) -> bool:
    """Best-effort signal. Returns False when disabled or not found."""
    if not _temporal_enabled():
        logger.info("temporal disabled; would-have-signalled %s -> %s", workflow_id, signal_name)
        return False
    try:
        _signal(workflow_id, signal_name, *args)
        return True
    except Exception:
        logger.exception("temporal signal failed: id=%s signal=%s", workflow_id, signal_name)
        return False


def terminate_workflow(workflow_id: str, reason: str) -> bool:
    """Best-effort terminate. Returns False when disabled or not found."""
    if not _temporal_enabled():
        logger.info("temporal disabled; would-have-terminated %s", workflow_id)
        return False
    try:
        _terminate(workflow_id, reason)
        return True
    except Exception:
        logger.exception("temporal terminate failed: id=%s", workflow_id)
        return False


# Known cluster-targeting workflow types. The workflow_id for each is
# always "<type>-<cluster_guid>" (mutations construct it that way so
# re-firing joins the running run instead of spawning a parallel one).
# Listed here so the visibility query is built from exact WorkflowId=
# predicates instead of a substring LIKE — standard SQL visibility (the
# default in temporalio/auto-setup) does NOT support LIKE on
# WorkflowId; only advanced/Elasticsearch visibility does. The exact-id
# approach works on both backends.
_CLUSTER_WORKFLOW_ID_PREFIXES: tuple[str, ...] = (
    "BringClusterIntoManagement",
    "DecommissionClusterWorkflow",
    "InstallClusterPrereqsWorkflow",
)


@async_to_sync
async def _list_for_cluster_async(
    cluster_guid: str,
    limit: int,
) -> list[dict[str, Any]]:
    # Build the visibility query from exact WorkflowId predicates for
    # each known cluster workflow type. Standard SQL visibility supports
    # ``WorkflowId="<exact>"`` and ``OR`` between predicates; it does NOT
    # support ``LIKE "%...%"`` (advanced visibility / Elasticsearch only),
    # which silently returned an empty list in prod for every Status tab
    # request and made it look like no workflow runs ever happened.
    #
    # Each ``WorkflowId="<type>-<guid>"`` predicate returns ALL historical
    # runs that ever used that workflow id, so repeated Install /
    # Bring / Refresh / Decommission invocations are all surfaced.
    predicates = [f'WorkflowId="{prefix}-{cluster_guid}"' for prefix in _CLUSTER_WORKFLOW_ID_PREFIXES]
    query = " OR ".join(predicates)
    rows: list[dict[str, Any]] = []
    try:
        client = await _get_client_async()
        async for run in client.list_workflows(query=query):
            rows.append(
                {
                    "workflow_id": run.id,
                    "workflow_type": run.workflow_type,
                    "status": run.status.name if run.status else "UNKNOWN",
                    "started_at": (run.start_time.isoformat() if run.start_time else ""),
                    "closed_at": (run.close_time.isoformat() if run.close_time else ""),
                    "run_id": run.run_id or "",
                },
            )
            if len(rows) >= limit:
                break
    except Exception as exc:  # noqa: BLE001 — log + return empty
        logger.warning("temporal list_workflows_for_cluster failed (guid=%s): %s", cluster_guid, exc)
        return []
    return rows


def list_workflows_for_cluster(
    cluster_guid: str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Recent workflow runs targeting a specific cluster (#394).

    Used by the cluster-detail Status tab to surface recent
    BringClusterIntoManagement / DecommissionCluster / InstallCluster-
    Prereqs / RefreshClusterManagement / DriftDetection runs without
    sending the operator to the Temporal UI.

    Returns ``[]`` when Temporal is disabled (Constance flag off) or
    the visibility query fails — the UI's empty-state copy handles
    both indistinguishably from "no runs yet".
    """
    if not _temporal_enabled():
        logger.info(
            "temporal disabled; list_workflows_for_cluster returns []",
        )
        return []
    return _list_for_cluster_async(cluster_guid, limit)


# ---------------------------------------------------------------------------
# Generic instance viewer helpers (#437)
# ---------------------------------------------------------------------------
#
# Powers the in-app workflow viewer surface so operators can debug async
# task issues without leaving for the Temporal UI. Three reads + three
# writes:
#
#   list_workflow_instances() — list by workflow type / status with limit
#   describe_workflow_instance() — single instance summary
#   workflow_history() — pre-shaped activity feed for the UI
#
#   cancel_workflow() — graceful cancel (cooperative signal)
#   terminate_workflow() — hard terminate (already above)
#   signal_workflow() — generic signal send (already above)


@async_to_sync
async def _list_instances_async(
    workflow_type: str | None,
    status: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    client = await _get_client_async()
    parts: list[str] = []
    if workflow_type:
        # Quote-escape per Temporal's visibility query language.
        safe = workflow_type.replace('"', '\\"')
        parts.append(f'WorkflowType="{safe}"')
    if status:
        safe = status.replace('"', '\\"')
        parts.append(f'ExecutionStatus="{safe}"')
    query = " AND ".join(parts) if parts else ""
    rows: list[dict[str, Any]] = []
    try:
        async for run in client.list_workflows(query=query):
            duration_seconds: float | None = None
            if run.start_time and run.close_time:
                duration_seconds = (run.close_time - run.start_time).total_seconds()
            rows.append(
                {
                    "workflow_id": run.id,
                    "workflow_type": run.workflow_type,
                    "status": run.status.name if run.status else "UNKNOWN",
                    "started_at": run.start_time.isoformat() if run.start_time else "",
                    "closed_at": run.close_time.isoformat() if run.close_time else "",
                    "run_id": run.run_id or "",
                    "duration_seconds": duration_seconds,
                    "task_queue": getattr(run, "task_queue", "") or "",
                },
            )
            if len(rows) >= limit:
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("temporal list_workflows failed: %s", exc)
        return []
    return rows


def list_workflow_instances(
    *,
    workflow_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List recent Temporal instances, optionally filtered.

    Returns ``[]`` when Temporal is disabled or the visibility query
    fails — keeps the UI render path identical for "no temporal" vs.
    "no runs"."""
    if not _temporal_enabled():
        return []
    return _list_instances_async(workflow_type, status, max(1, min(limit, 200)))


@async_to_sync
async def _describe_instance_async(workflow_id: str) -> dict[str, Any] | None:
    client = await _get_client_async()
    try:
        handle = client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
    except Exception as exc:  # noqa: BLE001
        logger.warning("temporal describe failed for %s: %s", workflow_id, exc)
        return None
    duration_seconds: float | None = None
    if desc.start_time and desc.close_time:
        duration_seconds = (desc.close_time - desc.start_time).total_seconds()
    return {
        "workflow_id": desc.id,
        "workflow_type": desc.workflow_type,
        "status": desc.status.name if desc.status else "UNKNOWN",
        "started_at": desc.start_time.isoformat() if desc.start_time else "",
        "closed_at": desc.close_time.isoformat() if desc.close_time else "",
        "run_id": desc.run_id or "",
        "duration_seconds": duration_seconds,
        "task_queue": getattr(desc, "task_queue", "") or "",
    }


def describe_workflow_instance(workflow_id: str) -> dict[str, Any] | None:
    """Single-instance summary for the drill-down sheet (#437)."""
    if not _temporal_enabled():
        return None
    return _describe_instance_async(workflow_id)


@async_to_sync
async def _fetch_history_async(workflow_id: str, limit: int) -> list[dict[str, Any]]:
    client = await _get_client_async()
    rows: list[dict[str, Any]] = []
    try:
        handle = client.get_workflow_handle(workflow_id)
        async for event in handle.fetch_history_events():
            row = _shape_history_event(event)
            if row is not None:
                rows.append(row)
            if len(rows) >= limit:
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("temporal history fetch failed for %s: %s", workflow_id, exc)
        return []
    return rows


def _shape_history_event(event: Any) -> dict[str, Any] | None:
    """Pre-shape one Temporal HistoryEvent into the UI's row format.

    The Temporal protobuf is dense and inconsistent across event kinds;
    the viewer only needs a few fields — type label, timestamp, retry
    count, decision, and a compact payload preview. This shaper picks
    those out and lets the rest sit in raw form for ``payload`` so a
    power user can still see everything via the JSON viewer.
    """
    try:
        # event_type is an enum like EVENT_TYPE_ACTIVITY_TASK_STARTED.
        event_type_raw = getattr(event, "event_type", None)
        event_type = event_type_raw.name if hasattr(event_type_raw, "name") else str(event_type_raw or "")
        ts = getattr(event, "event_time", None)
        timestamp_iso = ts.ToDatetime().isoformat() if ts and hasattr(ts, "ToDatetime") else ""

        retry_count = 0
        decision = ""
        payload_preview: dict[str, Any] = {}

        for attr_name in [
            "activity_task_scheduled_event_attributes",
            "activity_task_started_event_attributes",
            "activity_task_completed_event_attributes",
            "activity_task_failed_event_attributes",
            "activity_task_timed_out_event_attributes",
            "workflow_execution_started_event_attributes",
            "workflow_execution_completed_event_attributes",
            "workflow_execution_failed_event_attributes",
            "workflow_task_completed_event_attributes",
            "timer_started_event_attributes",
            "timer_fired_event_attributes",
        ]:
            attrs = getattr(event, attr_name, None)
            if attrs is None:
                continue
            if hasattr(attrs, "attempt"):
                retry_count = max(retry_count, int(getattr(attrs, "attempt", 0) or 0))
            if hasattr(attrs, "activity_type") and getattr(attrs, "activity_type", None):
                payload_preview["activity_type"] = getattr(attrs.activity_type, "name", "")
            if hasattr(attrs, "activity_id") and getattr(attrs, "activity_id", ""):
                payload_preview["activity_id"] = attrs.activity_id
            if hasattr(attrs, "failure") and getattr(attrs, "failure", None):
                payload_preview["failure"] = getattr(attrs.failure, "message", "") or ""
                decision = "failed"
            if "COMPLETED" in event_type:
                decision = decision or "completed"
            if "TIMED_OUT" in event_type:
                decision = decision or "timed_out"
            if "CANCELED" in event_type or "CANCELLED" in event_type:
                decision = decision or "cancelled"
            break

        return {
            "event_type": event_type,
            "timestamp": timestamp_iso,
            "payload": payload_preview,
            "retry_count": retry_count,
            "decision": decision,
        }
    except Exception:  # noqa: BLE001
        logger.exception("history-event shape failed")
        return None


def workflow_history(workflow_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    """Pre-shaped activity feed for the workflow viewer (#437).

    Each row maps one Temporal HistoryEvent to a compact UI dict:
    ``{event_type, timestamp, payload, retry_count, decision}``. The
    drill-down panel groups rows by activity and collapses them. Limit
    defaults to 200 — large enough for typical deploys, small enough
    to cap memory."""
    if not _temporal_enabled():
        return []
    return _fetch_history_async(workflow_id, max(1, min(limit, 500)))


@async_to_sync
async def _cancel_async(workflow_id: str) -> None:
    client = await _get_client_async()
    handle = client.get_workflow_handle(workflow_id)
    await handle.cancel()


def cancel_workflow(workflow_id: str) -> bool:
    """Cooperative cancel — sends Temporal's CancelRequested signal so
    the workflow can run cleanup before exiting. Returns False when
    Temporal is disabled or the handle is missing."""
    if not _temporal_enabled():
        logger.info("temporal disabled; would-have-cancelled %s", workflow_id)
        return False
    try:
        _cancel_async(workflow_id)
        return True
    except Exception:
        logger.exception("temporal cancel failed: id=%s", workflow_id)
        return False


@async_to_sync
async def _query_async(workflow_id: str, query_name: str) -> Any:
    client = await _get_client_async()
    handle = client.get_workflow_handle(workflow_id)
    return await handle.query(query_name)


def query_workflow(workflow_id: str, query_name: str) -> Any | None:
    """Query a running workflow's state via a registered query handler.

    Returns ``None`` when Temporal is disabled, the workflow is not
    found (already completed/terminated), or the query handler raises.
    Callers must treat ``None`` as "no live data available".

    Typical call::

        progress = query_workflow(
            f"OnboardAppWorkflow-{app.guid}",
            "provisioning_progress",
        )
    """
    if not _temporal_enabled():
        return None
    try:
        return _query_async(workflow_id, query_name)
    except Exception:
        logger.warning("temporal query failed: id=%s query=%s", workflow_id, query_name)
        return None
