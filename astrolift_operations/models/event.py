"""
Event — append-only log of platform-relevant facts.

Every state change of interest writes one row here. The event log
feeds the in-app activity feed, the outbound webhook fan-out, and
event-driven Temporal listeners.

Event rows are immutable at the application layer (AppendOnlyMixin);
a follow-up migration installs DB triggers refusing UPDATE / DELETE
to enforce the same at the storage layer.
"""

from __future__ import annotations

from django.db import models

from core.fields import UUIDv7Field
from core.mixins import AppendOnlyMixin


class Event(AppendOnlyMixin, models.Model):
    guid = UUIDv7Field(unique=True, db_index=True)
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="events",
        on_delete=models.CASCADE,
    )
    team = models.ForeignKey(
        "astrolift_identity.Team",
        related_name="events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    project = models.ForeignKey(
        "astrolift_identity.Project",
        related_name="events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    event_type = models.CharField(max_length=128, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "-occurred_at", "event_type"],
                name="event_org_time_type_idx",
            ),
        ]
