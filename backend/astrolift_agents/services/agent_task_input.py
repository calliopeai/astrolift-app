"""Queue + delivery for the agent steering channel (#1390).

Two operations, both deliberately below GraphQL and below the dispatch
REST view so the enqueue path (operator, via the ``sendAgentTaskInput``
mutation) and the consume path (runner, via the state callback) share one
implementation and one set of invariants:

* :func:`queue_agent_task_input` — validate and persist one follow-up
  prompt against a task.
* :func:`claim_pending_input` — hand the runner the next ordered batch of
  undelivered messages and mark exactly those delivered, atomically.

Neither function does tenancy or permission work. Both take an already
resolved :class:`AgentTask`; resolving that task fail-closed against the
caller's org is the caller's job (the resolver does it explicitly, the
callback view does it through the dispatcher/task-token binding).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from astrolift_agents.models.agent_task import AgentTask
from astrolift_agents.models.agent_task_input import (
    MAX_DELIVERY_BATCH,
    MAX_MESSAGE_CHARS,
    AgentTaskInputMessage,
)


@dataclass(slots=True)
class AgentTaskInputError(ValueError):
    """A queue request that is invalid or not currently acceptable."""

    code: str  # "validation" | "precondition"
    message: str
    field: str = ""

    def __str__(self) -> str:
        return self.message


# Statuses that can still consume a follow-up prompt. A task that has not
# started yet has no turn boundary to consume at, and a terminal task will
# never reach one — in both cases queueing would silently strand the
# message, so it is refused up front.
ACCEPTS_INPUT_STATUSES: frozenset[str] = frozenset({AgentTask.Status.RUNNING})


def queue_agent_task_input(
    *,
    task: AgentTask,
    message: str,
    author=None,
    author_label: str = "",
    client_request_id: str | None = None,
) -> AgentTaskInputMessage:
    """Persist one follow-up prompt for ``task``.

    Raises :class:`AgentTaskInputError` for an empty/oversized message or a
    task that cannot consume input. Whitespace is stripped at the edges but
    preserved inside — a multi-line instruction is a normal message. A supplied
    request UUID reserves one enqueue identity for the task, even after delivery
    or completion; callers must retain it across uncertain replies.
    """
    body = (message or "").strip()
    if not body:
        raise AgentTaskInputError("validation", "message is required", "message")
    if len(body) > MAX_MESSAGE_CHARS:
        raise AgentTaskInputError(
            "validation",
            f"message exceeds {MAX_MESSAGE_CHARS} characters",
            "message",
        )
    request_id = None
    if client_request_id is not None:
        try:
            request_id = UUID(str(client_request_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise AgentTaskInputError(
                "validation", "clientRequestId must be a UUID", "clientRequestId"
            ) from exc

    with transaction.atomic():
        # Serialize admission with task completion and competing retries.
        current = (
            AgentTask.objects.select_for_update()
            .filter(pk=task.pk, organization_id=task.organization_id)
            .first()
        )
        if current is None:
            raise AgentTaskInputError("precondition", "task is no longer available")
        if request_id is not None:
            previous = AgentTaskInputMessage.all_objects.filter(
                agent_task=current,
                organization_id=current.organization_id,
                client_request_id=request_id,
            ).first()
            if previous is not None:
                if previous.deleted_at is not None or previous.body != body:
                    raise AgentTaskInputError(
                        "precondition", "clientRequestId has already been used", "clientRequestId"
                    )
                return previous
        if current.status not in ACCEPTS_INPUT_STATUSES:
            raise AgentTaskInputError("precondition", f"task is not accepting input ({current.status})")
        return AgentTaskInputMessage.objects.create(
            organization_id=current.organization_id,
            agent_task=current,
            client_request_id=request_id,
            body=body,
            author=author,
            author_label=(author_label or "")[:255],
        )


def pending_input_count(task: AgentTask) -> int:
    """Undelivered message count for ``task``. Read-only — a peek."""
    return AgentTaskInputMessage.objects.filter(
        agent_task=task,
        delivered_at__isnull=True,
    ).count()


def claim_pending_input(
    task: AgentTask,
    *,
    limit: int = MAX_DELIVERY_BATCH,
) -> list[AgentTaskInputMessage]:
    """Claim the next ordered batch of undelivered messages for ``task``.

    Marks exactly the returned rows delivered inside one transaction, with
    ``select_for_update`` held over the read so two concurrent callbacks
    (a retried turn boundary, or a re-spawned pod) cannot both claim the
    same row. At-most-once by construction: a caller that never applies the
    returned batch loses it. See the model docstring for why that trade is
    the right one for a steering prompt.

    Returns the claimed rows in delivery order, oldest first.
    """
    batch = max(1, min(int(limit), MAX_DELIVERY_BATCH))
    with transaction.atomic():
        claimed = list(
            AgentTaskInputMessage.objects.select_for_update()
            .filter(agent_task=task, delivered_at__isnull=True)
            .order_by("created_at", "id")[:batch]
        )
        if not claimed:
            return []
        now = timezone.now()
        for row in claimed:
            row.delivered_at = now
        AgentTaskInputMessage.objects.bulk_update(claimed, ["delivered_at"])
    return claimed


def serialize_pending_input(rows: list[AgentTaskInputMessage]) -> list[dict]:
    """Render claimed messages for the state-callback response body."""
    return [
        {
            "id": str(row.guid),
            "message": row.body,
            "author": row.author_label,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
