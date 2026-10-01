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

import re
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from _sdk._telemetry import driver_op
from _sdk.azure_ownership import (
    OWNERSHIP_ERROR_CODE,
    AzureOperation,
    AzureOwnershipError,
    owner_of,
    verify_azure_ownership,
)
from _sdk.azure_tags import AzureTagError
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
    unsupported_update,
)
from _sdk.managed_service_tags import LEGACY_KEYS
from azure.managed.tags import BINDING_TAG, MANAGED_BY_TAG, MANAGED_SERVICE_ID_TAG
from azure.managed.tags import arm_tags_for as _tags_for

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
        try:
            queue_name = self._queue_name(spec=spec)
            metadata = _queue_metadata(spec)
            budget = _QueueCallBudget()
            existing = self._get_queue(queue_name, spec, budget)
            from azure.mgmt.servicebus.models import SBQueue, SBQueueProperties

            parameters = SBQueue(
                properties=SBQueueProperties(
                    max_size_in_megabytes=self._config.max_size_in_megabytes,
                    enable_partitioning=self._config.enable_partitioning,
                    default_message_time_to_live=timedelta(days=14),
                    lock_duration=timedelta(seconds=30),
                    max_delivery_count=10,
                    dead_lettering_on_message_expiration=True,
                    user_metadata=metadata,
                )
            )
            # The SDK serializes typed models into properties, unlike a raw dict.
            result = self._client.queues.create_or_update(
                **self._target(queue_name),
                parameters=parameters,
                **budget.options(),
            )
            self._assert_queue_owned(result, queue_name, spec)
            if existing is None:
                self._get_queue(queue_name, spec, budget, required=True)
        except Exception as exc:
            return ProvisionResult(ok=False, handle="", message=_queue_error_message(exc), errors=_queue_errors(exc))
        return ProvisionResult(ok=True, handle=f"{KIND}/{queue_name}", message="Service Bus queue provisioned")

    @driver_op(cloud="azure", driver="queue_servicebus")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        return unsupported_update(spec.handle, "Service Bus queue settings reconcile on provision, not in place")

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
        try:
            queue_name = self._validated_target(spec)
            budget = _QueueCallBudget()
            queue = self._get_queue(queue_name, spec, budget)
            if queue is None:
                return DeprovisionResult(ok=True, handle=spec.handle, message="already gone")
            if not delete_data:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message="Service Bus cannot retain messages on deletion; explicitly authorize delete_data=True",
                    errors=["delete_data_required"],
                    retryable=False,
                )
            try:
                self._client.queues.delete(**self._target(queue_name), **budget.options())
            except Exception as exc:
                if not _queue_absent(exc):
                    raise
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=_queue_error_message(exc),
                errors=_queue_errors(exc),
                retryable=not isinstance(exc, (AzureOwnershipError, AzureTagError)),
            )
        return DeprovisionResult(ok=True, handle=spec.handle, message="Service Bus queue deleted")

    @driver_op(cloud="azure", driver="queue_servicebus")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            queue_name = self._validated_target(handle)
            queue = self._get_queue(queue_name, handle, _QueueCallBudget())
            if queue is None:
                return ServiceStatus(handle=handle.handle, state="deprovisioned", message="queue does not exist")
            observed = _field(queue, "status", default="Unknown")
            state = _SB_STATUS_TO_PROTOCOL.get(str(getattr(observed, "value", observed)), "error")
            return ServiceStatus(handle=handle.handle, state=state, message="current owned queue observed")
        except Exception as exc:
            return ServiceStatus(handle=handle.handle, state="error", message=_queue_error_message(exc))

    @driver_op(cloud="azure", driver="queue_servicebus")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        del config
        queue_name = self._validated_target(handle)
        self._get_queue(queue_name, handle, _QueueCallBudget(), required=True)
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
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "managed_service.snapshot(ServiceBus) not supported -- Service "
            "Bus messages are ephemeral; no snapshot (#618)",
        )

    @driver_op(cloud="azure", driver="queue_servicebus")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "managed_service.restore(ServiceBus) not supported -- Service Bus doesn't restore from snapshot (#618)",
        )

    @driver_op(cloud="azure", driver="queue_servicebus", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
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
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "SERVICEBUS_NAMESPACE": "Service Bus namespace name",
                "SERVICEBUS_QUEUE": "Queue name",
                "SERVICEBUS_ENDPOINT": "sb:// endpoint",
            }
        )

    def editable_fields(self) -> list[str]:
        """No config key can be applied without a reprovision (#1376)."""
        # max_size_in_megabytes / enable_partitioning are applied by the
        # create_or_update call in provision(); this driver never issues one
        # from update(). The queue/azure_servicebus driver does.
        return []

    def _queue_name(self, *, spec: ProvisionSpec) -> str:
        identity = _queue_identity(spec)
        if spec.recorded_handle:
            return self._validated_target(ServiceHandle(spec.recorded_handle, managed_service_id=identity))
        prefix = re.sub(r"[^a-z0-9-]+", "-", self._config.queue_name_prefix.lower()).strip("-")[:227].rstrip("-")
        name = f"{prefix}-{UUID(identity).hex}" if prefix else UUID(identity).hex
        self._target(name)
        return name

    def _validated_target(self, source: object) -> str:
        _queue_identity(source)
        handle = str(getattr(source, "handle", ""))
        kind, sep, name = handle.partition("/")
        if kind != KIND or not sep:
            raise AzureOwnershipError("Service Bus queue handle does not name a queue")
        self._target(name)
        return name

    def _target(self, name: str) -> dict[str, str]:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,258}[A-Za-z0-9])?", name) or any(
            part in {"", ".", ".."} for part in name.split("/")
        ):
            raise AzureOwnershipError("invalid recorded Service Bus queue name")
        cfg = self._config
        if (
            not re.fullmatch(r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", cfg.subscription_id)
            or not UUID(cfg.subscription_id).int
        ):
            raise AzureOwnershipError("Service Bus subscription identity is invalid")
        if (
            not 1 <= len(cfg.resource_group) <= 90
            or any(not (char.isalnum() or char in "_.()-") for char in cfg.resource_group)
            or cfg.resource_group.endswith(".")
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{4,48}[A-Za-z0-9]", cfg.namespace_name)
        ):
            raise AzureOwnershipError("Service Bus placement identity is invalid")
        return dict(resource_group_name=cfg.resource_group, namespace_name=cfg.namespace_name, queue_name=name)

    def _arm_id(self, name: str) -> str:
        return (
            f"/subscriptions/{self._config.subscription_id}/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.ServiceBus/namespaces/{self._config.namespace_name}/queues/{name}"
        )

    def _assert_queue_owned(self, queue: Any, name: str, source: object) -> None:
        _queue_identity(source)
        actual_id = _field(queue, "id", default="")
        if not isinstance(actual_id, str) or not actual_id:
            raise _QueueOwnershipUnknown("Service Bus returned no immutable ARM target")
        if actual_id.casefold() != self._arm_id(name).casefold():
            raise AzureOwnershipError("Service Bus returned a different ARM target")
        blob = _field(queue, "user_metadata", "userMetadata", default="")
        if not isinstance(blob, str) or len(blob) > 1024:
            raise AzureOwnershipError("invalid Service Bus queue ownership metadata")
        envelope: dict[str, str] = {}
        for entry in blob.split(";"):
            key, sep, value = entry.partition("=")
            if not sep or not key or key in envelope:
                raise AzureOwnershipError("ambiguous Service Bus queue ownership metadata")
            envelope[key] = value
        expected = _queue_identity(source)
        if any(key in envelope and envelope[key] != expected for key in LEGACY_KEYS["azure"]):
            raise AzureOwnershipError("conflicting Service Bus queue ownership aliases")
        verify_azure_ownership(envelope, owner_of(source), operation=AzureOperation.UPDATE, resource="queue")

    def _get_queue(self, name: str, source: object, budget: _QueueCallBudget, *, required: bool = False) -> Any:
        try:
            queue = self._client.queues.get(**self._target(name), **budget.options())
        except Exception as exc:
            if not _queue_absent(exc):
                raise
            if required:
                raise _QueueOwnershipUnknown("current Service Bus queue is absent") from exc
            return None
        self._assert_queue_owned(queue, name, source)
        return queue


class _QueueOwnershipUnknown(RuntimeError):
    pass


class _QueueCallBudget:
    def __init__(self) -> None:
        self.deadline = time.monotonic() + 20

    def options(self) -> dict[str, Any]:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _QueueOwnershipUnknown("Service Bus queue operation deadline exhausted")
        timeout = min(5.0, remaining / 2)
        return dict(connection_timeout=timeout, read_timeout=timeout, retry_total=0, redirect_max=0)


def _queue_uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise AzureOwnershipError("Service Bus requires an immutable canonical nonzero UUID") from None
    if parsed.int == 0 or str(parsed) != value:
        raise AzureOwnershipError("Service Bus requires an immutable canonical nonzero UUID")
    return value


def _queue_identity(source: object) -> str:
    return _queue_uuid(str(getattr(source, "managed_service_id", "") or ""))


def _queue_metadata(spec: ProvisionSpec) -> str:
    _queue_identity(spec)
    tags = _tags_for(spec)
    if any(";" in key or "=" in key or ";" in value for key, value in tags.items()):
        raise AzureOwnershipError("Service Bus metadata tags cannot contain envelope delimiters")
    blob = _user_metadata(spec)
    # Reject instead of producing a partial final entry or losing descriptive tags.
    if len(";".join(f"{key}={value}" for key, value in tags.items())) > 1024:
        raise AzureOwnershipError("Service Bus ownership metadata exceeds 1024 characters")
    return blob


def _queue_absent(exc: Exception) -> bool:
    from azure.core.exceptions import ResourceNotFoundError

    return isinstance(exc, ResourceNotFoundError)


def _queue_errors(exc: Exception) -> list[str]:
    if isinstance(exc, (AzureOwnershipError, AzureTagError)):
        return [OWNERSHIP_ERROR_CODE, "ownership_refused"]
    return ["ownership_unknown"]


def _queue_error_message(exc: Exception) -> str:
    if isinstance(exc, (AzureOwnershipError, AzureTagError)):
        return f"Service Bus queue ownership refused: {exc}"
    return "Service Bus queue ownership or operation could not be confirmed"


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
    handle_kind: str = "queue"
    """Portable handle kind. ``queue`` preserves the legacy alias;
    ``topic`` exposes the same Azure resource through the correct topic
    contract."""

    location: str = ""
    """Namespace region used by the portable topic binding."""
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

    def __post_init__(self) -> None:
        if self.handle_kind not in {"queue", "topic"}:
            raise ValueError("Service Bus handle_kind must be queue or topic")


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

    @driver_op(cloud="azure", driver="queue_servicebus_v2", audit=True, sensitive_kind="managed_service_provision")
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        try:
            metadata = _topic_metadata(spec)
            topic_parameters, child_parameters = self._parameters(spec.config, metadata)
            if spec.recorded_handle:
                target = self._saved_target(spec.recorded_handle, spec)
            else:
                identity = UUID(_topic_identity(spec)).hex
                prefix = re.sub(r"[^a-z0-9-]+", "-", self._config.topic_name_prefix.lower()).strip("-")[:9].rstrip("-")
                name = f"{prefix}-{identity}" if prefix else identity
                target = self._coordinates(name, name + "-default")
            budget = _TopicCallBudget()
            topic, child = self._pair(target, spec, budget)
            if spec.recorded_handle and (topic is None or child is None):
                raise _TopicOwnershipUnknown("recorded topic or subscription is absent; refusing recreation")
            if topic is None and child is not None:
                raise _TopicOwnershipUnknown("subscription remains without the recorded topic")
            if topic is not None:
                self._inventory(target, spec, budget, child_present=child is not None)
            topic = self._client.topics.create_or_update(
                **target.topic_args(), parameters=topic_parameters, **budget.options()
            )
            self._assert_owned(topic, target.topic_id, spec)
            child = self._client.subscriptions.create_or_update(
                **target.child_args(), parameters=child_parameters, **budget.options()
            )
            self._assert_owned(child, target.child_id, spec)
        except Exception as exc:
            return ProvisionResult(ok=False, handle="", message=_topic_error_message(exc), errors=_topic_errors(exc))
        return ProvisionResult(
            ok=True, handle=target.handle(self._config.handle_kind), message="owned topic and subscription provisioned"
        )

    def editable_fields(self) -> list[str]:
        return ["max_size_in_megabytes", "default_message_ttl"]

    @driver_op(cloud="azure", driver="queue_servicebus_v2")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        cfg = spec.config or {}
        if not any(key in cfg for key in self.editable_fields()) or spec.size is not None:
            return unsupported_update(spec.handle, "Only topic capacity and TTL can update in place")
        try:
            target = self._saved_target(spec.handle, spec)
            budget = _TopicCallBudget()
            topic, child = self._pair(target, spec, budget, required=True)
            self._inventory(target, spec, budget, child_present=True)
            # Full desired config reaches the driver. Noneditable values must already match.
            immutable = {
                "enable_partitioning": (_field(topic, "enable_partitioning"), cfg.get("enable_partitioning")),
                "lock_duration": (
                    _field(child, "lock_duration"),
                    _topic_duration(cfg["lock_duration"]) if "lock_duration" in cfg else None,
                ),
                "max_delivery_count": (_field(child, "max_delivery_count"), cfg.get("max_delivery_count")),
                "dead_lettering_on_message_expiration": (
                    _field(child, "dead_lettering_on_message_expiration"),
                    cfg.get("dead_lettering_on_message_expiration"),
                ),
            }
            allowed = set(self.editable_fields()) | set(immutable)
            if set(cfg) - allowed or any(
                key in cfg and current != desired for key, (current, desired) in immutable.items()
            ):
                return unsupported_update(spec.handle, "Subscription and immutable topic settings require reprovision")
            self._parameters(cfg, _field(topic, "user_metadata"))
            from azure.mgmt.servicebus.models import SBTopic, SBTopicProperties

            values: dict[str, Any] = {"user_metadata": _field(topic, "user_metadata")}
            if "max_size_in_megabytes" in cfg:
                values["max_size_in_megabytes"] = _topic_capacity(cfg["max_size_in_megabytes"])
            if "default_message_ttl" in cfg:
                values["default_message_time_to_live"] = _topic_duration(cfg["default_message_ttl"])
            parameters = SBTopic(properties=SBTopicProperties(**values))
            observed = self._client.topics.create_or_update(
                **target.topic_args(), parameters=parameters, **budget.options()
            )
            self._assert_owned(observed, target.topic_id, spec)
            for key, value in values.items():
                if _field(observed, key) != value:
                    raise _TopicOwnershipUnknown("provider did not confirm requested topic settings")
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=_topic_error_message(exc),
                errors=_topic_errors(exc),
                retryable=not isinstance(exc, (AzureOwnershipError, AzureTagError)),
            )
        return UpdateResult(ok=True, handle=spec.handle, message="requested owned topic settings observed")

    @driver_op(cloud="azure", driver="queue_servicebus_v2", audit=True, sensitive_kind="managed_service_deprovision")
    def deprovision(
        self, spec: DeprovisionSpec, *, delete_data: bool = False, force_destroy: bool = False
    ) -> DeprovisionResult:
        try:
            target = self._saved_target(spec.handle, spec)
            budget = _TopicCallBudget()
            topic, child = self._pair(target, spec, budget)
            if topic is None:
                if child is not None:
                    raise _TopicOwnershipUnknown("recorded subscription remains without its topic")
                return DeprovisionResult(
                    ok=True, handle=spec.handle, message="recorded topic and subscription both absent"
                )
            self._inventory(target, spec, budget, child_present=child is not None)
            if not delete_data:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message="Service Bus cannot retain messages on deletion; explicitly authorize delete_data=True",
                    errors=["delete_data_required"],
                    retryable=False,
                )
            status = _topic_status(topic)
            if status not in {"Active", "Disabled", "SendDisabled", "ReceiveDisabled"}:
                raise _TopicOwnershipUnknown("topic is not in a confirmed deletable state")
            if status != "Active" and not force_destroy:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message="topic locked; explicitly authorize force_destroy=True",
                    errors=["topic_locked"],
                    retryable=False,
                )
            if child is not None:
                self._delete(self._client.subscriptions, target.child_args(), budget)
            self._delete(self._client.topics, target.topic_args(), budget)
            topic, child = self._pair(target, spec, budget)
            if topic is not None or child is not None:
                raise _TopicOwnershipUnknown("deletion absence could not be confirmed")
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=_topic_error_message(exc),
                errors=_topic_errors(exc),
                retryable=not isinstance(exc, (AzureOwnershipError, AzureTagError)),
            )
        return DeprovisionResult(
            ok=True, handle=spec.handle, message="recorded topic and subscription deleted; messages purged"
        )

    @driver_op(cloud="azure", driver="queue_servicebus_v2")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            target = self._saved_target(handle.handle, handle)
            topic, child = self._pair(target, handle, _TopicCallBudget())
            if topic is None and child is None:
                return ServiceStatus(
                    handle=handle.handle, state="deprovisioned", message="recorded topic and subscription both absent"
                )
            if topic is None or child is None:
                raise _TopicOwnershipUnknown("recorded topic/subscription pair is incomplete")
            statuses = [_topic_status(row) for row in (topic, child)]
            states = {
                "Active": "available",
                "Creating": "provisioning",
                "Deleting": "deprovisioning",
                "Renaming": "updating",
                "Restoring": "updating",
            }
            state = next((states.get(value, "error") for value in statuses if value != "Active"), "available")
            return ServiceStatus(
                handle=handle.handle, state=state, message="current owned topic and subscription observed"
            )
        except Exception as exc:
            return ServiceStatus(handle=handle.handle, state="error", message=_topic_error_message(exc))

    @driver_op(cloud="azure", driver="queue_servicebus_v2")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        del config
        target = self._saved_target(handle.handle, handle)
        budget = _TopicCallBudget()
        topic, child = self._pair(target, handle, budget, required=True)
        self._inventory(target, handle, budget, child_present=True)
        if any(_topic_status(row) != "Active" for row in (topic, child)):
            raise _TopicOwnershipUnknown("topic/subscription is not active for binding")
        env = {
            "SERVICEBUS_NAMESPACE": ValueRef(literal=target.namespace),
            "SERVICEBUS_TOPIC": ValueRef(literal=target.topic),
            "SERVICEBUS_SUBSCRIPTION": ValueRef(literal=target.child),
            "SERVICEBUS_ENDPOINT": ValueRef(literal=f"sb://{target.namespace}.servicebus.windows.net/"),
        }
        if self._config.handle_kind == "topic":
            env.update(
                TOPIC_ARN_OR_ID=ValueRef(literal=target.topic_id),
                TOPIC_NAME=ValueRef(literal=target.topic),
                TOPIC_REGION=ValueRef(literal=self._config.location),
            )
        return Binding(
            env_vars=env,
            iam_grants=[
                Grant(resource=target.topic_id, actions=["Azure Service Bus Data Sender"]),
                Grant(resource=target.child_id, actions=["Azure Service Bus Data Receiver"]),
            ],
            notes="Workload Identity sender on exact topic; receiver on exact saved subscription",
        )

    @driver_op(cloud="azure", driver="queue_servicebus_v2")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise AzureServiceBusError("Service Bus messages are ephemeral; no snapshot support")

    @driver_op(cloud="azure", driver="queue_servicebus_v2")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        raise AzureServiceBusError("Service Bus has no supported restore counterpart")

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
        env_vars = {
            "SERVICEBUS_NAMESPACE": "Service Bus namespace name",
            "SERVICEBUS_TOPIC": "Topic name (publisher endpoint)",
            "SERVICEBUS_SUBSCRIPTION": "Default subscription name (consumer endpoint)",
            "SERVICEBUS_ENDPOINT": "sb:// fully qualified namespace",
        }
        if self._config.handle_kind == "topic":
            env_vars.update(
                TOPIC_ARN_OR_ID="Azure topic resource ID",
                TOPIC_NAME="Portable topic name",
                TOPIC_REGION="Azure namespace region",
            )
        return BindingSchema(env_vars=env_vars)

    def _coordinates(self, topic: str, child: str) -> _TopicTarget:
        cfg = self._config
        try:
            subscription = str(UUID(cfg.subscription_id))
        except (ValueError, TypeError, AttributeError):
            raise AzureOwnershipError("invalid Azure subscription identity") from None
        if not UUID(subscription).int or cfg.subscription_id.casefold() != subscription:
            raise AzureOwnershipError("invalid Azure subscription identity")
        if (
            not 1 <= len(cfg.resource_group) <= 90
            or any(not (c.isalnum() or c in "_.()-") for c in cfg.resource_group)
            or cfg.resource_group.endswith(".")
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{4,48}[A-Za-z0-9]", cfg.namespace_name)
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,258}[A-Za-z0-9])?", topic)
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,48}[A-Za-z0-9])?", child)
        ):
            raise AzureOwnershipError("invalid Service Bus saved placement/entity coordinates")
        target = _TopicTarget(subscription, cfg.resource_group, cfg.namespace_name, topic, child)
        if len(target.handle(cfg.handle_kind)) > 512:
            raise AzureOwnershipError("Service Bus saved target exceeds handle storage limit")
        return target

    def _saved_target(self, handle: str, source: object) -> _TopicTarget:
        _topic_identity(source)
        parts = handle.split("/")
        if len(parts) != 7 or parts[0] != self._config.handle_kind or parts[1] != "arm-v1":
            raise _TopicOwnershipUnknown("saved Service Bus placement and child provenance are unavailable")
        target = self._coordinates(parts[5], parts[6])
        if tuple(parts[2:5]) != (target.subscription, target.group, target.namespace):
            raise AzureOwnershipError("saved Service Bus placement differs from current provider coordinates")
        return target

    def _assert_owned(self, row: Any, arm_id: str, source: object) -> None:
        expected = _topic_identity(source)
        actual = _field(row, "id", default="")
        if not isinstance(actual, str) or not actual:
            raise _TopicOwnershipUnknown("provider returned no ARM identity")
        if _topic_arm_key(actual) != _topic_arm_key(arm_id):
            raise AzureOwnershipError("provider returned a different ARM identity")
        blob = _field(row, "user_metadata", "userMetadata", default="")
        if not isinstance(blob, str) or not blob or len(blob) > 1024:
            raise _TopicOwnershipUnknown("current entity ownership metadata is unavailable")
        envelope: dict[str, str] = {}
        for entry in blob.split(";"):
            key, sep, value = entry.partition("=")
            if not sep or not key or key in envelope:
                raise AzureOwnershipError("ambiguous Service Bus ownership metadata")
            envelope[key] = value
        if any(key in envelope and envelope[key] != expected for key in LEGACY_KEYS["azure"]):
            raise AzureOwnershipError("conflicting Service Bus ownership aliases")
        verify_azure_ownership(
            envelope, owner_of(source), operation=AzureOperation.UPDATE, resource="topic/subscription"
        )

    def _get(self, operations: Any, args: dict[str, str], arm_id: str, source: object, budget: _TopicCallBudget) -> Any:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            row = operations.get(**args, **budget.options())
        except ResourceNotFoundError:
            return None
        self._assert_owned(row, arm_id, source)
        return row

    def _pair(
        self, target: _TopicTarget, source: object, budget: _TopicCallBudget, *, required: bool = False
    ) -> tuple[Any, Any]:
        topic = self._get(self._client.topics, target.topic_args(), target.topic_id, source, budget)
        child = self._get(self._client.subscriptions, target.child_args(), target.child_id, source, budget)
        if required and (topic is None or child is None):
            raise _TopicOwnershipUnknown("recorded topic/subscription is absent")
        return topic, child

    def _inventory(
        self, target: _TopicTarget, source: object, budget: _TopicCallBudget, *, child_present: bool
    ) -> None:
        from urllib.parse import unquote, urlsplit

        token = None
        tokens: set[str] = set()
        seen: set[str] = set()
        for _ in range(4):
            pages = self._client.subscriptions.list_by_topic(**target.topic_args(), top=65, **budget.options()).by_page(
                continuation_token=token
            )
            page = next(pages)
            for row in page:
                if len(seen) >= 64:
                    raise _TopicOwnershipUnknown("subscription inventory exceeds item budget")
                arm_id = _field(row, "id", default="")
                if (
                    not isinstance(arm_id, str)
                    or _topic_arm_key(arm_id) != _topic_arm_key(target.child_id)
                    or _topic_arm_key(arm_id) in seen
                ):
                    raise AzureOwnershipError("topic contains an unrecorded or ambiguous subscription")
                self._assert_owned(row, target.child_id, source)
                seen.add(_topic_arm_key(arm_id))
            token = pages.continuation_token
            if not token:
                if bool(seen) != child_present:
                    raise _TopicOwnershipUnknown("subscription inventory disagrees with exact lookup")
                return
            parsed = urlsplit(token)
            if (
                token in tokens
                or parsed.scheme != "https"
                or parsed.netloc != "management.azure.com"
                or _topic_arm_key(unquote(parsed.path)) != _topic_arm_key(target.topic_id + "/subscriptions")
                or parsed.fragment
            ):
                raise _TopicOwnershipUnknown("invalid subscription inventory continuation target")
            tokens.add(token)
        raise _TopicOwnershipUnknown("subscription inventory exceeds page budget")

    def _delete(self, operations: Any, args: dict[str, str], budget: _TopicCallBudget) -> None:
        from contextlib import suppress

        from azure.core.exceptions import ResourceNotFoundError

        with suppress(ResourceNotFoundError):
            operations.delete(**args, **budget.options())

    def _parameters(self, cfg: dict[str, Any], metadata: str) -> tuple[Any, Any]:
        from azure.mgmt.servicebus.models import SBSubscription, SBSubscriptionProperties, SBTopic, SBTopicProperties

        if set(cfg) - set(self.config_schema()["properties"]):
            raise AzureOwnershipError("unsupported Service Bus configuration key")
        delivery = cfg.get("max_delivery_count", self._config.max_delivery_count)
        if type(delivery) is not int or not 1 <= delivery <= 100:
            raise AzureOwnershipError("invalid subscription maximum delivery count")
        partitioned = cfg.get("enable_partitioning", self._config.enable_partitioning)
        dead_letter = cfg.get("dead_lettering_on_message_expiration", self._config.dead_lettering_on_message_expiration)
        if type(partitioned) is not bool or type(dead_letter) is not bool:
            raise AzureOwnershipError("invalid Service Bus boolean configuration")
        ttl = _topic_duration(cfg.get("default_message_ttl", self._config.default_message_ttl))
        lock = _topic_duration(cfg.get("lock_duration", self._config.lock_duration))
        if lock > timedelta(minutes=5):
            raise AzureOwnershipError("subscription lock duration exceeds five minutes")
        return (
            SBTopic(
                properties=SBTopicProperties(
                    max_size_in_megabytes=_topic_capacity(
                        cfg.get("max_size_in_megabytes", self._config.max_size_in_megabytes)
                    ),
                    enable_partitioning=partitioned,
                    support_ordering=True,
                    default_message_time_to_live=ttl,
                    user_metadata=metadata,
                )
            ),
            SBSubscription(
                properties=SBSubscriptionProperties(
                    lock_duration=lock,
                    max_delivery_count=delivery,
                    dead_lettering_on_message_expiration=dead_letter,
                    default_message_time_to_live=ttl,
                    user_metadata=metadata,
                )
            ),
        )


@dataclass(frozen=True)
class _TopicTarget:
    subscription: str
    group: str
    namespace: str
    topic: str
    child: str

    @property
    def topic_id(self) -> str:
        return (
            f"/subscriptions/{self.subscription}/resourceGroups/{self.group}"
            f"/providers/Microsoft.ServiceBus/namespaces/{self.namespace}/topics/{self.topic}"
        )

    @property
    def child_id(self) -> str:
        return self.topic_id + "/subscriptions/" + self.child

    def topic_args(self) -> dict[str, str]:
        return dict(resource_group_name=self.group, namespace_name=self.namespace, topic_name=self.topic)

    def child_args(self) -> dict[str, str]:
        return dict(**self.topic_args(), subscription_name=self.child)

    def handle(self, kind: str) -> str:
        return "/".join((kind, "arm-v1", self.subscription, self.group, self.namespace, self.topic, self.child))


class _TopicOwnershipUnknown(RuntimeError):
    pass


class _TopicCallBudget:
    def __init__(self) -> None:
        self.deadline = time.monotonic() + 20

    def options(self) -> dict[str, Any]:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _TopicOwnershipUnknown("Service Bus topic operation deadline exhausted")
        timeout = min(5.0, remaining / 2)
        return dict(connection_timeout=timeout, read_timeout=timeout, retry_total=0, redirect_max=0)


def _topic_identity(source: object) -> str:
    value = str(getattr(source, "managed_service_id", "") or "")
    try:
        identity = UUID(value)
    except (ValueError, AttributeError):
        raise AzureOwnershipError("immutable Service Bus source UUID is required") from None
    if not identity.int or str(identity) != value:
        raise AzureOwnershipError("immutable canonical nonzero Service Bus source UUID is required")
    return value


def _topic_metadata(spec: ProvisionSpec) -> str:
    _topic_identity(spec)
    tags = _tags_for(spec)
    if any(";" in key or "=" in key or ";" in value for key, value in tags.items()):
        raise AzureOwnershipError("Service Bus metadata cannot contain envelope delimiters")
    blob = ";".join(f"{key}={value}" for key, value in sorted(tags.items()))
    if len(blob) > 1024:
        raise AzureOwnershipError("Service Bus ownership metadata exceeds 1024 characters")
    return blob


def _topic_duration(value: Any) -> timedelta:
    if not isinstance(value, str) or not value:
        raise AzureOwnershipError("invalid Service Bus duration")
    match = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value)
    if match is None or not any(match.groups()):
        raise AzureOwnershipError("unsupported Service Bus duration; use integer days/hours/minutes/seconds")
    try:
        result = timedelta(
            days=int(match[1] or 0), hours=int(match[2] or 0), minutes=int(match[3] or 0), seconds=int(match[4] or 0)
        )
    except OverflowError:
        raise AzureOwnershipError("Service Bus duration exceeds supported range") from None
    if result <= timedelta(0):
        raise AzureOwnershipError("Service Bus duration must be positive")
    return result


def _topic_capacity(value: Any) -> int:
    if type(value) is not int or value not in {1024, 2048, 5120, 10240, 20480, 40960, 81920}:
        raise AzureOwnershipError("invalid Service Bus topic capacity")
    return int(value)


def _topic_arm_key(value: str) -> str:
    # ARM ASCII case variants cannot collapse distinct Unicode resource groups.
    return value.translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))


def _topic_status(row: Any) -> str:
    value = _field(row, "status", default="Unknown")
    return str(getattr(value, "value", value))


def _topic_errors(exc: Exception) -> list[str]:
    return (
        [OWNERSHIP_ERROR_CODE, "ownership_refused"]
        if isinstance(exc, (AzureOwnershipError, AzureTagError))
        else ["ownership_unknown"]
    )


def _topic_error_message(exc: Exception) -> str:
    return (
        f"Service Bus topic ownership/configuration refused: {exc}"
        if isinstance(exc, (AzureOwnershipError, AzureTagError))
        else "Service Bus topic ownership or operation could not be confirmed"
    )


# ----- module-level helpers --------------------------------------------


def _ownership_envelope(resource: Any) -> dict[str, str]:
    """Unpack the ``userMetadata`` blob back into the tag map it encodes.

    Service Bus entities take no ARM tags, so ``_user_metadata`` packs the same
    envelope every other Azure driver writes as tags into a ``k=v;`` string.
    Decoding it here keeps the ownership *decision* in the shared verifier;
    only the transport differs.
    """
    blob = _field(resource, "user_metadata", "userMetadata", default="")
    envelope: dict[str, str] = {}
    for entry in str(blob or "").split(";"):
        key, sep, value = entry.partition("=")
        if sep and key:
            envelope[key] = value
    return envelope


def _assert_owned(resource: Any, source: object, operation: AzureOperation, name: str) -> None:
    verify_azure_ownership(
        _ownership_envelope(resource),
        owner_of(source),
        operation=operation,
        resource=f"Service Bus {name}",
    )


def _field(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(value, dict):
            if name in value:
                return value[name]
            continue
        found = getattr(value, name, None)
        if found is not None:
            return found
    return default


def _user_metadata(spec: ProvisionSpec) -> str:
    """Service Bus topic ``userMetadata`` is a free-form string,
    capped at 1024 chars. We pack the astrolift tag set as a
    sorted ``k=v;`` blob so operators can grep for ownership.

    The ownership keys lead, ahead of the descriptive and custom tags, because
    the 1024-char cap truncates the tail: a long custom tag set must not be able
    to chop the identity marker off the end. A resource whose identity was
    truncated fails its own ownership check on every later update and teardown,
    which fails closed into a resource the platform can no longer remove.
    """
    tags = _tags_for(spec)
    leading = [key for key in (MANAGED_BY_TAG, MANAGED_SERVICE_ID_TAG, BINDING_TAG) if key in tags]
    ordered = leading + sorted(key for key in tags if key not in leading)
    blob = ";".join(f"{key}={tags[key]}" for key in ordered)
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
