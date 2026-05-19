"""RetentionPolicy — per-signal retention settings for a registered app (#742)."""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class RetentionPolicy(BaseCoreModel):
    class Signal(models.TextChoices):
        LOGS = "logs"
        METRICS = "metrics"
        TRACES = "traces"
        AUDIT_EVENTS = "audit_events"

    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="retention_policies",
        on_delete=models.CASCADE,
    )
    signal = models.CharField(max_length=32, choices=Signal.choices)
    retention_days = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                condition=models.Q(deleted_at__isnull=True),
                fields=("registered_app", "signal"),
                name="retention_policy_unique_active_app_signal",
            )
        ]
        indexes = [
            models.Index(fields=["registered_app", "signal"], name="ret_pol_app_signal_idx"),
        ]
