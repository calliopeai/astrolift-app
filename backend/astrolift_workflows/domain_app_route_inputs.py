"""Pure original-operation identity; no credential or infrastructure payload."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ManagedDomainAppRouteInput:
    operation_id: str
