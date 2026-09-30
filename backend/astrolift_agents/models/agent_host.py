"""Durable, rebuildable AHP projection and the install's ordered action stream."""

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class AgentHostAuthority(BaseCoreModel):
    organization = models.OneToOneField("astrolift_identity.Organization", on_delete=models.CASCADE)
    server_sequence = models.PositiveBigIntegerField(default=0)


class AgentHostProjection(BaseCoreModel):
    agent_task = models.OneToOneField("astrolift_agents.AgentTask", on_delete=models.CASCADE)
    event_sequence = models.PositiveIntegerField(default=0)
    task_version = models.PositiveIntegerField(default=0)
    chat = models.JSONField(default=dict)
    policy_decisions = models.JSONField(default=dict)
    runtime_state = models.JSONField(default=dict)


class AgentHostAction(BaseCoreModel):
    authority = models.ForeignKey(AgentHostAuthority, on_delete=models.CASCADE)
    agent_task = models.ForeignKey("astrolift_agents.AgentTask", null=True, on_delete=models.CASCADE)
    agent_box = models.ForeignKey("astrolift_agents.AgentBox", null=True, on_delete=models.CASCADE)
    server_sequence = models.PositiveBigIntegerField()
    channel = models.CharField(max_length=255)
    action = models.JSONField()
    origin = models.JSONField(null=True)
    rejection_reason = models.CharField(max_length=1000, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    client_id = models.CharField(max_length=128, blank=True)
    client_sequence = models.PositiveBigIntegerField(null=True)

    class Meta:
        ordering = ["server_sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["authority", "server_sequence"], name="ahp_authority_sequence_uniq"
            ),
            models.UniqueConstraint(
                fields=["authority", "actor", "client_id", "client_sequence"], name="ahp_client_sequence_uniq"
            ),
        ]


class AgentHostTerminal(BaseCoreModel):
    """One actor/client's durable terminal attachment, never the box process."""

    authority = models.ForeignKey(AgentHostAuthority, on_delete=models.CASCADE)
    agent_box = models.ForeignKey("astrolift_agents.AgentBox", on_delete=models.CASCADE)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    client_id = models.CharField(max_length=128)
    channel = models.CharField(max_length=255, unique=True)
    state = models.JSONField(default=dict)
    lease_owner = models.UUIDField(null=True)
    lease_expires_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["agent_box", "actor", "client_id"], name="ahp_terminal_attachment_uniq"
            )
        ]
