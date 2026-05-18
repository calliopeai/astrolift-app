"""ObjectStoreDriver protocol -- provision per-app blob buckets and bind credentials."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Bucket:
    name: str
    region: str
    endpoint: str


@dataclass(frozen=True)
class BucketBinding:
    """Credential and endpoint material for a provisioned bucket."""

    env_vars: dict[str, str]
    iam_grants: list[dict[str, Any]]


class ObjectStoreDriver(Protocol):
    """Protocol for provisioning per-app blob storage buckets and binding credentials.

    This covers the infrastructure-level bucket operations. For the
    managed-service-level bucket lifecycle (provision/deprovision as a
    declared service), see ManagedServiceDriver.
    """

    def ensure_bucket(
        self,
        name: str,
        region: str,
        *,
        tags: dict[str, str] | None = None,
    ) -> Bucket: ...

    def delete_bucket(self, name: str, *, force: bool = False) -> None: ...

    def get_binding(self, bucket_name: str, namespace: str) -> BucketBinding: ...
