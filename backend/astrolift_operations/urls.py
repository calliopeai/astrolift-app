"""URL routes for the operations app.

Exposes the token-gated audit-log export download (#433) and the
app-log export download (#483). Both are mounted under ``/app/`` by
``config.urls``.
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
    # App-log export download (#483). Same single-use token shape as
    # the audit-log export above; the mutation that builds the row
    # hands out the URL with the plaintext token.
    path(
        "app_log_exports/<str:guid>/<str:token>/",
        views.download_app_log_export,
        name="app-log-export-download",
    ),
]
