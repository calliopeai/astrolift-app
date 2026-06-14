"""URL routes for the Agent Dispatch REST surface.

Mounted at the project root from ``config.urls`` so the wire URLs resolve
without a base-URL prefix. All endpoints carry session or bearer auth and
must be ``csrf_exempt`` — API/deploy-token callers have no CSRF token.

Surfaces
--------
  * Skill AI assist              (/api/agents/v1/skills/ai-assist/, #884)

noVNC relay (#877)
------------------
The old ``novnc_ws_proxy`` Django view (#58) returned 501 and assumed a
WSGI/Channels relay through a separate Dispatch Service. The stack is now
ASGI (``config/asgi.py``) and relays at the ASGI layer, mirroring the
exec relay. The live endpoint is the WebSocket handler
``core.schema.vnc_ws.vnc_ws_application`` dispatched from
``config/asgi.py`` under ``/app/vnc/<task-guid>``. The stale view + route
were removed; there is no HTTP route for VNC anymore.
"""

from __future__ import annotations

from django.urls import path

from astrolift_agents.views.skill_ai_assist import skill_ai_assist

app_name = "astrolift_agents"

urlpatterns = [
    # Skill AI assist (#884).
    # Calls Anthropic directly to suggest a system prompt for a new skill.
    # No agent dispatch or containers involved.
    path(
        "api/agents/v1/skills/ai-assist/",
        skill_ai_assist,
        name="skill-ai-assist",
    ),
]
