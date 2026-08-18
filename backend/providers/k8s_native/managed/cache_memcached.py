"""In-cluster Memcached for the cloud-neutral ``cache`` kind (#1465).

``cache`` was executable on AWS only. GCP sells Memorystore for Memcached and
Azure sells nothing equivalent, so one in-cluster variant reaches both clouds
at once instead of one of them.

There is no widely deployed Memcached operator, and there is nothing for one to
reconcile: Memcached holds no durable state, has no clustering protocol, and
elects no leader. The driver therefore owns a StatefulSet, its governing
headless Service, and an ingress NetworkPolicy directly.

The StatefulSet (rather than a Deployment) is what makes the binding portable.
Memcached shards client-side over a fixed node list, which is what
``CACHE_NODES`` carries on AWS; stable per-pod DNS is the only way to hand an
app the same shape here.

Variant key: ``('cache', 'memcached')``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from _sdk._telemetry import driver_op
from _sdk.k8s_naming import agent_namespace, app_namespace, dns_label
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    ManagedServiceDriver,
    ProvisionResult,
    ProvisionSpec,
    ServiceHandle,
    ServiceStatus,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
    ValueRef,
    unsupported_update,
)
from k8s_native.managed._handle import pack as _pack_handle
from k8s_native.managed._handle import unpack as _unpack_handle

if TYPE_CHECKING:
    from k8s_native.managed._handle import ParsedHandle

KIND = "cache"
PORT = 11211
DEFAULT_IMAGE = "memcached:1.6-alpine"

_STATEFULSET = "apps/v1/StatefulSet"

#: The uid the upstream ``memcached`` image creates and drops to.
_RUN_AS_USER = 11211

_MAX_REPLICAS = 20
_MIN_MEMORY_MB = 64
_MAX_MEMORY_MB = 65536
_MIN_CONNECTIONS = 64
_MAX_CONNECTIONS = 65536

#: ``memory_limit`` is deliberately above ``max_memory_mb``: ``-m`` caps the
#: slab allocator only, and connection buffers plus per-item overhead live
#: outside it. A limit equal to ``-m`` gets the pod OOM-killed under load.
SIZE_TO_SPEC: dict[str, dict[str, Any]] = {
    "small": {
        "replicas": 1,
        "max_memory_mb": 256,
        "cpu_request": "100m",
        "memory_limit": "384Mi",
    },
    "medium": {
        "replicas": 2,
        "max_memory_mb": 1024,
        "cpu_request": "250m",
        "memory_limit": "1280Mi",
    },
    "large": {
        "replicas": 3,
        "max_memory_mb": 4096,
        "cpu_request": "500m",
        "memory_limit": "5Gi",
    },
    "xlarge": {
        "replicas": 3,
        "max_memory_mb": 16384,
        "cpu_request": "1",
        "memory_limit": "20Gi",
    },
}

DEFAULT_MAX_CONNECTIONS = 1024


@dataclass(frozen=True)
class MemcachedConfig:
    cluster_driver: Any | None = None
    image: str = DEFAULT_IMAGE


class MemcachedDriver(ManagedServiceDriver):
    def __init__(self, *, config: MemcachedConfig | None = None) -> None:
        self._config = config or MemcachedConfig()

    @driver_op(
        cloud="k8s_native",
        driver="cache_memcached",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_memcached_config"])
        name = self._cache_name(spec=spec)
        namespace = app_namespace(
            organization_slug=spec.organization_slug,
            app_slug=spec.app_slug,
        )
        handle = _pack_handle(
            kind=KIND,
            cluster_id=spec.tenant_cluster_id or "render-only",
            namespace=namespace,
            name=name,
        )
        manifests = self._render(spec=spec, name=name, namespace=namespace)
        if self._config.cluster_driver is None:
            return ProvisionResult(True, handle, "Memcached manifests rendered")
        result = self._config.cluster_driver.apply_manifests(
            spec.tenant_cluster_id,
            namespace,
            manifests,
        )
        if not result.ok:
            return ProvisionResult(
                False,
                "",
                f"failed to apply Memcached: {result.summary()}",
                result.summary(),
            )
        return ProvisionResult(True, handle, f"Memcached {name} applied")

    @driver_op(cloud="k8s_native", driver="cache_memcached")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        return unsupported_update(
            spec.handle,
            "resizing a Memcached ring rehashes every key, so the change goes through a reprovision",
        )

    @driver_op(
        cloud="k8s_native",
        driver="cache_memcached",
        audit=True,
        sensitive_kind="managed_service_deprovision",
    )
    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        # Memcached is RAM-only, so there is no volume to keep and nothing for
        # delete_data to protect.
        del delete_data, force_destroy
        if self._config.cluster_driver is None:
            return DeprovisionResult(True, spec.handle, "render-only mode")
        parsed = _unpack_handle(spec.handle)
        if parsed.is_legacy:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    "legacy 2-segment handle cannot be deprovisioned: "
                    "re-provision to refresh the handle, or pass a "
                    "4-segment handle (<kind>/<cluster>/<ns>/<name>)"
                ),
                errors=["legacy_handle_missing_locator"],
                retryable=False,
            )
        stubs = [
            _stub("apps/v1", "StatefulSet", parsed.name, parsed.namespace),
            _stub("v1", "Service", parsed.name, parsed.namespace),
            _stub("networking.k8s.io/v1", "NetworkPolicy", _policy_name(parsed.name), parsed.namespace),
        ]
        result = self._config.cluster_driver.delete_manifests(
            parsed.cluster_id,
            parsed.namespace,
            stubs,
        )
        if not result.ok:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"could not delete Memcached {parsed.name}",
                errors=result.summary(),
            )
        return DeprovisionResult(True, spec.handle, f"Memcached {parsed.name} deleted")

    @driver_op(cloud="k8s_native", driver="cache_memcached")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        if self._config.cluster_driver is None:
            return ServiceStatus(handle.handle, "provisioning", "render-only mode: no cluster to query")
        parsed = _unpack_handle(handle.handle)
        if parsed.is_legacy:
            return ServiceStatus(handle.handle, "error", "legacy Memcached handle has no cluster locator")
        workload = self._config.cluster_driver.get_manifest(
            parsed.cluster_id,
            parsed.namespace,
            _STATEFULSET,
            parsed.name,
        )
        if workload is None:
            return ServiceStatus(handle.handle, "deprovisioned", f"Memcached {parsed.name} not found")
        desired = int(dict(workload.get("spec") or {}).get("replicas", 0))
        ready = int(dict(workload.get("status") or {}).get("readyReplicas", 0))
        detail = f"{ready}/{desired} Memcached pods ready"
        # A partial ring still serves every key, just with a lower hit rate, so
        # the binding is only correct once every node the client will hash to
        # is actually up.
        if desired and ready >= desired:
            return ServiceStatus(handle.handle, "available", detail)
        return ServiceStatus(handle.handle, "provisioning", detail)

    @driver_op(cloud="k8s_native", driver="cache_memcached")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        del config
        parsed = _unpack_handle(handle.handle)
        if parsed.is_legacy:
            raise ValueError("legacy Memcached handle has no cluster locator")
        replicas = self._live_replicas(parsed)
        host = f"{parsed.name}.{parsed.namespace}.svc.cluster.local"
        nodes = ",".join(f"{parsed.name}-{ordinal}.{host}:{PORT}" for ordinal in range(replicas))
        return Binding(
            env_vars={
                "CACHE_HOST": ValueRef(literal=host),
                "CACHE_PORT": ValueRef(literal=str(PORT)),
                "CACHE_PROTOCOL": ValueRef(literal="memcached"),
                "CACHE_NODES": ValueRef(literal=nodes),
                "CACHE_TLS": ValueRef(literal="0"),
                "CACHE_RESOURCE_ARN": ValueRef(
                    literal=f"k8s://{parsed.cluster_id}/{parsed.namespace}/{parsed.name}",
                ),
            },
            iam_grants=[],
            notes=(
                "Memcached speaks plaintext and has no authentication; reachability is bounded "
                "by the NetworkPolicy rather than by a credential. Shard over CACHE_NODES; "
                "CACHE_HOST round-robins across the same pods for single-node clients."
            ),
        )

    @driver_op(cloud="k8s_native", driver="cache_memcached")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "managed_service.snapshot(Memcached) not supported -- Memcached holds no durable state",
        )

    @driver_op(cloud="k8s_native", driver="cache_memcached")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "managed_service.restore(Memcached) not supported -- Memcached holds no durable state",
        )

    @driver_op(cloud="k8s_native", driver="cache_memcached", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "replicas": {"type": "integer", "minimum": 1, "maximum": _MAX_REPLICAS},
                "max_memory_mb": {
                    "type": "integer",
                    "minimum": _MIN_MEMORY_MB,
                    "maximum": _MAX_MEMORY_MB,
                },
                "max_connections": {
                    "type": "integer",
                    "minimum": _MIN_CONNECTIONS,
                    "maximum": _MAX_CONNECTIONS,
                },
            },
        }

    @driver_op(cloud="k8s_native", driver="cache_memcached", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "CACHE_HOST": "Headless Service DNS name for the Memcached ring",
                "CACHE_PORT": "Memcached port (11211)",
                "CACHE_PROTOCOL": "memcached",
                "CACHE_NODES": "Comma-separated per-pod endpoints to shard over",
                "CACHE_TLS": "Always 0; the in-cluster Memcached listener is plaintext",
                "CACHE_RESOURCE_ARN": "Portable Kubernetes resource locator",
            },
        )

    def editable_fields(self) -> list[str]:
        """No config key can be applied without a reprovision (#1376)."""
        # replicas / max_memory_mb / max_connections all land in the rendered
        # StatefulSet, which only provision() applies to the cluster.
        return []

    # -- rendering ---------------------------------------------------------

    def _live_replicas(self, parsed: ParsedHandle) -> int:
        """Ring width read from the cluster, the way the AWS driver reads its
        node list from ``describe_cache_clusters``."""
        if self._config.cluster_driver is None:
            raise ValueError("Memcached binding requires a cluster driver")
        workload = self._config.cluster_driver.get_manifest(
            parsed.cluster_id,
            parsed.namespace,
            _STATEFULSET,
            parsed.name,
        )
        if workload is None:
            raise ValueError(f"Memcached StatefulSet {parsed.name} does not exist")
        replicas = int(dict(workload.get("spec") or {}).get("replicas", 0))
        if replicas < 1:
            raise ValueError(f"Memcached StatefulSet {parsed.name} declares no replicas")
        return replicas

    def _cache_name(self, *, spec: ProvisionSpec) -> str:
        # Pods take an ordinal suffix and are DNS labels themselves, so the
        # StatefulSet name has to leave room for it.
        return dns_label(
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint,
            max_length=50,
        )

    def _render(self, *, spec: ProvisionSpec, name: str, namespace: str) -> list[dict[str, Any]]:
        cfg = spec.config or {}
        size_spec = SIZE_TO_SPEC.get(spec.size, SIZE_TO_SPEC["small"])
        replicas = int(cfg.get("replicas", size_spec["replicas"]))
        max_memory_mb = int(cfg.get("max_memory_mb", size_spec["max_memory_mb"]))
        max_connections = int(cfg.get("max_connections", DEFAULT_MAX_CONNECTIONS))
        labels = {
            "astrolift.io/managed-by": "platform",
            "astrolift.io/app": dns_label(spec.app_slug),
            "astrolift.io/resource": name,
        }
        return [
            self._service(name=name, namespace=namespace, labels=labels),
            self._network_policy(
                name=name,
                namespace=namespace,
                labels=labels,
                organization_slug=spec.organization_slug,
            ),
            self._statefulset(
                name=name,
                namespace=namespace,
                labels=labels,
                replicas=replicas,
                max_memory_mb=max_memory_mb,
                max_connections=max_connections,
                memory_limit=str(size_spec["memory_limit"]),
                cpu_request=str(size_spec["cpu_request"]),
            ),
        ]

    def _service(self, *, name: str, namespace: str, labels: dict[str, str]) -> dict[str, Any]:
        return {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": name, "namespace": namespace, "labels": labels},
            "spec": {
                "clusterIP": "None",
                "selector": {"astrolift.io/resource": name},
                "ports": [{"name": "memcached", "port": PORT, "targetPort": PORT, "protocol": "TCP"}],
            },
        }

    def _network_policy(
        self,
        *,
        name: str,
        namespace: str,
        labels: dict[str, str],
        organization_slug: str,
    ) -> dict[str, Any]:
        # Memcached authenticates nobody, so the only thing standing between a
        # tenant's cache and the rest of the cluster is this policy. It is not
        # optional and there is no knob to turn it off.
        peers: list[dict[str, Any]] = [
            {"podSelector": {}},
            {
                "namespaceSelector": {
                    "matchLabels": {
                        "kubernetes.io/metadata.name": agent_namespace(organization_slug),
                    },
                },
            },
        ]
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": _policy_name(name), "namespace": namespace, "labels": labels},
            "spec": {
                "podSelector": {"matchLabels": {"astrolift.io/resource": name}},
                "policyTypes": ["Ingress"],
                "ingress": [{"from": peers, "ports": [{"protocol": "TCP", "port": PORT}]}],
            },
        }

    def _statefulset(
        self,
        *,
        name: str,
        namespace: str,
        labels: dict[str, str],
        replicas: int,
        max_memory_mb: int,
        max_connections: int,
        memory_limit: str,
        cpu_request: str,
    ) -> dict[str, Any]:
        return {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "metadata": {"name": name, "namespace": namespace, "labels": labels},
            "spec": {
                "serviceName": name,
                "replicas": replicas,
                # Nothing in the ring depends on another node, and an app is
                # waiting on all of them, so bring them up at once.
                "podManagementPolicy": "Parallel",
                "selector": {"matchLabels": {"astrolift.io/resource": name}},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "automountServiceAccountToken": False,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": _RUN_AS_USER,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [
                            {
                                "name": "memcached",
                                "image": self._config.image,
                                "args": ["-m", str(max_memory_mb), "-c", str(max_connections)],
                                "ports": [{"name": "memcached", "containerPort": PORT, "protocol": "TCP"}],
                                "resources": {
                                    "requests": {"cpu": cpu_request, "memory": memory_limit},
                                    "limits": {"memory": memory_limit},
                                },
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                                "readinessProbe": {"tcpSocket": {"port": PORT}, "periodSeconds": 5},
                                "livenessProbe": {
                                    "tcpSocket": {"port": PORT},
                                    "initialDelaySeconds": 10,
                                    "periodSeconds": 20,
                                },
                            },
                        ],
                    },
                },
            },
        }

    @staticmethod
    def _validate_config(cfg: dict[str, Any]) -> str:
        for key, low, high in (
            ("replicas", 1, _MAX_REPLICAS),
            ("max_memory_mb", _MIN_MEMORY_MB, _MAX_MEMORY_MB),
            ("max_connections", _MIN_CONNECTIONS, _MAX_CONNECTIONS),
        ):
            if key not in cfg:
                continue
            value = cfg[key]
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                return f"{key} must be an integer from {low} through {high}"
        return ""


def _policy_name(name: str) -> str:
    return dns_label(name, "netpol")


def _stub(api_version: str, kind: str, name: str, namespace: str) -> dict[str, Any]:
    return {
        "apiVersion": api_version,
        "kind": kind,
        "metadata": {"name": name, "namespace": namespace},
    }
