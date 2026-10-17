"""A stopping worker drains instead of throwing work away (#2435).

Scale-in sends SIGTERM to a worker. Temporal's default shutdown cancels every
running activity at once, so the entrypoint sets a drain window, stops all
of its workers together, and an activity that outlives the window is retried
on another worker from its last heartbeat.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import timedelta
from uuid import uuid4

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

from astrolift_workflows import __main__ as entrypoint
from astrolift_workflows import worker as registry

_started: asyncio.Event | None = None
_attempts: list[tuple[int, int]] = []
_threads: list[int] = []


@activity.defn(name="drain_2435_short")
async def short_activity() -> str:
    _attempts.append((activity.info().attempt, 0))
    _started.set()
    await asyncio.sleep(1)
    _attempts.append((activity.info().attempt, 1))
    return "finished"


@activity.defn(name="drain_2435_long")
async def long_activity() -> int:
    """Counts to 30, checkpointing each step in its heartbeat details."""
    info = activity.info()
    step = info.heartbeat_details[0] if info.heartbeat_details else 0
    _attempts.append((info.attempt, step))
    _started.set()
    while step < 30:
        step += 1
        activity.heartbeat(step)
        await asyncio.sleep(0.1)
    _attempts.append((info.attempt, step))
    return step


@activity.defn(name="drain_2435_sync")
def sync_activity() -> str:
    return "ok"


@workflow.defn(sandboxed=False)
class DrainWorkflow:
    @workflow.run
    async def run(self, name: str):
        return await workflow.execute_activity(
            name,
            start_to_close_timeout=timedelta(minutes=1),
            heartbeat_timeout=timedelta(seconds=2),
            retry_policy=RetryPolicy(initial_interval=timedelta(milliseconds=100)),
        )


def _register(monkeypatch, drain_seconds: str) -> None:
    global _started
    _started = asyncio.Event()
    _attempts.clear()
    monkeypatch.setenv("ASTROLIFT_WORKER_DRAIN_SECONDS", drain_seconds)
    monkeypatch.setattr(registry, "WORKFLOWS", [DrainWorkflow])
    monkeypatch.setattr(registry, "ACTIVITIES", [short_activity, long_activity, sync_activity])


async def _start(env, name: str):
    queue = f"drain-2435-{uuid4()}"
    workers = entrypoint.build_workers(env.client, (queue, f"{queue}-idle"))
    runs = [asyncio.create_task(w.run()) for w in workers]
    handle = await env.client.start_workflow(DrainWorkflow.run, name, id=queue, task_queue=queue)
    return queue, workers, runs, handle


async def _no_pending_activity(handle) -> bool:
    return not (await handle.describe()).raw_description.pending_activities


async def test_a_stopping_worker_lets_the_running_activity_finish(temporal_env, monkeypatch):
    _register(monkeypatch, "10")
    with temporal_env.auto_time_skipping_disabled():
        _queue, workers, runs, handle = await _start(temporal_env, "drain_2435_short")
        await asyncio.wait_for(_started.wait(), 15)

        began = time.monotonic()
        await entrypoint.drain(workers)
        await asyncio.gather(*runs)

        # Both workers stopped inside one window: the shutdowns ran together.
        assert time.monotonic() - began < 10
        # The activity ran to the end on the stopping worker, once, and its
        # result reached the server before the worker exited.
        assert _attempts == [(1, 0), (1, 1)]
        assert await _no_pending_activity(handle)


async def test_work_past_the_drain_window_resumes_from_its_heartbeat(temporal_env, monkeypatch):
    _register(monkeypatch, "0.5")
    with temporal_env.auto_time_skipping_disabled():
        queue, workers, runs, handle = await _start(temporal_env, "drain_2435_long")
        await asyncio.wait_for(_started.wait(), 15)
        await asyncio.sleep(1)

        await entrypoint.drain(workers)
        await asyncio.gather(*runs)
        assert not await _no_pending_activity(handle), "the cut-off activity was dropped"

        # The scaled-in worker is gone; a surviving one picks the work up.
        survivors = entrypoint.build_workers(temporal_env.client, (queue,))
        survivor_runs = [asyncio.create_task(w.run()) for w in survivors]
        try:
            deadline = time.monotonic() + 30
            while not await _no_pending_activity(handle):
                assert time.monotonic() < deadline, f"the retry never finished: {_attempts}"
                await asyncio.sleep(0.2)
        finally:
            await entrypoint.drain(survivors)
            await asyncio.gather(*survivor_runs)

    (first_attempt, first_step), (retry_attempt, resumed_at), done = _attempts
    assert (first_attempt, first_step) == (1, 0)
    assert retry_attempt == 2
    assert resumed_at > 0, "the retry started over instead of from its checkpoint"
    assert done == (2, 30)


async def test_a_sync_activity_releases_its_database_connection(temporal_env, monkeypatch):
    import django.db

    _register(monkeypatch, "5")
    calls: list[int] = []
    monkeypatch.setattr(django.db, "close_old_connections", lambda: calls.append(threading.get_ident()))
    with temporal_env.auto_time_skipping_disabled():
        _queue, workers, runs, handle = await _start(temporal_env, "drain_2435_sync")
        try:
            assert await asyncio.wait_for(handle.result(), 15) == "ok"
        finally:
            await entrypoint.drain(workers)
            await asyncio.gather(*runs)

    # Once before and once after, on the executor thread that ran it.
    assert len(calls) == 2 and calls[0] == calls[1] != threading.get_ident()


def test_the_drain_window_fits_the_default_ecs_stop_timeout(monkeypatch):
    monkeypatch.delenv("ASTROLIFT_WORKER_DRAIN_SECONDS", raising=False)

    assert entrypoint.drain_timeout() == timedelta(seconds=25)
