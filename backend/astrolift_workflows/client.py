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
    try:
        from constance import config as constance_config

        return bool(getattr(constance_config, "TEMPORAL_ENABLED", True))
    except Exception:
        # Constance unavailable (no DB, migrations not applied, plugin
        # disabled) — fall through to the env-backed default.
        pass
    return bool(getattr(settings, "ASTROLIFT_TEMPORAL_ENABLED", True))


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
    handle = await client.start_workflow(
        workflow,
        *args,
        id=workflow_id,
        task_queue=task_queue,
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
