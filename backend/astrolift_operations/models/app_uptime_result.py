"""Synthetic uptime probe results for registered apps.

Append-only time-series — the uptime prober (``astrolift_operations.
uptime_probe``) writes one row per check of an app's public health URL.
Powers the up/down state, the uptime %, and the OBSERVE latency graph.

Born from the 2026-07-07 ``pickup-windows-tool`` outage: the pod was
``Running`` the whole time and every pod/deploy signal said "healthy",
yet the app 504'd at the ALB. Nothing watched *actual reachability*.
This model + prober close that gap.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class AppUptimeResult(BaseCoreModel):
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="uptime_results",
        on_delete=models.CASCADE,
    )
    # When the probe ran (probe wall-clock), not the row-write time.
    checked_at = models.DateTimeField(db_index=True)
    target_url = models.CharField(max_length=512)
    # None when the request never completed (timeout / connection error).
    status_code = models.PositiveIntegerField(null=True, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    is_up = models.BooleanField()
    # Short reason when down ("HTTP 504", "ConnectTimeout: ...").
    detail = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-checked_at"]
        indexes = [
            models.Index(
                fields=["registered_app", "-checked_at"],
                name="app_uptime_app_checked_idx",
            ),
        ]

    def __str__(self) -> str:
        state = "up" if self.is_up else "down"
        return f"AppUptimeResult(app={self.registered_app_id}, {state}, {self.checked_at:%Y-%m-%dT%H:%M:%SZ})"
