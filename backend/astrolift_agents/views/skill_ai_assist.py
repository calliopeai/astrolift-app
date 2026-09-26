"""Skill AI assist endpoint — direct Anthropic call, no agent dispatch (#884).

POST /api/agents/v1/skills/ai-assist/
Body:  {"description": "<what this skill should do>"}
Returns: {"output": "<generated system prompt text>"}

Auth: standard session or bearer-token auth from the middleware chain.
The caller must be authenticated; no further RBAC is applied because the
endpoint only calls Anthropic — it reads no org-scoped data.

If ANTHROPIC_API_KEY is not set in the environment, the endpoint returns
503 so the frontend can surface a clear "not configured" state.
"""

from __future__ import annotations

import json
import logging
import os

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core.permissions import route_auth
from core.utils.browser_guard import require_ui_header_for_session

try:
    import anthropic
except ImportError:  # package not yet installed; 503 path handles the missing-key case
    anthropic = None  # type: ignore[assignment]

log = logging.getLogger("astrolift_agents.skill_ai_assist")

_SYSTEM_PROMPT = (
    "You are an expert agent-configuration assistant for the Astrolift platform. "
    "When given a short description of what a skill should do, generate a concise, "
    "clear system prompt that an AI agent would receive at boot. "
    "The system prompt should describe the agent's role, behavior, and any constraints. "
    "Reply with only the system prompt text — no preamble, no explanation, no markdown fences."
)


@csrf_exempt
@require_ui_header_for_session
@require_http_methods(["POST"])
@route_auth(
    credential="authenticated session (with the UI header) or API bearer",
    scope="none: it reads and writes no tenant data, only forwards the description to the model",
)
def skill_ai_assist(request: HttpRequest) -> JsonResponse:
    """Return an AI-generated system prompt suggestion for a new skill.

    Auth
    ----
    Standard session or bearer-token auth (ApiToken / DeployToken) via the
    existing middleware chain. Returns 401 for unauthenticated requests.

    Body
    ----
    {"description": "what this skill should do"}

    Response
    --------
    200: {"output": "<generated system prompt>"}
    400: {"error": "description is required"}
    401: {"error": "authentication required"}
    503: {"error": "AI assist not configured"}
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication required"}, status=401)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning("skill_ai_assist: ANTHROPIC_API_KEY not set")
        return JsonResponse({"error": "AI assist not configured"}, status=503)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "invalid JSON"}, status=400)

    description = (body.get("description") or "").strip()
    if not description:
        return JsonResponse({"error": "description is required"}, status=400)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": description},
            ],
        )
        output = message.content[0].text
    except Exception as exc:
        log.exception("skill_ai_assist: Anthropic call failed: %s", exc)
        return JsonResponse({"error": "AI assist request failed"}, status=502)

    return JsonResponse({"output": output})
