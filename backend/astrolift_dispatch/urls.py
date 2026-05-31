"""URL routes for the Dispatch Service Controller API."""

from __future__ import annotations

from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from astrolift_dispatch.views import (
    heartbeat,
    ingest_task_logs,
    ingest_task_meter,
    list_tasks,
    register,
    update_task_status,
)

app_name = "astrolift_dispatch"

urlpatterns = [
    # Registration and heartbeat
    path("api/dispatch/v1/register/", register, name="dispatch-register"),
    path("api/dispatch/v1/heartbeat/", heartbeat, name="dispatch-heartbeat"),
    # Task management
    path("api/dispatch/v1/tasks/", list_tasks, name="dispatch-tasks"),
    path("api/dispatch/v1/tasks/<str:task_id>/status/", update_task_status, name="dispatch-task-status"),
    # Log streaming and metering (#51, #56)
    path("api/dispatch/v1/tasks/<str:task_id>/logs/", csrf_exempt(ingest_task_logs), name="task-logs-ingest"),
    path("api/dispatch/v1/tasks/<str:task_id>/meter/", csrf_exempt(ingest_task_meter), name="task-meter-ingest"),
]
