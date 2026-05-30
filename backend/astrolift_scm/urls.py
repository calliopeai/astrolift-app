"""URL routes for the Astrolift SCM webhook receivers.

Spliced into ``config.urls`` at the project root so the wire URLs match
the contract the platform's ``installAstroliftSourceWebhook`` flow
registers on the source host (no ``/app/`` prefix). The receivers carry
HMAC signature checks instead of bearer auth — GitHub / GitLab post
without an Astrolift session — so each route is ``csrf_exempt`` and the
view's first action is to verify the per-app webhook secret.

Routes:

* ``POST /api/webhooks/github/<app_guid>/`` — GitHub PR webhook receiver
  (#778). Classifies PR events and starts ``BuildPreviewWorkflow`` /
  ``TearDownPreviewWorkflow`` against the matching RegisteredApp's
  preview environment.
"""

from __future__ import annotations

from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from astrolift_scm.webhook_views import pr_webhook

app_name = "astrolift_scm"

urlpatterns = [
    path(
        "api/webhooks/github/<str:app_guid>/",
        csrf_exempt(pr_webhook),
        name="scm-github-webhook",
    ),
]
