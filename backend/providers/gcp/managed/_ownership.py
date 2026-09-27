"""Adopt an existing GCP resource only when its labels say it is this service's (#1961).

Provision is name-idempotent and names are slug-joined, so another service,
or another org, can map to the same name. Drivers spell their labels several
ways (``astrolift-organization``, ``astrolift-io-organization``,
``astrolift_io_organization``); keys are compared without the ``astrolift``
/ ``io`` prefix and separators, values with ``-`` and ``_`` treated alike.

Every one of those spellings starts with ``astrolift`` (one legacy
managed-service-id spelling with ``x-astrolift``), so that namespace is the
platform's: a tenant may not set a label in it, and a label outside it is never
read as ownership (#2098).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from _sdk.managed_service import ProvisionSpec

_PREFIX = re.compile(r"^astrolift[-_.]*(?:io[-_.]+)?")
_PLATFORM_NAMESPACE = re.compile(r"^x?astrolift")


def _key(key: Any) -> str:
    text = str(key).lower()
    # A key outside the platform's namespace is a tenant's, whatever it says:
    # ``managed-service-id`` or ``organization`` must not read as ownership.
    if not _PREFIX.match(text):
        return ""
    return re.sub(r"[-_.]", "", _PREFIX.sub("", text))


def _value(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")


def is_platform_label_key(key: Any) -> bool:
    """Whether ``key`` is in the platform's label namespace, however it is cased or separated."""
    return bool(_PLATFORM_NAMESPACE.match(re.sub(r"[^a-z0-9]", "", str(key).lower())))


def reserved_label_keys(labels: Any) -> list[str]:
    """The keys of tenant ``labels`` that only the platform may set (#2098).

    Ownership, prune and teardown decide on platform labels, and a tenant label
    applied over one let a service claim another's id. Every spelling is
    reserved, not only the ones a given driver writes, because drivers read
    each other's (``label_adoption_refusal`` accepts any of them).
    """
    if not isinstance(labels, Mapping):
        return []
    return sorted(str(key) for key in labels if is_platform_label_key(key))


def managed_service_ids(labels: Any) -> set[str]:
    """Every managed-service id ``labels`` carry, under any platform spelling."""
    if not isinstance(labels, Mapping):
        return set()
    return {
        _value(value)
        for key, value in labels.items()
        if _key(key) in {"managedserviceid", "service"} and _value(value)  # Valkey spells it astrolift-service
    }


def is_marked_for(labels: Any, managed_service_id: str) -> bool:
    """Whether ``labels`` name this managed service, under every spelling they carry, and no other."""
    return bool(managed_service_id) and managed_service_ids(labels) == {_value(managed_service_id)}


def label_identity_refusal(labels: Any, managed_service_id: str, *, record_proves: bool, resource: str) -> str:
    """Why this service may not act on an Astrolift-made resource with ``labels``, or ``""`` (#2098).

    The managed-service id label decides whenever there is one, and every
    spelling of it must name this service. A resource with none predates the
    label, and only the platform's exclusive record of its handle says whose it
    is (#2086).
    """
    if not managed_service_id:
        return f"{resource} cannot be checked: the managed-service id is missing"
    owners = managed_service_ids(labels)
    if owners == {_value(managed_service_id)}:
        return ""
    if owners:
        return f"{resource} belongs to another managed service"
    if record_proves:
        return ""
    return (
        f"{resource} carries no managed-service id, and no exclusive platform record says it is this "
        "service's; an operator must mark its owner"
    )


def label_adoption_refusal(labels: Any, spec: ProvisionSpec, *, resource: str) -> str | None:
    """Why ``provision`` must not adopt a resource with ``labels``, or ``None``.

    Its managed-service id label must be this service's; a resource labeled
    before that label existed is adopted when its organization and app labels
    are this spec's. Every spelling of a label must agree.
    """
    labels = dict(labels or {})
    found: dict[str, set[str]] = {}
    for key, value in labels.items():
        name = _key(key)
        if name:
            found.setdefault(name, set()).add(_value(value))
    owners = managed_service_ids(labels)
    if owners and spec.managed_service_id:
        if owners == {_value(spec.managed_service_id)}:
            return None
        return f"{resource} already exists and belongs to another managed service; refusing to adopt it"
    if found.get("organization") == {_value(spec.organization_slug)} and found.get("app") == {_value(spec.app_slug)}:
        return None
    return f"{resource} already exists and is not labeled as this service's; refusing to adopt it"
