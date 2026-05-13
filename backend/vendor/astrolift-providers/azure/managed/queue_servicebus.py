"""Azure Service Bus queue managed-service driver (#47)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    Grant,
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


@dataclass(frozen=True)
class ServiceBusConfig:
    subscription_id: str
    resource_group: str
    namespace_name: str
    """Service Bus namespace (e.g. acme-prod-sb). Operator-provisioned."""

    queue_name_prefix: str = "astrolift"
    max_size_in_megabytes: int = 1024
    enable_partitioning: bool = False
    client: Any | None = None


class ServiceBusDriver(ManagedServiceDriver):
    def __init__(self, *, config: ServiceBusConfig) -> None:
        self._config = config
        if config.client is not None:
            self._client = config.client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.servicebus import ServiceBusManagementClient

            self._client = ServiceBusManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
            )

    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        queue_name = self._queue_name(spec=spec)
        try:
            self._client.queues.create_or_update(
                resource_group_name=self._config.resource_group,
                namespace_name=self._config.namespace_name,
                queue_name=queue_name,
                parameters={
                    "max_size_in_megabytes": (
                        self._config.max_size_in_megabytes
                    ),
                    "enable_partitioning": (
                        self._config.enable_partitioning
                    ),
                    "default_message_time_to_live": "P14D",
                    "lock_duration": "PT30S",
                    "max_delivery_count": 10,
                    "dead_lettering_on_message_expiration": True,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return ProvisionResult(
                ok=False, handle="",
                message=f"create_queue: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=f"{KIND}/{queue_name}",
            message=f"Service Bus queue {queue_name} provisioned",
        )

    def update(self, spec: UpdateSpec) -> UpdateResult:
        return UpdateResult(
            ok=True, handle=spec.handle,
            message="Service Bus queue mutable settings via "
                    "create_or_update; size + delivery count "
                    "are operator-managed",
        )

    def deprovision(
        self, spec: DeprovisionSpec, *, delete_data: bool = False,
    ) -> DeprovisionResult:
        _, _, queue_name = spec.handle.partition("/")
        try:
            self._client.queues.delete(
                resource_group_name=self._config.resource_group,
                namespace_name=self._config.namespace_name,
                queue_name=queue_name,
            )
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "ResourceNotFoundError":
                return DeprovisionResult(
                    ok=True, handle=spec.handle, message="already gone",
                )
            return DeprovisionResult(
                ok=False, handle=spec.handle,
                message=str(exc), errors=[str(exc)],
            )
        return DeprovisionResult(
            ok=True, handle=spec.handle,
            message=f"queue {queue_name} deleted",
        )

    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, _, queue_name = handle.handle.partition("/")
        try:
            self._client.queues.get(
                resource_group_name=self._config.resource_group,
                namespace_name=self._config.namespace_name,
                queue_name=queue_name,
            )
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "ResourceNotFoundError":
                return ServiceStatus(
                    handle=handle.handle, state="deprovisioned",
                    message=f"queue {queue_name} does not exist",
                )
            return ServiceStatus(
                handle=handle.handle, state="error", message=str(exc),
            )
        return ServiceStatus(
            handle=handle.handle, state="available",
            message=f"queue {queue_name} reachable",
        )

    def binding(self, handle: ServiceHandle) -> Binding:
        _, _, queue_name = handle.handle.partition("/")
        endpoint = (
            f"sb://{self._config.namespace_name}.servicebus.windows.net/"
        )
        return Binding(
            env_vars={
                "SERVICEBUS_NAMESPACE": ValueRef(
                    literal=self._config.namespace_name,
                ),
                "SERVICEBUS_QUEUE": ValueRef(literal=queue_name),
                "SERVICEBUS_ENDPOINT": ValueRef(literal=endpoint),
            },
            iam_grants=[
                Grant(
                    resource=(
                        f"/subscriptions/{self._config.subscription_id}"
                        f"/resourceGroups/{self._config.resource_group}"
                        f"/providers/Microsoft.ServiceBus/namespaces"
                        f"/{self._config.namespace_name}/queues/{queue_name}"
                    ),
                    actions=[
                        "Azure Service Bus Data Sender",
                        "Azure Service Bus Data Receiver",
                    ],
                ),
            ],
            notes=(
                "Workload Identity grants sender + receiver roles via "
                "Microsoft.Authorization/roleAssignments."
            ),
        )

    def snapshot(self, handle):
        raise NotImplementedError(
            "Service Bus messages are ephemeral; no snapshot",
        )

    def restore(self, snapshot, target):
        raise NotImplementedError("Service Bus doesn't restore")

    def config_schema(self):
        return {
            "type": "object",
            "properties": {
                "max_size_in_megabytes": {
                    "type": "integer",
                    "enum": [1024, 2048, 5120, 10240, 20480, 40960, 81920],
                },
                "enable_partitioning": {"type": "boolean"},
            },
        }

    def binding_schema(self):
        return BindingSchema(env_vars={
            "SERVICEBUS_NAMESPACE": "Service Bus namespace name",
            "SERVICEBUS_QUEUE": "Queue name",
            "SERVICEBUS_ENDPOINT": "sb:// endpoint",
        })

    def _queue_name(self, *, spec: ProvisionSpec) -> str:
        # Queue names: 1-260 chars; alphanumeric + . - _ /
        parts = [
            self._config.queue_name_prefix,
            spec.organization_slug, spec.app_slug,
            spec.environment_name,
        ]
        if spec.service_handle_hint:
            parts.append(spec.service_handle_hint)
        raw = "-".join(p for p in parts if p)
        clean = "".join(
            c if (c.isalnum() or c in "-_./") else "-" for c in raw
        )
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:260]
