"""Agent messages: what an agent tells people, and what they answer (#2434).

An :class:`AgentMessage` is sent by a run (or by the platform about a run:
finished, failed, a gate waiting, a policy alert) to people who can read the
agent. Each person addressed gets an :class:`AgentMessageRecipient` row that
carries their own delivery and read state. An answer is an
:class:`AgentMessageReply`; it goes back to the agent through the run's input
channel, the run's gate, or a queued wake (``route``).

Every row is org-scoped and every read filters on the organization and on the
reader's access to the agent, so a person who loses the agent stops seeing
its messages.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel

#: Markdown body cap. A message is a note to a person, not a report; a
#: report goes in an attachment.
MAX_BODY_CHARS = 8000
MAX_TITLE_CHARS = 200
MAX_REPLY_CHARS = 4000


class AgentMessage(BaseCoreModel):
    class Kind(models.TextChoices):
        UPDATE = "update"
        QUESTION = "question"
        APPROVAL = "approval"
        RESULT = "result"
        ALERT = "alert"

    class Source(models.TextChoices):
        AGENT = "agent"  # the run sent it
        PLATFORM = "platform"  # a platform signal about the run

    organization = models.ForeignKey(
        "astrolift_identity.Organization", related_name="agent_messages", on_delete=models.CASCADE
    )
    agent_definition = models.ForeignKey(
        "astrolift_registry.Workload",
        related_name="agent_messages",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    agent_task = models.ForeignKey(
        "astrolift_agents.AgentTask",
        related_name="agent_messages",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # Per execution by default (the run's guid); an agent may name its own.
    thread_id = models.UUIDField(db_index=True)
    reply_to = models.ForeignKey("self", related_name="+", null=True, blank=True, on_delete=models.SET_NULL)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.AGENT)
    title = models.CharField(max_length=MAX_TITLE_CHARS, blank=True, default="")
    body = models.TextField(blank=True, default="")
    # [{"id", "label", "description"}] for a question or an approval.
    options = models.JSONField(default=list, blank=True)
    # [{"kind": "artifact"|"url"|"citation", "ref", "label"}], references only.
    attachments = models.JSONField(default=list, blank=True)
    # The run's own input or approval request this message carries; an
    # answer then goes through that gate (``reply_to_request``).
    request_sequence = models.PositiveIntegerField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    # Idempotency: a platform signal is sent once; a run's retry with the
    # same client request id gets the message it already sent.
    signal_key = models.CharField(max_length=128, blank=True, default="")
    client_request_id = models.UUIDField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "signal_key"],
                condition=~models.Q(signal_key=""),
                name="agentmsg_org_signal_unique",
            ),
            models.UniqueConstraint(
                fields=["agent_task", "client_request_id"],
                name="agentmsg_task_request_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "thread_id", "created_at"], name="agentmsg_thread_idx"),
            models.Index(fields=["agent_definition", "source", "created_at"], name="agentmsg_agent_rate_idx"),
            models.Index(fields=["organization", "created_at"], name="agentmsg_org_created_idx"),
        ]

    def __str__(self) -> str:
        return f"AgentMessage {self.guid} ({self.kind})"


class AgentMessageRecipient(BaseCoreModel):
    """One person a message was addressed to, with their own delivery and read state."""

    organization = models.ForeignKey(
        "astrolift_identity.Organization", related_name="+", on_delete=models.CASCADE
    )
    message = models.ForeignKey(AgentMessage, related_name="recipients", on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="+", on_delete=models.CASCADE)
    # The team the person was reached through, when the agent addressed a team.
    team = models.ForeignKey(
        "astrolift_identity.Team", related_name="+", null=True, blank=True, on_delete=models.SET_NULL
    )
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["message", "user"], name="agentmsg_recipient_unique"),
        ]
        indexes = [
            models.Index(fields=["user", "organization", "read_at"], name="agentmsg_rcpt_unread_idx"),
            models.Index(fields=["user", "organization", "created_at"], name="agentmsg_rcpt_inbox_idx"),
        ]


class AgentMessageReply(BaseCoreModel):
    """A person's answer to a message, and the way it went back to the agent."""

    class Route(models.TextChoices):
        LIVE_RUN = "live_run"  # queued on the running run's input channel
        GATE = "gate"  # answered the run's own input or approval request
        WOKE = "woke"  # started a new run of the agent carrying the answer
        QUEUED = "queued"  # waits for the agent's next run to collect it

    organization = models.ForeignKey(
        "astrolift_identity.Organization", related_name="+", on_delete=models.CASCADE
    )
    message = models.ForeignKey(AgentMessage, related_name="replies", on_delete=models.CASCADE)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", null=True, blank=True, on_delete=models.SET_NULL
    )
    author_label = models.CharField(max_length=255, blank=True, default="")
    body = models.TextField(blank=True, default="")
    choices = models.JSONField(default=list, blank=True)
    decision = models.CharField(max_length=8, blank=True, default="")
    route = models.CharField(max_length=16, choices=Route.choices)
    # When the agent got it: the input channel's consume, the gate answer,
    # the woken run's payload, or a later run collecting a queued answer.
    delivered_at = models.DateTimeField(null=True, blank=True)
    input_message = models.ForeignKey(
        "astrolift_agents.AgentTaskInputMessage",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    gate_reply = models.ForeignKey(
        "astrolift_agents.AgentTaskInputReply",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    wake_task = models.ForeignKey(
        "astrolift_agents.AgentTask", related_name="+", null=True, blank=True, on_delete=models.SET_NULL
    )
    client_request_id = models.UUIDField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["message", "author", "client_request_id"],
                name="agentmsg_reply_request_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "route", "delivered_at"], name="agentmsg_reply_queue_idx"),
        ]
