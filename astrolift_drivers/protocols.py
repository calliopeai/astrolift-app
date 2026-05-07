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


@runtime_checkable
class ManagedServiceDriver(Protocol):
    async def provision(self, plan: ManagedServiceProvisionPlan) -> ManagedServiceHandle: ...
    async def update(self, handle: ManagedServiceHandle, plan: ManagedServiceProvisionPlan) -> ManagedServiceHandle: ...
    async def deprovision(self, handle: ManagedServiceHandle) -> None: ...


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
