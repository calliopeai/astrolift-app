"""URL routes for the Agent Dispatch REST surface.

Mounted at the project root from ``config.urls`` so the wire URLs resolve
without a base-URL prefix. All endpoints carry session or bearer auth and
must be ``csrf_exempt`` — API/deploy-token callers have no CSRF token.

Surfaces
--------
  * noVNC WebSocket proxy  (/api/agents/v1/, #58)

Completion note — noVNC WebSocket proxy
---------------------------------------
The ``novnc_ws_proxy`` view currently returns 501. Completing it requires
ASGI/Channels — see the module docstring in
``astrolift_agents/views/novnc_proxy.py`` for the full checklist.
"""

from __future__ import annotations

from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from astrolift_agents.views.novnc_proxy import novnc_ws_proxy

app_name = "astrolift_agents"

urlpatterns = [
    # noVNC WebSocket proxy (#58).
    # The browser's noVNC JS client connects here as a WebSocket.
    # Currently returns 501 until ASGI/Channels is configured.
    path(
        "api/agents/v1/tasks/<str:task_id>/vnc/ws/",
        csrf_exempt(novnc_ws_proxy),
        name="novnc-ws-proxy",
    ),
]
