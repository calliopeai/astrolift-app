"""
SecretChangeProposal — quorum-gated secret writes (#488).

Mirror of the deployment approval workflow (#410/#419/#420) for secret
changes.  When ``RegisteredApp.requires_secret_approval`` is on, the
secret-write mutations (``setAppSecret`` / ``deleteAppSecret`` /
``attachSecretBundle`` / ``detachSecretBundle``, and a
``setAppSecretMetadata`` that changes a scope) stop applying directly
and instead create a proposal row that needs N approvers before the
underlying op fires.

Two-table shape:

* ``SecretChangeProposal`` — one row per requested change, holds the
  payload, the pre-rendered diff for reviewers, the lifecycle status,
  the snapshotted approver count (copied at propose time so a later
  policy bump doesn't move the goalposts on an in-flight proposal),
  and the auto-expire deadline.

* ``SecretChangeApproval`` — one row per approver decision.  Approval
  rows let the UI render the per-decision attribution + reason without
  joining the audit log.

Status machine:

    pending --[N approve]--> approved --[apply ok]--> applied
    pending --[any reject]--> rejected
    pending --[proposer withdraws]--> withdrawn
    pending --[ttl elapsed]--> expired

Soft-deletes via ``BaseCoreModel`` — never hard-deleted so the audit
trail keeps the per-proposal history even when the parent app is gone.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class SecretChangeProposal(BaseCoreModel):
    class Op(models.TextChoices):
        SET = "set"
        DELETE = "delete"
        ATTACH_BUNDLE = "attach_bundle"
        DETACH_BUNDLE = "detach_bundle"
        SET_METADATA = "set_metadata"

    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"
        APPLIED = "applied"
        EXPIRED = "expired"
        WITHDRAWN = "withdrawn"

    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="secret_change_proposals",
        on_delete=models.CASCADE,
    )
    # Optional — global app-wide literal writes target an env name for
    # display but live in the manifest [env] table which is app-scoped.
    # Bundle attach/detach always carry an environment; literal set/
    # delete fall back to a synthesised "(app-wide)" env_name on
    # display when this FK is null.
    app_environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        related_name="secret_change_proposals",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # Cached env name so the UI/audit row keeps rendering after a
    # later env soft-delete clears the FK.
    environment_name = models.CharField(max_length=128, blank=True, default="")

    proposer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="proposed_secret_changes",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    op = models.CharField(max_length=32, choices=Op.choices)

    # The proposed change.  Shape varies by ``op``:
    # - set:           {"key": "API_KEY", "value": "...", "scope"?, "expires_at"?, "set_via"?}
    # - delete:        {"key": "API_KEY"}
    # - attach_bundle: {"bundle_slug": "...", "prefix": "..."}
    # - detach_bundle: {"attachment_id": "<guid>"}
    # - set_metadata:  {"key": "API_KEY", "scope": "...", "expires_at"?, "set_via"?}
    # set/delete gain "base_raw_digest" when applied (see secret_literals).
    payload = models.JSONField(default=dict, blank=True)

    # Pre-rendered diff for reviewers.  Built at propose time from the
    # current manifest state so the proposal page can render without
    # re-reading + re-diffing.
    # Shape: {"before": {...}, "after": {...}, "summary": "..."}
    payload_diff = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # Snapshotted at propose time from the app's
    # ``secret_minimum_approvals`` so a later policy change doesn't
    # move the goalposts on this in-flight proposal.
    required_approver_count = models.PositiveIntegerField(default=1)

    # TTL — proposals auto-expire after this stamp.  Default 7 days
    # via Constance ``SECRET_PROPOSAL_TTL_SECONDS`` at create time.
    expires_at = models.DateTimeField()

    # Stamped on terminal transitions for fast "when did this close?"
    # queries without scanning the audit log.
    decided_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    # Free-form notes from the apply step — populated with the
    # underlying mutation's error message on apply failure so the
    # UI can render "applied failed: <reason>" without a join.
    apply_error = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(
                fields=["registered_app", "status", "-created_at"],
                name="scp_app_status_created_idx",
            ),
            models.Index(fields=["expires_at"], name="scp_expires_idx"),
        ]

    _TRANSITIONS = {
        Status.PENDING: {
            Status.APPROVED,
            Status.REJECTED,
            Status.WITHDRAWN,
            Status.EXPIRED,
        },
        Status.APPROVED: {Status.APPLIED},
        Status.REJECTED: set(),
        Status.APPLIED: set(),
        Status.EXPIRED: set(),
        Status.WITHDRAWN: set(),
    }

    def transition_to(self, new_status: SecretChangeProposal.Status) -> None:
        """Single source of truth for status flips. Raises if the
        target state isn't reachable from the current one — keeps the
        state machine honest under concurrent approval / withdrawal
        races."""
        from django.utils import timezone

        current = SecretChangeProposal.Status(self.status)
        allowed = self._TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise ValueError(
                f"SecretChangeProposal({self.pk}) cannot transition {current.value} → {new_status.value}"
            )
        now = timezone.now()
        self.status = new_status.value
        if new_status in {
            SecretChangeProposal.Status.REJECTED,
            SecretChangeProposal.Status.WITHDRAWN,
            SecretChangeProposal.Status.EXPIRED,
            SecretChangeProposal.Status.APPROVED,
        }:
            self.decided_at = self.decided_at or now
        if new_status is SecretChangeProposal.Status.APPLIED:
            self.applied_at = self.applied_at or now
        self.save(
            update_fields=[
                "status",
                "decided_at",
                "applied_at",
                "updated_at",
                "version",
            ]
        )


class SecretChangeApproval(BaseCoreModel):
    class Decision(models.TextChoices):
        APPROVED = "approved"
        REJECTED = "rejected"

    proposal = models.ForeignKey(
        "astrolift_services.SecretChangeProposal",
        related_name="approvals",
        on_delete=models.CASCADE,
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="secret_change_approvals",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    decision = models.CharField(max_length=16, choices=Decision.choices)
    decided_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            # An approver can vote at most once per proposal.  Re-voting
            # is intentionally disallowed — flipping a decision is a
            # rare path and the audit-log story stays cleaner when each
            # row is immutable.
            models.UniqueConstraint(
                fields=["proposal", "approver"],
                condition=models.Q(deleted_at__isnull=True),
                name="scp_approval_unique_active_per_approver",
            ),
        ]
        indexes = [
            models.Index(fields=["proposal", "decision"], name="scp_approval_proposal_idx"),
        ]
