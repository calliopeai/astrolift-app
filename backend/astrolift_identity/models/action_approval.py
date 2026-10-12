"""ActionApprovalRequest: a gated action waiting on approvers (#2202).

When an action-safety rule adds approval to a destructive delete, the
mutation does not run: it records a request bound to the exact action,
target and requester and refuses with ``APPROVAL_REQUIRED``. Approvers
decide. The requester then runs the same mutation again, which consumes the
approved request once, under their current authority, against the target as
it is then (``astrolift_identity.action_safety.gate``).

Status machine:

    pending --[quorum approves]--> approved --[requester runs it]--> consumed
    pending --[any reject]--> rejected
    pending --[requester cancels]--> cancelled
    pending | approved --[ttl elapsed]--> expired

Secret changes keep their own proposal flow (``SecretChangeProposal``), which
a rule can switch on; this table is for actions with no such flow.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class ActionApprovalRequest(BaseCoreModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"
        CANCELLED = "cancelled"
        EXPIRED = "expired"
        CONSUMED = "consumed"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="action_approval_requests",
        on_delete=models.CASCADE,
    )
    action = models.CharField(max_length=64)
    # What the action is about, by kind and guid ("app", "cluster",
    # "managed_service", "cluster_model"); the label keeps rendering after
    # the target is gone.
    target_kind = models.CharField(max_length=32)
    target_guid = models.CharField(max_length=64)
    target_label = models.CharField(max_length=255, blank=True, default="")
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="action_approval_requests",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="action_approval_requests",
        null=True,
        on_delete=models.SET_NULL,
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    # Snapshotted when requested, so a later rule change does not move the
    # goalposts on a request already in flight.
    required_approvals = models.PositiveSmallIntegerField(default=1)
    allow_self_approval = models.BooleanField(default=True)
    expires_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # One open request per requester, action and target: asking again
            # returns the one already pending instead of piling up duplicates.
            models.UniqueConstraint(
                fields=["organization", "action", "target_kind", "target_guid", "requester"],
                condition=models.Q(deleted_at__isnull=True, status__in=["pending", "approved"]),
                name="actionapproval_one_open_per_requester",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status", "-created_at"], name="actionapproval_org_status")
        ]

    _TRANSITIONS = {
        Status.PENDING: {Status.APPROVED, Status.REJECTED, Status.CANCELLED, Status.EXPIRED},
        Status.APPROVED: {Status.CONSUMED, Status.EXPIRED},
    }

    def transition_to(self, new_status: ActionApprovalRequest.Status) -> None:
        from django.utils import timezone

        current = ActionApprovalRequest.Status(self.status)
        if new_status not in self._TRANSITIONS.get(current, set()):
            raise ValueError(
                f"ActionApprovalRequest({self.pk}) cannot go {current.value} -> {new_status.value}"
            )
        now = timezone.now()
        self.status = new_status.value
        if new_status is ActionApprovalRequest.Status.CONSUMED:
            self.consumed_at = now
        elif current is ActionApprovalRequest.Status.PENDING:
            self.decided_at = now

    def __str__(self) -> str:  # pragma: no cover — admin display only
        return f"{self.action} {self.target_kind}:{self.target_guid} {self.status}"


class ActionApprovalDecision(BaseCoreModel):
    class Decision(models.TextChoices):
        APPROVED = "approved"
        REJECTED = "rejected"

    request = models.ForeignKey(ActionApprovalRequest, related_name="decisions", on_delete=models.CASCADE)
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="action_approval_decisions",
        null=True,
        on_delete=models.SET_NULL,
    )
    decision = models.CharField(max_length=16, choices=Decision.choices)
    # The requester approving their own request, where the rule allows it.
    self_approval = models.BooleanField(default=False)
    reason = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["request", "approver"],
                condition=models.Q(deleted_at__isnull=True),
                name="actionapproval_one_decision_per_approver",
            ),
        ]
