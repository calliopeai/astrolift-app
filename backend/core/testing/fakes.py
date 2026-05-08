"""
``FakeDriver`` base for provider unit tests.

Provider plugins (AWS, GCP, Azure, k8s) implement a small set of
typed driver protocols (``ClusterDriver``, ``DnsDriver``,
``TlsDriver``, ``ManagedServiceDriver``, ``SecretsDriver``,
``IngressDriver``…). The FakeDriver records every call into an
in-memory event log so unit tests can assert behavior without standing
up real cloud resources.

The FakeDriver protocol is intentionally permissive — sub-classes
implement only the methods their test exercises. Anything else either:

* returns a ``DEFAULT`` sentinel (caller decides), or
* raises :class:`UnimplementedFakeMethod` so the test author sees a
  clear failure rather than an opaque ``AttributeError``.
"""

from __future__ import annotations

import dataclasses
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

    Sub-classes override individual methods. The ``calls`` list records
    every method that was invoked, in order, so tests can assert::

        assert fake.calls[-1].method == "ensure_namespace"

    """

    def __init__(self) -> None:
        self.calls: list[DriverCall] = []

    def _record(self, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self.calls.append(DriverCall(method=method, args=args, kwargs=kwargs))

    def __getattr__(self, name: str):
        # Methods declared on the subclass land via normal attribute
        # lookup; this only fires for unknown ones.
        if name.startswith("_"):
            raise AttributeError(name)

        def _missing(*args, **kwargs):
            raise UnimplementedFakeMethod(f"{type(self).__name__}.{name}() is not stubbed in this test")

        return _missing

    def reset(self) -> None:
        self.calls.clear()

    def assert_called(self, method: str, *, times: int | None = None) -> list[DriverCall]:
        matching = [c for c in self.calls if c.method == method]
        if times is not None and len(matching) != times:
            raise AssertionError(f"expected {method} to be called {times} times, got {len(matching)}")
        if times is None and not matching:
            raise AssertionError(f"expected {method} to be called at least once")
        return matching
