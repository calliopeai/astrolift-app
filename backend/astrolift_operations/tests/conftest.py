"""Shared fixtures for the operations tests.

The clock fixture exists because the Prometheus response cache is a wall-clock
TTL, and a test asserting "the second call was served from cache" is really
asserting "less than thirty seconds of wall clock passed between these two
lines". On a contended CI runner that is not a safe assumption, and the test
fails on branches that touch nothing near it, then passes on a bare re-run
(#1421).

Widening the window would only make it rarer. Freezing the clock makes "within
the cache window" mean what the test says it means.
"""

from __future__ import annotations

import time as prometheus_client_time

import pytest

from astrolift_operations import prometheus_client


class FrozenClock:
    """A monotonic clock that only moves when a test moves it.

    Tests asserting a cache *hit* leave it alone. A test that wants an entry to
    expire calls ``advance``, which is explicit and independent of how loaded
    the machine is.
    """

    def __init__(self, start: float = 1_000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        self._now += seconds
        return self._now


@pytest.fixture(autouse=True)
def frozen_prometheus_clock(monkeypatch) -> FrozenClock:
    """Freeze the clock the Prometheus TTL cache reads.

    Replaces the module's ``time`` binding rather than setting ``monotonic`` on
    the ``time`` module itself. ``prometheus_client.time`` *is* the stdlib
    module, so patching its attribute would freeze ``time.monotonic`` for the
    whole process for the duration of every test in this package, including
    pytest's own timing and any driver that measures elapsed time. The shim
    delegates everything else, so only the two call sites in this module see a
    frozen clock.

    Autouse rather than opt-in: the failure it prevents appeared in a test that
    never mentions time, and any test that populates the cache and reads it
    back has the same exposure. A test that needs elapsed time takes this
    fixture by name and calls ``advance``.
    """
    clock = FrozenClock()

    class _FrozenTime:
        monotonic = staticmethod(clock)

        def __getattr__(self, name):
            return getattr(prometheus_client_time, name)

    monkeypatch.setattr(prometheus_client, "time", _FrozenTime())
    return clock
