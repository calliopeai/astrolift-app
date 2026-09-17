"""
Temporal test environment fixture.

Workflow tests use the official ``WorkflowEnvironment.start_time_skipping``
fixture. We wrap it so the rest of the test suite can request ``temporal_env``
without importing the SDK directly, and so the worker registration list
is centralized: when we add a new workflow we register it once here, and
every test picks it up automatically.

Set ASTROLIFT_TEST_TEMPORAL_ADDRESS to use an existing disposable server
instead, such as the compose service on architectures without a test binary.

This file is imported lazily by ``conftest.py`` — importing temporalio
at module load time would slow the unit-test path that doesn't need it.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


@asynccontextmanager
async def temporal_test_env() -> AsyncIterator[Any]:
    from temporalio.client import Client
    from temporalio.testing import WorkflowEnvironment

    if address := os.getenv("ASTROLIFT_TEST_TEMPORAL_ADDRESS"):
        client = await Client.connect(address, namespace=os.getenv("TEMPORAL_NAMESPACE", "default"))
        async with WorkflowEnvironment.from_client(client) as env:
            yield env
        return
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
