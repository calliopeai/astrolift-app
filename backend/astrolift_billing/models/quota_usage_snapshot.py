"""
QuotaUsageSnapshot — append-only point-in-time reading of a Quota (#1182).

``Quota`` stores only ``current_usage``, a single scalar overwritten by
the periodic reconciliation, so no time dimension exists and history
can't be reconstructed: count-type resources (apps, preview_envs) could
be rebuilt from object timestamps, but cpu/memory/storage/egress/requests
are point-in-time cluster measurements with no per-object history.

This model captures ``(quota, captured_at, used, limit)`` once per pass so
the quota detail view can plot usage-vs-limit over time. Rows are
immutable — a correction comes as a new row with a later ``captured_at``.
Mirrors the ``CostSnapshot`` pattern (append-only, denormalized org FK for
fail-closed tenant scoping, daily granularity).
"""

from __future__ import annotations

from django.db import models

from core.fields import UUIDv7Field
from core.mixins import AppendOnlyMixin


class QuotaUsageSnapshot(AppendOnlyMixin, models.Model):
    guid = UUIDv7Field(unique=True, db_index=True)
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="quota_usage_snapshots",
        on_delete=models.CASCADE,
    )
    quota = models.ForeignKey(
        "astrolift_billing.Quota",
        related_name="usage_snapshots",
        on_delete=models.CASCADE,
    )
    captured_at = models.DateField(db_index=True)
    used = models.DecimalField(max_digits=20, decimal_places=4)
    limit = models.DecimalField(max_digits=20, decimal_places=4)

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "quota", "-captured_at"],
                name="quota_usage_org_quota_date_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["quota", "captured_at"],
                name="quota_usage_snapshot_unique_per_day",
            ),
        ]

    def __str__(self) -> str:
        return f"QuotaUsageSnapshot {self.guid} {self.captured_at} {self.used}/{self.limit}"
