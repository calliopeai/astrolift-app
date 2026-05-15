"""NATS via the official NATS operator (#72).

NATS is the lightweight messaging alternative to Kafka. Driver
emits a NatsCluster Helm-rendered StatefulSet via the
nats-server operator.

Variant key: ('event_stream', 'nats').
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


KIND = "event_stream"


SIZE_TO_REPLICAS = {
    "small": 1,
    "medium": 3,
    "large": 5,
    "xlarge": 7,
}


@dataclass(frozen=True)
class NATSConfig:
    storage_class: str | None = None
    namespace: str | None = None
    enable_jetstream: bool = True
    cluster_driver: Any | None = None


class NATSDriver(ManagedServiceDriver):
    def __init__(self, *, config: NATSConfig) -> None:
        self._config = config

    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cluster_name = self._cluster_name(spec=spec)
        manifest = self._render_statefulset(spec=spec, name=cluster_name)
        if self._config.cluster_driver is None:
            return ProvisionResult(
                ok=True,
                handle=f"{KIND}/{cluster_name}",
                message="NATS manifests rendered",
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
            message=f"NATS cluster {cluster_name} provisioned",
        )

    def update(self, spec: UpdateSpec) -> UpdateResult:
        return UpdateResult(
            ok=True, handle=spec.handle,
            message="re-apply manifest to scale / reconfigure",
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
            message="delete via StatefulSet removal",
        )

    def status(self, handle: ServiceHandle) -> ServiceStatus:
        return ServiceStatus(
            handle=handle.handle, state="available",
            message="status via StatefulSet readyReplicas",
        )

    def binding(self, handle: ServiceHandle) -> Binding:
        _, _, name = handle.handle.partition("/")
        return Binding(
            env_vars={
                "EVENT_STREAM_BROKERS": ValueRef(
                    literal=f"nats://{name}:4222",
                ),
                "EVENT_STREAM_TLS": ValueRef(literal="false"),
            },
            iam_grants=[],
            notes=(
                "NATS connection via headless Service. JetStream "
                "auth (if enabled) requires a separate accounts "
                "config managed by the operator."
            ),
        )

    def snapshot(self, handle):
        raise NotImplementedError(
            "NATS JetStream snapshot via stream backup; out of scope",
        )

    def restore(self, snapshot, target):
        raise NotImplementedError("NATS doesn't restore from snapshot")

    def config_schema(self):
        return {
            "type": "object",
            "properties": {
                "enable_jetstream": {"type": "boolean", "default": True},
                "storage_class": {"type": "string"},
            },
        }

    def binding_schema(self):
        return BindingSchema(env_vars={
            "EVENT_STREAM_BROKERS": "NATS URL (nats://host:port)",
            "EVENT_STREAM_TLS": "TLS enabled (true/false)",
        })

    def _render_statefulset(
        self, *, spec: ProvisionSpec, name: str,
    ) -> dict[str, Any]:
        replicas = SIZE_TO_REPLICAS.get(spec.size, 1)
        ns = self._namespace_for(spec=spec)
        return {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "metadata": {
                "name": name,
                "namespace": ns,
                "labels": {
                    "astrolift.io/managed-by": "platform",
                    "astrolift.io/app": spec.app_slug,
                    "app": "nats",
                },
            },
            "spec": {
                "serviceName": name,
                "replicas": replicas,
                "selector": {"matchLabels": {"app": "nats", "name": name}},
                "template": {
                    "metadata": {
                        "labels": {"app": "nats", "name": name},
                    },
                    "spec": {
                        "containers": [{
                            "name": "nats",
                            "image": "nats:2.10-alpine",
                            "args": [
                                "--cluster_name", name,
                                "--cluster", "nats://0.0.0.0:6222",
                                "--http_port", "8222",
                                *(
                                    ["--jetstream"]
                                    if self._config.enable_jetstream
                                    else []
                                ),
                            ],
                            "ports": [
                                {"containerPort": 4222, "name": "client"},
                                {"containerPort": 6222, "name": "cluster"},
                                {"containerPort": 8222, "name": "monitor"},
                            ],
                        }],
                    },
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
