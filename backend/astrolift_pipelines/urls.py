"""
Runner agent REST URL routes (#83).

Mounted at the project root via config/urls.py so the wire URLs are:

  POST /api/pipelines/v1/runners/register/
  POST /api/pipelines/v1/runners/heartbeat/
  GET  /api/pipelines/v1/runners/claim/
  POST /api/pipelines/v1/runners/<runner_guid>/jobs/<job_run_guid>/complete/
"""

from django.urls import path

from astrolift_pipelines.views import (
    runner_claim,
    runner_heartbeat,
    runner_job_complete,
    runner_register,
)

urlpatterns = [
    path(
        "api/pipelines/v1/runners/register/",
        runner_register,
        name="pipelines-runner-register",
    ),
    path(
        "api/pipelines/v1/runners/heartbeat/",
        runner_heartbeat,
        name="pipelines-runner-heartbeat",
    ),
    path(
        "api/pipelines/v1/runners/claim/",
        runner_claim,
        name="pipelines-runner-claim",
    ),
    path(
        "api/pipelines/v1/runners/<str:runner_guid>/jobs/<str:job_run_guid>/complete/",
        runner_job_complete,
        name="pipelines-runner-job-complete",
    ),
]
