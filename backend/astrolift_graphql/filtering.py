"""Declared filters for the list contract (spec 44 §5.1, #2149).

A list query takes one ``filter`` input whose fields are the filters the
list declares. Most of them are "this column is one of these values", so
a list declares them as a table and ``filter_q`` turns whatever the client
set into one ``Q``:

    APP_FILTERS = {
        "project": FilterField("project__slug"),
        "cluster": FilterField(q=lambda slugs: Q(pk__in=...)),
    }
    qs = qs.filter(filter_q(filter, APP_FILTERS))

Rules, the same on every list:

* An unset field (``None``, strawberry ``UNSET``, an empty list) does not
  filter. Filters combine with AND; the values of one list field with OR.
* A list value becomes ``__in``; a scalar becomes an exact match. Enum
  members are unwrapped to their ``.value``.
* ``me`` is the viewer. A field declared ``me=True`` swaps the literal
  ``"me"`` for the ``me`` the resolver passes (the viewer's pk); with no
  viewer it matches nothing rather than everything.
* Input fields missing from the table are the resolver's own to apply
  (a tri-state like ``archived``, or one that needs an annotation first).
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django.db.models import Q
from strawberry import UNSET

__all__ = ["FilterField", "filter_q", "filter_values"]


@dataclass(frozen=True)
class FilterField:
    """One declared filter: an ORM path, or a function of the value to a ``Q``."""

    lookup: str | None = None
    q: Callable[[Any], Q] | None = None
    me: bool = False


def filter_values(filter_input: Any) -> dict[str, Any]:
    """The fields a client actually set, from a strawberry input or a mapping."""
    if filter_input is None:
        return {}
    if isinstance(filter_input, Mapping):
        raw = dict(filter_input)
    else:
        raw = {f.name: getattr(filter_input, f.name) for f in dataclasses.fields(filter_input)}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None or value is UNSET:
            continue
        if isinstance(value, list | tuple | set | frozenset):
            if not value:
                continue
            value = [v.value if isinstance(v, enum.Enum) else v for v in value]
        elif isinstance(value, enum.Enum):
            value = value.value
        out[key] = value
    return out


def _resolve_me(value: Any, me: Any) -> Any:
    if isinstance(value, list):
        return [me if v == "me" else v for v in value]
    return me if value == "me" else value


def filter_q(filter_input: Any, fields: Mapping[str, FilterField], *, me: Any = None) -> Q:
    """AND of every declared filter the client set. See the module docstring."""
    query = Q()
    for key, value in filter_values(filter_input).items():
        field = fields.get(key)
        if field is None:
            continue
        if field.me:
            if me is None and (value == "me" or (isinstance(value, list) and "me" in value)):
                query &= Q(pk__in=[])
                continue
            value = _resolve_me(value, me)
        if field.q is not None:
            query &= field.q(value)
        elif isinstance(value, list):
            query &= Q(**{f"{field.lookup}__in": value})
        else:
            query &= Q(**{field.lookup: value})
    return query
