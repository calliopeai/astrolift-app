from django.db import models

from core.models.base import BaseCoreModel


class AgentTaskEvent(BaseCoreModel):
    class Kind(models.TextChoices):
        ASSISTANT_DELTA = "assistant_delta"
        TERMINAL_DELTA = "terminal_delta"
        MESSAGE_END = "message_end"
        INPUT_REQUIRED = "input_required"
        APPROVAL_REQUIRED = "approval_required"
        INPUT_RESOLVED = "input_resolved"

    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.CASCADE)
    agent_task = models.ForeignKey(
        "astrolift_agents.AgentTask", related_name="events", on_delete=models.CASCADE
    )
    sequence = models.PositiveIntegerField()
    turn_id = models.CharField(max_length=64)
    message_id = models.CharField(max_length=64)
    kind = models.CharField(max_length=32, choices=Kind.choices)
    text = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(fields=["agent_task", "sequence"], name="agent_event_task_sequence_uniq"),
        ]
