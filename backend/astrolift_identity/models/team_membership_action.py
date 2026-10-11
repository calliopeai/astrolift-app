"""Durable actor-bound receipt, not an alternative membership store (#2273)."""

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class TeamMembershipAction(BaseCoreModel):
    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    subject = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="team_membership_action_receipts", on_delete=models.PROTECT
    )
    request_id = models.UUIDField()
    # HMAC over the immutable request and original credential identity. No
    # credential material or identifying profile snapshots are persisted.
    request_digest = models.CharField(max_length=64)
    result = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "actor", "request_id"],
                name="team_membership_action_request",
            ),
        ]
