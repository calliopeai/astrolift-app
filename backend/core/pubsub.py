"""
Publish/subscribe for GraphQL subscriptions.

Producers (model ``transition_to`` hooks, mutation resolvers, signal
handlers) call ``publish(topic, event)``; consumers (Strawberry
subscription resolvers) ``async for event in subscribe(topic)``.

With ``settings.PUBSUB_REDIS_URL`` set (``ASTROLIFT_PUBSUB_REDIS_URL``)
events go through Redis pub/sub, so a deployment transition recorded by a
Temporal worker reaches a websocket held by any webservice replica (#2435).
Only topics a subscription resolver serves (``_SHARED_TOPICS``) cross
processes; the rest, such as the plaintext approval token event, never
leave the process that published them. Without the URL, a single-process
asyncio broker serves everything (tests, local dev).

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
import json
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)


_DEFAULT_QUEUE_SIZE = 64
_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
_lock = asyncio.Lock()
_redis_clients: dict[str, Any] = {}


# Topic prefixes served by core.schema.subscriptions; events are JSON.
_SHARED_TOPICS = ("deployment.lifecycle.",)


def _redis_url(topic: str) -> str:
    from django.conf import settings

    if not topic.startswith(_SHARED_TOPICS):
        return ""
    return getattr(settings, "PUBSUB_REDIS_URL", "") or ""


def _channel(topic: str) -> str:
    return f"astrolift:pubsub:{topic}"


def _redis_publish(url: str, topic: str, event: Any) -> None:
    import redis

    client = _redis_clients.get(url)
    if client is None:
        client = _redis_clients.setdefault(url, redis.Redis.from_url(url))
    client.publish(_channel(topic), json.dumps(event, default=str))


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
    if url := _redis_url(topic):
        await asyncio.to_thread(_redis_publish, url, topic, event)
        return
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
    if url := _redis_url(topic):
        _redis_publish(url, topic, event)
        return
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
    if url := _redis_url(topic):
        async with contextlib.aclosing(_redis_subscribe(url, topic)) as events:
            async for event in events:
                yield event
        return
    queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
    await _add_subscriber(topic, queue)
    try:
        while True:
            event = await queue.get()
            yield event
    finally:
        with contextlib.suppress(Exception):
            await _remove_subscriber(topic, queue)


async def _redis_subscribe(url: str, topic: str) -> AsyncIterator[Any]:
    import redis.asyncio

    client = redis.asyncio.Redis.from_url(url)
    channel = client.pubsub(ignore_subscribe_messages=True)
    try:
        await channel.subscribe(_channel(topic))
        async for message in channel.listen():
            yield json.loads(message["data"])
    finally:
        with contextlib.suppress(Exception):
            await channel.aclose()
        with contextlib.suppress(Exception):
            await client.aclose()


def subscriber_count(topic: str) -> int:
    """Snapshot of active subscribers on ``topic``. For diagnostics
    only — the number can change in the next instruction."""
    return len(_subscribers.get(topic, ()))
