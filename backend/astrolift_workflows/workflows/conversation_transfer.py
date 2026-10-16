"""Conversation Move workflows (#1971).

``ValidateConversationTransferWorkflow`` is the worker half of
``prepareConversationTransfer``: one activity streams the uploaded envelope,
validates it and dispatches the paused task, so a 150 MiB envelope is never
downloaded or parsed on an API pod. Its id is
``ValidateConversationTransferWorkflow-<transfer guid>``; a repeated prepare
joins it, or restarts it after it failed.

``ConversationTransferExpiryTickWorkflow`` is the expiry sweep, run by the
platform schedule registry every 60 s. Coalescing tick model, like the
agent-box reaper: the activity timeout stays under the interval, so a slow
sweep cannot overlap the next.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from astrolift_workflows.inputs import ValidateConversationTransferInput

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.conversation_transfer import (
        ConversationTransferExpirySummary,
        expire_conversation_transfers_tick,
        validate_conversation_transfer,
    )


# A refusal settles inside the activity; only infrastructure failures (the
# store, the database, the dispatch) raise, and each retry re-reads the row.
_VALIDATE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=5,
)
_VALIDATE_TIMEOUT = timedelta(minutes=10)
_TICK_TIMEOUT = timedelta(seconds=50)
"""Under the 60 s interval so a slow sweep can't overlap the next tick."""


@workflow.defn(name="ValidateConversationTransferWorkflow")
class ValidateConversationTransferWorkflow:
    @workflow.run
    async def run(self, input: ValidateConversationTransferInput) -> str:
        return await workflow.execute_activity(
            validate_conversation_transfer,
            input,
            start_to_close_timeout=_VALIDATE_TIMEOUT,
            retry_policy=_VALIDATE_RETRY,
        )


@workflow.defn(name="ConversationTransferExpiryTickWorkflow")
class ConversationTransferExpiryTickWorkflow:
    @workflow.run
    async def run(self) -> ConversationTransferExpirySummary:
        return await workflow.execute_activity(
            expire_conversation_transfers_tick,
            start_to_close_timeout=_TICK_TIMEOUT,
        )
