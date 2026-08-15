"""
AgentTaskInputMessage — a follow-up prompt queued for a running AgentTask.

The steering channel (#1390) is deliberately *queued*, not duplex: an
operator enqueues a message against a task, and the runner consumes it at
the next turn boundary — the point where the harness finishes one headless
invocation and the runner decides whether to run another. Nothing here
opens a live stream into the pod; that is the (larger) interactive-steering
direction #1390 weighed and deferred.

Delivery contract
=================

* **In order.** Rows are consumed oldest-first. ``created_at`` alone is not
  a total order (two mutations in the same millisecond tie), so the ordering
  is ``(created_at, id)`` — the monotonic integer pk breaks the tie. That is
  the meaning of "ordering" for this model; there is no separate sequence
  column, which would need a racy per-task max()+1 on every insert.
* **At most once.** ``delivered_at`` is stamped inside the same locked
  transaction that hands the batch to the runner, so a message is never
  delivered twice. The deliberate trade is that a batch lost in transit
  (control plane committed, response never reached the pod) is *not*
  redelivered. At-most-once beats at-least-once here: a steering prompt
  replayed into a second turn would re-instruct the agent with a stale
  nudge, which is worse than dropping it — and the operator can see the
  message was marked delivered.

Org scope is required and never nullable: every read of this surface
filters on it fail-closed, mirroring :class:`AgentInteraction`.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel

# Upper bound on a single queued message. Large enough for a real
# follow-up instruction, small enough that a queue of them cannot bloat
# the callback response the runner has to read at a turn boundary.
MAX_MESSAGE_CHARS = 4000

# Most messages handed to the runner in one turn-boundary consume. A burst
# beyond this is delivered across successive turns rather than in one
# oversized callback response.
MAX_DELIVERY_BATCH = 20


class AgentTaskInputMessage(BaseCoreModel):
    """One operator-queued follow-up prompt for an :class:`AgentTask`."""

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="agent_task_input_messages",
        on_delete=models.CASCADE,
        db_index=True,
    )
    agent_task = models.ForeignKey(
        "astrolift_agents.AgentTask",
        related_name="input_messages",
        on_delete=models.CASCADE,
        db_index=True,
    )
    # The follow-up prompt text handed to the harness verbatim.
    body = models.TextField()
    # Who queued it. Nullable + SET_NULL so a message survives the author's
    # deletion (the run history must stay readable), and so a non-user
    # caller (API token, automation) can still enqueue.
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # Display label frozen at enqueue time. The FK can go null and a
    # username can change; the audit-facing "who nudged this agent" answer
    # should not. Empty for a system/automation caller.
    author_label = models.CharField(max_length=255, blank=True, default="")
    # Stamped when the batch containing this row is handed to the runner.
    # Null means still queued.
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        # Oldest first, pk breaking the created_at tie — see the module
        # docstring. Every consumer relies on this being a total order.
        ordering = ["created_at", "id"]
        indexes = [
            # The delivery query: undelivered rows for one task, in order.
            models.Index(
                fields=["agent_task", "delivered_at", "created_at"],
                name="agentinput_task_undeliv_idx",
            ),
        ]

    def __str__(self) -> str:
        state = "delivered" if self.delivered_at else "queued"
        return f"AgentTaskInputMessage {self.guid} ({state})"
