"""URL routes for the Astrolift Pipelines webhook receivers.

Spliced into config.urls at the project root (no /app/ prefix) so the
URLs match the contract registered with GitHub via installPipelineWebhook.
"""

from __future__ import annotations

from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from astrolift_pipelines.gitlab_webhook_views import pipeline_gitlab_webhook
from astrolift_pipelines.runner_views import (
    runner_claim_job,
    runner_complete_job,
    runner_heartbeat,
    runner_register,
)
from astrolift_pipelines.webhook_views import pipeline_github_webhook

app_name = "astrolift_pipelines"

urlpatterns = [
    # SCM webhook receivers
    path(
        "webhooks/pipelines/github/<str:org_slug>/",
        csrf_exempt(pipeline_github_webhook),
        name="pipelines-github-webhook",
    ),
    path(
        "webhooks/pipelines/gitlab/<str:org_slug>/",
        csrf_exempt(pipeline_gitlab_webhook),
        name="pipelines-gitlab-webhook",
    ),
    # Self-hosted runner protocol (#81, #83)
    path("api/pipelines/v1/runners/register/", csrf_exempt(runner_register), name="runner-register"),
    path("api/pipelines/v1/runners/heartbeat/", csrf_exempt(runner_heartbeat), name="runner-heartbeat"),
    path("api/pipelines/v1/runners/claim/", csrf_exempt(runner_claim_job), name="runner-claim"),
    path(
        "api/pipelines/v1/runners/<str:runner_guid>/jobs/<str:job_run_guid>/complete/",
        csrf_exempt(runner_complete_job),
        name="runner-complete-job",
    ),
]
