"""
OrganizationModule — an org admin's switch for one optional module (#1859).

Modules like the Chat Studio integration are install-wide code but a
per-organization decision: an org admin turns one on for their org, and
the install admin can still force it off everywhere (see
``astrolift_identity.org_modules``, which is the only reader).

No row means the org never turned the module on, so it is off.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class OrganizationModule(BaseCoreModel):
    class Key(models.TextChoices):
        CHAT_STUDIO_INTEGRATION = "chat_studio_integration"
        AGENT_LIVE_ATTACH = "agent_live_attach"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="module_flags",
        on_delete=models.CASCADE,
    )
    key = models.CharField(max_length=64, choices=Key.choices)
    enabled = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "key"],
                condition=models.Q(deleted_at__isnull=True),
                name="orgmodule_unique_org_key_active",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover — admin display only
        return f"{self.key}={self.enabled} → {self.organization_id}"
