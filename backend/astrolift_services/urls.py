"""URL routes for the astrolift_services app.

Currently exposes one unauthenticated POST endpoint: the SES → SNS
event webhook receiver (#756). The route is mounted under ``/app/``
by ``config.urls`` so the public URL is
``https://<host>/app/webhooks/ses-events/``.
"""

from __future__ import annotations

from django.urls import path

from astrolift_services import views

app_name = "astrolift_services"

urlpatterns = [
    path(
        "webhooks/ses-events/",
        views.ses_events_webhook,
        name="ses-events-webhook",
    ),
]
