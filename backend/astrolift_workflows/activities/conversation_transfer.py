"""Conversation Move activities (#1971).

Thin Temporal activities around the synchronous halves of the paused
admission in ``astrolift_agents.services.conversation_transfer``. The ORM,
blob-store and cluster calls are blocking, so each runs in a worker thread
(``thread_sensitive=False``), like ``activities/agent_box_reap.py``.

* ``validate_conversation_transfer`` keeps the envelope off the API pod:
  it streams the upload, validates it and admits the paused task.
* ``expire_conversation_transfers_tick`` is the expiry sweep: it retires a
  transfer that passed its admission deadline or whose paused session
  ended, deleting its stored envelope and cancelling its paused task.
"""

from __future__ import annotations

import dataclasses

from temporalio import activity

from astrolift_workflows.inputs import ValidateConversationTransferInput


@dataclasses.dataclass(frozen=True, slots=True)
class ConversationTransferExpirySummary:
    evaluated: int
    expired: int
    ended: int
    errors: int


@activity.defn(name="astrolift.agents.validate_conversation_transfer")
async def validate_conversation_transfer(input: ValidateConversationTransferInput) -> str:
    """Validate one uploaded envelope and admit its paused session; the
    transfer's state afterwards."""
    from asgiref.sync import sync_to_async

    return await sync_to_async(_validate_sync, thread_sensitive=False)(input)


def _validate_sync(input: ValidateConversationTransferInput) -> str:
    from astrolift_agents.services.conversation_transfer import validate_uploaded_envelope

    return validate_uploaded_envelope(
        input.transfer_id,
        actor=input.actor,
        team_id=input.team_id,
        undo=input.undo,
        trigger_kind=input.trigger_kind,
    )


@activity.defn(name="astrolift.agents.expire_conversation_transfers_tick")
async def expire_conversation_transfers_tick() -> ConversationTransferExpirySummary:
    """One sweep of the open transfers past their admission deadline."""
    from asgiref.sync import sync_to_async

    return await sync_to_async(_expire_sync, thread_sensitive=False)()


def _expire_sync() -> ConversationTransferExpirySummary:
    from astrolift_agents.services.conversation_transfer import expire_transfers

    summary = expire_transfers()
    return ConversationTransferExpirySummary(
        evaluated=summary["evaluated"],
        expired=summary["expired"],
        ended=summary["ended"],
        errors=summary["errors"],
    )
