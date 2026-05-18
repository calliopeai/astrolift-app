"""Kafka via Strimzi operator (#72).

Strimzi is the production-mature Kafka operator. Driver emits Kafka
+ KafkaUser CRDs that Strimzi reconciles into StatefulSets +
NodePool resources.

Variant key: ('event_stream', 'kafka_strimzi').
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
    UpdateResult,
    UpdateSpec,
    ValueRef,
)
from k8s_native.managed._handle import pack as _pack_handle
from k8s_native.managed._handle import unpack as _unpack_handle

KIND = "event_stream"


SIZE_TO_SPEC = {
    "small": {
        "kafka_replicas": 3,
        "controller_replicas": 3,
        "kafka_resources": {
            "requests": {"cpu": "200m", "memory": "1Gi"},
            "limits": {"cpu": "1", "memory": "2Gi"},
        },
        "storage_size": "10Gi",
    },
    "medium": {
        "kafka_replicas": 3,
        "controller_replicas": 3,
        "kafka_resources": {
            "requests": {"cpu": "500m", "memory": "4Gi"},
            "limits": {"cpu": "2", "memory": "8Gi"},
        },
        "storage_size": "100Gi",
    },
    "large": {
        "kafka_replicas": 5,
        "controller_replicas": 3,
        "kafka_resources": {
            "requests": {"cpu": "1", "memory": "8Gi"},
            "limits": {"cpu": "4", "memory": "16Gi"},
        },
        "storage_size": "500Gi",
    },
    "xlarge": {
        "kafka_replicas": 7,
        "controller_replicas": 5,
        "kafka_resources": {
            "requests": {"cpu": "2", "memory": "16Gi"},
            "limits": {"cpu": "8", "memory": "32Gi"},
        },
        "storage_size": "1Ti",
    },
}


@dataclass(frozen=True)
class StrimziKafkaConfig:
    storage_class: str | None = None
    namespace: str | None = None
    cluster_driver: Any | None = None


class StrimziKafkaDriver(ManagedServiceDriver):
    def __init__(self, *, config: StrimziKafkaConfig) -> None:
        self._config = config

    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cluster_name = self._cluster_name(spec=spec)
        namespace = self._namespace_for(spec=spec)
        handle = _pack_handle(
            kind=KIND,
            cluster_id=spec.tenant_cluster_id or "render-only",
            namespace=namespace,
            name=cluster_name,
        )
        manifests = self._render_manifests(spec=spec, name=cluster_name)
        if self._config.cluster_driver is None:
            return ProvisionResult(
                ok=True,
                handle=handle,
                message="Strimzi Kafka CRDs rendered",
            )
        result = self._config.cluster_driver.apply_manifests(
            spec.tenant_cluster_id,
            namespace,
            manifests,
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
            message=f"Kafka cluster {cluster_name} provisioned",
        )

    def update(self, spec: UpdateSpec) -> UpdateResult:
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message="Strimzi reconciles topology updates via re-applied CRD",
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
        # Delete the four resources provision emitted: the Kafka CR
        # (which the operator cascades into the underlying
        # StatefulSets), both KafkaNodePool CRs (controllers +
        # brokers), and the KafkaUser. The operator finalizer also
        # cascades pod-disruption-budgets and Services.
        stubs = [
            {
                "apiVersion": "kafka.strimzi.io/v1beta2",
                "kind": "Kafka",
                "metadata": {
                    "name": parsed.name,
                    "namespace": parsed.namespace,
                },
            },
            {
                "apiVersion": "kafka.strimzi.io/v1beta2",
                "kind": "KafkaNodePool",
                "metadata": {
                    "name": "controllers",
                    "namespace": parsed.namespace,
                },
            },
            {
                "apiVersion": "kafka.strimzi.io/v1beta2",
                "kind": "KafkaNodePool",
                "metadata": {
                    "name": "brokers",
                    "namespace": parsed.namespace,
                },
            },
            {
                "apiVersion": "kafka.strimzi.io/v1beta2",
                "kind": "KafkaUser",
                "metadata": {
                    "name": f"{parsed.name}-app-user",
                    "namespace": parsed.namespace,
                },
            },
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
                message=str(result.summary()),
                errors=result.summary(),
            )
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=f"Kafka cluster {parsed.name} deleted",
        )

    def status(self, handle: ServiceHandle) -> ServiceStatus:
        return ServiceStatus(
            handle=handle.handle,
            state="available",
            message="status delegated to Strimzi reconciliation",
        )

    def binding(self, handle: ServiceHandle) -> Binding:
        parsed = _unpack_handle(handle.handle)
        name = parsed.name
        ns = parsed.namespace or self._fallback_ns(name=name)
        bootstrap = f"{name}-kafka-bootstrap.{ns}.svc:9092"
        return Binding(
            env_vars={
                "EVENT_STREAM_BROKERS": ValueRef(literal=bootstrap),
                "EVENT_STREAM_USERNAME": ValueRef(
                    secret_ref=f"{name}-app-user#username",
                ),
                "EVENT_STREAM_PASSWORD": ValueRef(
                    secret_ref=f"{name}-app-user#password",
                ),
                "EVENT_STREAM_TLS": ValueRef(literal="false"),
            },
            iam_grants=[],
            notes=(
                "Kafka bootstrap routed via Strimzi-managed Service. "
                "App user credentials live in <cluster>-app-user."
            ),
        )

    def snapshot(self, handle):
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "managed_service.snapshot(Kafka) not supported -- snapshot via "
            "MirrorMaker2 / cluster mirroring; out of scope for this driver (#618)",
        )

    def restore(self, snapshot, target):
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "managed_service.restore(Kafka) not supported -- Kafka doesn't " "restore from a snapshot (#618)",
        )

    def config_schema(self):
        return {
            "type": "object",
            "properties": {
                "storage_class": {"type": "string"},
            },
        }

    def binding_schema(self):
        return BindingSchema(
            env_vars={
                "EVENT_STREAM_BROKERS": "Bootstrap server (host:port)",
                "EVENT_STREAM_USERNAME": "SASL username (from Secret)",
                "EVENT_STREAM_PASSWORD": "SASL password (from Secret)",
                "EVENT_STREAM_TLS": "TLS enabled (true/false)",
            }
        )

    def _render_manifests(
        self,
        *,
        spec: ProvisionSpec,
        name: str,
    ) -> list[dict[str, Any]]:
        size_spec = SIZE_TO_SPEC.get(spec.size, SIZE_TO_SPEC["small"])
        ns = self._namespace_for(spec=spec)
        return [
            {
                "apiVersion": "kafka.strimzi.io/v1beta2",
                "kind": "Kafka",
                "metadata": {
                    "name": name,
                    "namespace": ns,
                    "annotations": {
                        "strimzi.io/node-pools": "enabled",
                        "strimzi.io/kraft": "enabled",
                    },
                    "labels": {
                        "astrolift.io/managed-by": "platform",
                        "astrolift.io/app": spec.app_slug,
                    },
                },
                "spec": {
                    "kafka": {
                        "version": "3.7.0",
                        "metadataVersion": "3.7-IV4",
                        "listeners": [
                            {
                                "name": "plain",
                                "port": 9092,
                                "type": "internal",
                                "tls": False,
                                "authentication": {
                                    "type": "scram-sha-512",
                                },
                            },
                        ],
                        "config": {
                            "default.replication.factor": min(
                                size_spec["kafka_replicas"],
                                3,
                            ),
                            "min.insync.replicas": min(
                                size_spec["kafka_replicas"],
                                2,
                            ),
                            "offsets.topic.replication.factor": min(
                                size_spec["kafka_replicas"],
                                3,
                            ),
                        },
                    },
                    "entityOperator": {
                        "topicOperator": {},
                        "userOperator": {},
                    },
                },
            },
            {
                "apiVersion": "kafka.strimzi.io/v1beta2",
                "kind": "KafkaNodePool",
                "metadata": {
                    "name": "controllers",
                    "namespace": ns,
                    "labels": {
                        "strimzi.io/cluster": name,
                    },
                },
                "spec": {
                    "replicas": size_spec["controller_replicas"],
                    "roles": ["controller"],
                    "resources": size_spec["kafka_resources"],
                    "storage": {
                        "type": "persistent-claim",
                        "size": size_spec["storage_size"],
                        "class": self._config.storage_class,
                    },
                },
            },
            {
                "apiVersion": "kafka.strimzi.io/v1beta2",
                "kind": "KafkaNodePool",
                "metadata": {
                    "name": "brokers",
                    "namespace": ns,
                    "labels": {"strimzi.io/cluster": name},
                },
                "spec": {
                    "replicas": size_spec["kafka_replicas"],
                    "roles": ["broker"],
                    "resources": size_spec["kafka_resources"],
                    "storage": {
                        "type": "persistent-claim",
                        "size": size_spec["storage_size"],
                        "class": self._config.storage_class,
                    },
                },
            },
            {
                "apiVersion": "kafka.strimzi.io/v1beta2",
                "kind": "KafkaUser",
                "metadata": {
                    "name": f"{name}-app-user",
                    "namespace": ns,
                    "labels": {"strimzi.io/cluster": name},
                },
                "spec": {
                    "authentication": {"type": "scram-sha-512"},
                    "authorization": {
                        "type": "simple",
                        "acls": [
                            {
                                "resource": {
                                    "type": "topic",
                                    "name": "*",
                                    "patternType": "literal",
                                },
                                "operations": [
                                    "Read",
                                    "Write",
                                    "Describe",
                                    "Create",
                                ],
                            }
                        ],
                    },
                },
            },
        ]

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

    def _fallback_ns(self, *, name: str) -> str:
        if self._config.namespace:
            return self._config.namespace
        return name
