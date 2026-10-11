"""The production worker set starts with every registered activity.

The deploy of astrolift-app 68ca1869 to CONFLICT crash-looped the worker:
``validate_deployment_identity_origin`` (#2278) is a synchronous activity and
the entrypoint gave its Workers no executor, so Temporal refused to construct
them. Tests registered it on workers with an executor of their own, so nothing
built the real set. This builds it.
"""

from __future__ import annotations

import inspect

import pytest

from astrolift_workflows.__main__ import build_workers
from astrolift_workflows.worker import ACTIVITIES


@pytest.mark.asyncio
async def test_the_entrypoint_builds_a_worker_per_queue_with_every_activity(temporal_env):
    workers = build_workers(temporal_env.client, ("q-one", "q-two"))

    assert len(workers) == 2


def test_the_registered_set_still_has_a_synchronous_activity_to_guard():
    # If every activity becomes async this guard is moot; keep it honest.
    assert any(not inspect.iscoroutinefunction(fn) for fn in ACTIVITIES)
