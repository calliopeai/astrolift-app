"""
ScmWebhookInstallation — record of a webhook we installed on a SCM
host through a stored ``SourceConnection`` (#293).

GitHub Apps install a webhook at App-install time, so for those
connections this row is created with ``provider_short_circuited=True``
and ``hook_id`` empty — purely a UI affordance ("✓ Already installed
via the App"). For PAT and OAuth-user connections we POST against the
host's hooks API and persist the host-side identifier so we can later
rotate / delete the hook through the same connection.

Soft-deleted via ``deleted_at`` on its way out so we keep a record of
what was installed where for audit purposes.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class ScmWebhookInstallation(BaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="scm_webhook_installations",
        on_delete=models.CASCADE,
    )
    source_connection = models.ForeignKey(
        "astrolift_scm.SourceConnection",
        related_name="webhook_installations",
        on_delete=models.CASCADE,
    )
    # Free-form host-side repo identifier — for GitHub it's
    # ``owner/repo``, for GitLab it's the URL-encoded project path or
    # the numeric ID returned in the hook response.
    repo_full_name = models.CharField(max_length=255)
    # Host-side webhook identifier. Empty when the connection was a
    # GitHub-App install (the host webhook is the App's, not ours).
    hook_id = models.CharField(max_length=64, blank=True, default="")
    webhook_url = models.URLField(max_length=1024, blank=True, default="")
    # True when the install resolver short-circuited because the
    # connection kind already gives us deliveries (GitHub Apps).
    provider_short_circuited = models.BooleanField(default=False)

    class Meta:
        constraints = [
            # One active install per (connection, repo) so the UI can
            # show idempotent state and re-installs land as updates.
            models.UniqueConstraint(
                fields=["source_connection", "repo_full_name"],
                condition=models.Q(deleted_at__isnull=True),
                name="scm_webhook_install_unique_active",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "source_connection"]),
        ]
