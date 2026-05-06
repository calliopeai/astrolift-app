"""ManagedServiceDriver protocol -- provision backing services and emit binding env vars."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ProvisionSpec:
    """Normalized request to provision a managed service."""

    organization_id: str
    organization_slug: str
    app_id: str
    app_slug: str
    environment_id: str
    environment_name: str
    tenant_cluster_id: str
    service_handle_hint: str
    size: str  # small | medium | large | xlarge | custom
    config: dict[str, Any] = field(default_factory=dict)
    desired_extensions: list[str] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)
    isolation: str = "shared"  # shared | dedicated


@dataclass(frozen=True)
class ProvisionResult:
    ok: bool
    handle: str
    message: str
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class UpdateSpec:
    handle: str
    size: str | None = None
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UpdateResult:
    ok: bool
    handle: str
    message: str
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DeprovisionSpec:
    handle: str


@dataclass(frozen=True)
class DeprovisionResult:
    ok: bool
    handle: str
    message: str
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ServiceHandle:
    handle: str


@dataclass(frozen=True)
class ServiceStatus:
    handle: str
    state: str  # provisioning | available | updating | error | deprovisioning | deprovisioned
    message: str


@dataclass(frozen=True)
class ValueRef:
    """A value that is either a literal or a reference to a secret."""

    literal: str | None = None
    secret_ref: str | None = None


@dataclass(frozen=True)
class VolumeMount:
    name: str
    mount_path: str
    sub_path: str | None = None


@dataclass(frozen=True)
class Grant:
    resource: str
    actions: list[str]


@dataclass(frozen=True)
class Binding:
    """Connection material for a provisioned managed service."""

    env_vars: dict[str, ValueRef]
    pod_volume_mounts: list[VolumeMount] = field(default_factory=list)
    iam_grants: list[Grant] = field(default_factory=list)
    notes: str = ""


@dataclass(frozen=True)
class SnapshotHandle:
    handle: str
    snapshot_id: str
    created_at: str


@dataclass(frozen=True)
class BindingSchema:
    """Describes what env vars a driver produces."""

    env_vars: dict[str, str]  # key -> description


class ManagedServiceDriver(Protocol):
    """Protocol for provisioning and managing a backing service of a given kind.

    Each (kind, variant) tuple maps to a separate driver implementation.
    The driver is responsible for the full lifecycle: provision, update,
    deprovision, snapshot, restore.

    All lifecycle methods are idempotent. Provisioning probes existing
    resources and reconciles state.
    """

    def provision(self, spec: ProvisionSpec) -> ProvisionResult: ...

    def update(self, spec: UpdateSpec) -> UpdateResult: ...

    def deprovision(self, spec: DeprovisionSpec, *, delete_data: bool = False) -> DeprovisionResult: ...

    def status(self, handle: ServiceHandle) -> ServiceStatus: ...

    def binding(self, handle: ServiceHandle) -> Binding: ...

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle: ...

    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult: ...

    def config_schema(self) -> dict[str, Any]: ...

    def binding_schema(self) -> BindingSchema: ...
