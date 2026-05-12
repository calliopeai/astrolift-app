"""
Typed driver protocols.

Each Protocol mirrors one entry from the driver catalog in spec §02.2.
Methods are defined with the minimum surface area the control plane
needs; provider plugins are free to add private helpers but the
public contract is exactly what's here.

We split the catalog into many small Protocols (instead of one big
"plugin object") so partial implementations type-check cleanly: a k8s
plugin only exposes ``ClusterDriver``, ``IngressDriver``,
``LogStreamDriver``, and ``WorkloadIdentityDriver`` and that's fine.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import AsyncIterator, Iterable
from typing import Any, Protocol, runtime_checkable

# ---- value types -----------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ClusterRef:
    cluster_id: int
    name: str
    region: str
    endpoint: str


@dataclasses.dataclass(frozen=True, slots=True)
class K8sObjectRef:
    api_version: str
    kind: str
    name: str
    namespace: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class DnsRecord:
    zone: str
    name: str
    rrtype: str
    values: tuple[str, ...]
    ttl: int = 60


class CertificateState(str, enum.Enum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


@dataclasses.dataclass(frozen=True, slots=True)
class CertificateHandle:
    handle_id: str
    state: CertificateState
    not_before: str | None = None
    not_after: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class SecretRef:
    path: str  # backend-specific path / id


@dataclasses.dataclass(frozen=True, slots=True)
class ManagedServiceProvisionPlan:
    kind: str
    variant: str
    config: dict[str, Any]


@dataclasses.dataclass(frozen=True, slots=True)
class ManagedServiceHandle:
    external_id: str
    connection_secret_ref: SecretRef
    bindings: dict[str, str]


# ---- protocols -------------------------------------------------------


@runtime_checkable
class ClusterDriver(Protocol):
    """Apply / get / delete Kubernetes objects in a target cluster."""

    async def apply(self, cluster: ClusterRef, objects: Iterable[dict[str, Any]]) -> None: ...
    async def delete(self, cluster: ClusterRef, refs: Iterable[K8sObjectRef]) -> None: ...
    async def get(self, cluster: ClusterRef, ref: K8sObjectRef) -> dict[str, Any] | None: ...
    async def probe_capabilities(self, cluster: ClusterRef) -> dict[str, Any]: ...


@runtime_checkable
class IngressDriver(Protocol):
    """Render ingress objects for the cluster's controller."""

    def render(
        self,
        *,
        hostname: str,
        service_name: str,
        service_port: int,
        annotations: dict[str, str] | None = None,
        tls_secret: str | None = None,
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class DnsDriver(Protocol):
    async def upsert(self, record: DnsRecord) -> None: ...
    async def delete(self, record: DnsRecord) -> None: ...
    async def list(self, zone: str) -> list[DnsRecord]: ...


@runtime_checkable
class TlsDriver(Protocol):
    async def issue(self, *, hostnames: list[str], context: dict[str, Any]) -> CertificateHandle: ...
    async def status(self, handle: CertificateHandle) -> CertificateHandle: ...
    async def revoke(self, handle: CertificateHandle) -> None: ...


@runtime_checkable
class SecretsBackend(Protocol):
    async def read(self, ref: SecretRef) -> dict[str, str]: ...
    async def write(self, ref: SecretRef, values: dict[str, str]) -> None: ...
    async def delete(self, ref: SecretRef) -> None: ...
    async def list(self, prefix: str) -> list[SecretRef]: ...


@runtime_checkable
class WorkloadIdentityDriver(Protocol):
    async def bind_service_account(
        self,
        cluster: ClusterRef,
        *,
        namespace: str,
        service_account: str,
        cloud_role_id: str,
    ) -> None: ...

    async def unbind_service_account(
        self,
        cluster: ClusterRef,
        *,
        namespace: str,
        service_account: str,
    ) -> None: ...


@runtime_checkable
class ImageRegistryDriver(Protocol):
    async def ensure_repo(self, *, owner: str, app: str, workload: str) -> str: ...
    async def issue_pull_credential(self, *, repo_uri: str) -> SecretRef: ...
    async def issue_push_credential(self, *, repo_uri: str) -> SecretRef: ...


@runtime_checkable
class ObjectStoreDriver(Protocol):
    async def ensure_bucket(self, *, owner: str, app: str, name: str) -> dict[str, str]: ...
    async def issue_credentials(self, *, bucket: str) -> SecretRef: ...
    async def delete_bucket(self, *, bucket: str) -> None: ...


@dataclasses.dataclass(frozen=True, slots=True)
class ProvisionSpec:
    """Spec 11 §2 provision input. ``isolation`` is one of
    ``"shared"`` / ``"dedicated"`` (mirrors astrolift_drivers.isolation
    Isolation enum but kept as a string here so the protocol module
    doesn't pull in the policy module)."""

    organization: str
    app: str
    environment: str
    kind: str
    size: str
    config: dict[str, Any]
    isolation: str
    tags: dict[str, str] = dataclasses.field(default_factory=dict)
    service_handle_hint: str = ""
    desired_extensions: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class ServiceStatus:
    """Result of ``status(handle)``."""

    handle: ManagedServiceHandle
    state: str  # 'provisioning' | 'ready' | 'updating' | 'failed' | 'deprovisioning' | 'gone'
    message: str = ""
    last_observed_at: str = ""  # ISO-8601


@dataclasses.dataclass(frozen=True, slots=True)
class Binding:
    """Spec 11 §2 binding output: what a workload sees and what
    cloud-side grants it needs."""

    env_vars: dict[str, str]
    """Concrete values OR ``secret_ref://...`` placeholders the
    secret materializer resolves at pod start."""

    pod_volume_mounts: tuple[dict[str, str], ...] = ()
    """For NFS-shaped services."""

    iam_grants: tuple[dict[str, Any], ...] = ()
    """For object-store-shaped services — list of grants the workload
    identity needs (e.g. ``s3:GetObject`` on this bucket)."""

    notes: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class SnapshotHandle:
    snapshot_id: str
    handle: ManagedServiceHandle
    created_at: str = ""


@runtime_checkable
class ManagedServiceDriver(Protocol):
    """Spec 11 §2 driver interface. Plugins implement this; the
    activity layer calls into it.

    ``provision`` and ``update`` are idempotent — re-running with the
    same spec is a no-op. ``deprovision`` carries an explicit
    ``delete_data`` flag so a dropped binding can preserve the
    backing data (per spec 11 §2; recovery from accidental detach).

    ``snapshot`` / ``restore`` are optional — drivers raise
    ``NotImplementedError`` if the underlying service has no
    snapshot semantics. The activity catches that and records a
    'snapshot unsupported' event without failing the deploy.
    """

    async def provision(self, spec: ProvisionSpec) -> ManagedServiceHandle: ...
    async def update(self, handle: ManagedServiceHandle, spec: ProvisionSpec) -> ManagedServiceHandle: ...
    async def deprovision(self, handle: ManagedServiceHandle, *, delete_data: bool) -> None: ...

    async def status(self, handle: ManagedServiceHandle) -> ServiceStatus: ...
    async def binding(self, handle: ManagedServiceHandle) -> Binding: ...

    async def snapshot(self, handle: ManagedServiceHandle) -> SnapshotHandle: ...
    async def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ManagedServiceHandle: ...

    def config_schema(self) -> dict[str, Any]:
        """JSON Schema describing ``ProvisionSpec.config`` shape this
        driver accepts."""

    def binding_schema(self) -> dict[str, Any]:
        """JSON Schema describing the ``Binding.env_vars`` shape this
        driver produces. Mirrors the env_injection.py envelope for
        the kind, but per-driver may add extras."""


@runtime_checkable
class LogStreamDriver(Protocol):
    async def stream(
        self,
        cluster: ClusterRef,
        *,
        namespace: str,
        pod: str,
        container: str | None = None,
        tail_lines: int = 100,
    ) -> AsyncIterator[str]: ...


@runtime_checkable
class MetricsDriver(Protocol):
    async def query(
        self,
        cluster: ClusterRef,
        *,
        promql: str,
        start_unix: int,
        end_unix: int,
        step_seconds: int = 60,
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class TraceDriver(Protocol):
    async def search(self, *, service: str, query: str, limit: int = 50) -> list[dict[str, Any]]: ...
    async def get_trace(self, trace_id: str) -> dict[str, Any] | None: ...


@runtime_checkable
class EventDriver(Protocol):
    async def stream(
        self,
        cluster: ClusterRef,
        *,
        namespace: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]: ...


@runtime_checkable
class BuildDriver(Protocol):
    async def build(
        self,
        *,
        source_url: str,
        context_path: str,
        dockerfile_path: str,
        image_tag: str,
    ) -> str: ...
