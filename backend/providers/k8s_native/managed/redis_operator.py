"""Redis ManagedServiceDriver via Bitnami Redis operator (#53).

Emits Bitnami's Redis CRD; alternative operators (OT-CONTAINER-KIT,
Spotahome) follow similar shapes. Driver targets the Bitnami CRD
because it's the most widely deployed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
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
)
from k8s_native.managed._handle import pack as _pack_handle
from k8s_native.managed._handle import unpack as _unpack_handle

KIND = "redis"


SIZE_TO_SPEC = {
    "small": {
        "replicas": 1,
        "memory": "256Mi",
        "storage_size": "1Gi",
    },
    "medium": {
        "replicas": 2,
        "memory": "1Gi",
        "storage_size": "5Gi",
    },
    "large": {
        "replicas": 3,
        "memory": "4Gi",
        "storage_size": "20Gi",
    },
    "xlarge": {
        "replicas": 3,
        "memory": "16Gi",
        "storage_size": "100Gi",
    },
}


@dataclass(frozen=True)
class RedisOperatorConfig:
    cluster_driver: Any | None = None
    storage_class: str = ""
    persistent: bool = True
    """When False, Redis runs from RAM (cache-only mode). True
    persists via the operator's PVC."""


class RedisOperatorDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: RedisOperatorConfig | None = None,
    ) -> None:
        self._config = config or RedisOperatorConfig()

    @driver_op(
        cloud="k8s_native",
        driver="redis_operator",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cluster_name = self._cluster_name(spec=spec)
        namespace = f"{spec.organization_slug}-{spec.app_slug}"
        manifest = self._render_cluster(
            spec=spec,
            cluster_name=cluster_name,
        )
        if self._config.cluster_driver is not None:
            result = self._config.cluster_driver.apply_manifests(
                spec.tenant_cluster_id,
                namespace,
                [manifest],
            )
            if not result.ok:
                return ProvisionResult(
                    ok=False,
                    handle="",
                    message=f"failed to apply Redis: {result.summary()}",
                    errors=result.summary(),
                )
        handle = _pack_handle(
            kind=KIND,
            cluster_id=spec.tenant_cluster_id or "render-only",
            namespace=namespace,
            name=cluster_name,
        )
        return ProvisionResult(
            ok=True,
            handle=handle,
            message=f"Redis {cluster_name} applied",
        )

    @driver_op(cloud="k8s_native", driver="redis_operator")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message="re-call provision with new size to update",
        )

    @driver_op(
        cloud="k8s_native",
        driver="redis_operator",
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
        del delete_data, force_destroy
        if self._config.cluster_driver is None:
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message="render-only mode",
            )
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
            )
        # The operator's reconciler watches both the standalone
        # ``Redis`` CR and the replicated ``RedisReplication`` CR.
        # Provision picks one based on size; deprovision emits both
        # stubs so we don't have to thread the size back through the
        # handle (the cluster_driver treats not-found as a no-op via
        # ``DeleteResult.not_found``).
        stubs = [
            {
                "apiVersion": "redis.redis.opstreelabs.in/v1beta2",
                "kind": kind,
                "metadata": {
                    "name": parsed.name,
                    "namespace": parsed.namespace,
                },
            }
            for kind in ("Redis", "RedisReplication")
        ]
        result = self._config.cluster_driver.delete_manifests(
            parsed.cluster_id,
            parsed.namespace,
            stubs,
        )
        if result.errors:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete failed: {result.summary()}",
                errors=result.summary(),
            )
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=f"Redis {parsed.name} deleted",
        )

    @driver_op(cloud="k8s_native", driver="redis_operator")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        return ServiceStatus(
            handle=handle.handle,
            state="provisioning",
            message="query the Redis CRD's status block via cluster_driver",
        )

    @driver_op(cloud="k8s_native", driver="redis_operator")
    def binding(self, handle: ServiceHandle) -> Binding:
        name = _unpack_handle(handle.handle).name
        # Bitnami Redis operator generates a Service named
        # <cluster>-redis on port 6379.
        host = f"{name}-redis"
        secret_name = f"{name}-redis"
        return Binding(
            env_vars={
                "REDIS_HOST": ValueRef(literal=host),
                "REDIS_PORT": ValueRef(literal="6379"),
                "REDIS_PASSWORD": ValueRef(
                    secret_ref=f"{secret_name}#redis-password",
                ),
                "REDIS_URL": ValueRef(
                    literal=f"redis://{host}:6379",
                ),
            },
            iam_grants=[],
            notes=("Connect via REDIS_HOST + port; password from the operator-generated Secret."),
        )

    @driver_op(cloud="k8s_native", driver="redis_operator")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        snapshot_id = f"rdb-{datetime.now(tz=UTC).strftime('%Y%m%d-%H%M%S')}"
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=snapshot_id,
            created_at=datetime.now(tz=UTC).isoformat(),
        )

    @driver_op(cloud="k8s_native", driver="redis_operator")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        return ProvisionResult(
            ok=False,
            handle="",
            message="redis restore via RDB import is operator-managed",
            errors=["not_implemented_in_driver"],
        )

    @driver_op(cloud="k8s_native", driver="redis_operator", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "persistent": {"type": "boolean"},
                "memory_override": {"type": "string"},
            },
        }

    @driver_op(cloud="k8s_native", driver="redis_operator", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "REDIS_HOST": "Service hostname",
                "REDIS_PORT": "6379",
                "REDIS_PASSWORD": "Auth password from operator Secret",
                "REDIS_URL": "Full redis:// URL",
            }
        )

    def _cluster_name(self, *, spec: ProvisionSpec) -> str:
        parts = [spec.app_slug, spec.environment_name]
        if spec.service_handle_hint:
            parts.append(spec.service_handle_hint)
        raw = "-".join(p for p in parts if p)
        clean = "".join(c if c.isalnum() or c == "-" else "-" for c in raw.lower())
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:60]

    def _render_cluster(
        self,
        *,
        spec: ProvisionSpec,
        cluster_name: str,
    ) -> dict[str, Any]:
        size_spec = SIZE_TO_SPEC.get(spec.size, SIZE_TO_SPEC["small"])
        memory = spec.config.get(
            "memory_override",
            size_spec["memory"],
        )
        persistent = spec.config.get(
            "persistent",
            self._config.persistent,
        )
        manifest: dict[str, Any] = {
            "apiVersion": "redis.redis.opstreelabs.in/v1beta2",
            "kind": "Redis" if size_spec["replicas"] == 1 else "RedisReplication",
            "metadata": {
                "name": cluster_name,
                "labels": {
                    "astrolift.io/managed-by": "platform",
                    "astrolift.io/app": spec.app_slug,
                },
            },
            "spec": {
                "kubernetesConfig": {
                    "image": "quay.io/opstree/redis:v7.0.12",
                    "resources": {
                        "requests": {"memory": memory},
                    },
                },
            },
        }
        if size_spec["replicas"] > 1:
            manifest["spec"]["clusterSize"] = size_spec["replicas"]
        if persistent:
            manifest["spec"]["storage"] = {
                "volumeClaimTemplate": {
                    "spec": {
                        "accessModes": ["ReadWriteOnce"],
                        "resources": {
                            "requests": {
                                "storage": size_spec["storage_size"],
                            },
                        },
                    },
                },
            }
            if self._config.storage_class:
                manifest["spec"]["storage"]["volumeClaimTemplate"]["spec"]["storageClassName"] = (
                    self._config.storage_class
                )
        return manifest
