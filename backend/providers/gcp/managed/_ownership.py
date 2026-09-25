"""Adopt an existing GCP resource only when its labels say it is this service's (#1961).

Provision is name-idempotent and names are slug-joined, so another service,
or another org, can map to the same name. Drivers spell their labels several
ways (``astrolift-organization``, ``astrolift-io-organization``,
``astrolift_io_organization``); keys are compared without the ``astrolift``
/ ``io`` prefix and separators, values with ``-`` and ``_`` treated alike.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from _sdk.managed_service import ProvisionSpec

_PREFIX = re.compile(r"^astrolift[-_.]*(?:io[-_.]+)?")


def _key(key: Any) -> str:
    return re.sub(r"[-_.]", "", _PREFIX.sub("", str(key).lower()))


def _value(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")


def label_adoption_refusal(labels: Any, spec: ProvisionSpec, *, resource: str) -> str | None:
    """Why ``provision`` must not adopt a resource with ``labels``, or ``None``.

    Its managed-service id label must be this service's; a resource labeled
    before that label existed is adopted when its organization and app labels
    are this spec's.
    """
    found = {_key(k): _value(v) for k, v in dict(labels or {}).items()}
    owner = found.get("managedserviceid") or found.get("service")  # Valkey spells it astrolift-service
    if owner and spec.managed_service_id:
        if owner == _value(spec.managed_service_id):
            return None
        return f"{resource} already exists and belongs to another managed service; refusing to adopt it"
    if found.get("organization") == _value(spec.organization_slug) and found.get("app") == _value(spec.app_slug):
        return None
    return f"{resource} already exists and is not labeled as this service's; refusing to adopt it"
