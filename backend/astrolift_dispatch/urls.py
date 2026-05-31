"""URL routes for the Dispatch Service → Controller REST surface.

Mounted at the project root from config.urls. The entire prefix is gated
by DispatchServiceAuthMiddleware; views themselves do not repeat the auth
check.
"""

from __future__ import annotations

from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from astrolift_dispatch import views

app_name = "astrolift_dispatch"

urlpatterns = [
    path(
        "api/dispatch/v1/tasks/<str:task_id>/logs/",
        csrf_exempt(views.ingest_task_logs),
        name="task-logs-ingest",
    ),
    path(
        "api/dispatch/v1/tasks/<str:task_id>/meter/",
        csrf_exempt(views.ingest_task_meter),
        name="task-meter-ingest",
    ),
]
