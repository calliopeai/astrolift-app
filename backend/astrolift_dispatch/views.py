"""Controller-side REST API endpoints for the Dispatch Service (#48, #51, #56).

These endpoints are called by the Dispatch Service process, not browser clients.
Authentication: Bearer token (API key issued at registration).

Endpoints:
    POST /api/dispatch/v1/register/                     — register Dispatch Service
    POST /api/dispatch/v1/heartbeat/                    — update heartbeat
    GET  /api/dispatch/v1/tasks/                        — poll QUEUED tasks
    POST /api/dispatch/v1/tasks/<id>/status/            — update task status
    POST /api/dispatch/v1/tasks/<id>/logs/              — stream log lines (#51)
    POST /api/dispatch/v1/tasks/<id>/meter/             — report metering data (#56)
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from functools import wraps

from django.core.exceptions import RequestDataTooBig
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from astrolift_agents.models import (
    AgentInteraction,
    AgentTask,
    DispatcherInstance,
    record_interaction,
)
from astrolift_agents.services.agent_task_events import (
    TaskEventError,
    commit_task_events,
    prepare_task_events,
    validate_task_events,
)
from astrolift_agents.services.agent_task_requests import TaskInputRequestError, callback_input_response

logger = logging.getLogger(__name__)

_API_KEY_BYTES = 32  # 256-bit random key
_BOOTSTRAP_TOKEN_SETTING = "DISPATCHER_BOOTSTRAP_TOKEN"
_MAX_CALLBACK_FINDING_BYTES = 64 * 1024
_MAX_CALLBACK_FINDINGS = 100
_MAX_CALLBACK_FINDINGS_TOTAL_BYTES = 1024 * 1024
_MAX_CALLBACK_BODY_BYTES = 2 * 1024 * 1024


def _get_bootstrap_token() -> str | None:
    """Return the bootstrap token from settings, or None if not configured."""
    from django.conf import settings

    return getattr(settings, _BOOTSTRAP_TOKEN_SETTING, None) or None


def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of an API key for storage."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _reject_nonfinite_json(value: str):
    """Reject Python's non-standard NaN/Infinity JSON extensions."""
    raise ValueError(f"non-finite JSON number {value!r} is not allowed")


def _get_dispatcher_from_request(request: HttpRequest) -> DispatcherInstance | None:
    """Authenticate a dispatcher API request via Bearer token.

    Returns the DispatcherInstance on success, None on failure.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    raw_key = auth[len("Bearer ") :]
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


def _require_agent_callback_auth(view_func):
    """Authenticate a callback with either a dispatcher or task-scoped key.

    K8s one-shot pods do not possess a DispatcherInstance plaintext key (the
    Controller stores only its hash), so spawn injects a unique callback key
    whose hash is frozen on the exact AgentTask.  Binding both the hash and URL
    task id prevents that credential from reading or updating another task.
    """

    @wraps(view_func)
    def wrapper(request: HttpRequest, task_id: str, *args, **kwargs):
        dispatcher = _get_dispatcher_from_request(request)
        if dispatcher is not None:
            request.dispatcher = dispatcher
            return view_func(request, task_id, *args, **kwargs)

        auth = request.headers.get("Authorization", "")
        raw_key = auth[len("Bearer ") :] if auth.startswith("Bearer ") else ""
        if not raw_key.startswith("alft_cb_"):
            return JsonResponse({"error": "unauthorized"}, status=401)
        task = AgentTask.objects.filter(
            guid=task_id,
            callback_token_hash=_hash_key(raw_key),
            deleted_at__isnull=True,
        ).first()
        if task is None:
            return JsonResponse({"error": "unauthorized"}, status=401)
        request.agent_task = task
        request.agent_callback_token_hash = _hash_key(raw_key)
        return view_func(request, task_id, *args, **kwargs)

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
    if not bootstrap_token:
        logger.error("dispatch.register refused: DISPATCHER_BOOTSTRAP_TOKEN is not configured")
        return JsonResponse({"error": "dispatcher registration is not configured"}, status=503)
    auth = request.headers.get("Authorization", "")
    if not secrets.compare_digest(auth, f"Bearer {bootstrap_token}"):
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

    return JsonResponse(
        {
            "dispatcher_id": str(dispatcher.guid),
            "api_key": raw_key,  # Returned only once — must be stored by the client
        },
        status=201,
    )


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
# Fleet and runtime read surface
# ---------------------------------------------------------------------------


def _read_scope(request: HttpRequest):
    kind = request.GET.get("scope", "organization")
    if kind not in {"organization", "dispatcher"}:
        return None
    return {
        "version": 1,
        "kind": kind,
        "organization_id": str(request.dispatcher.organization.guid),
        "dispatcher_id": str(request.dispatcher.guid),
    }


@require_http_methods(["GET"])
@_require_dispatcher
def list_fleet(request: HttpRequest) -> JsonResponse:
    """List this dispatcher's organization's agent task fleet.

    The dispatcher credential is organization-scoped, so this is safe for
    CLI and overseer clients that use a dispatch service token.  By default
    only non-terminal tasks are returned; ``include_terminal=1`` includes
    recent terminal rows as well.  Secrets, prompts, and task results are
    deliberately excluded from this operational view. ``scope=dispatcher``
    restricts rows to the authenticated dispatcher for deployment-bound clients.
    """
    from astrolift_agents.models import AgentTask

    scope = _read_scope(request)
    if scope is None:
        return JsonResponse({"error": "invalid scope"}, status=400)
    if request.dispatcher.organization.deleted_at is not None:
        return JsonResponse({"error": "unauthorized"}, status=401)
    tasks = AgentTask.objects.filter(
        organization=request.dispatcher.organization,
        deleted_at__isnull=True,
    )
    if scope["kind"] == "dispatcher":
        tasks = tasks.filter(dispatcher=request.dispatcher)
    if request.GET.get("include_terminal") not in {"1", "true", "yes"}:
        tasks = tasks.filter(status__in=AgentTask.NON_TERMINAL_STATUSES)
    tasks = list(
        tasks.select_related("agent_definition", "environment_spec", "dispatcher").order_by(
            "-updated_at", "pk"
        )[:201]
    )

    return JsonResponse(
        {
            "scope": scope,
            "has_more": len(tasks) > 200,
            "agents": [
                {
                    "task_id": str(task.guid),
                    "status": task.status,
                    "agent_slug": task.agent_definition.slug if task.agent_definition_id else None,
                    "runtime": task.environment_spec.runtime if task.environment_spec_id else "",
                    "dispatcher_id": str(task.dispatcher.guid) if task.dispatcher_id else None,
                    "external_id": task.external_id,
                    "started_at": task.started_at.isoformat() if task.started_at else None,
                    "ended_at": task.ended_at.isoformat() if task.ended_at else None,
                    "vnc_url": task.vnc_url or None,
                }
                for task in tasks[:200]
            ],
        }
    )


@require_http_methods(["GET"])
@_require_dispatcher
def list_runtimes(request: HttpRequest) -> JsonResponse:
    """List the install-wide runtime catalog available to agent tasks."""
    from astrolift_agents.runtime_catalog import catalog_entries

    scope = _read_scope(request)
    if scope is None:
        return JsonResponse({"error": "invalid scope"}, status=400)
    if request.dispatcher.organization.deleted_at is not None:
        return JsonResponse({"error": "unauthorized"}, status=401)
    return JsonResponse({"scope": scope, "runtimes": catalog_entries()})


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

    return JsonResponse(
        {
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
        }
    )


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

    with transaction.atomic():
        task = (
            AgentTask.objects.select_for_update()
            .filter(
                guid=task_id,
                dispatcher=dispatcher,
                deleted_at__isnull=True,
            )
            .first()
        )
        if task is None:
            return JsonResponse({"error": "task not found"}, status=404)

        target = status_map[new_status]
        if task.cancel_requested_at and task.status not in AgentTask.TERMINAL_STATUSES:
            return JsonResponse({"error": "task cancellation is pending", "continue": False}, status=409)
        if task.status in AgentTask.TERMINAL_STATUSES:
            if task.status != target:
                return JsonResponse(
                    {"error": f"task is already terminal ({task.status})"},
                    status=409,
                )
        else:
            update_fields: list[str] = []
            if body.get("external_id"):
                task.external_id = str(body["external_id"])[:255]
                update_fields.append("external_id")
            if body.get("error_message"):
                task.failure = {"message": str(body["error_message"])}
                update_fields.append("failure")
            if update_fields:
                task.save(update_fields=[*update_fields, "updated_at", "version"])

            try:
                if target == AgentTask.Status.RUNNING and task.status == AgentTask.Status.QUEUED:
                    task.transition_to(AgentTask.Status.PROVISIONING)
                if target == AgentTask.Status.COMPLETED and task.status == AgentTask.Status.PROVISIONING:
                    task.transition_to(AgentTask.Status.RUNNING)
                if task.status != target:
                    task.transition_to(target)
            except ValueError as exc:
                return JsonResponse({"error": str(exc)}, status=409)

    record_interaction(
        task,
        kind=AgentInteraction.Kind.CONTROL_API,
        name="status",
        status="error" if new_status == "failed" else "ok",
        detail={"new_status": new_status},
    )

    logger.info("dispatch.task_status: task %s → %s", task_id, new_status)
    return JsonResponse({"ok": True, "status": task.status})


# ---------------------------------------------------------------------------
# Thread-mode agent checkin + callback
# ---------------------------------------------------------------------------


def _advance_to_running(task: AgentTask) -> None:
    """Walk a task forward to RUNNING following the allowed state graph.

    The runner checks in once its pod is live, so the task may legitimately
    be QUEUED (dispatcher about to mark provisioning), PROVISIONING (spawn
    in flight), or already RUNNING (re-checkin / race). The transition graph
    is QUEUED → PROVISIONING → RUNNING, so a single jump to RUNNING from
    QUEUED is illegal — we step through PROVISIONING first. Each step is
    guarded so a concurrent advance by ``update_task_status`` is harmless.
    """
    if task.status == AgentTask.Status.QUEUED:
        try:
            task.transition_to(AgentTask.Status.PROVISIONING)
        except ValueError:
            return  # raced past QUEUED — re-read below or let caller proceed
    if task.status == AgentTask.Status.PROVISIONING:
        try:
            task.transition_to(AgentTask.Status.RUNNING)
        except ValueError:
            pass  # already advanced by a race — fine


@csrf_exempt
@require_http_methods(["POST"])
@_require_dispatcher
def agent_checkin(request: HttpRequest, task_id: str) -> JsonResponse:
    """Thread-mode agent checkin endpoint.

    Called by ``runner.py`` after the pod starts — returns the Brief manifest
    as the instruction packet the agent should execute.

    Auth: same Bearer token as other dispatch endpoints (DispatcherInstance).
    The task must belong to this dispatcher's org.

    Returns JSON:
    {
      "task_id": "...",
      "prompt": "...",           # from Brief.manifest_snapshot.system_prompt
      "system": "...",           # same (may be identical or separate)
      "tools": [...],            # from Brief.manifest_snapshot.tools
      "context": {...},          # from Brief.context
      "model": "...",            # from Brief.manifest_snapshot.model (if set)
      "callback_url": "..."      # from AgentTask.callback_url
    }
    """
    dispatcher = request.dispatcher

    task = (
        AgentTask.objects.filter(
            guid=task_id,
            organization=dispatcher.organization,
            deleted_at__isnull=True,
        )
        .select_related("brief")
        .first()
    )

    if task is None:
        return JsonResponse({"error": "task not found"}, status=404)

    dispatchable = {
        AgentTask.Status.QUEUED,
        AgentTask.Status.PROVISIONING,
        AgentTask.Status.RUNNING,
    }
    if task.status not in dispatchable:
        return JsonResponse(
            {"error": f"task not in dispatchable state: {task.status}"},
            status=409,
        )

    # Advance to RUNNING following the QUEUED → PROVISIONING → RUNNING graph.
    if task.status != AgentTask.Status.RUNNING:
        _advance_to_running(task)

    # Build the instruction packet from the Brief manifest snapshot.
    brief = task.brief
    manifest = brief.manifest_snapshot if brief else {}

    packet = {
        "task_id": str(task.guid),
        "prompt": manifest.get("system_prompt", ""),
        "system": manifest.get(
            "system_prompt",
            "You are a helpful agent. Complete the task and report results.",
        ),
        "tools": manifest.get("tools", []),
        "context": (brief.context if brief else {}),
        "model": manifest.get("model", ""),
        "callback_url": task.callback_url or "",
    }

    record_interaction(
        task,
        kind=AgentInteraction.Kind.CONTROL_API,
        name="checkin",
        detail={"status": task.status},
    )

    logger.info("dispatch.agent_checkin: task %s checked in", task_id)
    return JsonResponse(packet)


@csrf_exempt
@require_http_methods(["POST"])
@_require_agent_callback_auth
def agent_callback(request: HttpRequest, task_id: str) -> JsonResponse:
    """Heartbeat and result callback from thread-mode agents.

    Body: {status, result?, partial?, error?, continue?, input_intent?, events?}

    A terminal ``status`` of "completed"/"failed" records the outcome and
    transitions the task. ``running`` (or an omitted status) is a heartbeat;
    unknown values are rejected. The response carries ``continue`` — false
    tells the agent to stop (the task is no longer RUNNING).

    Steering channel (#1390). ``input_intent`` is how the runner asks for
    queued follow-up prompts on the state callback it already posts, so the
    steering channel needs no second transport, no poll loop and no extra
    credential:

    * omitted — a plain heartbeat. No queue work at all, so the frequent
      heartbeat path costs exactly what it costs today.
    * ``"peek"`` — response gains ``pending_input_count``; nothing is
      marked delivered. This is what a runner whose harness cannot accept a
      follow-up prompt sends, so the message stays visibly queued instead
      of being silently consumed and dropped.
    * ``"consume"`` — response gains ``pending_input_count`` AND
      ``pending_input``: the next ordered batch, marked delivered in the
      same transaction. At-most-once by construction.

    ``pending_input`` entries are ``{id, message, author, created_at}``.
    Input is only ever handed out for a RUNNING task — a terminal callback
    consumes nothing, so a message queued against a run that is finishing
    is not lost to a dying pod.
    """
    try:
        content_length = int(request.META.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        content_length = 0
    if content_length > _MAX_CALLBACK_BODY_BYTES:
        return JsonResponse(
            {"error": f"callback body exceeds {_MAX_CALLBACK_BODY_BYTES} bytes"},
            status=413,
        )
    try:
        body_bytes = request.body
    except RequestDataTooBig:
        return JsonResponse(
            {"error": f"callback body exceeds {_MAX_CALLBACK_BODY_BYTES} bytes"},
            status=413,
        )
    if len(body_bytes) > _MAX_CALLBACK_BODY_BYTES:
        return JsonResponse(
            {"error": f"callback body exceeds {_MAX_CALLBACK_BODY_BYTES} bytes"},
            status=413,
        )
    try:
        body = json.loads(body_bytes, parse_constant=_reject_nonfinite_json)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"error": "callback body must be a JSON object"}, status=400)

    new_status = body.get("status")
    if new_status not in {None, "running", "completed", "failed"}:
        return JsonResponse({"error": f"invalid callback status: {new_status!r}"}, status=400)
    input_intent = body.get("input_intent")
    if input_intent not in {None, "peek", "consume"}:
        return JsonResponse({"error": f"invalid input_intent: {input_intent!r}"}, status=400)
    try:
        events = validate_task_events(body["events"]) if "events" in body else None
    except TaskEventError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status)
    finding = body.get("finding")
    if finding is not None:
        if not isinstance(finding, dict):
            return JsonResponse({"error": "finding must be a JSON object"}, status=400)
        finding_size = len(json.dumps(finding, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        if finding_size > _MAX_CALLBACK_FINDING_BYTES:
            return JsonResponse(
                {"error": f"finding exceeds {_MAX_CALLBACK_FINDING_BYTES} bytes"},
                status=413,
            )

    finding_index: int | None = None
    input_response = None

    with transaction.atomic():
        query = AgentTask.objects.select_for_update().filter(guid=task_id, deleted_at__isnull=True)
        callback_hash = getattr(request, "agent_callback_token_hash", "")
        if callback_hash:
            query = query.filter(callback_token_hash=callback_hash)
        else:
            query = query.filter(
                organization=request.dispatcher.organization,
                dispatcher=request.dispatcher,
            )
        task = query.first()

        if task is None:
            # A task-token request that authenticated immediately before an
            # operator cancellation may wait on the row lock and find the
            # token revoked. Do not let that stale callback mutate the task.
            return JsonResponse({"error": "task not found or callback token expired"}, status=404)

        if task.cancel_requested_at and task.status not in AgentTask.TERMINAL_STATUSES:
            return JsonResponse({"error": "task cancellation is pending", "continue": False}, status=409)

        from astrolift_agents.services.task_timeout import task_timeout_reason

        if task.dispatch_target and task.status not in AgentTask.TERMINAL_STATUSES:
            if reason := task_timeout_reason(task):
                return JsonResponse({"error": reason, "continue": False}, status=409)

        try:
            prepared_events = prepare_task_events(task, events or [])
            if "input_request" in body:
                if new_status not in {None, "running"} or input_intent is not None:
                    raise TaskInputRequestError(
                        "input_request cannot accompany terminal status or input_intent"
                    )
                input_response = callback_input_response(task, body["input_request"])
        except TaskEventError as exc:
            return JsonResponse({"error": str(exc), "event_sequence": task.event_sequence}, status=exc.status)
        except TaskInputRequestError as exc:
            return JsonResponse({"error": str(exc)}, status=exc.status)

        if finding is not None:
            if task.status != AgentTask.Status.RUNNING:
                return JsonResponse(
                    {"error": f"task is not accepting findings ({task.status})", "continue": False},
                    status=409,
                )
            result = dict(task.result) if isinstance(task.result, dict) else {}
            findings = list(result.get("findings") or [])
            if len(findings) >= _MAX_CALLBACK_FINDINGS:
                return JsonResponse(
                    {"error": f"task already has {_MAX_CALLBACK_FINDINGS} findings", "continue": False},
                    status=409,
                )
            findings.append(finding)
            findings_size = len(json.dumps(findings, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            if findings_size > _MAX_CALLBACK_FINDINGS_TOTAL_BYTES:
                return JsonResponse(
                    {
                        "error": (
                            f"task findings exceed {_MAX_CALLBACK_FINDINGS_TOTAL_BYTES} aggregate bytes"
                        ),
                        "continue": False,
                    },
                    status=413,
                )
            finding_index = len(findings) - 1
            result["findings"] = findings
            task.result = result
            task.save(update_fields=["result", "updated_at", "version"])

        if new_status in {"completed", "failed"}:
            if task.status in AgentTask.TERMINAL_STATUSES:
                return JsonResponse(
                    {"error": f"task is already terminal ({task.status})", "continue": False},
                    status=409,
                )
            # A very fast one-shot pod can finish before the status poll sees
            # it running. Preserve the lifecycle graph rather than dropping a
            # valid result from PROVISIONING.
            if task.status == AgentTask.Status.PROVISIONING:
                task.transition_to(AgentTask.Status.RUNNING)
            if task.status != AgentTask.Status.RUNNING:
                return JsonResponse(
                    {"error": f"task is not accepting results ({task.status})", "continue": False},
                    status=409,
                )

            if new_status == "completed":
                result = dict(task.result) if isinstance(task.result, dict) else {}
                result["output"] = body["result"] if "result" in body else body.get("error", "")
                task.result = result
            else:
                task.failure = {
                    "message": body.get("error", ""),
                    "output": body.get("result", ""),
                }
            task.save(update_fields=["result", "failure", "updated_at", "version"])
            target = AgentTask.Status.COMPLETED if new_status == "completed" else AgentTask.Status.FAILED
            task.transition_to(target)

        from astrolift_agents.services.task_timeout import reserve_input_wait

        try:
            reserve_input_wait(task, prepared_events)
        except Exception:
            # Roll back findings/results too. A failed reservation is retryable
            # and must not acknowledge a question whose pod is about to expire.
            transaction.set_rollback(True)
            logger.warning("Could not reserve input wait for task %s", task.guid, exc_info=True)
            return JsonResponse({"error": "Could not reserve input wait; retry the callback"}, status=503)
        commit_task_events(task, prepared_events)

    record_interaction(
        task,
        kind=AgentInteraction.Kind.CONTROL_API,
        name="finding" if finding is not None else "callback",
        status="error" if new_status == "failed" else "ok",
        detail={
            "new_status": new_status or "heartbeat",
            **(
                {"finding_index": finding_index, "finding_keys": sorted(finding)}
                if finding is not None
                else {}
            ),
        },
    )

    payload = {"ok": True, "continue": task.status == AgentTask.Status.RUNNING}
    if events is not None:
        payload["event_sequence"] = task.event_sequence
        payload["input_protocol_version"] = 1
        payload["structured_event_protocol_version"] = 1
    if "input_request" in body:
        payload["input_response"] = input_response
    payload.update(_steering_input_payload(task, input_intent))
    return JsonResponse(payload)


def _steering_input_payload(task: AgentTask, input_intent: str | None) -> dict:
    """Queued follow-up prompts for the state-callback response (#1390).

    Returns ``{}`` for a plain heartbeat so the common path adds no query.
    Only a RUNNING task is served: a terminal callback has no next turn to
    consume at, and claiming there would mark messages delivered into a
    pod that is on its way out.
    """
    if input_intent is None:
        return {}
    if task.status != AgentTask.Status.RUNNING:
        return {"pending_input_count": 0, **({"pending_input": []} if input_intent == "consume" else {})}

    from astrolift_agents.services.agent_task_input import (
        claim_pending_input,
        pending_input_count,
        serialize_pending_input,
    )

    if input_intent == "peek":
        return {"pending_input_count": pending_input_count(task)}

    claimed = claim_pending_input(task)
    if claimed:
        # The genuine agent-signal path, captured the same way the cancel
        # signal is (#1216/#1217): this is control-plane-observed input
        # crossing into the pod, not just another Control API call.
        record_interaction(
            task,
            kind=AgentInteraction.Kind.SIGNAL,
            name="input",
            detail={
                "count": len(claimed),
                "message_ids": [str(row.guid) for row in claimed],
            },
        )
    return {
        "pending_input": serialize_pending_input(claimed),
        # What is STILL queued after this claim, so a runner that hit the
        # batch cap knows to come back at the next turn boundary.
        "pending_input_count": pending_input_count(task),
    }


# ---------------------------------------------------------------------------
# Log streaming and metering (#51, #56)
# ---------------------------------------------------------------------------


from astrolift_agents.models.task_meter import TaskMeteringRecord  # noqa: E402
from astrolift_dispatch.log_collector import store_agent_log_lines  # noqa: E402
from astrolift_lifecycle.models import AgentRun  # noqa: E402


def _agent_task_for_run(run: AgentRun | None) -> AgentTask | None:
    """Best-effort resolve the AgentTask a fleet-history AgentRun dispatched.

    The logs/meter endpoints key off ``AgentRun.guid``, but interactions
    attribute to the :class:`AgentTask`. Delegates to the shared
    :func:`~astrolift_agents.models.resolve_agent_task_for_run`, which is
    FK-first (``AgentTask.agent_run``) and falls back to the historical
    ``(workload, external_id == k8s_pod_name)`` join for rows that predate
    the FK (#1217). Returns ``None`` when no task is linked yet (push-mode,
    pre-spawn, or a run with no AgentTask) so capture is skipped rather than
    mis-attributed.
    """
    from astrolift_agents.models import resolve_agent_task_for_run

    return resolve_agent_task_for_run(run)


def _record_run_interaction(
    task_id: str,
    *,
    name: str,
    status: str = "ok",
    detail: dict | None = None,
    run: AgentRun | None = None,
) -> None:
    """Capture one Control API interaction for a run-keyed dispatch endpoint.

    ``logs`` / ``meter`` resolve an :class:`AgentRun` (not an AgentTask), so
    bridge to the owning task via :func:`_agent_task_for_run` and record only
    when one is found. Fully defensive: capture is additive/side-effect-only,
    so any failure here (including the bridge lookup) is swallowed and must
    never break the endpoint.
    """
    try:
        if run is None:
            run = AgentRun.all_objects.filter(guid=task_id).first()
        task = _agent_task_for_run(run)
        if task is not None:
            record_interaction(
                task,
                kind=AgentInteraction.Kind.CONTROL_API,
                name=name,
                status=status,
                detail=detail,
            )
    except Exception:  # noqa: BLE001 — capture must never break the endpoint
        logger.exception("failed to capture run interaction for task %s", task_id)


@require_http_methods(["POST"])
def ingest_task_logs(request: HttpRequest, task_id: str) -> JsonResponse:
    """Receive log lines from the Dispatch Service.

    Request body (JSON):
        {"lines": ["line 1", "line 2", ...]}

    Response 200:
        {"stored": <int>}   — total lines now in the buffer.
    Response 404: unknown task_id.
    Response 400: malformed body.
    """
    try:
        body = json.loads(request.body)
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "invalid JSON"}, status=400)

    lines = body.get("lines")
    if not isinstance(lines, list):
        return JsonResponse({"error": "'lines' must be a list"}, status=400)

    try:
        stored = store_agent_log_lines(task_id, lines)
    except AgentRun.DoesNotExist:
        return JsonResponse({"error": "task not found"}, status=404)
    except Exception as exc:
        logger.exception("log ingestion error for task %s", task_id)
        return JsonResponse({"error": str(exc)}, status=500)

    _record_run_interaction(
        task_id,
        name="logs",
        detail={"lines": len(lines), "stored": stored},
    )

    return JsonResponse({"stored": stored})


@require_http_methods(["POST"])
def ingest_task_meter(request: HttpRequest, task_id: str) -> JsonResponse:
    """Receive terminal metering data from the Dispatch Service.

    Request body (JSON):
        {
            "cpu_seconds":      <float | null>,
            "memory_peak_mb":   <int | null>,
            "token_input":      <int | null>,
            "token_output":     <int | null>,
            "source":           "k8s_metrics" | "ecs_metadata" |
                                "wall_time_estimate" | "agent_reported"
        }

    Response 201: metering record created.
    Response 404: unknown task_id.
    Response 409: record already exists for this task.
    """
    try:
        body = json.loads(request.body)
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "invalid JSON"}, status=400)

    try:
        run = AgentRun.all_objects.get(guid=task_id)
    except AgentRun.DoesNotExist:
        return JsonResponse({"error": "task not found"}, status=404)

    if TaskMeteringRecord.objects.filter(agent_run=run).exists():
        return JsonResponse({"error": "metering record already exists"}, status=409)

    source = body.get("source", TaskMeteringRecord.MeteringSource.WALL_TIME_ESTIMATE)
    if source not in TaskMeteringRecord.MeteringSource.values:
        return JsonResponse({"error": f"unknown source '{source}'"}, status=400)

    try:
        record = TaskMeteringRecord.objects.create(
            agent_run=run,
            cpu_seconds=body.get("cpu_seconds"),
            memory_peak_mb=body.get("memory_peak_mb"),
            token_input=body.get("token_input"),
            token_output=body.get("token_output"),
            metering_source=source,
        )
    except Exception as exc:
        logger.exception("metering write error for task %s", task_id)
        return JsonResponse({"error": str(exc)}, status=500)

    _record_run_interaction(
        task_id,
        name="meter",
        detail={"metering_source": source},
        run=run,
    )

    return JsonResponse({"id": str(record.pk), "task_id": task_id}, status=201)
