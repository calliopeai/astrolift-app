"""Read the object key out of a resolver's bound arguments (#1731).

``require_permission(scope=...)`` hands the factory the resolver's
arguments already bound to its signature. Most resolvers name their
target directly (``app_slug``), but every mutation in the codebase takes
a single strawberry input object instead (``input.app_slug``), so the
factories accept a dotted path and this walks it -- mapping keys first,
then attributes, so the same factory serves both shapes.

A path that does not resolve returns ``None``, and every factory turns
that into "no scope", leaving the stricter org-scope check standing. A
key the caller cannot be trusted about must never open a door.
"""

from __future__ import annotations

from typing import Any

_UNSET = object()


def read_arg(args: dict[str, Any], path: str) -> Any | None:
    """Follow a dotted ``path`` through bound arguments.

    ``"app_slug"`` reads one argument; ``"input.app_slug"`` reads a field
    off the input object. Missing segments, ``None`` and strawberry's
    ``UNSET`` sentinel all read as ``None``.
    """
    current: Any = args
    for segment in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(segment, _UNSET)
        else:
            current = getattr(current, segment, _UNSET)
        if current is _UNSET:
            return None
    if current is None:
        return None
    # strawberry.UNSET is falsy and not None; an omitted optional field
    # must read as absent rather than as the string "UNSET".
    if type(current).__name__ == "UnsetType":
        return None
    return current
