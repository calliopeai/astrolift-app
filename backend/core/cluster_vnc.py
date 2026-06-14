"""Resolver-facing bridge between the VNC WS relay (#877) and the
cluster driver SDK port-forward.

The WS relay in :mod:`core.schema.vnc_ws` calls into a :class:`VncBackend`
to open a port-forward to a RUNNING agent task's pod noVNC port (6080).
The backend's job is to:

1. Re-resolve the AgentTask to its managed cluster + namespace + pod.
2. Open a kubernetes port-forward via the provider SDK driver.
3. Wrap the blocking port-forward socket in an asyncio surface
   (:class:`providers._sdk.async_port_forward.AsyncK8sPortForward`).

This mirrors :mod:`core.cluster_exec`: the blocking SDK open runs in
the executor, and the per-port socket reads/writes are executored so
the relay's pump loop never blocks the event loop.

Wiring: :class:`K8sVncBackend` is registered as the relay's backend
from ``core.apps.CoreConfig.ready`` so the production stack ships a
real VNC path without an operator opt-in. Tests use the stub backend
in :mod:`core.schema.vnc_ws` (no cluster needed).
"""

from __future__ import annotations

import logging
from typing import Any

from asgiref.sync import sync_to_async

from core.schema.vnc_ws import VNC_PORT, VncBackend, VncSession

logger = logging.getLogger(__name__)


@sync_to_async
def _resolve_vnc_runtime(*, task_guid: str) -> dict | None:
    """Resolve the cluster + namespace + pod for a VNC target.

    The relay has already gated the task on tenant ownership + RUNNING +
    vnc_enabled; this re-resolves the runtime coordinates needed to open
    the port-forward. Returns ``None`` if the task or its cluster has
    gone away between the gate and the open.
    """
    from astrolift_agents.models import AgentTask
    from astrolift_workflows.activities.agent_stage import (
        _agent_namespace,
        _resolve_managed_cluster,
    )

    task = (
        AgentTask.objects.select_related("organization")
        .filter(guid=task_guid, deleted_at__isnull=True)
        .first()
    )
    if task is None or not task.pod_name:
        return None
    try:
        cluster = _resolve_managed_cluster(task.organization)
    except Exception:  # noqa: BLE001
        logger.exception("cluster_vnc: managed-cluster resolution failed")
        return None
    if cluster is None:
        return None
    return {
        "cluster": cluster,
        "namespace": _agent_namespace(task.organization.slug),
        # NOTE: in the workflow spawn path pod_name is set to the Job
        # name, which the kubelet also accepts as a pod selector for the
        # single-pod Job (completions=1, backoffLimit=0). If a future
        # multi-pod shape lands, resolve the pod via the
        # ``astrolift.dev/task-id`` label here before forwarding.
        "pod": task.pod_name,
    }


def _driver_for(cluster: Any) -> Any:
    from core.cluster_observability import _driver_for_cluster

    return _driver_for_cluster(cluster)


class _AsyncForwardSession(VncSession):
    """Adapts :class:`AsyncK8sPortForward` to the :class:`VncSession`
    protocol the relay consumes."""

    def __init__(self, forward: Any) -> None:
        self._forward = forward

    async def recv(self) -> bytes:
        return await self._forward.recv()

    async def sendall(self, data: bytes) -> None:
        await self._forward.sendall(data)

    async def close(self) -> None:
        await self._forward.close()


class _ClosedVncSession(VncSession):
    """Returned when the open path bailed — keeps the relay's recv/close
    calls from raising and EOFs the pump immediately."""

    async def recv(self) -> bytes:
        return b""

    async def sendall(self, data: bytes) -> None:
        return None

    async def close(self) -> None:
        return None


class K8sVncBackend(VncBackend):
    """Production backend — resolves the task's cluster + namespace +
    pod and opens a kubernetes port-forward to the noVNC port, wrapped
    in an asyncio-friendly session for the relay's byte pump."""

    async def open(self, *, task_guid: str) -> VncSession:
        from providers._sdk.async_port_forward import AsyncK8sPortForward

        resolved = await _resolve_vnc_runtime(task_guid=task_guid)
        if resolved is None:
            logger.warning("cluster_vnc: no runtime resolved for task %s", task_guid)
            return _ClosedVncSession()

        cluster = resolved["cluster"]
        namespace = resolved["namespace"]
        pod = resolved["pod"]

        try:
            driver = _driver_for(cluster)
        except Exception:  # noqa: BLE001
            logger.exception("cluster_vnc: driver resolution failed")
            return _ClosedVncSession()

        def _open() -> Any:
            return driver.port_forward(
                cluster.slug,
                namespace,
                pod,
                [(VNC_PORT, VNC_PORT)],
            )

        try:
            forward = await AsyncK8sPortForward.open(
                open_fn=_open,
                port=VNC_PORT,
            )
        except Exception:  # noqa: BLE001
            logger.exception("cluster_vnc: port-forward open failed")
            return _ClosedVncSession()

        return _AsyncForwardSession(forward)
