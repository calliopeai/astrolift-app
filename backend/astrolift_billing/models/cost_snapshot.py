"""
CostSnapshot — periodic cost rollup per (org, project, app).

Two sources are recorded with a flag (``source``):
* ``provider_estimate`` — what the cloud's billing API reports.
* ``platform_meter`` — what the platform's own usage meter computes.

Snapshots are immutable; corrections come as new rows with a later
``taken_at`` date.
"""

from __future__ import annotations

from django.db import models

from core.fields import UUIDv7Field
from core.mixins import AppendOnlyMixin


class CostSnapshot(AppendOnlyMixin, models.Model):
    class CostBy(models.TextChoices):
        WORKLOAD = "workload"
        MANAGED_SERVICE = "managed_service"
        EGRESS = "egress"
        STORAGE = "storage"
        OTHER = "other"

    class Source(models.TextChoices):
        PROVIDER_ESTIMATE = "provider_estimate"
        PLATFORM_METER = "platform_meter"

    guid = UUIDv7Field(unique=True, db_index=True)
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="cost_snapshots",
        on_delete=models.CASCADE,
    )
    project = models.ForeignKey(
        "astrolift_identity.Project",
        related_name="cost_snapshots",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="cost_snapshots",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    managed_service_binding = models.ForeignKey(
        "astrolift_services.ManagedServiceBinding",
        related_name="cost_snapshots",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    """Legacy attribution target. Never populated by the collector:
    a binding is one row per injected env var, so it was never the
    grain a cloud resource could be tagged with (#1418). Kept so
    historical rows keep their shape; new rows use
    ``managed_service``."""

    managed_service = models.ForeignKey(
        "astrolift_services.ManagedService",
        related_name="cost_snapshots",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    """The service the billed cloud resource belongs to, resolved from
    the resource's ``astrolift.io/managed_service_id`` tag. NULL for
    spend the platform did not provision (operator-managed or shared
    resources), which the UI rolls up as "Shared / untagged"."""
    taken_at = models.DateField(db_index=True)
    by = models.CharField(max_length=32, choices=CostBy.choices)
    amount_cents = models.BigIntegerField()
    currency = models.CharField(max_length=8, default="USD")
    source = models.CharField(max_length=32, choices=Source.choices)

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "-taken_at", "by"],
                name="cost_org_date_kind_idx",
            ),
            models.Index(
                fields=["organization", "managed_service_binding", "-taken_at"],
                name="cost_org_binding_date_idx",
            ),
            models.Index(
                fields=["organization", "managed_service", "-taken_at"],
                name="cost_org_msvc_date_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "organization",
                    "registered_app",
                    "managed_service_binding",
                    "managed_service",
                    "by",
                    "taken_at",
                    "source",
                ],
                name="cost_snapshot_unique_per_day_scope",
            ),
        ]

    def __str__(self) -> str:
        return f"CostSnapshot {self.guid} {self.by} {self.amount_cents}c {self.currency}"
