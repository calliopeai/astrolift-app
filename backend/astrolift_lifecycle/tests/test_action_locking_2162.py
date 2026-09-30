# ruff: noqa: F811
"""Concurrent real-Postgres quick actions serialize before the external call."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import connection, connections

from astrolift_lifecycle.tests.test_action_preconditions_2162 import invoke, targets  # noqa: F401
from astrolift_lifecycle.tests.test_owner_scopes_2104 import no_search, subject, world  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("action", ["restart", "scale"])
def test_same_version_concurrent_runtime_requests_do_not_both_reach_driver(
    world, targets, monkeypatch, action
):
    from time import monotonic, sleep

    entered, release, attempted = Event(), Event(), Event()
    calls, backend_pids = [], []

    class Driver:
        def patch_workload(self, *args):
            calls.append(args)
            entered.set()
            assert release.wait(5), "test did not release the driver"
            return {"spec": {"replicas": 2}, "status": {"observedGeneration": 3, "readyReplicas": 2}}

    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: Driver())

    def run(second=False):
        try:
            connection.ensure_connection()
            backend_pids.append(connection.connection.info.backend_pid)
            if second:
                attempted.set()
            with subject(world):
                return invoke(world, targets, action, 11)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run)
        try:
            assert entered.wait(5), "first action did not reach the driver"
            second = pool.submit(run, True)
            assert attempted.wait(5)
            deadline = monotonic() + 5
            blocked = False
            while monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [backend_pids[-1]]
                    )
                    row = cursor.fetchone()
                if row and row[0] == "Lock":
                    blocked = True
                    break
                sleep(0.005)
            assert blocked, "the second real connection did not wait on the target lock"
            assert len(calls) == 1
        finally:
            release.set()
        assert first.result(timeout=5).ok
        denied = second.result(timeout=5)
        assert not denied.ok and denied.errors[0].code == "VERSION_MISMATCH"
    assert len(calls) == 1
    targets.workload.refresh_from_db()
    assert targets.workload.version == 12
