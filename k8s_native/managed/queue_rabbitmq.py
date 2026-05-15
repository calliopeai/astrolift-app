"""RabbitMQ via the official RabbitMQ Cluster Operator (#72).

The driver emits RabbitmqCluster CRDs that the operator reconciles
into StatefulSets + erlang clustering.

Variant key: ('queue', 'rabbitmq_operator').
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


KIND = "queue"


SIZE_TO_SPEC = {
    "small": {
        "replicas": 1,
        "resources": {
            "requests": {"cpu": "200m", "memory": "512Mi"},
            "limits": {"cpu": "1", "memory": "2Gi"},
        },
        "storage_size": "10Gi",
    },
    "medium": {
        "replicas": 3,
        "resources": {
            "requests": {"cpu": "500m", "memory": "2Gi"},
            "limits": {"cpu": "2", "memory": "8Gi"},
        },
        "storage_size": "50Gi",
    },
    "large": {
        "replicas": 3,
        "resources": {
            "requests": {"cpu": "1", "memory": "4Gi"},
            "limits": {"cpu": "4", "memory": "16Gi"},
        },
        "storage_size": "100Gi",
    },
    "xlarge": {
        "replicas": 5,
        "resources": {
            "requests": {"cpu": "2", "memory": "8Gi"},
            "limits": {"cpu": "8", "memory": "32Gi"},
        },
        "storage_size": "500Gi",
    },
}


@dataclass(frozen=True)
class RabbitMQOperatorConfig:
    storage_class: str | None = None
    namespace: str | None = None
    cluster_driver: Any | None = None


class RabbitMQOperatorDriver(ManagedServiceDriver):
    def __init__(self, *, config: RabbitMQOperatorConfig) -> None:
        self._config = config

    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cluster_name = self._cluster_name(spec=spec)
        manifest = self._render_cluster_crd(spec=spec, name=cluster_name)
        if self._config.cluster_driver is None:
            return ProvisionResult(
                ok=True,
                handle=f"{KIND}/{cluster_name}",
                message="RabbitmqCluster CRD rendered",
            )
        result = self._config.cluster_driver.apply_manifests(
            spec.tenant_cluster_id,
            self._namespace_for(spec=spec),
            [manifest],
        )
        if not result.ok:
            return ProvisionResult(
                ok=False, handle="",
                message="apply_manifests failed",
                errors=result.errors,
            )
        return ProvisionResult(
            ok=True,
            handle=f"{KIND}/{cluster_name}",
            message=f"RabbitMQ cluster {cluster_name} provisioned",
        )

    def update(self, spec: UpdateSpec) -> UpdateResult:
        return UpdateResult(
            ok=True, handle=spec.handle,
            message="operator reconciles via re-applied CRD",
        )

    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        del delete_data, force_destroy
        return DeprovisionResult(
            ok=True, handle=spec.handle,
            message="delete via RabbitmqCluster CRD removal",
        )

    def status(self, handle: ServiceHandle) -> ServiceStatus:
        return ServiceStatus(
            handle=handle.handle, state="available",
            message="status delegated to operator reconciliation",
        )

    def binding(self, handle: ServiceHandle) -> Binding:
        _, _, name = handle.handle.partition("/")
        return Binding(
            env_vars={
                "RABBITMQ_HOST": ValueRef(literal=name),
                "RABBITMQ_PORT": ValueRef(literal="5672"),
                "RABBITMQ_USER": ValueRef(
                    secret_ref=f"{name}-default-user#username",
                ),
                "RABBITMQ_PASSWORD": ValueRef(
                    secret_ref=f"{name}-default-user#password",
                ),
            },
            iam_grants=[],
            notes=(
                "RabbitMQ AMQP routed via operator-managed Service. "
                "Default-user credentials live in <cluster>-default-user."
            ),
        )

    def snapshot(self, handle):
        raise NotImplementedError(
            "RabbitMQ snapshots via cluster export; out of scope",
        )

    def restore(self, snapshot, target):
        raise NotImplementedError("RabbitMQ doesn't restore from snapshot")

    def config_schema(self):
        return {
            "type": "object",
            "properties": {
                "storage_class": {"type": "string"},
            },
        }

    def binding_schema(self):
        return BindingSchema(env_vars={
            "RABBITMQ_HOST": "Service hostname",
            "RABBITMQ_PORT": "AMQP port (5672)",
            "RABBITMQ_USER": "Default user (from Secret)",
            "RABBITMQ_PASSWORD": "Default password (from Secret)",
        })

    def _render_cluster_crd(
        self, *, spec: ProvisionSpec, name: str,
    ) -> dict[str, Any]:
        size_spec = SIZE_TO_SPEC.get(spec.size, SIZE_TO_SPEC["small"])
        return {
            "apiVersion": "rabbitmq.com/v1beta1",
            "kind": "RabbitmqCluster",
            "metadata": {
                "name": name,
                "namespace": self._namespace_for(spec=spec),
                "labels": {
                    "astrolift.io/managed-by": "platform",
                    "astrolift.io/app": spec.app_slug,
                },
            },
            "spec": {
                "replicas": size_spec["replicas"],
                "resources": size_spec["resources"],
                "persistence": {
                    "storageClassName": self._config.storage_class,
                    "storage": size_spec["storage_size"],
                },
                "rabbitmq": {
                    "additionalConfig": (
                        "cluster_partition_handling = pause_minority"
                    ),
                },
            },
        }

    def _cluster_name(self, *, spec: ProvisionSpec) -> str:
        parts = [spec.app_slug, spec.environment_name]
        if spec.service_handle_hint:
            parts.append(spec.service_handle_hint)
        clean = "-".join(p for p in parts if p).lower()
        clean = "".join(c if c.isalnum() or c == "-" else "-" for c in clean)
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:50]

    def _namespace_for(self, *, spec: ProvisionSpec) -> str:
        if self._config.namespace:
            return self._config.namespace
        return f"{spec.organization_slug}-{spec.app_slug}".lower()
