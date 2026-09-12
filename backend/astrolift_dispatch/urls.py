"""URL routes for the Dispatch Service Controller API."""

from __future__ import annotations

from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from astrolift_dispatch.views import (
    agent_callback,
    agent_checkin,
    heartbeat,
    ingest_task_logs,
    ingest_task_meter,
    list_tasks,
    list_fleet,
    list_runtimes,
    register,
    send_task_input,
    update_task_status,
)

app_name = "astrolift_dispatch"

urlpatterns = [
    # Registration and heartbeat
    path("api/dispatch/v1/register/", register, name="dispatch-register"),
    path("api/dispatch/v1/heartbeat/", heartbeat, name="dispatch-heartbeat"),
    path("api/dispatch/v1/fleet/", list_fleet, name="dispatch-fleet"),
    path("api/dispatch/v1/runtimes/", list_runtimes, name="dispatch-runtimes"),
    # Task management
    path("api/dispatch/v1/tasks/", list_tasks, name="dispatch-tasks"),
    path("api/dispatch/v1/tasks/<str:task_id>/status/", update_task_status, name="dispatch-task-status"),
    path("api/dispatch/v1/tasks/<str:task_id>/input/", send_task_input, name="dispatch-task-input"),
    # Thread-mode agent checkin + callback
    path("api/dispatch/v1/agents/<str:task_id>/checkin/", agent_checkin, name="agent-checkin"),
    path("api/dispatch/v1/agents/<str:task_id>/callback/", agent_callback, name="agent-callback"),
    # Log streaming and metering (#51, #56)
    path("api/dispatch/v1/tasks/<str:task_id>/logs/", csrf_exempt(ingest_task_logs), name="task-logs-ingest"),
    path(
        "api/dispatch/v1/tasks/<str:task_id>/meter/", csrf_exempt(ingest_task_meter), name="task-meter-ingest"
    ),
]
