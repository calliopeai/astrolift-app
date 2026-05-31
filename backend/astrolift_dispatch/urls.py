"""URL routes for the Dispatch Service Controller API (#48)."""

from __future__ import annotations

from django.urls import path

from astrolift_dispatch.views import (
    heartbeat,
    list_tasks,
    register,
    update_task_status,
)

app_name = "astrolift_dispatch"

urlpatterns = [
    path("api/dispatch/v1/register/", register, name="dispatch-register"),
    path("api/dispatch/v1/heartbeat/", heartbeat, name="dispatch-heartbeat"),
    path("api/dispatch/v1/tasks/", list_tasks, name="dispatch-tasks"),
    path("api/dispatch/v1/tasks/<str:task_id>/status/", update_task_status, name="dispatch-task-status"),
]
