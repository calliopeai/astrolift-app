"""Long activities prove their worker is alive (#2435).

A worker killed outright (stop timeout exceeded, host lost) reports nothing,
so Temporal notices only when the activity's ``heartbeat_timeout`` lapses,
or, without one, at ``start_to_close_timeout``: up to six hours for a
pipeline job poll. Callers therefore pass ``heartbeat_timeout``, and the
activity must beat more often than that for as long as it runs.

Most activities beat once and hand their work to ``sync_to_async``. A beat
from that thread never reaches the server, because an async activity
heartbeats through the event loop (``maybe_heartbeat`` swallows the
error). Every thread-sensitive ``sync_to_async`` call in the worker also
shares one thread, so the work may first wait behind another activity's.
``heartbeat_while`` beats from the event loop until the work finishes,
queued or running.

It proves the worker is alive, not that the work progresses: a hung call
still ends at ``start_to_close_timeout``. An activity whose beats mean
progress, or that checkpoints in its heartbeat details (these beats carry
none), paces its own beats instead.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import timedelta
from typing import TypeVar

from temporalio import activity

T = TypeVar("T")

#: How often ``heartbeat_while`` beats. The SDK forwards at most one beat per
#: min(80% of the heartbeat timeout, 60 s), so the server hears one a minute.
HEARTBEAT_EVERY = timedelta(seconds=30)

#: The ``heartbeat_timeout`` callers set: four beats, two forwarded ones, so a
#: busy event loop is not mistaken for a dead worker.
HEARTBEAT_TIMEOUT = timedelta(minutes=2)


async def heartbeat_while(work: Awaitable[T]) -> T:
    """Await ``work``, heartbeating every ``HEARTBEAT_EVERY`` until it finishes.

    Cancelling the activity cancels ``work``, as a plain ``await`` would.
    """
    task = asyncio.ensure_future(work)
    try:
        while not task.done():
            activity.heartbeat()
            await asyncio.wait({task}, timeout=HEARTBEAT_EVERY.total_seconds())
    except BaseException:
        task.cancel()
        raise
    return task.result()
