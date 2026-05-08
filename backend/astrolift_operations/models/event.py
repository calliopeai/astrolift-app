"""
Event — append-only log of platform-relevant facts.

Every state change of interest writes one row here. The event log
feeds the in-app activity feed, the outbound webhook fan-out, and
event-driven Temporal listeners.

Event rows are immutable at the application layer (AppendOnlyMixin);
a follow-up migration installs DB triggers refusing UPDATE / DELETE
to enforce the same at the storage layer.

Each row captures enough provenance to reconstruct a deploy or audit
incident from the event log alone:

* ``event_type``                 — ``{resource}.{action}`` (e.g. ``app.deployed``)
* ``resource_kind`` / ``resource_id`` — the model name + GUID/key the event is about
* ``actor_user``                 — the user who triggered the action (nullable: system actors)
* ``request_id`` / ``trace_id``  — pulled from the request-scoped contextvars so logs,
                                   audit entries, and events all correlate
* ``payload``                    — JSON snapshot at the time of emission
"""

from __future__ import annotations

from django.conf import settings
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

    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    resource_kind = models.CharField(max_length=64, blank=True, default="", db_index=True)
    resource_id = models.CharField(max_length=128, blank=True, default="")
    request_id = models.CharField(max_length=64, blank=True, default="")
    trace_id = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "-occurred_at", "event_type"],
                name="event_org_time_type_idx",
            ),
            models.Index(
                fields=["resource_kind", "resource_id"],
                name="event_resource_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Event {self.event_type} {self.resource_kind}:{self.resource_id}"
