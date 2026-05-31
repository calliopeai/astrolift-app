"""URL routes for the Astrolift Pipelines webhook receivers.

Spliced into config.urls at the project root (no /app/ prefix) so the
URLs match the contract registered with GitHub via installPipelineWebhook.
"""

from __future__ import annotations

from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from astrolift_pipelines.webhook_views import pipeline_github_webhook

app_name = "astrolift_pipelines"

urlpatterns = [
    path(
        "webhooks/pipelines/github/<str:org_slug>/",
        csrf_exempt(pipeline_github_webhook),
        name="pipelines-github-webhook",
    ),
]
