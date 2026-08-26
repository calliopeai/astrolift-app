"""Wayfinding assistant: read-only help and screen-finding (#1101).

``POST /api/agents/v1/wayfinding/ask/``
``{"question": "where do I manage members?"}``

Answers with prose plus the routes it cited. Extends the direct-Anthropic
pattern in ``skill_ai_assist.py`` rather than standing up a second
retrieval service, which would duplicate the key handling and the request
shape for no gain.

**Read-only by construction, not by instruction.** The endpoint has no
mutation path: it reads the route dictionary, filters it, and asks a model
to answer over that text. There is nothing here that could deploy, change
config or dispatch an agent even if a prompt asked it to, which is a
stronger guarantee than a system prompt saying "do not take actions".

**The entitlement filter is the point.** Candidate routes are filtered
through the server-computed ``me.modules`` manifest before the model ever
sees them, so the assistant cannot suggest a screen the viewer cannot
open. Filtering after the model answers would be too late; filtering in the
browser would be a suggestion rather than a guarantee. Pointing someone at
a screen they cannot reach is worse than saying nothing, because they will
believe the tool over the 403.
"""

from __future__ import annotations

import json
import logging
import os

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

try:
    import anthropic
except ImportError:  # mirrors skill_ai_assist: the 503 path covers it
    anthropic = None  # type: ignore[assignment]

log = logging.getLogger("astrolift_agents.wayfinding")

MAX_QUESTION_CHARS = 500
"""A wayfinding question is a sentence. A cap keeps a pasted logfile from
becoming the prompt."""

_SYSTEM_PROMPT = (
    "You are a wayfinding assistant for the Astrolift platform. You help people "
    "find the right screen and understand how to do things.\n\n"
    "You will be given the list of screens THIS user can reach. It is already "
    "filtered to their entitlements.\n\n"
    "Rules:\n"
    "- Only ever name a route from the list. If the answer is not in it, say so "
    "plainly; do not guess a plausible-looking path.\n"
    "- You cannot take actions. You do not deploy, change settings, or run "
    "anything. The most you do is tell someone where to go.\n"
    "- Be brief. One or two sentences, then the route.\n"
    "- If a screen exists but has no nav entry, say it is reachable by direct "
    "link so the person is not left hunting the sidebar for it."
)


@csrf_exempt
@require_http_methods(["POST"])
def wayfinding_ask(request: HttpRequest) -> JsonResponse:
    """Answer a "where do I…" question over the routes this viewer can reach.

    200: ``{"answer": "...", "routes": ["/administration/members"]}``
    400: invalid body / empty or oversized question
    401: unauthenticated
    503: no API key configured
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication required"}, status=401)

    try:
        body = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"error": "invalid JSON"}, status=400)

    question = (body.get("question") or "").strip() if isinstance(body.get("question"), str) else ""
    if not question:
        return JsonResponse({"error": "question is required"}, status=400)
    if len(question) > MAX_QUESTION_CHARS:
        return JsonResponse(
            {"error": f"question must be {MAX_QUESTION_CHARS} characters or fewer"},
            status=400,
        )

    from astrolift_agents.services.wayfinding import index_for_prompt, visible_routes

    routes = visible_routes(_viewer_modules(request))
    if not routes:
        # No entitlements resolved, or no dictionary on this image. Answered
        # rather than 500'd, and answered honestly: claiming there are no
        # screens would be a different lie from admitting we cannot see them.
        return JsonResponse(
            {
                "answer": "I can't see any screens for your account right now, so I can't point you at one.",
                "routes": [],
            }
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning("wayfinding: ANTHROPIC_API_KEY not set")
        return JsonResponse({"error": "wayfinding not configured"}, status=503)

    prompt = f"Screens this user can reach:\n{index_for_prompt(routes)}\n\nQuestion: {question}"
    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = message.content[0].text
    except Exception as exc:
        log.exception("wayfinding: Anthropic call failed: %s", exc)
        return JsonResponse({"error": "wayfinding request failed"}, status=502)

    # Cited routes are recovered from the answer and intersected with what
    # the viewer can reach -- belt and braces over the pre-filter. The model
    # is instructed not to invent a path, and a model following instructions
    # is not a security boundary; this is.
    reachable = {r.path for r in routes}
    cited = [path for path in sorted(reachable, key=len, reverse=True) if path in answer]

    return JsonResponse({"answer": answer, "routes": cited})


def _viewer_modules(request: HttpRequest) -> dict[str, bool]:
    """The viewer's ``me.modules`` view flags, computed server-side.

    Failures resolve to an empty map, which `visible_routes` treats as "can
    view nothing". Fail-closed on purpose: an entitlement lookup that
    errors must not widen what the assistant offers.
    """
    try:
        from astrolift_identity.permission_resolver import resolve_effective_permissions
        from core.permissions import module_entitlements
        from core.tenancy import get_current_tenant

        tenant = get_current_tenant()
        if tenant is None:
            # No tenant resolved. Fail closed rather than falling back to a
            # user-only permission read: without an org there is no scope
            # the entitlements would be correct for.
            return {}
        perms = resolve_effective_permissions(tenant)
        entitlements = module_entitlements(
            perms,
            is_superuser=bool(getattr(request.user, "is_superuser", False)),
            is_staff=bool(getattr(request.user, "is_staff", False)),
        )
        return {e.key: e.can_view for e in entitlements}
    except Exception:
        log.exception("wayfinding: could not resolve viewer modules; treating as no access")
        return {}
