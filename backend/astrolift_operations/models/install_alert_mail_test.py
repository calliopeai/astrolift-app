"""Original-caller install-channel diagnostics, without message content."""

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class InstallAlertMailTest(BaseCoreModel):
    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.PROTECT)
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    request_id = models.UUIDField()
    event_kind = models.CharField(max_length=64)
    source_sha256 = models.CharField(max_length=64)
    sender = models.EmailField(max_length=254)
    recipient = models.EmailField(max_length=254)
    status = models.CharField(max_length=16, default="reserved")
    accepted_at = models.DateTimeField(null=True, blank=True)
    reason_code = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "request_id"], name="alert_mail_org_request_uniq"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["reserved", "sent", "accepted", "failed", "unknown"]),
                name="alert_mail_status_known",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "requester", "created_at"], name="alert_mail_org_actor_time")
        ]
