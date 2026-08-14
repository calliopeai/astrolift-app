"""Azure Service Bus queue managed-service driver (#47, #364).

Two driver classes ship from this module:

* ``ServiceBusDriver`` -- the original #47 MVP. Provisions a single
  Service Bus queue under an operator-managed namespace. Kept for
  backward-compat with the ``queue/servicebus`` plugin variant.
* ``AzureServiceBusDriver`` -- the #364 cross-cloud-symmetry
  driver. Provisions a topic + a default subscription named
  ``<service-name>-default`` per the Pub/Sub model so cross-cloud
  workloads see the same publish/subscribe shape.

Four-corner deprovision matrix on ``AzureServiceBusDriver``:

  delete_data=False, force_destroy=False (default):
    Refuse. Service Bus has no snapshot primitive and immediately
    deleting a disabled subscription would still discard messages.

  delete_data=False, force_destroy=True:
    Refuse for the same reason; force never bypasses data retention.

  delete_data=True, force_destroy=False:
    Skip drain (in-flight messages are lost), delete subscription
    + topic. Honours lock state.

  delete_data=True, force_destroy=True:
    Atomic -- skip drain, bypass lock state. Equivalent to a
    Terraform ``force_destroy = true`` semantic.
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


# ---- legacy #47 MVP driver -------------------------------------------


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

    @driver_op(
        cloud="azure",
        driver="queue_servicebus",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        queue_name = self._queue_name(spec=spec)
        try:
            self._client.queues.create_or_update(
                resource_group_name=self._config.resource_group,
                namespace_name=self._config.namespace_name,
                queue_name=queue_name,
                parameters={
                    "max_size_in_megabytes": (self._config.max_size_in_megabytes),
                    "enable_partitioning": (self._config.enable_partitioning),
                    "default_message_time_to_live": "P14D",
                    "lock_duration": "PT30S",
                    "max_delivery_count": 10,
                    "dead_lettering_on_message_expiration": True,
                },
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_queue: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=f"{KIND}/{queue_name}",
            message=f"Service Bus queue {queue_name} provisioned",
        )

    @driver_op(cloud="azure", driver="queue_servicebus")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message="Service Bus queue mutable settings via "
            "create_or_update; size + delivery count "
            "are operator-managed",
        )

    @driver_op(
        cloud="azure",
        driver="queue_servicebus",
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
        del force_destroy
        _, _, queue_name = spec.handle.partition("/")
        if not delete_data:
            try:
                self._client.queues.get(
                    resource_group_name=self._config.resource_group,
                    namespace_name=self._config.namespace_name,
                    queue_name=queue_name,
                )
            except Exception as exc:
                if type(exc).__name__ == "ResourceNotFoundError":
                    return DeprovisionResult(
                        ok=True,
                        handle=spec.handle,
                        message="already gone",
                    )
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=str(exc),
                    errors=[str(exc)],
                )
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"queue {queue_name} cannot be deleted while retaining "
                    "messages; drain it externally or pass delete_data=True"
                ),
                errors=["delete_data_required"],
                retryable=False,
            )
        try:
            self._client.queues.delete(
                resource_group_name=self._config.resource_group,
                namespace_name=self._config.namespace_name,
                queue_name=queue_name,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return DeprovisionResult(
                    ok=True,
                    handle=spec.handle,
                    message="already gone",
                )
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=str(exc),
                errors=[str(exc)],
            )
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=f"queue {queue_name} deleted",
        )

    @driver_op(cloud="azure", driver="queue_servicebus")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, _, queue_name = handle.handle.partition("/")
        try:
            self._client.queues.get(
                resource_group_name=self._config.resource_group,
                namespace_name=self._config.namespace_name,
                queue_name=queue_name,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return ServiceStatus(
                    handle=handle.handle,
                    state="deprovisioned",
                    message=f"queue {queue_name} does not exist",
                )
            return ServiceStatus(
                handle=handle.handle,
                state="error",
                message=str(exc),
            )
        return ServiceStatus(
            handle=handle.handle,
            state="available",
            message=f"queue {queue_name} reachable",
        )

    @driver_op(cloud="azure", driver="queue_servicebus")
    def binding(self, handle: ServiceHandle) -> Binding:
        _, _, queue_name = handle.handle.partition("/")
        endpoint = f"sb://{self._config.namespace_name}.servicebus.windows.net/"
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
            notes=("Workload Identity grants sender + receiver roles via Microsoft.Authorization/roleAssignments."),
        )

    @driver_op(cloud="azure", driver="queue_servicebus")
    def snapshot(self, handle):
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "managed_service.snapshot(ServiceBus) not supported -- Service "
            "Bus messages are ephemeral; no snapshot (#618)",
        )

    @driver_op(cloud="azure", driver="queue_servicebus")
    def restore(self, snapshot, target):
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "managed_service.restore(ServiceBus) not supported -- Service Bus doesn't restore from snapshot (#618)",
        )

    @driver_op(cloud="azure", driver="queue_servicebus", heartbeat=False)
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

    @driver_op(cloud="azure", driver="queue_servicebus", heartbeat=False)
    def binding_schema(self):
        return BindingSchema(
            env_vars={
                "SERVICEBUS_NAMESPACE": "Service Bus namespace name",
                "SERVICEBUS_QUEUE": "Queue name",
                "SERVICEBUS_ENDPOINT": "sb:// endpoint",
            }
        )

    def _queue_name(self, *, spec: ProvisionSpec) -> str:
        # Queue names: 1-260 chars; alphanumeric + . - _ /
        parts = [
            self._config.queue_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
        ]
        if spec.service_handle_hint:
            parts.append(spec.service_handle_hint)
        raw = "-".join(p for p in parts if p)
        clean = "".join(c if (c.isalnum() or c in "-_./") else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:260]


# ---- #364 cross-cloud-symmetry driver --------------------------------


class AzureServiceBusError(Exception):
    """Distinct from the generic plugin error so the control plane can
    tell managed-service failures from infra-driver failures."""


@dataclass(frozen=True)
class AzureServiceBusConfig:
    """Driver-instance config for ``AzureServiceBusDriver``."""

    subscription_id: str
    resource_group: str
    namespace_name: str
    """Service Bus namespace (e.g. acme-prod-sb). Operator-provisioned
    via the install workflow; the driver creates topic + default
    subscription within it."""

    topic_name_prefix: str = "astrolift"
    default_message_ttl: str = "P14D"
    """ISO-8601 duration; 14 days matches the AWS SQS / GCP Pub/Sub
    defaults for cross-cloud symmetry."""

    max_size_in_megabytes: int = 1024
    enable_partitioning: bool = False
    dead_lettering_on_message_expiration: bool = True
    max_delivery_count: int = 10
    lock_duration: str = "PT30S"

    client: Any | None = None
    """Injected ``ServiceBusManagementClient`` for tests."""


def _tags_for(spec: ProvisionSpec) -> dict[str, str]:
    base = {
        "astrolift.io/managed-by": "platform",
        "astrolift.io/organization": spec.organization_slug,
        "astrolift.io/app": spec.app_slug,
        "astrolift.io/environment": spec.environment_name,
        "astrolift.io/cluster": spec.tenant_cluster_id,
        "astrolift.io/isolation": spec.isolation,
    }
    # Per-binding cost-attribution keys (#438). Azure allows the
    # slash + dot form so keys stay identical to the canonical
    # platform schema.
    if spec.binding_id:
        base["astrolift.io/binding"] = spec.binding_id
    if spec.managed_service_id:
        base["astrolift.io/managed_service_id"] = spec.managed_service_id
    base.update({f"astrolift.io/extra/{k}": v for k, v in (spec.tags or {}).items()})
    return base


class AzureServiceBusDriver(ManagedServiceDriver):
    """Topic + default subscription per service handle.

    Service Bus distinguishes queues (single-consumer) from topics
    (multi-subscription). The platform's cross-cloud queue contract
    is closer to the topic-with-subscriptions shape (matches Pub/Sub
    + SNS), so this driver provisions a topic and a default
    subscription named ``<topic>-default`` per the Pub/Sub model.
    """

    def __init__(self, *, config: AzureServiceBusConfig) -> None:
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

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="azure",
        driver="queue_servicebus_v2",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        topic_name = self._topic_name(spec=spec)
        sub_name = self._default_sub_name(topic_name=topic_name)
        cfg = spec.config or {}
        try:
            self._client.topics.create_or_update(
                resource_group_name=self._config.resource_group,
                namespace_name=self._config.namespace_name,
                topic_name=topic_name,
                parameters={
                    "max_size_in_megabytes": int(
                        cfg.get(
                            "max_size_in_megabytes",
                            self._config.max_size_in_megabytes,
                        ),
                    ),
                    "enable_partitioning": bool(
                        cfg.get(
                            "enable_partitioning",
                            self._config.enable_partitioning,
                        ),
                    ),
                    "default_message_time_to_live": (
                        cfg.get("default_message_ttl") or self._config.default_message_ttl
                    ),
                    "support_ordering": True,
                    "userMetadata": _user_metadata(spec),
                },
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_topic: {exc}",
                errors=[str(exc)],
            )
        try:
            self._client.subscriptions.create_or_update(
                resource_group_name=self._config.resource_group,
                namespace_name=self._config.namespace_name,
                topic_name=topic_name,
                subscription_name=sub_name,
                parameters={
                    "lock_duration": (cfg.get("lock_duration") or self._config.lock_duration),
                    "max_delivery_count": int(
                        cfg.get(
                            "max_delivery_count",
                            self._config.max_delivery_count,
                        ),
                    ),
                    "dead_lettering_on_message_expiration": bool(
                        cfg.get(
                            "dead_lettering_on_message_expiration",
                            self._config.dead_lettering_on_message_expiration,
                        ),
                    ),
                    "default_message_time_to_live": (
                        cfg.get("default_message_ttl") or self._config.default_message_ttl
                    ),
                },
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_subscription: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=self._handle_for(topic_name=topic_name),
            message=(f"Service Bus topic {topic_name} + subscription {sub_name} provisioned"),
        )

    @driver_op(cloud="azure", driver="queue_servicebus_v2")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        topic_name = self._topic_name_from_handle(spec.handle)
        cfg = spec.config or {}

        topic_body: dict[str, Any] = {}
        if "max_size_in_megabytes" in cfg:
            topic_body["max_size_in_megabytes"] = int(cfg["max_size_in_megabytes"])
        if "enable_partitioning" in cfg:
            topic_body["enable_partitioning"] = bool(cfg["enable_partitioning"])
        if cfg.get("default_message_ttl"):
            topic_body["default_message_time_to_live"] = cfg["default_message_ttl"]
        if not topic_body:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided -- no-op",
            )
        try:
            self._client.topics.create_or_update(
                resource_group_name=self._config.resource_group,
                namespace_name=self._config.namespace_name,
                topic_name=topic_name,
                parameters=topic_body,
            )
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"create_or_update: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=f"topic {topic_name} update queued",
        )

    @driver_op(
        cloud="azure",
        driver="queue_servicebus_v2",
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
        topic_name = self._topic_name_from_handle(spec.handle)
        sub_name = self._default_sub_name(topic_name=topic_name)

        # Probe topic existence first; idempotent gone path.
        topic_state = self._topic_state(topic_name)
        if topic_state is None:
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=f"topic {topic_name} already gone",
            )

        if not delete_data:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"topic {topic_name} cannot be deleted while retaining "
                    "messages; drain every subscription externally or pass "
                    "delete_data=True"
                ),
                errors=["delete_data_required"],
                retryable=False,
            )

        # Lock check: namespace + topic-level locks. If the topic has
        # an active lock-state and force_destroy=False, refuse.
        if _is_locked(topic_state) and not force_destroy:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"topic {topic_name} is locked (status="
                    f"{_status_of(topic_state)}) -- pass "
                    f"force_destroy=True to bypass"
                ),
                errors=["topic_locked"],
            )

        # Subscription delete first; topic delete refuses while
        # subscriptions are attached (similar to the SNS/Pub-Sub
        # pattern).
        try:
            self._client.subscriptions.delete(
                resource_group_name=self._config.resource_group,
                namespace_name=self._config.namespace_name,
                topic_name=topic_name,
                subscription_name=sub_name,
            )
        except Exception as exc:
            if type(exc).__name__ != "ResourceNotFoundError":
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=f"delete_subscription: {exc}",
                    errors=[str(exc)],
                )

        try:
            self._client.topics.delete(
                resource_group_name=self._config.resource_group,
                namespace_name=self._config.namespace_name,
                topic_name=topic_name,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return DeprovisionResult(
                    ok=True,
                    handle=spec.handle,
                    message=f"topic {topic_name} already gone",
                )
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete_topic: {exc}",
                errors=[str(exc)],
            )
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"topic {topic_name} + subscription {sub_name} deleted (messages purged, force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="azure", driver="queue_servicebus_v2")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        topic_name = self._topic_name_from_handle(handle.handle)
        topic = self._topic_state(topic_name)
        if topic is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=f"topic {topic_name} does not exist",
            )
        sb_status = _status_of(topic)
        return ServiceStatus(
            handle=handle.handle,
            state=_SB_STATUS_TO_PROTOCOL.get(sb_status, "available"),
            message=f"azure reports {sb_status}",
        )

    @driver_op(cloud="azure", driver="queue_servicebus_v2")
    def binding(self, handle: ServiceHandle) -> Binding:
        topic_name = self._topic_name_from_handle(handle.handle)
        sub_name = self._default_sub_name(topic_name=topic_name)
        endpoint = f"sb://{self._config.namespace_name}.servicebus.windows.net/"
        topic_resource = (
            f"/subscriptions/{self._config.subscription_id}"
            f"/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.ServiceBus/namespaces"
            f"/{self._config.namespace_name}/topics/{topic_name}"
        )
        subscription_resource = f"{topic_resource}/subscriptions/{sub_name}"
        return Binding(
            env_vars={
                "SERVICEBUS_NAMESPACE": ValueRef(
                    literal=self._config.namespace_name,
                ),
                "SERVICEBUS_TOPIC": ValueRef(literal=topic_name),
                "SERVICEBUS_SUBSCRIPTION": ValueRef(literal=sub_name),
                "SERVICEBUS_ENDPOINT": ValueRef(literal=endpoint),
            },
            iam_grants=[
                Grant(
                    resource=topic_resource,
                    actions=["Azure Service Bus Data Sender"],
                ),
                Grant(
                    resource=subscription_resource,
                    actions=["Azure Service Bus Data Receiver"],
                ),
            ],
            notes=(
                "Sender role on the topic + receiver role on the "
                "default subscription, via Workload Identity + "
                "Microsoft.Authorization/roleAssignments."
            ),
        )

    @driver_op(cloud="azure", driver="queue_servicebus_v2")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise AzureServiceBusError(
            "Service Bus messages are ephemeral; no snapshot support",
        )

    @driver_op(cloud="azure", driver="queue_servicebus_v2")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        raise AzureServiceBusError(
            "Service Bus has no restore counterpart -- in-flight messages have no recovery value",
        )

    @driver_op(cloud="azure", driver="queue_servicebus_v2", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "max_size_in_megabytes": {
                    "type": "integer",
                    "enum": [1024, 2048, 5120, 10240, 20480, 40960, 81920],
                },
                "enable_partitioning": {"type": "boolean"},
                "default_message_ttl": {
                    "type": "string",
                    "description": ("ISO-8601 duration, e.g. P14D for 14 days."),
                },
                "lock_duration": {
                    "type": "string",
                    "description": ("ISO-8601 duration, e.g. PT30S for 30 seconds."),
                },
                "max_delivery_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                },
                "dead_lettering_on_message_expiration": {"type": "boolean"},
            },
        }

    @driver_op(cloud="azure", driver="queue_servicebus_v2", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "SERVICEBUS_NAMESPACE": "Service Bus namespace name",
                "SERVICEBUS_TOPIC": "Topic name (publisher endpoint)",
                "SERVICEBUS_SUBSCRIPTION": ("Default subscription name (consumer endpoint)"),
                "SERVICEBUS_ENDPOINT": "sb:// fully qualified namespace",
            }
        )

    # ---- internals ----------------------------------------------------

    def _topic_state(self, topic_name: str) -> Any | None:
        try:
            return self._client.topics.get(
                resource_group_name=self._config.resource_group,
                namespace_name=self._config.namespace_name,
                topic_name=topic_name,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return None
            if "ResourceNotFound" in str(exc):
                return None
            raise

    def _topic_name(self, *, spec: ProvisionSpec) -> str:
        # Service Bus topic names: 1-260 chars; alphanumeric +
        # . - _ /. We use the same safe subset as the queue driver.
        parts = [
            self._config.topic_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
        ]
        if spec.service_handle_hint:
            parts.append(spec.service_handle_hint)
        raw = "-".join(p for p in parts if p)
        clean = "".join(c if (c.isalnum() or c in "-_./") else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:260]

    def _default_sub_name(self, *, topic_name: str) -> str:
        # Subscription names: 1-50 chars; alphanumeric + . - _.
        # The "<topic>-default" suffix per the issue spec; if that
        # would exceed 50 chars we shorten the topic-derived prefix.
        suffix = "-default"
        max_topic = 50 - len(suffix)
        return f"{topic_name[:max_topic]}{suffix}"

    def _handle_for(self, *, topic_name: str) -> str:
        return f"{KIND}/{topic_name}"

    def _topic_name_from_handle(self, handle: str) -> str:
        if "/" not in handle:
            raise AzureServiceBusError(
                f"handle {handle!r} must be '<kind>/<topic>'",
            )
        kind, _, topic_name = handle.partition("/")
        if not kind or not topic_name:
            raise AzureServiceBusError(
                f"handle {handle!r} has empty component",
            )
        return topic_name


# ----- module-level helpers --------------------------------------------


def _user_metadata(spec: ProvisionSpec) -> str:
    """Service Bus topic ``userMetadata`` is a free-form string,
    capped at 1024 chars. We pack the astrolift tag set as a
    sorted ``k=v;`` blob so operators can grep for ownership."""
    items = sorted(_tags_for(spec).items())
    blob = ";".join(f"{k}={v}" for k, v in items)
    return blob[:1024]


def _status_of(topic: Any) -> str:
    status = getattr(topic, "status", None)
    if status is not None:
        return str(status)
    if isinstance(topic, dict) and "status" in topic:
        return str(topic["status"])
    return "Active"


def _is_locked(topic: Any) -> bool:
    """Service Bus has no explicit per-topic lock state, but the
    ``status`` attribute can flag the topic as ``Disabled``,
    ``SendDisabled``, or ``ReceiveDisabled``. We treat any of the
    operator-set Disabled-* states as a soft lock the operator must
    explicitly bypass via force_destroy=True. Ordinary ``Active``
    + ``Creating`` + ``Deleting`` are not locks."""
    status = _status_of(topic)
    return status in {"Disabled"}


_SB_STATUS_TO_PROTOCOL = {
    "Active": "available",
    "Creating": "provisioning",
    "Deleting": "deprovisioning",
    "Disabled": "error",
    "ReceiveDisabled": "available",
    "SendDisabled": "available",
    "Renaming": "updating",
    "Restoring": "updating",
    "Unknown": "updating",
}
