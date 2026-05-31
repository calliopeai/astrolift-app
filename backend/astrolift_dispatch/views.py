"""
REST views for the Dispatch Service → Controller communication surface.

Mounted at /api/dispatch/v1/ from config.urls. All endpoints expect the
Dispatch Service bearer token (alft_ds_...) validated by
DispatchServiceAuthMiddleware before the view runs.

Endpoints
---------
POST /api/dispatch/v1/tasks/<task_id>/logs/
    Receive a batch of log lines from the Dispatch Service and append them
    to AgentRun.log_excerpt (#51).

POST /api/dispatch/v1/tasks/<task_id>/meter/
    Receive terminal metering payload from the Dispatch Service and create
    a TaskMeteringRecord (#56).
"""

from __future__ import annotations

import json
import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from astrolift_dispatch.log_collector import store_agent_log_lines
from astrolift_agents.models.task_meter import TaskMeteringRecord
from astrolift_lifecycle.models import AgentRun

log = logging.getLogger(__name__)


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
        log.exception("log ingestion error for task %s", task_id)
        return JsonResponse({"error": str(exc)}, status=500)

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
        log.exception("metering write error for task %s", task_id)
        return JsonResponse({"error": str(exc)}, status=500)

    return JsonResponse({"id": str(record.pk), "task_id": task_id}, status=201)
