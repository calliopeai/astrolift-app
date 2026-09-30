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
        TURN_STARTED = "turn_started"
        TURN_COMPLETED = "turn_completed"
        TURN_FAILED = "turn_failed"
        TURN_CANCELLED = "turn_cancelled"
        STOP_REQUESTED = "stop_requested"
        TOOL_CALL_STARTED = "tool_call_started"
        TOOL_CALL_INPUT = "tool_call_input"
        TOOL_CALL_RESULT = "tool_call_result"
        TOOL_CALL_COMPLETED = "tool_call_completed"
        TOOL_CALL_FAILED = "tool_call_failed"

    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.CASCADE)
    agent_task = models.ForeignKey(
        "astrolift_agents.AgentTask", related_name="events", on_delete=models.CASCADE
    )
    sequence = models.PositiveIntegerField()
    turn_id = models.CharField(max_length=64)
    message_id = models.CharField(max_length=64)
    kind = models.CharField(max_length=32, choices=Kind.choices)
    text = models.TextField(blank=True, default="")
    request = models.JSONField(null=True, blank=True)
    data = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["sequence"]
        indexes = [
            models.Index(fields=["agent_task", "turn_id", "message_id"], name="agent_event_message_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["agent_task", "sequence"], name="agent_event_task_sequence_uniq"),
        ]
