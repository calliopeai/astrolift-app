"""Controller-side REST API endpoints for the Dispatch Service (#48).

These endpoints are called by the Dispatch Service process (not by browser
clients). Authentication uses Bearer tokens — the API key issued at registration.

Endpoints:
    POST /api/dispatch/v1/register/         — register a new Dispatch Service
    POST /api/dispatch/v1/heartbeat/        — update last_heartbeat_at
    GET  /api/dispatch/v1/tasks/            — poll for QUEUED tasks
    POST /api/dispatch/v1/tasks/<task_id>/status/  — update task status

Auth: Bearer <dispatcher_api_key> (scoped key issued at registration)
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from functools import wraps

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from astrolift_agents.models import AgentTask, DispatcherInstance

logger = logging.getLogger(__name__)

_API_KEY_BYTES = 32  # 256-bit random key
_BOOTSTRAP_TOKEN_SETTING = "DISPATCHER_BOOTSTRAP_TOKEN"


def _get_bootstrap_token() -> str | None:
    """Return the bootstrap token from settings, or None if not configured."""
    from django.conf import settings
    return getattr(settings, _BOOTSTRAP_TOKEN_SETTING, None) or None


def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of an API key for storage."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _get_dispatcher_from_request(request: HttpRequest) -> DispatcherInstance | None:
    """Authenticate a dispatcher API request via Bearer token.

    Returns the DispatcherInstance on success, None on failure.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    raw_key = auth[len("Bearer "):]
    key_hash = _hash_key(raw_key)
    return DispatcherInstance.objects.filter(
        api_key_hash=key_hash,
        status__in=["active", "pending"],
        deleted_at__isnull=True,
    ).first()


def _require_dispatcher(view_func):
    """Decorator: authenticate the request as a Dispatch Service call."""
    @wraps(view_func)
    def wrapper(request: HttpRequest, *args, **kwargs):
        dispatcher = _get_dispatcher_from_request(request)
        if dispatcher is None:
            return JsonResponse({"error": "unauthorized"}, status=401)
        request.dispatcher = dispatcher
        return view_func(request, *args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
def register(request: HttpRequest) -> JsonResponse:
    """Register a new Dispatch Service instance.

    Auth: Bearer <bootstrap_token> (set in settings as DISPATCHER_BOOTSTRAP_TOKEN)
    Body: {name, endpoint, cloud, region, backend, capability_labels}
    Returns: {dispatcher_id, api_key}
    """
    bootstrap_token = _get_bootstrap_token()
    if bootstrap_token:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {bootstrap_token}":
            return JsonResponse({"error": "invalid bootstrap token"}, status=401)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "invalid JSON"}, status=400)

    name = (body.get("name") or "").strip()
    endpoint = (body.get("endpoint") or "").strip()
    cloud = body.get("cloud", "k8s_native")
    region = body.get("region", "")
    backend = body.get("backend", "k8s_job")
    capability_labels = body.get("capability_labels") or {}
    org_slug = body.get("org_slug", "")

    if not name or not endpoint:
        return JsonResponse({"error": "name and endpoint are required"}, status=400)

    from astrolift_identity.models import Organization

    org = Organization.objects.filter(slug=org_slug, deleted_at__isnull=True).first()
    if org is None:
        return JsonResponse({"error": f"org '{org_slug}' not found"}, status=404)

    # Generate scoped API key
    raw_key = secrets.token_hex(_API_KEY_BYTES)
    key_hash = _hash_key(raw_key)

    dispatcher, _ = DispatcherInstance.objects.update_or_create(
        organization=org,
        name=name,
        defaults={
            "slug": f"{org_slug}-{name}".lower().replace(" ", "-")[:80],
            "endpoint": endpoint,
            "api_key_hash": key_hash,
            "cloud": cloud,
            "region": region,
            "backend": backend,
            "capability_labels": capability_labels,
            "status": "pending",
        },
    )

    logger.info("dispatch.register: registered dispatcher %s for org %s", name, org_slug)

    return JsonResponse({
        "dispatcher_id": str(dispatcher.guid),
        "api_key": raw_key,  # Returned only once — must be stored by the client
    }, status=201)


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
@_require_dispatcher
def heartbeat(request: HttpRequest) -> JsonResponse:
    """Update a Dispatch Service's last_heartbeat_at.

    Transitions PENDING → ACTIVE on first successful heartbeat.
    """
    dispatcher = request.dispatcher
    now = timezone.now()

    update_fields = ["last_heartbeat_at", "updated_at", "version"]
    dispatcher.last_heartbeat_at = now

    if dispatcher.status == "pending":
        dispatcher.status = "active"
        update_fields.append("status")

    dispatcher.save(update_fields=update_fields)

    return JsonResponse({"ok": True, "status": dispatcher.status})


# ---------------------------------------------------------------------------
# Task polling
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["GET"])
@_require_dispatcher
def list_tasks(request: HttpRequest) -> JsonResponse:
    """Return QUEUED tasks assigned to this dispatcher for spawning."""
    dispatcher = request.dispatcher

    tasks = AgentTask.objects.filter(
        dispatcher=dispatcher,
        status="queued",
        deleted_at__isnull=True,
    ).select_related("agent_definition", "brief")[:50]

    return JsonResponse({
        "tasks": [
            {
                "id": str(t.guid),
                "agent_workload_slug": t.agent_definition.slug if t.agent_definition_id else None,
                "brief_id": str(t.brief.guid) if t.brief_id else None,
                "timeout_seconds": t.timeout_seconds,
                "callback_url": t.callback_url or "",
            }
            for t in tasks
        ]
    })


# ---------------------------------------------------------------------------
# Task status update
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
@_require_dispatcher
def update_task_status(request: HttpRequest, task_id: str) -> JsonResponse:
    """Update the status of a dispatched task.

    Body: {status, external_id?, error_message?}
    Valid status values: running, completed, failed, cancelled
    """
    dispatcher = request.dispatcher

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "invalid JSON"}, status=400)

    task = AgentTask.objects.filter(
        guid=task_id,
        dispatcher=dispatcher,
        deleted_at__isnull=True,
    ).first()

    if task is None:
        return JsonResponse({"error": "task not found"}, status=404)

    new_status = body.get("status")
    if new_status not in {"running", "completed", "failed", "cancelled"}:
        return JsonResponse({"error": f"invalid status: {new_status!r}"}, status=400)

    # Map dispatch service status to AgentTask status
    status_map = {
        "running": AgentTask.Status.RUNNING,
        "completed": AgentTask.Status.COMPLETED,
        "failed": AgentTask.Status.FAILED,
        "cancelled": AgentTask.Status.CANCELLED,
    }

    update_fields = ["status", "updated_at", "version"]
    task.status = status_map[new_status]

    if body.get("external_id"):
        task.external_id = body["external_id"]
        update_fields.append("external_id")

    if body.get("error_message"):
        task.failure = {"message": body["error_message"]}
        update_fields.append("failure")

    now = timezone.now()
    if new_status == "running" and not task.started_at:
        task.started_at = now
        update_fields.append("started_at")
    elif new_status in {"completed", "failed", "cancelled"} and not task.ended_at:
        task.ended_at = now
        update_fields.append("ended_at")

    task.save(update_fields=update_fields)

    logger.info("dispatch.task_status: task %s → %s", task_id, new_status)
    return JsonResponse({"ok": True, "status": task.status})
