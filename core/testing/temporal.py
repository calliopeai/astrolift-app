"""
Temporal test environment fixture.

Workflow tests use the official ``WorkflowEnvironment.start_time_skipping``
fixture. We wrap it so the rest of the test suite can request ``temporal_env``
without importing the SDK directly, and so the worker registration list
is centralized: when we add a new workflow we register it once here, and
every test picks it up automatically.

This file is imported lazily by ``conftest.py`` — importing temporalio
at module load time would slow the unit-test path that doesn't need it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


@asynccontextmanager
async def temporal_test_env() -> AsyncIterator[Any]:
    from temporalio.testing import WorkflowEnvironment

    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


@asynccontextmanager
async def temporal_worker(
    env: Any,
    *,
    task_queue: str = "astrolift-test",
    workflows: list[Any] | None = None,
    activities: list[Any] | None = None,
) -> AsyncIterator[Any]:
    from temporalio.worker import Worker

    worker = Worker(
        env.client,
        task_queue=task_queue,
        workflows=workflows or [],
        activities=activities or [],
    )
    async with worker:
        yield worker
