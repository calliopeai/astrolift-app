"""
In-process pub/sub broker.

The contract:
- ``publish(topic, event)`` fans out to every active subscriber.
- ``subscribe(topic)`` yields events as they arrive; bounded queue
  per subscriber drops oldest under load.
- Cleanup happens automatically on consumer cancel / disconnect.
"""

from __future__ import annotations

import asyncio

import pytest

from core import pubsub


@pytest.fixture(autouse=True)
def _reset_subscribers():
    pubsub._subscribers.clear()
    yield
    pubsub._subscribers.clear()


@pytest.mark.asyncio
async def test_publish_fans_out_to_every_subscriber():
    received_a: list[int] = []
    received_b: list[int] = []

    async def consumer(out: list[int]) -> None:
        async for event in pubsub.subscribe("topic.x"):
            out.append(event)
            if len(out) == 3:
                return

    task_a = asyncio.create_task(consumer(received_a))
    task_b = asyncio.create_task(consumer(received_b))
    # Both consumers need to be registered before publishing or the
    # event lands in an empty subscriber set.
    while pubsub.subscriber_count("topic.x") < 2:
        await asyncio.sleep(0)

    for i in (1, 2, 3):
        await pubsub.publish("topic.x", i)

    await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=1.0)

    assert received_a == [1, 2, 3]
    assert received_b == [1, 2, 3]


@pytest.mark.asyncio
async def test_topics_are_isolated():
    seen_x: list[int] = []
    seen_y: list[int] = []

    async def consumer(topic: str, out: list[int]) -> None:
        async for event in pubsub.subscribe(topic):
            out.append(event)
            if len(out) == 1:
                return

    task_x = asyncio.create_task(consumer("topic.x", seen_x))
    task_y = asyncio.create_task(consumer("topic.y", seen_y))
    while pubsub.subscriber_count("topic.x") == 0 or pubsub.subscriber_count("topic.y") == 0:
        await asyncio.sleep(0)

    await pubsub.publish("topic.x", "x-event")
    await pubsub.publish("topic.y", "y-event")

    await asyncio.wait_for(asyncio.gather(task_x, task_y), timeout=1.0)
    assert seen_x == ["x-event"]
    assert seen_y == ["y-event"]


async def _wait_for_subscriber(topic: str, timeout: float = 1.0) -> None:
    """Yield until ``subscribe(topic)`` has registered. The consumer
    task needs the loop to schedule it and complete `_add_subscriber`
    before we publish, otherwise the event lands in an empty
    subscriber set and the test races itself."""
    deadline = asyncio.get_event_loop().time() + timeout
    while pubsub.subscriber_count(topic) == 0:
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(f"no subscriber on {topic}")
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_subscribe_cancel_cleans_up():
    """Cancelling the consumer task removes it from the subscriber set
    via the async-generator's ``finally`` block."""
    queue_started = asyncio.Event()

    async def consumer() -> None:
        async for _ in pubsub.subscribe("topic.cleanup"):
            queue_started.set()

    task = asyncio.create_task(consumer())
    await _wait_for_subscriber("topic.cleanup")
    await pubsub.publish("topic.cleanup", "warm-up")
    await asyncio.wait_for(queue_started.wait(), timeout=1.0)
    assert pubsub.subscriber_count("topic.cleanup") == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.sleep(0)
    assert pubsub.subscriber_count("topic.cleanup") == 0


@pytest.mark.asyncio
async def test_slow_consumer_drops_oldest_event():
    """A consumer slower than the producer keeps receiving — the
    bounded queue drops the oldest event rather than blocking the
    publisher."""
    seen: list[int] = []
    queue_size = 4

    async def consumer() -> None:
        async for event in pubsub.subscribe("topic.slow", max_queue=queue_size):
            seen.append(event)
            if len(seen) == queue_size:
                return
            # Yield control without consuming so the queue fills up.
            await asyncio.sleep(0)

    task = asyncio.create_task(consumer())
    while pubsub.subscriber_count("topic.slow") == 0:
        await asyncio.sleep(0)

    # Publish many more events than the queue holds.
    for i in range(queue_size * 4):
        await pubsub.publish("topic.slow", i)

    await asyncio.wait_for(task, timeout=1.0)
    assert len(seen) == queue_size
    # The most recent events should win — proves the broker dropped
    # the oldest, not the newest.
    assert seen[-1] >= queue_size * 3 - 1


def test_publish_sync_from_sync_context():
    """publish_sync is callable from non-async code (mutation
    resolvers, signal handlers)."""
    seen: list[str] = []

    async def consumer() -> None:
        async for event in pubsub.subscribe("topic.sync"):
            seen.append(event)
            return

    async def runner() -> None:
        task = asyncio.create_task(consumer())
        while pubsub.subscriber_count("topic.sync") == 0:
            await asyncio.sleep(0)
        # publish_sync from inside a running loop schedules the
        # coroutine via ensure_future; the consumer picks it up.
        pubsub.publish_sync("topic.sync", "hello")
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(runner())
    assert seen == ["hello"]


# ---------------------------------------------------------------------------
# Deployment.transition_to publish hook
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_transition_publishes_lifecycle_event(org, app, env, actor, monkeypatch):
    """Every Deployment status transition publishes to the broker.

    We don't run an async loop here — just monkeypatch the publish
    helper and assert the call shape. The real broker fan-out is
    tested in the asyncio tests above; this test isolates the model
    hook."""
    from astrolift_lifecycle.models import Deployment

    captured: list[tuple[str, dict]] = []

    def fake_publish_sync(topic, event):
        captured.append((topic, event))

    # The hook does `from core.pubsub import publish_sync` inside
    # the method body, so we patch the source module — the late
    # import resolves to whatever `core.pubsub.publish_sync` points
    # at when the method runs.
    monkeypatch.setattr("core.pubsub.publish_sync", fake_publish_sync)

    deploy = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user=actor,
        trigger_kind="manual",
        status=Deployment.Status.PENDING.value,
        image_tag="v1.0.0",
    )

    captured.clear()  # ignore any publishes from .create()
    deploy.transition_to(Deployment.Status.DEPLOYING)

    # Two publishes per transition: org-scoped + app-scoped.
    topics = [t for t, _ in captured]
    assert any(t.startswith("deployment.lifecycle.") for t in topics)
    assert any(".lifecycle.app." in t for t in topics)

    payloads = [p for _, p in captured]
    assert all(p["status"] == "deploying" for p in payloads)
    assert all(p["deployment_id"] == str(deploy.guid) for p in payloads)
    assert all(p["registered_app_slug"] == app.slug for p in payloads)
    assert all(p["environment_name"] == env.name for p in payloads)
