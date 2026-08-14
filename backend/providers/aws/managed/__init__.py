"""AWS managed-service drivers (#35).

Per (kind, variant) driver implementations registered via the
PLUGIN manifest's `managed_service_drivers` map.

The provider plugin manifest is the source of truth for shipped variants; keep
individual lifecycle implementations isolated in this package.
"""

from aws.managed._base import (
    ManagedServiceError,
    handle_for,
    parse_handle,
    tags_for,
)

__all__ = [
    "ManagedServiceError",
    "handle_for",
    "parse_handle",
    "tags_for",
]
