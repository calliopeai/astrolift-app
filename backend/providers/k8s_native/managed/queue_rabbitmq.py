"""RabbitMQ via the official RabbitMQ Cluster Operator (#72).

The driver emits RabbitmqCluster CRDs that the operator reconciles
into StatefulSets + erlang clustering.

Variant key: ('queue', 'rabbitmq_operator').
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.k8s_naming import app_namespace, dns_label
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
    UpdateResult,
    UpdateSpec,
    ValueRef,
    unsupported_update,
)
from k8s_native.managed._handle import pack as _pack_handle
from k8s_native.managed._handle import unpack as _unpack_handle
from k8s_native.managed._secret_refs import shared_namespace_owner_refusal

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

    @driver_op(
        cloud="k8s_native",
        driver="queue_rabbitmq",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cluster_name = self._cluster_name(spec=spec)
        namespace = self._namespace_for(spec=spec)
        handle = _pack_handle(
            kind=KIND,
            cluster_id=spec.tenant_cluster_id or "render-only",
            namespace=namespace,
            name=cluster_name,
        )
        manifest = self._render_cluster_crd(spec=spec, name=cluster_name)
        if self._config.cluster_driver is None:
            return ProvisionResult(
                ok=True,
                handle=handle,
                message="RabbitmqCluster CRD rendered",
            )
        refusal = shared_namespace_owner_refusal(self._config.namespace, self._config.cluster_driver, spec, [manifest])
        if refusal:
            return ProvisionResult(ok=False, handle="", message=refusal, errors=["resource_not_owned"])
        result = self._config.cluster_driver.apply_manifests(
            spec.tenant_cluster_id,
            namespace,
            [manifest],
        )
        if not result.ok:
            return ProvisionResult(
                ok=False,
                handle="",
                message="apply_manifests failed",
                errors=result.summary(),
            )
        return ProvisionResult(
            ok=True,
            handle=handle,
            message=f"RabbitMQ cluster {cluster_name} provisioned",
        )

    @driver_op(cloud="k8s_native", driver="queue_rabbitmq")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        return unsupported_update(spec.handle, "the RabbitMQ operator reconciles from the CRD re-applied on provision")

    @driver_op(
        cloud="k8s_native",
        driver="queue_rabbitmq",
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
                message="no cluster_driver — manifest deletion skipped",
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
        stub = {
            "apiVersion": "rabbitmq.com/v1beta1",
            "kind": "RabbitmqCluster",
            "metadata": {
                "name": parsed.name,
                "namespace": parsed.namespace,
            },
        }
        result = self._config.cluster_driver.delete_manifests(
            parsed.cluster_id,
            parsed.namespace,
            [stub],
        )
        if result.errors:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=str(result.summary()),
                errors=result.summary(),
            )
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=f"RabbitmqCluster {parsed.name} deleted",
        )

    @driver_op(cloud="k8s_native", driver="queue_rabbitmq")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        return ServiceStatus(
            handle=handle.handle,
            state="available",
            message="status delegated to operator reconciliation",
        )

    @driver_op(cloud="k8s_native", driver="queue_rabbitmq")
    def binding(self, handle: ServiceHandle) -> Binding:
        name = _unpack_handle(handle.handle).name
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

    @driver_op(cloud="k8s_native", driver="queue_rabbitmq")
    def snapshot(self, handle):
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "managed_service.snapshot(RabbitMQ) not supported -- snapshots via cluster export; out of scope (#618)",
        )

    @driver_op(cloud="k8s_native", driver="queue_rabbitmq")
    def restore(self, snapshot, target):
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "managed_service.restore(RabbitMQ) not supported -- RabbitMQ doesn't restore from snapshot (#618)",
        )

    @driver_op(cloud="k8s_native", driver="queue_rabbitmq", heartbeat=False)
    def config_schema(self):
        return {
            "type": "object",
            "properties": {
                "storage_class": {"type": "string"},
            },
        }

    @driver_op(cloud="k8s_native", driver="queue_rabbitmq", heartbeat=False)
    def binding_schema(self):
        return BindingSchema(
            env_vars={
                "RABBITMQ_HOST": "Service hostname",
                "RABBITMQ_PORT": "AMQP port (5672)",
                "RABBITMQ_USER": "Default user (from Secret)",
                "RABBITMQ_PASSWORD": "Default password (from Secret)",
            }
        )

    def editable_fields(self) -> list[str]:
        """No config key can be applied without a reprovision (#1376)."""
        # storage_class lands in the RabbitmqCluster CRD, which only provision()
        # applies to the cluster.
        return []

    def _render_cluster_crd(
        self,
        *,
        spec: ProvisionSpec,
        name: str,
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
                    "astrolift.io/organization": spec.organization_slug,
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
                    "additionalConfig": ("cluster_partition_handling = pause_minority"),
                },
            },
        }

    def _cluster_name(self, *, spec: ProvisionSpec) -> str:
        return dns_label(
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint,
            max_length=50,
        )

    def _namespace_for(self, *, spec: ProvisionSpec) -> str:
        if self._config.namespace:
            return self._config.namespace
        return app_namespace(
            organization_slug=spec.organization_slug,
            app_slug=spec.app_slug,
        )
