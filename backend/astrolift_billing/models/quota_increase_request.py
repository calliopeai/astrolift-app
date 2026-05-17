"""
QuotaIncreaseRequest — operator-initiated request to bump a quota.

When an app team is approaching a soft limit (typically >80%
consumption), the UI surfaces a "Request bump" affordance. Submitting
opens a row here and notifies org admins so they can approve or
reject before the hard limit blocks admission.

The model intentionally stays append-mostly: pending requests live
until decided; decided requests stay around for auditability so the
operator can see "we asked for 2x last week and were rejected with
this reason." Soft delete (via :class:`BaseCoreModel`) is reserved
for cancellation by the requester before a decision lands.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class QuotaIncreaseRequest(BaseCoreModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    quota = models.ForeignKey(
        "astrolift_billing.Quota",
        related_name="increase_requests",
        on_delete=models.CASCADE,
    )
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="quota_increase_requests",
        on_delete=models.CASCADE,
    )
    requested_factor = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        help_text="Multiplier requested against the current hard limit (e.g. 2.0 = double).",
    )
    reason = models.TextField(
        help_text="Operator's justification. Required at the mutation entry.",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="quota_requests_made",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="quota_requests_decided",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(
        blank=True,
        default="",
        help_text="Approver's note. Surfaces under the row in the requester's UI when present.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["quota"],
                condition=models.Q(status="pending", deleted_at__isnull=True),
                name="quota_request_one_pending_per_quota",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "status", "-created_at"],
                name="quota_req_org_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"QuotaIncreaseRequest quota={self.quota_id} "
            f"factor={self.requested_factor} status={self.status}"
        )
