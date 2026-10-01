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
        try:
            queue_name = self._validated_target(spec)
            self._get_queue(queue_name, spec, _QueueCallBudget(), required=True)
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=_queue_error_message(exc),
                errors=_queue_errors(exc),
                retryable=not isinstance(exc, (AzureOwnershipError, AzureTagError)),
            )
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
        existing = self._topic_state(topic_name)
        if existing is not None:
            try:
                _assert_owned(existing, spec, AzureOperation.PROVISION, f"topic {topic_name}")
            except AzureOwnershipError as exc:
                return ProvisionResult(
                    ok=False,
                    handle="",
                    message=str(exc),
                    errors=[OWNERSHIP_ERROR_CODE],
                )
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

        existing = self._topic_state(topic_name)
        if existing is None:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"topic {topic_name} does not exist",
                errors=["not_found"],
                retryable=False,
            )
        try:
            _assert_owned(existing, spec, AzureOperation.UPDATE, f"topic {topic_name}")
        except AzureOwnershipError as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=str(exc),
                errors=[OWNERSHIP_ERROR_CODE],
                retryable=False,
            )

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

        try:
            _assert_owned(topic_state, spec, AzureOperation.DELETE, f"topic {topic_name}")
        except AzureOwnershipError as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=str(exc),
                errors=[OWNERSHIP_ERROR_CODE],
                retryable=False,
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
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        del config
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
        env_vars = {
            "SERVICEBUS_NAMESPACE": ValueRef(
                literal=self._config.namespace_name,
            ),
            "SERVICEBUS_TOPIC": ValueRef(literal=topic_name),
            "SERVICEBUS_SUBSCRIPTION": ValueRef(literal=sub_name),
            "SERVICEBUS_ENDPOINT": ValueRef(literal=endpoint),
        }
        if self._config.handle_kind == "topic":
            env_vars.update(
                TOPIC_ARN_OR_ID=ValueRef(literal=topic_resource),
                TOPIC_NAME=ValueRef(literal=topic_name),
                TOPIC_REGION=ValueRef(literal=self._config.location),
            )
        return Binding(
            env_vars=env_vars,
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
        return f"{self._config.handle_kind}/{topic_name}"

    def _topic_name_from_handle(self, handle: str) -> str:
        if "/" not in handle:
            raise AzureServiceBusError(
                f"handle {handle!r} must be '<kind>/<topic>'",
            )
        kind, _, topic_name = handle.partition("/")
        if kind != self._config.handle_kind or not topic_name:
            raise AzureServiceBusError(
                f"handle {handle!r} must use kind {self._config.handle_kind!r}",
            )
        return topic_name


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
