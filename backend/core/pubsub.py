"""
In-process publish/subscribe for GraphQL subscriptions.

Phase 1: a single-process asyncio broker. Producers (model
``transition_to`` hooks, mutation resolvers, signal handlers) call
``publish(topic, event)``; consumers (Strawberry subscription
resolvers) ``async for event in subscribe(topic)``.

Single-process only. A multi-worker deployment swaps this for
Redis (or NATS / Kafka / Postgres LISTEN-NOTIFY) at the broker
layer; the call shape on both sides — ``publish`` and
``subscribe`` — stays the same. The swap is one file change in
this module.

Topic discipline:

  ``deployment.lifecycle.<organization-id>``
    Every Deployment status transition for the given org. Filtering
    by app/env happens at the resolver layer.

  ``deployment.lifecycle.app.<app-guid>``
    Same shape, scoped to a single registered app — useful for the
    /apps/[slug] page.

We use ``asyncio.Queue`` per subscriber so a slow consumer doesn't
block other subscribers or producers. A bounded queue (default 64
events) caps memory; if a consumer is slower than that, oldest
events drop and the consumer reconciles via the standard query
on reconnect.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)


_DEFAULT_QUEUE_SIZE = 64
_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
_lock = asyncio.Lock()


async def _add_subscriber(topic: str, queue: asyncio.Queue) -> None:
    async with _lock:
        _subscribers[topic].add(queue)


async def _remove_subscriber(topic: str, queue: asyncio.Queue) -> None:
    async with _lock:
        _subscribers[topic].discard(queue)
        if not _subscribers[topic]:
            del _subscribers[topic]


def _sync_remove_subscriber(topic: str, queue: asyncio.Queue) -> None:
    """Cleanup hook callable from non-async contexts (e.g. an
    async-generator's ``finally`` running outside the loop)."""
    bucket = _subscribers.get(topic)
    if bucket is None:
        return
    bucket.discard(queue)
    if not bucket:
        _subscribers.pop(topic, None)


async def publish(topic: str, event: Any) -> None:
    """Fan out ``event`` to every active subscriber on ``topic``.

    Drops oldest queued events for slow consumers (bounded queue);
    never blocks the producer. Producers can call this from any
    event loop — schedule it via ``asyncio.ensure_future`` from
    sync code (see ``publish_sync``).
    """
    bucket = _subscribers.get(topic)
    if not bucket:
        return
    for queue in tuple(bucket):
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # Race with another producer; drop this event for this
            # subscriber. Recovery is the consumer's normal-query
            # reconcile on reconnect.
            logger.warning("pubsub queue full, dropping event on %s", topic)


def publish_sync(topic: str, event: Any) -> None:
    """Sync-call entry point.

    Subscriptions are async, but the publishers we care about
    (Deployment.transition_to, mutation resolvers) run inside the
    sync request path. This helper schedules the publish on the
    running loop if there is one, or uses a fresh loop if not.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None or not loop.is_running():
        # Sync context — run the coroutine to completion in a
        # transient loop. Cheap for in-process; never reaches the
        # network.
        asyncio.run(publish(topic, event))
        return
    asyncio.ensure_future(publish(topic, event))


async def subscribe(
    topic: str,
    *,
    max_queue: int = _DEFAULT_QUEUE_SIZE,
) -> AsyncIterator[Any]:
    """Yield events as they're published to ``topic``.

    Cancellation-safe: when the consumer's ``async for`` is
    cancelled (client disconnect), the queue is removed from the
    subscriber set in the ``finally`` block. The ``contextlib.suppress``
    wrapper keeps the cleanup path clean even when the consumer
    bails mid-iteration.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
    await _add_subscriber(topic, queue)
    try:
        while True:
            event = await queue.get()
            yield event
    finally:
        with contextlib.suppress(Exception):
            await _remove_subscriber(topic, queue)


def subscriber_count(topic: str) -> int:
    """Snapshot of active subscribers on ``topic``. For diagnostics
    only — the number can change in the next instruction."""
    return len(_subscribers.get(topic, ()))
