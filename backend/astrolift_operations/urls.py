"""URL routes for the operations app.

Currently exposes the token-gated audit-log export download. Mounted
under ``/app/audit_exports/<guid>/<token>/`` by ``config.urls``.
"""

from __future__ import annotations

from django.urls import path

from astrolift_operations import views

app_name = "astrolift_operations"

urlpatterns = [
    path(
        "audit_exports/<str:guid>/<str:token>/",
        views.download_audit_export,
        name="audit-export-download",
    ),
]
