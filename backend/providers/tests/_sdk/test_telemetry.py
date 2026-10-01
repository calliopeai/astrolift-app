"""Tests for ``_sdk/_telemetry.py`` -- the ``@driver_op`` decorator (#586-#623).

Covers:

* Sync method success + failure paths emit the right log record + metric.
* Async method success + failure paths.
* Async-generator dispatch (``stream_logs``-shaped drivers).
* ``redact_args`` masks designated kwargs in the log record's ``extra``.
* ``audit=True`` emits an ``AuditEntry`` via ``core.mutations.emit_audit``
  when available, no-ops otherwise (providers tree is import-isolated).
* Temporal ``activity.heartbeat`` is invoked when ``in_activity()`` is
  truthy and never raises into the driver.
* Exceptions re-raise *unmodified* so retry-class inspection by the
  workflow caller stays correct.
* The decorator attaches ``__wrapped__`` + ``__astrolift_driver_op__`` so
  callers can introspect.

Tests use the live ``prometheus_client.REGISTRY`` so they double as a smoke
test for the ``/metrics`` view's discoverability.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest
from prometheus_client import REGISTRY

from _sdk._telemetry import (
    _op_counter,
    _op_histogram,
    driver_op,
    maybe_heartbeat,
)


@pytest.fixture(autouse=True)
def _propagate_provider_logs():
    """Force propagation so ``caplog`` sees the records.

    The backend test job runs this file under Django's LOGGING config,
    which sets ``propagate: False`` on the ``astrolift`` logger tree —
    pytest's caplog handler sits on the root logger and would otherwise
    receive nothing. The standalone providers job has no such config;
    this keeps the assertions identical in both environments."""
    loggers = [logging.getLogger("astrolift.providers"), logging.getLogger("astrolift")]
    prev = [lg.propagate for lg in loggers]
    for lg in loggers:
        lg.propagate = True
    yield
    for lg, val in zip(loggers, prev, strict=True):
        lg.propagate = val


def _counter_value(cloud: str, driver: str, method: str, outcome: str) -> float:
    """Read the current value of the driver-op counter for the labels."""
    return _op_counter.labels(cloud, driver, method, outcome)._value.get()


def _histogram_count(cloud: str, driver: str, method: str) -> float:
    """Read the histogram's cumulative observation count for the labels.

    ``prometheus_client.Histogram`` doesn't expose a public ``_count``
    attribute; walk the collected samples and pull
    ``<name>_count`` for the matching label tuple. This is the same path
    Prometheus scrape would use.
    """
    target_labels = {"cloud": cloud, "driver": driver, "method": method}
    for metric in _op_histogram.collect():
        for sample in metric.samples:
            if sample.name.endswith("_count") and sample.labels == target_labels:
                return float(sample.value)
    return 0.0


class _SyncDriver:
    """Tiny driver with one decorated sync method to exercise the wrapper."""

    @driver_op(cloud="aws", driver="testdriver")
    def do_thing(self, value: int, *, redacted: str = "secret") -> int:
        if value < 0:
            raise ValueError(f"negative value {value}")
        return value * 2


class _AsyncDriver:
    @driver_op(cloud="gcp", driver="asyncdrv")
    async def do_async(self, value: int) -> int:
        if value < 0:
            raise RuntimeError("async failed")
        return value + 1


class _AsyncGenDriver:
    @driver_op(cloud="k8s_native", driver="streamdrv")
    async def stream(self, count: int):
        for i in range(count):
            yield i


def test_sync_success_increments_counter_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    before = _counter_value("aws", "testdriver", "do_thing", "success")
    with caplog.at_level(logging.INFO, logger="astrolift.providers"):
        result = _SyncDriver().do_thing(5)
    assert result == 10
    after = _counter_value("aws", "testdriver", "do_thing", "success")
    assert after == before + 1
    starts = [r for r in caplog.records if r.message == "driver_op start"]
    assert starts, "expected at least one 'driver_op start' log record"
    record = starts[-1]
    assert record.cloud == "aws"
    assert record.driver == "testdriver"
    assert record.method == "do_thing"


def test_sync_failure_increments_error_counter_and_reraises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    before = _counter_value("aws", "testdriver", "do_thing", "error")
    with caplog.at_level(logging.ERROR, logger="astrolift.providers"), pytest.raises(ValueError):
        _SyncDriver().do_thing(-1)
    after = _counter_value("aws", "testdriver", "do_thing", "error")
    assert after == before + 1
    errors = [r for r in caplog.records if r.message == "driver_op error"]
    assert errors
    record = errors[-1]
    assert record.exception_type == "ValueError"


def test_exception_type_is_preserved() -> None:
    """The wrapper must NOT swallow + rewrap exceptions.

    Workflows decide retry policy by inspecting ``type(exc).__name__``;
    a generic re-raise would break ``classify_apply_error``.
    """

    class _CustomError(Exception):
        pass

    class _D:
        @driver_op(cloud="aws", driver="x")
        def go(self) -> None:
            raise _CustomError("nope")

    with pytest.raises(_CustomError):
        _D().go()


def test_redact_args_masks_designated_kwargs(caplog: pytest.LogCaptureFixture) -> None:
    class _D:
        @driver_op(cloud="aws", driver="secrets", redact_args=("payload",))
        def write(self, *, path: str, payload: dict) -> None:
            return None

    with caplog.at_level(logging.INFO, logger="astrolift.providers"):
        _D().write(path="ops/db", payload={"PASSWORD": "hunter2"})
    starts = [r for r in caplog.records if r.message == "driver_op start"]
    assert starts
    record = starts[-1]
    # The decorator stamps ``driver_args`` (renamed away from ``args``
    # which collides with logging.LogRecord's reserved field).
    extra = record.__dict__.get("driver_args")
    assert isinstance(extra, dict)
    assert extra.get("payload") == "<redacted>"
    assert extra.get("path") == "ops/db"


def test_async_success_increments_counter() -> None:
    before = _counter_value("gcp", "asyncdrv", "do_async", "success")
    result = asyncio.run(_AsyncDriver().do_async(3))
    assert result == 4
    after = _counter_value("gcp", "asyncdrv", "do_async", "success")
    assert after == before + 1


def test_async_failure_increments_error_counter() -> None:
    before = _counter_value("gcp", "asyncdrv", "do_async", "error")
    with pytest.raises(RuntimeError):
        asyncio.run(_AsyncDriver().do_async(-1))
    after = _counter_value("gcp", "asyncdrv", "do_async", "error")
    assert after == before + 1


def test_async_generator_yields_items_and_counts_success() -> None:
    before = _counter_value("k8s_native", "streamdrv", "stream", "success")

    async def _collect() -> list[int]:
        out = []
        async for item in _AsyncGenDriver().stream(3):
            out.append(item)
        return out

    items = asyncio.run(_collect())
    assert items == [0, 1, 2]
    after = _counter_value("k8s_native", "streamdrv", "stream", "success")
    assert after == before + 1


def test_decorator_attaches_wrapped_and_marker() -> None:
    """Tests that introspect ``__wrapped__`` (e.g. checking that every
    driver method has the decorator applied) should keep working."""
    fn = _SyncDriver.do_thing
    assert hasattr(fn, "__wrapped__")
    marker = getattr(fn, "__astrolift_driver_op__", None)
    assert marker is not None
    assert marker.cloud == "aws"
    assert marker.driver == "testdriver"
    assert marker.method == "do_thing"


def test_heartbeat_invoked_when_in_activity() -> None:
    """The decorator must call ``activity.heartbeat`` at entry when the
    method runs inside a Temporal activity."""
    calls: list[str] = []

    class _FakeActivity:
        @staticmethod
        def in_activity() -> bool:
            return True

        @staticmethod
        def heartbeat(*args: object) -> None:
            calls.append(str(args))

    with (
        patch("_sdk._telemetry._temporal_activity", _FakeActivity),
        patch("_sdk._telemetry._TEMPORAL_AVAILABLE", True),
    ):
        _SyncDriver().do_thing(1)
    assert calls, "expected at least one heartbeat call when in_activity=True"


def test_heartbeat_silent_outside_activity() -> None:
    """When not inside a Temporal activity, heartbeat must be a no-op."""
    calls: list[str] = []

    class _FakeActivity:
        @staticmethod
        def in_activity() -> bool:
            return False

        @staticmethod
        def heartbeat(*args: object) -> None:
            calls.append(str(args))

    with (
        patch("_sdk._telemetry._temporal_activity", _FakeActivity),
        patch("_sdk._telemetry._TEMPORAL_AVAILABLE", True),
    ):
        _SyncDriver().do_thing(1)
    assert calls == []


def test_heartbeat_tolerates_raises_inside_check() -> None:
    """``activity.in_activity()`` can raise outside a worker thread on
    some Temporal SDK versions; the wrapper MUST NOT propagate that."""

    class _ExplodingActivity:
        @staticmethod
        def in_activity() -> bool:
            raise RuntimeError("no current activity")

        @staticmethod
        def heartbeat(*args: object) -> None:
            return None

    with (
        patch("_sdk._telemetry._temporal_activity", _ExplodingActivity),
        patch("_sdk._telemetry._TEMPORAL_AVAILABLE", True),
    ):
        result = _SyncDriver().do_thing(2)
    assert result == 4


def test_maybe_heartbeat_no_op_outside_temporal() -> None:
    # No exception when there's no Temporal at all.
    with patch("_sdk._telemetry._TEMPORAL_AVAILABLE", False):
        maybe_heartbeat("test")


def test_audit_emit_called_when_audit_true() -> None:
    """When ``audit=True``, the decorator must call ``emit_audit`` with
    an ``AuditEntry`` carrying the ``sensitive_kind`` action."""
    captured: list[object] = []

    audit_entry_class = type(
        "AuditEntry",
        (),
        {
            "__init__": lambda self, **kw: setattr(self, "__dict__", kw) or None,
        },
    )

    with patch.dict(
        "sys.modules",
        {
            "core.mutations": type(
                "M",
                (),
                {
                    "AuditEntry": audit_entry_class,
                    "emit_audit": lambda e: captured.append(e),
                },
            ),
            "core.tenancy": type("T", (), {"get_current_tenant": staticmethod(lambda: None)}),
        },
    ):

        class _D:
            @driver_op(
                cloud="aws",
                driver="secrets",
                audit=True,
                sensitive_kind="secret.read",
            )
            def read(self, path: str) -> str:
                return "value"

        _D().read("/some/path")

    assert captured, "expected emit_audit to be called when audit=True"
    entry = captured[-1]
    assert entry.__dict__.get("action") == "secret.read"
    assert entry.__dict__.get("decision") == "ALLOW"


def test_audit_emit_records_error_decision_on_failure() -> None:
    captured: list[object] = []

    audit_entry_class = type(
        "AuditEntry",
        (),
        {"__init__": lambda self, **kw: setattr(self, "__dict__", kw) or None},
    )

    with patch.dict(
        "sys.modules",
        {
            "core.mutations": type(
                "M",
                (),
                {
                    "AuditEntry": audit_entry_class,
                    "emit_audit": lambda e: captured.append(e),
                },
            ),
            "core.tenancy": type("T", (), {"get_current_tenant": staticmethod(lambda: None)}),
        },
    ):

        class _D:
            @driver_op(
                cloud="aws",
                driver="secrets",
                audit=True,
                sensitive_kind="secret.read",
            )
            def read(self, path: str) -> str:
                raise PermissionError("nope")

        with pytest.raises(PermissionError):
            _D().read("/p")

    assert captured
    entry = captured[-1]
    assert entry.__dict__.get("decision") == "ERROR"
    assert entry.__dict__.get("error_code") == "PermissionError"


def test_audit_soft_failure_when_core_missing() -> None:
    """When ``core.mutations`` isn't importable the decorator must still
    succeed -- providers tree is import-isolated by design."""
    import sys

    original_mutations = sys.modules.get("core.mutations")
    original_tenancy = sys.modules.get("core.tenancy")
    sys.modules.pop("core.mutations", None)
    sys.modules.pop("core.tenancy", None)
    try:
        # Pre-poison: if a previous test inserted fakes, drop them so the
        # ImportError path is exercised.

        class _D:
            @driver_op(
                cloud="aws",
                driver="secrets",
                audit=True,
                sensitive_kind="secret.read",
            )
            def read(self, path: str) -> str:
                return "value"

        result = _D().read("/p")
        assert result == "value"
    finally:
        if original_mutations is not None:
            sys.modules["core.mutations"] = original_mutations
        if original_tenancy is not None:
            sys.modules["core.tenancy"] = original_tenancy


def test_histogram_observes_latency() -> None:
    before_count = _histogram_count("aws", "testdriver", "do_thing")
    _SyncDriver().do_thing(1)
    after_count = _histogram_count("aws", "testdriver", "do_thing")
    assert after_count == before_count + 1


def test_registry_exposes_counter_for_metrics_view() -> None:
    """The counter must live on ``prometheus_client.REGISTRY`` so the
    existing ``backend/config/views.py:metrics_view`` discovers it."""
    found = REGISTRY._names_to_collectors.get("astrolift_provider_driver_op_total")
    assert found is _op_counter


def test_context_heartbeat_sink_is_nested_and_restored_without_temporal():
    from _sdk._telemetry import heartbeat_sink

    seen = []
    with patch("_sdk._telemetry._TEMPORAL_AVAILABLE", False):
        with heartbeat_sink(lambda detail: seen.append(("outer", detail))):
            maybe_heartbeat("before")
            with heartbeat_sink(lambda detail: seen.append(("inner", detail))):
                maybe_heartbeat("during")
            maybe_heartbeat("after")
        maybe_heartbeat("outside")
    assert seen == [("outer", "before"), ("inner", "during"), ("outer", "after")]


@pytest.mark.asyncio
async def test_driver_thread_heartbeat_returns_to_caller_event_loop():
    from _sdk._telemetry import heartbeat_sink

    loop = asyncio.get_running_loop()
    recorded = []
    with heartbeat_sink(lambda detail: loop.call_soon_threadsafe(recorded.append, detail)):
        await asyncio.to_thread(maybe_heartbeat, "from-driver-thread")
    await asyncio.sleep(0)
    assert recorded == ["from-driver-thread"]
