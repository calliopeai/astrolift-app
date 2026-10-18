"""Subscription events cross processes through Redis (#2435).

Deployment transitions are mostly recorded by the Temporal worker, and a
browser's websocket is held by one of N webservice replicas; the in-memory
broker reached neither. CI and the local rig provide Redis as the cache.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import redis

from core import pubsub

URL = os.getenv("DJANGO_CACHE_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("redis://"), reason="needs the cache Redis")


@pytest.fixture
def shared(settings):
    settings.PUBSUB_REDIS_URL = URL
    pubsub._subscribers.clear()
    yield redis.Redis.from_url(URL)
    pubsub._subscribers.clear()


async def _subscribed(client, topic):
    channel = pubsub._channel(topic)
    while not dict(await asyncio.to_thread(client.pubsub_numsub, channel)).get(channel.encode()):
        await asyncio.sleep(0.01)


async def test_an_event_from_another_process_reaches_the_subscriber(shared):
    topic = f"deployment.lifecycle.{uuid.uuid4()}"
    events = pubsub.subscribe(topic)
    first = asyncio.ensure_future(events.__anext__())
    await asyncio.wait_for(_subscribed(shared, topic), 5)

    # What a worker's ``publish_sync`` puts on the wire, from its own client.
    await asyncio.to_thread(pubsub.publish_sync, topic, {"status": "running"})

    assert await asyncio.wait_for(first, 5) == {"status": "running"}
    assert pubsub.subscriber_count(topic) == 0, "the event went through Redis, not the local set"
    await events.aclose()
    channel = pubsub._channel(topic)
    assert (
        dict(shared.pubsub_numsub(channel))[channel.encode()] == 0
    ), "closing left the Redis subscription open"


def test_topics_without_a_subscription_resolver_stay_in_process(shared, monkeypatch):
    # The approval token event carries a plaintext token; it must not land on Redis.
    monkeypatch.setattr(pubsub, "_redis_publish", lambda *a: pytest.fail("published to Redis"))

    pubsub.publish_sync("deployment.approval_token.minted.1", {"approval_token": "secret"})
