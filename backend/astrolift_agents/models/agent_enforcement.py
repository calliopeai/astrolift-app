"""Signed action receipts and dispatch quarantines owned by the control plane."""

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class AgentEnforcementAction(BaseCoreModel):
    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.CASCADE)
    connection = models.ForeignKey("astrolift_operations.ZentinelleConnection", on_delete=models.PROTECT)
    external_id = models.UUIDField()
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default="applying")
    outcome = models.JSONField(default=dict)
    agent_task = models.ForeignKey(
        "astrolift_agents.AgentTask", null=True, blank=True, on_delete=models.PROTECT
    )
    agent_box = models.ForeignKey(
        "astrolift_agents.AgentBox", null=True, blank=True, on_delete=models.PROTECT
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "external_id"], name="unique_agent_enforcement_action"
            )
        ]


class AgentEnforcementNonce(BaseCoreModel):
    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.CASCADE)
    connection = models.ForeignKey("astrolift_operations.ZentinelleConnection", on_delete=models.PROTECT)
    nonce = models.UUIDField()
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["connection", "nonce"], name="unique_agent_enforcement_nonce")
        ]


class AgentDispatchQuarantine(BaseCoreModel):
    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.CASCADE)
    target_kind = models.CharField(max_length=16, choices=[("agent", "Agent"), ("spec", "Environment spec")])
    target_guid = models.UUIDField()
    reason = models.TextField(blank=True, default="")
    policy_id = models.CharField(max_length=255, blank=True, default="")
    evidence_url = models.CharField(max_length=1000, blank=True, default="")
    cleared_at = models.DateTimeField(null=True, blank=True)
    cleared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "target_kind", "target_guid"],
                condition=models.Q(deleted_at__isnull=True, cleared_at__isnull=True),
                name="unique_live_agent_quarantine",
            )
        ]
