"""noVNC WebSocket proxy — Controller → Dispatch Service → agent container.

Issue #58. Part of the Agent Dispatch Layer (#39).

Architecture
------------
VNC-capable agents (terminal_novnc, terminal_novnc_browser variants) expose
noVNC on port 6080 inside their container. Rather than punching per-agent
ports through cluster ingress, traffic flows through a stable proxy chain:

    Browser (noVNC JS client)
      → wss://<astrolift-host>/api/agents/v1/tasks/<task_id>/vnc/ws/
        → ws://<dispatcher-endpoint>/tasks/<task_id>/vnc
          → ws://127.0.0.1:6080   (noVNC inside the agent container)

URL
---
GET /api/agents/v1/tasks/<task_id>/vnc/ws/

The initial HTTP request is upgraded to a WebSocket by the browser/nginx.
Standard session or bearer-token auth on the HTTP upgrade; RBAC checked
before the upstream connection opens.

Completion requirements
-----------------------
Full bidirectional WebSocket proxying requires Django Channels (ASGI) or an
asyncio-capable view layer. The current Django setup is WSGI-only. To ship
this endpoint:

1. Add `channels` to Pipfile and configure `CHANNEL_LAYERS` in settings.
2. Switch the Django WSGI entry point to an ASGI entry point
   (`config/asgi.py`) — Daphne or Uvicorn as the process server.
3. Replace the stub below with a `WebsocketConsumer` (or
   `AsyncWebsocketConsumer`) subclass that:
   a. Performs the RBAC check (task must belong to requester's org).
   b. Opens an upstream `websockets` connection to the Dispatch Service.
   c. Forwards frames bidirectionally until either side closes.
4. Register the consumer in a Channels routing config and mount it under
   the URL above.

Until channels is in place, this module provides:
  - The view skeleton that performs RBAC and returns the correct HTTP
    errors for non-WebSocket requests.
  - URL registration so the route exists and returns 501 rather than 404.
  - Documentation of the Dispatch Service contract the upstream consumer
    must honour.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

if TYPE_CHECKING:
    pass

log = logging.getLogger("astrolift_agents.novnc_proxy")

# Status returned while the ASGI/Channels upgrade is pending (#58).
_NOT_IMPLEMENTED_MSG = (
    "noVNC WebSocket proxy requires ASGI/Channels. "
    "See astrolift_agents/views/novnc_proxy.py for completion requirements."
)


def _resolve_task(task_id: str, request: HttpRequest):
    """Look up an AgentTask and verify the requesting user has access.

    Returns ``(task, None)`` on success, ``(None, JsonResponse)`` on failure.

    The task model lives in `astrolift_agent_dispatch` (issue #44). Once
    that app lands, replace the import stub below with the real model.
    """
    # Placeholder import — replace when astrolift_agent_dispatch ships.
    try:
        from astrolift_agent_dispatch.models import AgentTask  # type: ignore[import]
    except ImportError:
        return None, JsonResponse(
            {"error": "Agent dispatch app not yet installed"},
            status=503,
        )

    try:
        task = AgentTask.objects.select_related("dispatcher_instance__org").get(
            guid=task_id,
            deleted_at__isnull=True,
        )
    except AgentTask.DoesNotExist:
        return None, JsonResponse({"error": "Task not found"}, status=404)

    # Org-scoped permission check — requester must be a member of the
    # same org that owns the task.
    from organization.models import Member  # noqa: PLC0415 — deferred import

    if not Member.objects.filter(
        user=request.user,
        organization=task.dispatcher_instance.org,
        deleted_at__isnull=True,
    ).exists():
        return None, JsonResponse({"error": "Not found"}, status=404)

    return task, None


def _validate_vnc_ready(task) -> JsonResponse | None:
    """Return a JsonResponse error if the task cannot serve a VNC session.

    None means the task is eligible — caller should proceed.
    """
    RUNNING = "running"
    VNC_VARIANTS = {"terminal_novnc", "terminal_novnc_browser"}

    if task.status != RUNNING:
        return JsonResponse(
            {
                "error": "VNC session not available",
                "detail": (
                    f"Task is in status '{task.status}'. "
                    "VNC is only available while the task is RUNNING."
                ),
            },
            status=410,  # Gone — task has left RUNNING
        )

    variant = getattr(task, "agent_variant", None)
    if variant not in VNC_VARIANTS:
        return JsonResponse(
            {
                "error": "Agent variant does not support VNC",
                "detail": (
                    f"Agent variant '{variant}' does not expose a noVNC server. "
                    f"Supported variants: {sorted(VNC_VARIANTS)}"
                ),
            },
            status=400,
        )

    return None


@csrf_exempt
@require_http_methods(["GET"])
def novnc_ws_proxy(request: HttpRequest, task_id: str) -> JsonResponse:
    """WebSocket upgrade endpoint for the noVNC proxy.

    Expected usage
    --------------
    The noVNC JS client in the browser connects to this URL as a WebSocket.
    nginx (or the Channels ASGI server) upgrades the connection; this view
    (when replaced by a Channels consumer) then proxies frames to the
    Dispatch Service and onwards to the agent container.

    Current state
    -------------
    Returns 501 for all requests — WebSocket proxying requires ASGI/Channels
    which is not yet configured. See module docstring for completion steps.

    Auth
    ----
    Standard session or bearer-token auth (ApiToken / DeployToken) via the
    existing middleware chain. Checked before the upgrade attempt.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    task, err = _resolve_task(task_id, request)
    if err is not None:
        return err

    vnc_err = _validate_vnc_ready(task)
    if vnc_err is not None:
        return vnc_err

    # At this point, the task exists, the user is authorised, and the task
    # is RUNNING with a VNC-capable variant. The WebSocket upgrade should
    # proceed here once Channels is configured.
    #
    # Dispatch Service target URL pattern:
    #   ws://<dispatcher_instance.endpoint>/tasks/<task_id>/vnc
    #
    # The Dispatch Service authenticates Controller requests via a scoped
    # API key stored on the DispatcherInstance model.

    dispatcher_endpoint = getattr(
        getattr(task, "dispatcher_instance", None), "endpoint", None
    )
    upstream_url = (
        f"{dispatcher_endpoint}/tasks/{task_id}/vnc" if dispatcher_endpoint else None
    )
    log.info(
        "noVNC proxy requested for task %s → upstream %s (ASGI/Channels pending)",
        task_id,
        upstream_url,
    )

    return JsonResponse(
        {
            "error": "Not implemented",
            "detail": _NOT_IMPLEMENTED_MSG,
            "upstream_url": upstream_url,
        },
        status=501,
    )
