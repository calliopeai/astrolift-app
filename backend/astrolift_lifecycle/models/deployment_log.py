"""
DeploymentLog — append-only stream of deployment lifecycle entries.

Lifecycle transitions, phase observations and captured build output remain
readable independently of pod lifetime. Rows cannot be updated or deleted at
the application layer (AppendOnlyMixin); a DB-level rule layered in a
follow-up migration enforces the same at the storage layer.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.fields import UUIDv7Field
from core.mixins import AppendOnlyMixin


class DeploymentLog(AppendOnlyMixin, models.Model):
    guid = UUIDv7Field(unique=True, db_index=True)
    deployment = models.ForeignKey(
        "astrolift_lifecycle.Deployment",
        related_name="logs",
        on_delete=models.CASCADE,
    )
    status = models.CharField(max_length=32, blank=True, default="")
    phase = models.CharField(max_length=16, blank=True, default="")
    event = models.CharField(max_length=32, blank=True, default="")
    message = models.TextField(blank=True, default="")
    detail = models.JSONField(null=True, blank=True)
    by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="deployment_log_entries",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    by_token_kind = models.CharField(max_length=32, blank=True, default="")
    by_token_id = models.BigIntegerField(null=True, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["deployment", "occurred_at"], name="deploylog_deploy_idx"),
            models.Index(fields=["deployment", "id"], name="deploylog_cursor_idx"),
        ]

    def __str__(self) -> str:
        return f"DeploymentLog {self.deployment_id} → {self.status or self.phase + '/' + self.event}"
