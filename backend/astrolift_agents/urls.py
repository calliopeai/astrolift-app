"""URL routes for the Agent Dispatch REST surface.

Mounted at the project root from ``config.urls`` so the wire URLs resolve
without a base-URL prefix. All endpoints carry session or bearer auth and
must be ``csrf_exempt`` — API/deploy-token callers have no CSRF token.

Surfaces
--------
  * noVNC WebSocket proxy        (/api/agents/v1/, #58)
  * Skill AI assist              (/api/agents/v1/skills/ai-assist/, #884)

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
from astrolift_agents.views.skill_ai_assist import skill_ai_assist

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
    # Skill AI assist (#884).
    # Calls Anthropic directly to suggest a system prompt for a new skill.
    # No agent dispatch or containers involved.
    path(
        "api/agents/v1/skills/ai-assist/",
        skill_ai_assist,
        name="skill-ai-assist",
    ),
]
