"""
AuditEvent — append-only audit log.

Records every permission decision and every state-changing action
with full reasoning. Append-only at both the application
(``AppendOnlyMixin``) and DB layer (trigger added in a follow-up
migration). Tenant-scoped for queries: orgs see only their events.

Retention is per-organization (``Organization.audit_log_retention_days``),
default 365 days, max 7 years per spec §11.
"""

from __future__ import annotations

from django.db import models

from core.fields import UUIDv7Field
from core.mixins import AppendOnlyMixin


class AuditEvent(AppendOnlyMixin, models.Model):
    class Decision(models.TextChoices):
        ALLOW = "ALLOW"
        DENY = "DENY"
        UNKNOWN = "UNKNOWN"

    guid = UUIDv7Field(unique=True, db_index=True)
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="audit_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    actor_kind = models.CharField(max_length=32)  # user | api_token | deploy_token | system
    actor_id = models.CharField(max_length=64, blank=True, default="")
    actor_display = models.CharField(max_length=255, blank=True, default="")

    action = models.CharField(max_length=128, db_index=True)
    decision = models.CharField(
        max_length=8,
        choices=Decision.choices,
        default=Decision.UNKNOWN,
    )

    target_kind = models.CharField(max_length=64, blank=True, default="")
    target_id = models.CharField(max_length=64, blank=True, default="")
    target_slug = models.CharField(max_length=255, blank=True, default="")
    target_parent_chain = models.JSONField(default=list, blank=True)

    request_id = models.CharField(max_length=64, blank=True, default="")
    request_ip = models.CharField(max_length=64, blank=True, default="")
    request_user_agent = models.TextField(blank=True, default="")
    request_session_age_seconds = models.IntegerField(null=True, blank=True)

    data = models.JSONField(default=dict, blank=True)
    reasoning = models.JSONField(default=list, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "-occurred_at", "action"],
                name="audit_org_time_action_idx",
            ),
            models.Index(fields=["actor_kind", "actor_id"], name="audit_actor_idx"),
        ]

    def __str__(self) -> str:
        return f"AuditEvent {self.action} ({self.decision}) by {self.actor_kind}:{self.actor_id}"
