"""
Object factories for tests.

We deliberately do *not* use ``factory_boy`` here — the platform's
soft-delete + tenant-scoped semantics interact badly with FB's
``DjangoModelFactory`` (which calls ``Model.objects.create``, going
through the scoped manager). Plain helper functions are clearer and
have no surprises.

These factories are added per-tier as the corresponding models land.
P1.T1 fills in ``user``, ``organization``, ``team``, ``project``;
P1.T2 adds ``role`` and ``role_binding``; etc.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_seq: dict[str, int] = {}


def seq(prefix: str = "") -> str:
    """Return a unique integer-suffixed token within the test process.

    Useful for generating per-test slugs without collisions.
    """
    _seq[prefix] = _seq.get(prefix, 0) + 1
    return f"{prefix}{_seq[prefix]}"


def reset_sequences() -> None:
    _seq.clear()


# Registry of factory callables, populated by sub-modules so tests can
# write ``factories.organization()`` once everything is wired in.
_registry: dict[str, Callable[..., Any]] = {}


def register(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        _registry[name] = fn
        return fn

    return deco


def make(name: str, **overrides: Any) -> Any:
    if name not in _registry:
        raise LookupError(f"factory '{name}' is not registered")
    return _registry[name](**overrides)
