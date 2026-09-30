"""Durable approval identities; counters and repeated requests are not votes."""

from django.db import models

from core.models.base import BaseCoreModel


class DeploymentApproval(BaseCoreModel):
    deployment = models.ForeignKey(
        "astrolift_lifecycle.Deployment", related_name="approval_votes", on_delete=models.CASCADE
    )
    # Keep the immutable identity even if a user is later hard-deleted.
    # Authenticated entry points supply this value from the actor, never input.
    voter_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    # An emailed bearer link is one credential vote, not an identified human.
    # Only its existing hash is retained; it cannot satisfy ABAC min_approvers.
    credential_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(voter_user_id__isnull=False, voter_user_id__gt=0, credential_hash="")
                    | (models.Q(voter_user_id__isnull=True) & ~models.Q(credential_hash=""))
                ),
                name="deployment_approval_identity",
            ),
            models.UniqueConstraint(
                fields=["deployment", "voter_user_id"],
                condition=models.Q(deleted_at__isnull=True, voter_user_id__isnull=False),
                name="deployment_approval_user_unique",
            ),
            models.UniqueConstraint(
                fields=["deployment", "credential_hash"],
                condition=models.Q(deleted_at__isnull=True, voter_user_id__isnull=True),
                name="deployment_approval_token_unique",
            ),
        ]
