"""Names for new managed resources; recorded handles remain authoritative locators."""

from __future__ import annotations

import hashlib
import re
from uuid import UUID


def managed_service_identity(value: str) -> UUID:
    try:
        identity = UUID(value) if isinstance(value, str) else None
    except (ValueError, AttributeError):
        identity = None
    if identity is None or not identity.int or str(identity) != value:
        raise ValueError("managed-service identity must be a persisted canonical nonzero UUID")
    return identity


def physical_name(value: str, *, prefix: str, max_length: int, separator: str = "-") -> str:
    """Retain the UUID when it fits; compact provider limits hash the whole UUID.

    The narrow form retains at least 80 bits of SHA-256 identity. Human slugs
    never enter either identity suffix, so joining or truncating slugs cannot
    merge two services. Existing ownership checks still govern resource access.
    """
    identity = managed_service_identity(value)
    if separator not in ("-", "_", "") or not isinstance(prefix, str) or max_length < 22:
        raise ValueError("physical-name constraints must retain a prefix and at least 80 identity bits")
    cleaned = re.sub(r"[^a-z0-9]+", "-", prefix.lower()).strip("-")
    cleaned = cleaned.replace("-", separator) or "service"
    if not cleaned[0].isalpha():
        cleaned = "s" + separator + cleaned
    suffix = identity.hex if max_length >= 33 + len(separator) else hashlib.sha256(identity.bytes).hexdigest()[:20]
    budget = max_length - len(suffix) - len(separator)
    cosmetic = cleaned[:budget].rstrip("-_") or "s"
    return cosmetic + separator + suffix


def recorded_resource_name(handle: str, *, kind: str) -> str | None:
    """Decode only the simple kind/name shape, without authorizing its owner."""
    if not handle:
        return None
    recorded_kind, separator, name = handle.partition("/")
    if recorded_kind != kind or not separator or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None:
        raise ValueError("recorded managed-service handle does not match the driver target")
    return name
