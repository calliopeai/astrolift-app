"""Redis ManagedServiceDriver via Bitnami Redis operator (#53).

Emits Bitnami's Redis CRD; alternative operators (OT-CONTAINER-KIT,
Spotahome) follow similar shapes. Driver targets the Bitnami CRD
because it's the most widely deployed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
        self, *, config: RedisOperatorConfig | None = None,
    ) -> None:
        self._config = config or RedisOperatorConfig()

    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cluster_name = self._cluster_name(spec=spec)
        manifest = self._render_cluster(
            spec=spec, cluster_name=cluster_name,
        )
        if self._config.cluster_driver is not None:
            namespace = f"{spec.organization_slug}-{spec.app_slug}"
            result = self._config.cluster_driver.apply_manifests(
                spec.tenant_cluster_id, namespace, [manifest],
            )
            if not result.ok:
                return ProvisionResult(
                    ok=False, handle="",
                    message=f"failed to apply Redis: {result.errors}",
                    errors=result.errors,
                )
        return ProvisionResult(
            ok=True, handle=f"{KIND}/{cluster_name}",
            message=f"Redis {cluster_name} applied",
        )

    def update(self, spec: UpdateSpec) -> UpdateResult:
        return UpdateResult(
            ok=True, handle=spec.handle,
            message="re-call provision with new size to update",
        )

    def deprovision(
        self, spec: DeprovisionSpec, *, delete_data: bool = False,
    ) -> DeprovisionResult:
        if self._config.cluster_driver is None:
            return DeprovisionResult(
                ok=True, handle=spec.handle,
                message="render-only mode",
            )
        _, _, name = spec.handle.partition("/")
        stub = {
            "apiVersion": "redis.redis.opstreelabs.in/v1beta2",
            "kind": "Redis",
            "metadata": {"name": name},
        }
        result = self._config.cluster_driver.delete_manifests(
            "default", "redis-operator", [stub],
        )
        if result.errors:
            return DeprovisionResult(
                ok=False, handle=spec.handle,
                message=f"delete failed: {result.errors}",
                errors=result.errors,
            )
        return DeprovisionResult(
            ok=True, handle=spec.handle,
            message=f"Redis {name} deleted",
        )

    def status(self, handle: ServiceHandle) -> ServiceStatus:
        return ServiceStatus(
            handle=handle.handle, state="provisioning",
            message="query the Redis CRD's status block via cluster_driver",
        )

    def binding(self, handle: ServiceHandle) -> Binding:
        _, _, name = handle.handle.partition("/")
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
            notes=(
                "Connect via REDIS_HOST + port; password from "
                "the operator-generated Secret."
            ),
        )

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        snapshot_id = f"rdb-{datetime.now(tz=UTC).strftime('%Y%m%d-%H%M%S')}"
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=snapshot_id,
            created_at=datetime.now(tz=UTC).isoformat(),
        )

    def restore(
        self, snapshot: SnapshotHandle, target: ProvisionSpec,
    ) -> ProvisionResult:
        return ProvisionResult(
            ok=False, handle="",
            message="redis restore via RDB import is operator-managed",
            errors=["not_implemented_in_driver"],
        )

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "persistent": {"type": "boolean"},
                "memory_override": {"type": "string"},
            },
        }

    def binding_schema(self) -> BindingSchema:
        return BindingSchema(env_vars={
            "REDIS_HOST": "Service hostname",
            "REDIS_PORT": "6379",
            "REDIS_PASSWORD": "Auth password from operator Secret",
            "REDIS_URL": "Full redis:// URL",
        })

    def _cluster_name(self, *, spec: ProvisionSpec) -> str:
        parts = [spec.app_slug, spec.environment_name]
        if spec.service_handle_hint:
            parts.append(spec.service_handle_hint)
        raw = "-".join(p for p in parts if p)
        clean = "".join(
            c if c.isalnum() or c == "-" else "-"
            for c in raw.lower()
        )
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:60]

    def _render_cluster(
        self, *, spec: ProvisionSpec, cluster_name: str,
    ) -> dict[str, Any]:
        size_spec = SIZE_TO_SPEC.get(spec.size, SIZE_TO_SPEC["small"])
        memory = spec.config.get(
            "memory_override", size_spec["memory"],
        )
        persistent = spec.config.get(
            "persistent", self._config.persistent,
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
                manifest["spec"]["storage"][
                    "volumeClaimTemplate"
                ]["spec"]["storageClassName"] = (
                    self._config.storage_class
                )
        return manifest
