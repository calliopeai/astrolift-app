"""Long activities keep beating while their work runs (#2435).

A ``heartbeat_timeout`` lets Temporal retry an activity soon after its worker
dies, but it also kills a healthy activity that goes quiet for longer. Most
activities beat once and then wait on ``sync_to_async``, where a beat cannot
reach the server, so ``heartbeat_while`` beats from the event loop instead.
These tests run the real server in real time: each heartbeat timeout below
is two seconds and the work takes four.
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any

import pytest
from _sdk import maybe_heartbeat
from asgiref.sync import sync_to_async
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, TimeoutError, TimeoutType
from temporalio.testing import ActivityEnvironment

from astrolift_workflows import activity_heartbeat
from astrolift_workflows.activities import static_site
from astrolift_workflows.activity_heartbeat import heartbeat_while
from core.testing.temporal import temporal_worker

_WORK_SECONDS = 4


@pytest.fixture(autouse=True)
def _beat_fast(monkeypatch):
    monkeypatch.setattr(activity_heartbeat, "HEARTBEAT_EVERY", timedelta(seconds=0.2))


@activity.defn(name="heartbeat_2435_pumped")
async def pumped() -> str:
    await heartbeat_while(sync_to_async(time.sleep)(_WORK_SECONDS))
    return "done"


@activity.defn(name="heartbeat_2435_entry_beat")
async def entry_beat() -> str:
    """The shape most activities had: one beat, then the whole call."""
    activity.heartbeat()
    await sync_to_async(time.sleep)(_WORK_SECONDS)
    return "done"


def _driver_loop() -> None:
    """A provider driver polling inside the thread, beating every cycle."""
    deadline = time.monotonic() + _WORK_SECONDS
    while time.monotonic() < deadline:
        maybe_heartbeat("polling")
        time.sleep(0.2)


@activity.defn(name="heartbeat_2435_thread_beats")
async def thread_beats() -> str:
    activity.heartbeat()
    await sync_to_async(_driver_loop)()
    return "done"


@workflow.defn(sandboxed=False)
class HeartbeatProbeWorkflow:
    @workflow.run
    async def run(self, name: str, args: list) -> Any:
        return await workflow.execute_activity(
            name,
            args=args,
            start_to_close_timeout=timedelta(seconds=60),
            heartbeat_timeout=timedelta(seconds=2),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


async def _run(env, name: str, args: list | None = None, activities=()):
    with env.auto_time_skipping_disabled():
        async with temporal_worker(
            env,
            task_queue="heartbeat-2435",
            workflows=[HeartbeatProbeWorkflow],
            activities=[pumped, entry_beat, thread_beats, *activities],
        ):
            return await env.client.execute_workflow(
                HeartbeatProbeWorkflow.run,
                args=[name, args or []],
                id=f"heartbeat-2435-{name}",
                task_queue="heartbeat-2435",
            )


def _heartbeat_timeout(failure: WorkflowFailureError) -> bool:
    cause = failure.cause
    return (
        isinstance(cause, ActivityError)
        and isinstance(cause.cause, TimeoutError)
        and cause.cause.type == TimeoutType.HEARTBEAT
    )


async def test_work_longer_than_the_heartbeat_timeout_finishes(temporal_env):
    assert await _run(temporal_env, "heartbeat_2435_pumped") == "done"


async def test_one_beat_before_the_same_work_is_killed_as_stuck(temporal_env):
    with pytest.raises(WorkflowFailureError) as failure:
        await _run(temporal_env, "heartbeat_2435_entry_beat")

    assert _heartbeat_timeout(failure.value)


async def test_beats_from_inside_the_worker_thread_never_reach_the_server(temporal_env):
    """Why a provider driver's own polling beats never kept a deploy step alive."""
    with pytest.raises(WorkflowFailureError) as failure:
        await _run(temporal_env, "heartbeat_2435_thread_beats")

    assert _heartbeat_timeout(failure.value)


async def test_a_static_build_longer_than_the_heartbeat_timeout_completes(temporal_env, monkeypatch):
    """``sync_static_assets`` beat only before and after the in-cluster build,
    under a two-minute heartbeat timeout and a single attempt, so any build
    longer than that failed the deploy."""

    def slow_build(inp):
        time.sleep(_WORK_SECONDS)
        return {"stub": False, "deployment_id": inp.deployment_id}

    monkeypatch.setattr(static_site, "_sync_static_assets_sync", slow_build)

    result = await _run(
        temporal_env,
        "astrolift.deploy.sync_static_assets",
        [{"deployment_id": 7, "commit_sha": "abc"}],
        activities=[static_site.sync_static_assets],
    )

    assert result == {"stub": False, "deployment_id": 7}


async def test_beats_until_the_work_finishes_then_returns_its_result():
    beats: list[tuple] = []
    env = ActivityEnvironment()
    env.on_heartbeat = lambda *details: beats.append(details)

    async def work():
        await asyncio.sleep(0.7)
        return "result"

    async def run():
        return await heartbeat_while(work())

    assert await env.run(run) == "result"
    assert len(beats) >= 3
    assert all(details == () for details in beats)


async def test_a_failure_in_the_work_is_raised_unchanged():
    async def work():
        raise ValueError("driver refused")

    async def run():
        return await heartbeat_while(work())

    with pytest.raises(ValueError, match="driver refused"):
        await ActivityEnvironment().run(run)


async def test_cancelling_the_activity_cancels_the_work():
    env = ActivityEnvironment()
    started = asyncio.Event()
    work_cancelled = asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            work_cancelled.set()
            raise

    async def run():
        return await heartbeat_while(work())

    running = asyncio.ensure_future(env.run(run))
    await asyncio.wait_for(started.wait(), 5)
    env.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running
    await asyncio.wait_for(work_cancelled.wait(), 5)
