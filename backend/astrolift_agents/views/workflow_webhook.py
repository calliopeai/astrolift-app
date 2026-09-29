"""Generic inbound workflow/agent webhook endpoint (#983).

``POST /api/webhooks/workflow/<org_slug>/<slug>`` — the org-level trigger
endpoint the ``WorkflowWebhook`` model documents. Resolves the webhook by
slug (org-scoped), verifies the signing secret, then dispatches:

  * an ``agent_definition`` webhook -> an AgentTask via the PR-1 dispatch
    path (``dispatch_agent_task_from_webhook``), or
  * a ``workflow_definition`` webhook -> a WorkflowInstance.

Auth is the webhook secret, not a session/bearer — callers are external.
Per the model contract the caller sends the plaintext secret in the
``X-Astrolift-Signature`` header; we compare SHA-256(provided) against the
stored ``secret_hash`` with ``hmac.compare_digest`` (constant-time).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

log = logging.getLogger("astrolift_agents.views.workflow_webhook")


def verify_signature(*, provided: str, secret_hash: str) -> bool:
    """Constant-time check of an inbound WorkflowWebhook signature (#983).

    The caller sends the plaintext signing secret in the
    ``X-Astrolift-Signature`` header; we compare SHA-256(provided)
    against the stored ``secret_hash`` with ``hmac.compare_digest``.
    Returns False on a missing header or any mismatch — the caller turns
    False into a generic 401.

    Named ``verify_signature`` so the ``test_webhook_signature_guard`` CI
    guard (#529) recognizes it as the auth boundary on this view.
    """
    if not provided or not secret_hash:
        return False
    return hmac.compare_digest(hashlib.sha256(provided.encode()).hexdigest(), secret_hash)


@csrf_exempt
@require_POST
def workflow_webhook(request: HttpRequest, org_slug: str, slug: str) -> JsonResponse:
    from astrolift_agents.models import WorkflowWebhook
    from astrolift_agents.services.workflow_triggers import (
        dispatch_agent_task_from_webhook,
        trigger_workflow_instance,
    )
    from core.run_trigger import RunTrigger
    from workflows.run_service import start_workflow_definition_run

    webhook = (
        WorkflowWebhook.objects.filter(slug=slug, enabled=True)
        .select_related("organization", "agent_definition__registered_app", "workflow_definition")
        .first()
    )
    # Same generic 404 whether the webhook is missing, disabled, or org
    # mismatched — don't leak which slugs exist across orgs.
    if webhook is None or getattr(webhook.organization, "slug", None) != org_slug:
        return JsonResponse({"ok": False, "error": "not found"}, status=404)

    provided = request.headers.get("X-Astrolift-Signature", "")
    if not verify_signature(provided=provided, secret_hash=webhook.secret_hash or ""):
        return JsonResponse({"ok": False, "error": "invalid signature"}, status=401)

    try:
        payload = json.loads(request.body or b"{}")
        if not isinstance(payload, dict):
            payload = {"payload": payload}
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid JSON body"}, status=400)

    dispatched = False
    task_id: str | None = None
    if webhook.agent_definition_id is not None:
        task = dispatch_agent_task_from_webhook(webhook, payload)
        if task is not None:
            dispatched = True
            task_id = str(task.guid)
    elif webhook.workflow_definition_id is not None:
        definition = webhook.workflow_definition
        # A stage-based (agent-orchestration) definition runs through the
        # WorkflowDefinitionRunWorkflow executor — the same path
        # runWorkflowDefinition uses — so the webhook actually executes its
        # stages (#1020). Definitions without stages fall back to the legacy
        # state-machine WorkflowInstance path.
        if definition.stages.filter(deleted_at__isnull=True).exists():
            run, _wid = start_workflow_definition_run(
                definition,
                trigger_payload=payload,
                organization_id=webhook.organization_id,
                trigger_kind=RunTrigger.WEBHOOK,
            )
            task_id = str(run.workflow_id)
        else:
            trigger_workflow_instance(
                definition, payload, trigger_kind="webhook", organization_id=webhook.organization_id
            )
        dispatched = True

    webhook.last_triggered_at = timezone.now()
    webhook.save(update_fields=["last_triggered_at", "updated_at"])

    return JsonResponse({"ok": True, "dispatched": dispatched, "taskId": task_id})
