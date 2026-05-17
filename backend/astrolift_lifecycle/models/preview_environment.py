"""
PreviewEnvironment — a per-PR ephemeral deploy.

Created when ``preview_enabled`` is true on the registered app and a
PR is opened. Hostname follows ``pr-{n}-{slug}.pr.{org}.{base-zone}``;
namespace is segregated per preview. Torn down on PR close (with a
short grace period for re-open).
"""

from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.utils import timezone

from core.models.base import BaseCoreModel

# Hard ceiling on the TTL extension window (#431). Anything longer is
# either an abandoned PR (operator should pin it explicitly via a
# different surface) or a developer-leaked preview racking up cost.
PREVIEW_TTL_MAX_DAYS = 30

# Default TTL applied at creation time when the caller doesn't pass an
# explicit ``ttl_until``. Mirrors the stale-after threshold the UI
# uses to flag dormant previews.
PREVIEW_TTL_DEFAULT_DAYS = 7

# Allowed values for the extend mutation. Locked to a small set so the
# UI's buttons map one-to-one and operators can't sneak through a
# 365-day extension by editing the request payload.
PREVIEW_TTL_EXTEND_DAYS = (1, 7, 30)


def _default_ttl_until():
    """Default ``ttl_until`` value for newly-created previews.

    Lives at module scope (not as a lambda on the field) so the
    migration's ``default`` can serialise it without dragging the
    whole model import into the migration file.
    """
    return timezone.now() + timedelta(days=PREVIEW_TTL_DEFAULT_DAYS)


class PreviewEnvironment(BaseCoreModel):
    class Status(models.TextChoices):
        BUILDING = "building"
        RUNNING = "running"
        FAILED = "failed"
        TORN_DOWN = "torn_down"

    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="preview_environments",
        on_delete=models.CASCADE,
    )
    pr_number = models.PositiveIntegerField()
    branch = models.CharField(max_length=255)
    commit_sha = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.BUILDING)
    hostname = models.CharField(max_length=255)
    namespace = models.CharField(max_length=128)
    app_environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        related_name="preview_environments",
        on_delete=models.CASCADE,
    )
    last_deployed_at = models.DateTimeField(null=True, blank=True)
    torn_down_at = models.DateTimeField(null=True, blank=True)
    ttl_until = models.DateTimeField(
        default=_default_ttl_until,
        help_text=(
            "Auto-teardown target. Defaults to created_at + 7 days. The "
            "preview-GC workflow tears down anything past this timestamp; "
            "operators can push it out via the extendPreviewTtl mutation "
            "in 1/7/30-day increments, capped at +30 days from now."
        ),
    )
    workflow_run = models.ForeignKey(
        "astrolift_operations.WorkflowRun",
        related_name="preview_environments",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registered_app", "pr_number"],
                condition=models.Q(deleted_at__isnull=True),
                name="preview_pr_unique_active_per_app",
            ),
        ]

    def extend_ttl(self, *, days: int) -> None:
        """Push ``ttl_until`` out by ``days``, capped at +``PREVIEW_TTL_MAX_DAYS``
        from now.

        ``days`` must be one of :data:`PREVIEW_TTL_EXTEND_DAYS` — the
        mutation resolver validates the input but having the guard here
        too means programmatic callers (workflows, fixtures) can't
        accidentally extend by an out-of-range value.

        Anchors off ``max(now, ttl_until)`` so a fresh extension on a
        not-yet-expired preview adds to the existing window rather than
        silently shortening it; an extension on an already-expired
        preview re-anchors at now.
        """
        if days not in PREVIEW_TTL_EXTEND_DAYS:
            raise ValueError(
                f"extend days must be one of {PREVIEW_TTL_EXTEND_DAYS}; got {days!r}",
            )
        now = timezone.now()
        anchor = self.ttl_until if self.ttl_until and self.ttl_until > now else now
        proposed = anchor + timedelta(days=days)
        ceiling = now + timedelta(days=PREVIEW_TTL_MAX_DAYS)
        self.ttl_until = min(proposed, ceiling)
