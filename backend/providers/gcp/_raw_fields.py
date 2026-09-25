"""Shared guard for GCP managed-service "raw field" passthrough.

Several managed-service drivers let a config declare provider-native fields
that Astrolift does not model (``raw_fields``, ``clear_fields``, and a few
driver-specific equivalents), guarded against a protected set of fields the
driver itself owns. Google's JSON parser accepts a field's proto name
(``service_account_email``) as well as its lowerCamelCase JSON name
(``serviceAccountEmail``), so a guard that recognizes only one spelling is
bypassed by the other: a raw field spelled in its proto form carries a
protected field past a check that only knows the JSON spelling (#1921, #1947,
#1981).

A raw or cleared field name must always be the API's lowerCamelCase JSON
name, so this refuses any name containing ``_`` outright rather than trying
to enumerate every field's proto spelling, and it compares the rest against
the protected set case-blind.
"""

from __future__ import annotations

from typing import Any


def raw_field_conflicts(names: Any, protected: set[str]) -> tuple[list[str], list[str]]:
    """Split ``names`` into proto-spelled names and protected-name conflicts.

    ``names`` is any iterable of field names, typically a raw-fields dict
    (iterating it yields keys) or a clear-fields list. Returns
    ``(proto_names, forbidden)``, both sorted and deduplicated: ``proto_names``
    are entries spelled with an underscore, which are never a valid raw or
    cleared field name; ``forbidden`` are entries that case-blind match a name
    in ``protected``. A caller should refuse on ``proto_names`` before
    ``forbidden``, since an underscored name never appears in ``protected``
    (that set is always spelled in lowerCamelCase) and would otherwise pass
    the protected check while still reaching the API as a live proto name.
    """
    listed = [str(name) for name in names or ()]
    proto_names = sorted({name for name in listed if "_" in name})
    guarded = {name.casefold() for name in protected}
    forbidden = sorted({name for name in listed if name.casefold() in guarded})
    return proto_names, forbidden
