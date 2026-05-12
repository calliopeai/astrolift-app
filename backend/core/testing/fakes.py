"""
``FakeDriver`` base for provider unit tests (#188).

Provider plugins (AWS, GCP, Azure, k8s) implement a small set of
typed driver protocols (``ClusterDriver``, ``DnsDriver``,
``TlsDriver``, ``ManagedServiceDriver``, ``SecretsDriver``,
``IngressDriver``…). FakeDriver records every call into an in-memory
event log and lets the test queue per-method responses or inject
errors so unit tests can drive control-plane logic without standing
up real cloud resources.

Spy + stub combined. Three things every test needs:

* ``record_call(method, *args, **kwargs)`` — log the call so the
  test can assert sequence + args afterwards.
* ``set_responses(method, [...])`` — pop one return value per call
  to that method, in order. Used for happy-path stubs.
* ``set_error(method, exc)`` — make every subsequent call to that
  method raise ``exc``. Used for failure-injection tests.

Subclasses implement only the protocol methods their tests exercise;
unhandled methods raise :class:`UnimplementedFakeMethod` so the test
author sees a clear failure rather than an opaque ``AttributeError``.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict, deque
from collections.abc import Iterable
from typing import Any

DEFAULT = object()


class UnimplementedFakeMethod(NotImplementedError):
    """Raised when test code calls a FakeDriver method we haven't stubbed."""


@dataclasses.dataclass(slots=True)
class DriverCall:
    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class FakeDriver:
    """In-memory base for all provider fakes.

    Subclasses override the methods they need. Each override calls
    ``self.record_call(...)`` and then either returns
    ``self.next_response(method)`` or its own constant. Tests assert
    against ``self.calls``::

        fake = FakeClusterDriver()
        fake.set_responses("apply", [None])
        await deploy(fake, ...)
        assert fake.calls[-1].method == "apply"
    """

    def __init__(self) -> None:
        self.calls: list[DriverCall] = []
        self._responses: dict[str, deque[Any]] = defaultdict(deque)
        self._errors: dict[str, BaseException] = {}

    # -- spy ------------------------------------------------------------

    def record_call(self, method: str, *args: Any, **kwargs: Any) -> None:
        """Log the call. If an error is registered for the method,
        raise it now — keeps subclass implementations a single line:
        ``self.record_call("apply", cluster, objects)`` is enough."""
        self.calls.append(DriverCall(method=method, args=args, kwargs=kwargs))
        if method in self._errors:
            raise self._errors[method]

    def reset(self) -> None:
        self.calls.clear()
        self._responses.clear()
        self._errors.clear()

    # -- stub -----------------------------------------------------------

    def set_responses(self, method: str, responses: Iterable[Any]) -> None:
        """Queue return values for ``method``, popped in order. A test
        that calls ``method`` more times than there are responses will
        get ``DEFAULT`` (caller decides what that means) — fail-loud
        is left to ``next_response(method, required=True)``."""
        self._responses[method] = deque(responses)

    def set_error(self, method: str, exc: BaseException) -> None:
        """Make every call to ``method`` raise ``exc`` from
        ``record_call()``. Clears with ``reset()``."""
        self._errors[method] = exc

    def next_response(self, method: str, *, required: bool = False) -> Any:
        """Pop the next queued response for ``method``. With
        ``required=True``, an empty queue raises (catches tests that
        forgot to ``set_responses``); otherwise returns ``DEFAULT``."""
        queue = self._responses.get(method)
        if queue:
            return queue.popleft()
        if required:
            raise UnimplementedFakeMethod(f"{type(self).__name__}.{method}() called but no response queued")
        return DEFAULT

    # -- assertions -----------------------------------------------------

    def assert_called(self, method: str, *, times: int | None = None) -> list[DriverCall]:
        matching = [c for c in self.calls if c.method == method]
        if times is not None and len(matching) != times:
            raise AssertionError(f"expected {method} to be called {times} times, got {len(matching)}")
        if times is None and not matching:
            raise AssertionError(f"expected {method} to be called at least once")
        return matching

    def assert_not_called(self, method: str) -> None:
        matching = [c for c in self.calls if c.method == method]
        if matching:
            raise AssertionError(f"expected {method} not to be called, got {len(matching)} call(s)")

    # -- fall-through ---------------------------------------------------

    def __getattr__(self, name: str):
        # Concrete methods on the subclass go through normal attribute
        # lookup; this only fires for protocol methods the subclass
        # didn't bother stubbing. The error tells the author exactly
        # what's missing.
        if name.startswith("_"):
            raise AttributeError(name)

        def _missing(*args, **kwargs):
            raise UnimplementedFakeMethod(f"{type(self).__name__}.{name}() is not stubbed in this test")

        return _missing


# ---- concrete fakes for the most-used drivers -------------------------
#
# These are intentionally minimal — they cover the shape of the protocol
# methods we already use in workflows/activities. Tests can subclass
# them further or stub additional methods inline.


class FakeClusterDriver(FakeDriver):
    """Fake of :class:`astrolift_drivers.protocols.ClusterDriver`."""

    async def apply(self, cluster, objects):
        objs = list(objects)
        self.record_call("apply", cluster=cluster, objects=objs)
        return self.next_response("apply")

    async def delete(self, cluster, refs):
        refs = list(refs)
        self.record_call("delete", cluster=cluster, refs=refs)
        return self.next_response("delete")

    async def get(self, cluster, ref):
        self.record_call("get", cluster=cluster, ref=ref)
        out = self.next_response("get")
        return None if out is DEFAULT else out

    async def probe_capabilities(self, cluster):
        self.record_call("probe_capabilities", cluster=cluster)
        out = self.next_response("probe_capabilities")
        return {} if out is DEFAULT else out


class FakeDnsDriver(FakeDriver):
    """Fake of :class:`astrolift_drivers.protocols.DnsDriver`."""

    async def upsert(self, record):
        self.record_call("upsert", record=record)
        return self.next_response("upsert")

    async def delete(self, record):
        self.record_call("delete", record=record)
        return self.next_response("delete")

    async def list(self, zone):
        self.record_call("list", zone=zone)
        out = self.next_response("list")
        return [] if out is DEFAULT else out


class FakeManagedServiceDriver(FakeDriver):
    """Fake of :class:`astrolift_drivers.protocols.ManagedServiceDriver`."""

    async def provision(self, plan):
        self.record_call("provision", plan=plan)
        return self.next_response("provision", required=True)

    async def update(self, handle, plan):
        self.record_call("update", handle=handle, plan=plan)
        return self.next_response("update", required=True)

    async def deprovision(self, handle):
        self.record_call("deprovision", handle=handle)
        return self.next_response("deprovision")
