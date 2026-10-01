"""Azure Event Hubs native-stream and Kafka-compatible managed services."""

from __future__ import annotations

import ipaddress
import json
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

if TYPE_CHECKING:
    from collections.abc import Callable
from uuid import UUID

from _sdk._telemetry import driver_op
from _sdk.azure_ownership import (
    AzureOperation,
    AzureOwnershipError,
    owner_of,
    verify_azure_ownership,
)
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
from azure._managed_identities import unlisted_identity
from azure.managed.tags import arm_tags_for as tags_for


@dataclass(frozen=True)
class _Profile:
    kind: str
    kafka: bool


PROFILES = {
    "event_hubs": _Profile("stream", False),
    "event_hubs_kafka": _Profile("event_stream", True),
}

_SIZE_TO_CAPACITY = {"small": 1, "medium": 2, "large": 4, "xlarge": 8}
_STATE_MAP = {
    "Active": "available",
    "Creating": "provisioning",
    "Deleting": "deprovisioning",
    "Renaming": "updating",
    "Restoring": "updating",
    "Disabled": "error",
    "SendDisabled": "error",
    "ReceiveDisabled": "error",
    "Unknown": "error",
}
_ARCHIVE_FORMAT = "{Namespace}/{EventHub}/{PartitionId}/{Year}/{Month}/{Day}/{Hour}/{Minute}/{Second}"


class AzureEventHubsError(ValueError):
    """Event Hubs lifecycle or contract failure."""


@dataclass(frozen=True)
class AzureEventHubsConfig:
    subscription_id: str
    resource_group: str
    variant: str
    location: str = "eastus"
    namespace_name_prefix: str = "astrolift-eh"
    event_hub_name_prefix: str = "astrolift"
    default_sku: str = "Standard"
    default_capacity: int = 1
    default_consumer_group: str = "astrolift"
    public_network_access_default: str = "Enabled"
    mgmt_client: Any | None = None
    locks_client: Any | None = None
    # User-assigned identities a namespace may attach for Capture or
    # customer-managed keys. Empty refuses every one (#2087).
    allowed_identity_resource_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.variant not in PROFILES:
            raise ValueError(f"unsupported Event Hubs variant {self.variant!r}")
        if self.default_sku not in {"Basic", "Standard", "Premium"}:
            raise ValueError(f"unsupported Event Hubs SKU {self.default_sku!r}")
        if not 1 <= self.default_capacity <= 40:
            raise ValueError("Event Hubs default capacity must be between 1 and 40")
        if self.public_network_access_default not in {"Enabled", "Disabled", "SecuredByPerimeter"}:
            raise ValueError("unsupported Event Hubs public network access default")


class AzureEventHubsDriver(ManagedServiceDriver):
    def __init__(self, *, config: AzureEventHubsConfig) -> None:
        self._config = config
        self._profile = PROFILES[config.variant]
        if config.mgmt_client is not None:
            self._mgmt = config.mgmt_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.eventhub import EventHubManagementClient

            self._mgmt = EventHubManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
                api_version="2024-01-01",
            )
        if config.locks_client is not None:
            self._locks = config.locks_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.resource.locks import ManagementLockClient

            self._locks = ManagementLockClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
            )

    @driver_op(cloud="azure", driver="event_hubs", audit=True, sensitive_kind="managed_service_provision")
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        handle = spec.recorded_handle
        token = _DEADLINE.set(time.monotonic() + 20)
        try:
            target = self._provision_target(spec)
            handle = target.handle(self._profile.kind)
            cfg = spec.config or {}
            validation = self._validate(cfg)
            if validation:
                return ProvisionResult(False, handle, validation, ["invalid_runtime_controls"])
            namespace = self._describe_namespace(target.namespace)
            if namespace is None:
                if spec.recorded_handle:
                    raise _Ownership("ownership_unknown", "recorded namespace is missing; recreation is not authorized")
                self._rpc(
                    self._mgmt.namespaces.begin_create_or_update,
                    resource_group_name=target.resource_group,
                    namespace_name=target.namespace,
                    parameters=self._namespace_create_parameters(spec),
                    polling=False,
                ).result()
                namespace = self._describe_namespace(target.namespace)
                if namespace is None:
                    return ProvisionResult(
                        False, handle, "namespace creation is not yet observed", ["provision_pending"]
                    )
            self._namespace_owned(namespace, target, spec)
            if not self._namespace_active(namespace):
                return ProvisionResult(False, handle, "namespace is not yet active", ["provision_pending"])
            hub, groups = self._inventory(target, spec)
            self._immutable_settings(namespace, hub, target, cfg)
            self._group_changes(target, groups, cfg)
            if hub is None and spec.recorded_handle:
                raise _Ownership("ownership_unknown", "recorded event hub is missing; recreation is not authorized")
            validation = self._validate({"sku": self._sku_name(namespace), **cfg})
            if validation:
                return ProvisionResult(False, handle, validation, ["invalid_runtime_controls"])
            update = self._namespace_update_parameters(cfg, current_sku=self._sku_name(namespace))
            if self._payload_has_changes(update, ignored={"tags"}):
                self._rpc(
                    self._mgmt.namespaces.update,
                    resource_group_name=target.resource_group,
                    namespace_name=target.namespace,
                    parameters=update,
                )
            self._reconcile_network_rules(target.namespace, cfg)
            if hub is None or self._event_hub_fields_present(cfg):
                self._rpc(
                    self._mgmt.event_hubs.create_or_update,
                    resource_group_name=target.resource_group,
                    namespace_name=target.namespace,
                    event_hub_name=target.hub,
                    parameters=self._event_hub_parameters(spec, existing=hub),
                )
            hub = self._describe_event_hub(target.namespace, target.hub)
            if hub is None:
                return ProvisionResult(False, handle, "event hub creation is not yet observed", ["provision_pending"])
            self._child_owned(hub, target.hub_id, spec)
            if _enum(self._property(hub, "status", "")) != "Active":
                return ProvisionResult(False, handle, "event hub is not yet active", ["provision_pending"])
            self._reconcile_groups(target, spec, cfg)
            namespace = self._describe_namespace(target.namespace)
            if namespace is None:
                raise _Ownership("ownership_unknown", "namespace disappeared during reconciliation")
            self._namespace_owned(namespace, target, spec)
            hub, _ = self._inventory(target, spec)
            ready = (
                self._namespace_active(namespace)
                and hub is not None
                and _enum(self._property(hub, "status", "")) == "Active"
            )
            return ProvisionResult(
                True, handle, "Event Hubs target reconciled; readiness is observed separately", ready=ready
            )
        except (AzureEventHubsError, AzureOwnershipError) as exc:
            return ProvisionResult(False, handle, str(exc), [_error_code(exc)])
        except Exception:
            return ProvisionResult(False, handle, "Event Hubs reconciliation observation failed", ["ownership_unknown"])
        finally:
            _DEADLINE.reset(token)

    @driver_op(cloud="azure", driver="event_hubs")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        token = _DEADLINE.set(time.monotonic() + 20)
        try:
            target = self._saved_target(spec.handle, spec)
            namespace, hub, groups = self._existing(target, spec)
            cfg = spec.config or {}
            self._immutable_settings(namespace, hub, target, cfg)
            self._group_changes(target, groups, cfg)
            validation = self._validate({"sku": self._sku_name(namespace), **cfg})
            if validation:
                return UpdateResult(False, spec.handle, validation, ["invalid_runtime_controls"], retryable=False)
            current_status = _enum(self._property(hub, "status", ""))
            if not self._namespace_active(namespace) or current_status not in {
                "Active",
                "Disabled",
                "SendDisabled",
                "ReceiveDisabled",
            }:
                return UpdateResult(False, spec.handle, "target is not in a stable observed state", ["update_pending"])
            if (
                "partition_count" in cfg
                and self._sku_name(namespace) != "Premium"
                and self._property(hub, "partition_count", None) != cfg["partition_count"]
            ):
                return UpdateResult(
                    False,
                    spec.handle,
                    "partition count requires reprovision on this tier",
                    ["reprovision_required"],
                    retryable=False,
                )
            namespace_cfg = dict(cfg)
            if spec.size:
                if spec.size not in _SIZE_TO_CAPACITY:
                    return UpdateResult(
                        False, spec.handle, "unsupported Event Hubs size", ["invalid_runtime_controls"], retryable=False
                    )
                namespace_cfg.setdefault("capacity", _SIZE_TO_CAPACITY[spec.size])
            update = self._namespace_update_parameters(namespace_cfg, current_sku=self._sku_name(namespace))
            if self._payload_has_changes(update, ignored={"tags"}):
                self._rpc(
                    self._mgmt.namespaces.update,
                    resource_group_name=target.resource_group,
                    namespace_name=target.namespace,
                    parameters=update,
                )
            self._reconcile_network_rules(target.namespace, cfg)
            if self._event_hub_fields_present(cfg):
                self._rpc(
                    self._mgmt.event_hubs.create_or_update,
                    resource_group_name=target.resource_group,
                    namespace_name=target.namespace,
                    event_hub_name=target.hub,
                    parameters=self._event_hub_parameters(spec, existing=hub),
                )
            self._reconcile_groups(target, spec, cfg)
            namespace, hub, _ = self._existing(target, spec)
            expected_status = cfg.get("status", current_status)
            if not self._namespace_active(namespace) or _enum(self._property(hub, "status", "")) != expected_status:
                return UpdateResult(False, spec.handle, "updated desired state is not yet observed", ["update_pending"])
            return UpdateResult(True, spec.handle, "Event Hubs supported settings reconciled")
        except (AzureEventHubsError, AzureOwnershipError) as exc:
            return UpdateResult(False, spec.handle, str(exc), [_error_code(exc)], retryable=False)
        except Exception:
            return UpdateResult(False, spec.handle, "Event Hubs update observation failed", ["ownership_unknown"])
        finally:
            _DEADLINE.reset(token)

    @driver_op(cloud="azure", driver="event_hubs", audit=True, sensitive_kind="managed_service_deprovision")
    def deprovision(
        self, spec: DeprovisionSpec, *, delete_data: bool = False, force_destroy: bool = False
    ) -> DeprovisionResult:
        del force_destroy
        token = _DEADLINE.set(time.monotonic() + 20)
        try:
            target = self._saved_target(spec.handle, spec)
            namespace = self._describe_namespace(target.namespace)
            if namespace is None:
                return DeprovisionResult(True, spec.handle, "exact recorded namespace is absent")
            self._namespace_owned(namespace, target, spec)
            self._inventory(target, spec)
            if not delete_data:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "Event Hubs has no exact snapshot; deletion requires delete_data=True",
                    ["delete_data_required"],
                    retryable=False,
                )
            self._rpc(
                self._mgmt.namespaces.begin_delete,
                resource_group_name=target.resource_group,
                namespace_name=target.namespace,
                polling=False,
            ).result()
            if self._describe_namespace(target.namespace) is not None:
                return DeprovisionResult(
                    False, spec.handle, "namespace deletion is not yet observed", ["delete_pending"]
                )
            return DeprovisionResult(True, spec.handle, "exact recorded namespace deletion observed")
        except (AzureEventHubsError, AzureOwnershipError) as exc:
            return DeprovisionResult(False, spec.handle, str(exc), [_error_code(exc)], retryable=False)
        except Exception:
            return DeprovisionResult(
                False, spec.handle, "Event Hubs deletion observation failed", ["ownership_unknown"]
            )
        finally:
            _DEADLINE.reset(token)

    @driver_op(cloud="azure", driver="event_hubs")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        token = _DEADLINE.set(time.monotonic() + 20)
        try:
            target = self._saved_target(handle.handle, handle)
            namespace = self._describe_namespace(target.namespace)
            if namespace is None:
                return ServiceStatus(handle.handle, "deprovisioned", "exact recorded namespace is absent")
            self._namespace_owned(namespace, target, handle)
            hub, groups = self._inventory(target, handle)
            if hub is None:
                return ServiceStatus(handle.handle, "error", "recorded event hub is absent")
            ns_state = _enum(self._property(namespace, "status", ""))
            hub_state = _enum(self._property(hub, "status", ""))
            if not self._namespace_active(namespace):
                state = (
                    "provisioning"
                    if _enum(self._property(namespace, "provisioning_state", ""))
                    in {"Creating", "Updating", "Accepted"}
                    else "error"
                )
            else:
                state = _STATE_MAP.get(hub_state, "error")
            if target.group != "-" and target.group not in groups:
                state = "error"
            return ServiceStatus(
                handle.handle,
                state,
                f"Azure observed namespace={ns_state or 'unknown'}, event hub={hub_state or 'unknown'}",
            )
        except Exception:
            return ServiceStatus(handle.handle, "error", "Event Hubs ownership/readiness observation unavailable")
        finally:
            _DEADLINE.reset(token)

    @driver_op(cloud="azure", driver="event_hubs")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        del config
        token = _DEADLINE.set(time.monotonic() + 20)
        try:
            target = self._saved_target(handle.handle, handle)
            namespace, hub, groups = self._existing(target, handle)
            if not self._namespace_active(namespace) or _enum(self._property(hub, "status", "")) != "Active":
                raise AzureEventHubsError("binding requires an actually active target")
            if target.group != "-" and target.group not in groups:
                raise _Ownership("ownership_unknown", "saved native consumer group is absent")
            fqdn = f"{target.namespace}.servicebus.windows.net"
            common = {
                "EVENTHUB_NAMESPACE": ValueRef(literal=target.namespace),
                "EVENTHUB_NAME": ValueRef(literal=target.hub),
                "EVENTHUB_FULLY_QUALIFIED_NAMESPACE": ValueRef(literal=fqdn),
                "EVENTHUB_RESOURCE_ID": ValueRef(literal=target.hub_id),
            }
            if self._profile.kafka:
                common.update(
                    EVENT_STREAM_BROKERS=ValueRef(literal=f"{fqdn}:9093"),
                    EVENT_STREAM_TLS=ValueRef(literal="true"),
                    EVENT_STREAM_AUTH_MECHANISM=ValueRef(literal="OAUTHBEARER"),
                    EVENTHUB_KAFKA_TOPIC=ValueRef(literal=target.hub),
                )
            else:
                common.update(
                    STREAM_NAME=ValueRef(literal=target.hub),
                    STREAM_ARN=ValueRef(literal=target.hub_id),
                    STREAM_ENDPOINT=ValueRef(literal=f"sb://{fqdn}/{target.hub}"),
                    STREAM_REGION=ValueRef(literal=self._config.location),
                    EVENTHUB_CONSUMER_GROUP=ValueRef(literal=target.group),
                )
            return Binding(
                env_vars=common,
                iam_grants=[
                    Grant(target.hub_id, ["Azure Event Hubs Data Sender"]),
                    Grant(target.hub_id, ["Azure Event Hubs Data Receiver"]),
                ],
                notes="Exact event-hub-scoped Microsoft Entra workload identity; no SAS credentials.",
            )
        finally:
            _DEADLINE.reset(token)

    @driver_op(cloud="azure", driver="event_hubs")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise AzureEventHubsError(
            "Event Hubs has no exact stream snapshot; Capture is an asynchronous downstream archive",
        )

    @driver_op(cloud="azure", driver="event_hubs")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        raise AzureEventHubsError("Event Hubs cannot restore an exact stream snapshot")

    @driver_op(cloud="azure", driver="event_hubs", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "namespace_name": {"type": "string"},
                "event_hub_name": {"type": "string"},
                "sku": {"type": "string", "enum": ["Basic", "Standard", "Premium"]},
                "capacity": {"type": "integer", "minimum": 1, "maximum": 40},
                "auto_inflate_enabled": {"type": "boolean"},
                "maximum_throughput_units": {"type": "integer", "minimum": 1, "maximum": 40},
                "kafka_enabled": {"type": "boolean"},
                "zone_redundant": {"type": "boolean"},
                "minimum_tls_version": {"type": "string", "enum": ["1.2", "1.3"]},
                "public_network_access": {
                    "type": "string",
                    "enum": ["Enabled", "Disabled", "SecuredByPerimeter"],
                },
                "disable_local_auth": {"type": "boolean"},
                "network_default_action": {"type": "string", "enum": ["Allow", "Deny"]},
                "trusted_service_access_enabled": {"type": "boolean"},
                "ip_rules": {"type": "array", "items": {"type": "string"}},
                "virtual_network_rule_ids": {"type": "array", "items": {"type": "string"}},
                "ignore_missing_vnet_service_endpoint": {"type": "boolean"},
                "partition_count": {"type": "integer", "minimum": 1, "maximum": 100},
                "cleanup_policy": {"type": "string", "enum": ["Delete", "Compact"]},
                "retention_time_in_hours": {"type": "integer", "minimum": -1},
                "min_compaction_lag_time_in_minutes": {"type": "integer", "minimum": 0},
                "tombstone_retention_time_in_hours": {"type": "integer", "minimum": 1},
                "status": {
                    "type": "string",
                    "enum": ["Active", "Disabled", "SendDisabled", "ReceiveDisabled"],
                },
                "consumer_groups": {"type": "array", "items": {"type": "string"}},
                "capture_enabled": {"type": "boolean"},
                "capture_storage_account_resource_id": {"type": "string"},
                "capture_blob_container": {"type": "string"},
                "capture_archive_name_format": {"type": "string"},
                "capture_interval_seconds": {"type": "integer", "minimum": 60, "maximum": 900},
                "capture_size_limit_bytes": {
                    "type": "integer",
                    "minimum": 10485760,
                    "maximum": 524288000,
                },
                "capture_skip_empty_archives": {"type": "boolean"},
                "capture_identity_type": {"type": "string", "enum": ["SystemAssigned", "UserAssigned"]},
                "capture_user_assigned_identity_resource_id": {
                    "type": "string",
                    "description": (
                        "Pre-authorized UAMI with Storage Blob Data Contributor on the Capture destination."
                    ),
                },
                "customer_managed_key_name": {"type": "string"},
                "customer_managed_key_vault_uri": {"type": "string", "format": "uri"},
                "customer_managed_key_version": {"type": "string"},
                "customer_managed_identity_resource_id": {"type": "string"},
                "require_infrastructure_encryption": {"type": "boolean"},
            },
        }

    @driver_op(cloud="azure", driver="event_hubs", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        common = {
            "EVENTHUB_NAMESPACE": "Event Hubs namespace name",
            "EVENTHUB_NAME": "Event hub or Kafka topic name",
            "EVENTHUB_FULLY_QUALIFIED_NAMESPACE": "Event Hubs namespace FQDN",
            "EVENTHUB_RESOURCE_ID": "Azure event hub resource ID",
        }
        if self._profile.kafka:
            common.update(
                EVENT_STREAM_BROKERS="Kafka bootstrap server",
                EVENT_STREAM_TLS="TLS requirement",
                EVENT_STREAM_AUTH_MECHANISM="Kafka OAuth mechanism",
                EVENTHUB_KAFKA_TOPIC="Kafka topic name",
            )
        else:
            common.update(
                STREAM_NAME="Portable stream name",
                STREAM_ARN="Portable provider resource ID",
                STREAM_ENDPOINT="Native AMQP endpoint",
                STREAM_REGION="Azure region",
                EVENTHUB_CONSUMER_GROUP="Default native consumer group",
            )
        return BindingSchema(env_vars=common)

    def editable_fields(self) -> list[str]:
        return [
            "size",
            "capacity",
            "auto_inflate_enabled",
            "maximum_throughput_units",
            "minimum_tls_version",
            "public_network_access",
            "disable_local_auth",
            "network_default_action",
            "trusted_service_access_enabled",
            "ip_rules",
            "virtual_network_rule_ids",
            "ignore_missing_vnet_service_endpoint",
            "partition_count",
            "retention_time_in_hours",
            "min_compaction_lag_time_in_minutes",
            "tombstone_retention_time_in_hours",
            "status",
            "consumer_groups",
            "capture_enabled",
            "capture_storage_account_resource_id",
            "capture_blob_container",
            "capture_archive_name_format",
            "capture_interval_seconds",
            "capture_size_limit_bytes",
            "capture_skip_empty_archives",
            "capture_identity_type",
            "capture_user_assigned_identity_resource_id",
            "customer_managed_key_name",
            "customer_managed_key_vault_uri",
            "customer_managed_key_version",
            "customer_managed_identity_resource_id",
            "require_infrastructure_encryption",
        ]

    def _validate(self, cfg: dict[str, Any]) -> str:
        unknown = sorted(set(cfg) - set(self.config_schema()["properties"]))
        if unknown:
            return f"unsupported Event Hubs config fields: {', '.join(unknown)}"
        types = {"integer": int, "boolean": bool, "string": str, "array": list}
        for name, value in cfg.items():
            expected = types[self.config_schema()["properties"][name]["type"]]
            if type(value) is not expected:
                return f"Event Hubs {name} must be {expected.__name__}"
        sku = str(cfg.get("sku", self._config.default_sku))
        if sku not in {"Basic", "Standard", "Premium"}:
            return f"unsupported Event Hubs SKU {sku!r}"
        try:
            capacity = int(cfg.get("capacity", self._config.default_capacity))
        except (TypeError, ValueError):
            return "Event Hubs capacity must be an integer"
        max_capacity = 16 if sku == "Premium" else 40
        if not 1 <= capacity <= max_capacity:
            return f"Event Hubs {sku} capacity must be between 1 and {max_capacity}"
        if cfg.get("auto_inflate_enabled") and sku != "Standard":
            return "Event Hubs auto-inflate is supported only on Standard"
        if "maximum_throughput_units" in cfg:
            try:
                maximum = int(cfg["maximum_throughput_units"])
            except (TypeError, ValueError):
                return "Event Hubs maximum_throughput_units must be an integer"
            if not 1 <= maximum <= 40:
                return "Event Hubs maximum_throughput_units must be between 1 and 40"
            if not cfg.get("auto_inflate_enabled"):
                return "maximum_throughput_units requires auto_inflate_enabled=true"
        if self._profile.kafka and cfg.get("kafka_enabled") is False:
            return "event_hubs_kafka requires kafka_enabled=true"
        if self._profile.kafka and sku == "Basic":
            return "Event Hubs Kafka endpoint requires Standard or Premium"
        public_access = str(cfg.get("public_network_access", self._config.public_network_access_default))
        if public_access not in {"Enabled", "Disabled", "SecuredByPerimeter"}:
            return "unsupported Event Hubs public network access"
        if public_access != "Enabled":
            return (
                f"Event Hubs public_network_access={public_access} requires a managed Private Endpoint "
                "or Network Security Perimeter attachment"
            )
        if str(cfg.get("network_default_action", "Allow")) not in {"Allow", "Deny"}:
            return "unsupported Event Hubs network default action"
        if (
            cfg.get("network_default_action") == "Deny"
            and not cfg.get("ip_rules")
            and not cfg.get("virtual_network_rule_ids")
        ):
            return "Event Hubs network_default_action=Deny requires an IP or virtual-network rule"
        for rule in cfg.get("ip_rules", []) or []:
            try:
                ipaddress.ip_network(str(rule), strict=False)
            except ValueError:
                return f"Event Hubs ip_rules entry is not an IP address or CIDR: {rule!r}"
        for resource_id in cfg.get("virtual_network_rule_ids", []) or []:
            if not isinstance(resource_id, str) or not resource_id.startswith("/subscriptions/"):
                return "Event Hubs virtual_network_rule_ids entries must be ARM resource IDs"
        try:
            partitions = int(cfg.get("partition_count", 2))
        except (TypeError, ValueError):
            return "Event Hubs partition_count must be an integer"
        partition_limit = 100 if sku == "Premium" else 32
        if not 1 <= partitions <= partition_limit:
            return f"Event Hubs {sku} partition_count must be between 1 and {partition_limit}"
        cleanup = str(cfg.get("cleanup_policy", "Delete"))
        if cleanup not in {"Delete", "Compact"}:
            return "unsupported Event Hubs cleanup policy"
        if cleanup in {"Compact"} and sku == "Basic":
            return "Event Hubs log compaction requires Standard or Premium"
        try:
            retention = int(cfg.get("retention_time_in_hours", 24))
        except (TypeError, ValueError):
            return "Event Hubs retention_time_in_hours must be an integer"
        retention_limit = {"Basic": 24, "Standard": 168, "Premium": 2160}[sku]
        if retention != -1 and not 1 <= retention <= retention_limit:
            return f"Event Hubs {sku} retention must be -1 or between 1 and {retention_limit} hours"
        if retention == -1 and cleanup == "Delete":
            return "infinite Event Hubs retention requires Compact"
        groups = cfg.get("consumer_groups", []) or []
        if not isinstance(groups, list) or any(not isinstance(value, str) or not value for value in groups):
            return "Event Hubs consumer_groups must be a list of non-empty strings"
        for group in groups:
            try:
                _group_name(group)
            except AzureEventHubsError:
                return "native consumer group is not representable"
            if group == "$Default":
                return "$Default is structural and cannot be requested as a managed group"
        if len(groups) != len({group.casefold() for group in groups}):
            return "Event Hubs consumer_groups must be unique"
        if self._profile.kafka and groups:
            return "Event Hubs Kafka consumer groups are client-managed and must not be provisioned"
        if sku == "Basic" and groups:
            return "Event Hubs Basic supports only the built-in $Default consumer group"
        group_limit = {"Basic": 1, "Standard": 20, "Premium": 100}[sku]
        native_group_count = (
            0 if sku == "Basic" or self._profile.kafka else len(set(groups) | {self._config.default_consumer_group})
        )
        if native_group_count > group_limit:
            return f"Event Hubs {sku} supports at most {group_limit} managed consumer groups"
        capture_fields_present = any(key.startswith("capture_") for key in cfg)
        capture_enabled = bool(cfg.get("capture_enabled", False))
        capture_requires_full_block = capture_fields_present and cfg.get("capture_enabled") is not False
        capture_storage = str(cfg.get("capture_storage_account_resource_id") or "")
        capture_container = str(cfg.get("capture_blob_container") or "")
        if capture_requires_full_block and (not capture_storage or not capture_container):
            return (
                "Event Hubs Capture enable/update requires the full storage account, blob container, and identity block"
            )
        if capture_enabled and sku == "Basic":
            return "Event Hubs Capture requires Standard or Premium"
        if capture_storage and not capture_storage.startswith("/subscriptions/"):
            return "Event Hubs Capture storage account must be an ARM resource ID"
        try:
            capture_interval = int(cfg.get("capture_interval_seconds", 300))
            capture_size = int(cfg.get("capture_size_limit_bytes", 104857600))
        except (TypeError, ValueError):
            return "Event Hubs Capture interval and size must be integers"
        if not 60 <= capture_interval <= 900:
            return "Event Hubs Capture interval must be between 60 and 900 seconds"
        if not 10485760 <= capture_size <= 524288000:
            return "Event Hubs Capture size must be between 10485760 and 524288000 bytes"
        capture_identity = str(cfg.get("capture_identity_type", "SystemAssigned"))
        capture_uami = str(cfg.get("capture_user_assigned_identity_resource_id") or "")
        if capture_identity not in {"SystemAssigned", "UserAssigned"}:
            return "unsupported Event Hubs Capture identity type"
        if capture_enabled and capture_identity != "UserAssigned":
            return (
                "Event Hubs Capture requires a pre-authorized UserAssigned identity until "
                "Astrolift manages the storage role assignment"
            )
        if capture_identity == "UserAssigned" and not capture_uami.startswith("/subscriptions/"):
            return "Event Hubs Capture UserAssigned identity requires an ARM resource ID"
        cmk_fields = (
            str(cfg.get("customer_managed_key_name") or ""),
            str(cfg.get("customer_managed_key_vault_uri") or ""),
            str(cfg.get("customer_managed_identity_resource_id") or ""),
        )
        if any(cmk_fields) and not all(cmk_fields):
            return "Event Hubs customer-managed encryption requires key name, vault URI, and identity"
        if any(cmk_fields) and sku != "Premium":
            return "Event Hubs customer-managed encryption requires Premium"
        if cfg.get("require_infrastructure_encryption") and not all(cmk_fields):
            return "Event Hubs infrastructure encryption requires customer-managed encryption controls"
        if cmk_fields[1] and not cmk_fields[1].startswith("https://"):
            return "Event Hubs customer-managed_key_vault_uri must be HTTPS"
        if cmk_fields[2] and not cmk_fields[2].startswith("/subscriptions/"):
            return "Event Hubs customer-managed identity must be an ARM resource ID"
        # Both identities land on the namespace whenever they are set, whatever
        # capture_enabled or capture_identity_type say, so both are judged
        # whenever they are set.
        for field in ("capture_user_assigned_identity_resource_id", "customer_managed_identity_resource_id"):
            unlisted = unlisted_identity(cfg.get(field), self._config.allowed_identity_resource_ids)
            if unlisted:
                return (
                    f"Event Hubs {field} {unlisted!r} is not allowed by the cluster install policy "
                    "eventhubs_allowed_identity_resource_ids"
                )
        return ""

    def _namespace_create_parameters(self, spec: ProvisionSpec) -> Any:
        from azure.mgmt.eventhub import models

        cfg = spec.config or {}
        sku = str(cfg.get("sku", self._config.default_sku))
        identity, encryption = self._identity_and_encryption(cfg)
        return models.EHNamespace(
            location=self._config.location,
            tags=tags_for(spec),
            sku=models.Sku(
                name=sku,
                tier=sku,
                capacity=int(cfg.get("capacity", _SIZE_TO_CAPACITY.get(spec.size, self._config.default_capacity))),
            ),
            identity=identity,
            properties=models.EHNamespaceProperties(
                minimum_tls_version=str(cfg.get("minimum_tls_version", "1.2")),
                is_auto_inflate_enabled=bool(cfg.get("auto_inflate_enabled", False)),
                maximum_throughput_units=cfg.get("maximum_throughput_units"),
                kafka_enabled=True if self._profile.kafka else bool(cfg.get("kafka_enabled", False)),
                zone_redundant=bool(cfg.get("zone_redundant", False)),
                public_network_access="Enabled",
                disable_local_auth=bool(cfg.get("disable_local_auth", True)),
                encryption=encryption,
            ),
        )

    def _namespace_update_parameters(
        self,
        cfg: dict[str, Any],
        *,
        tags: dict[str, str] | None = None,
        current_sku: str | None = None,
    ) -> Any:
        from azure.mgmt.eventhub import models

        properties: dict[str, Any] = {}
        mapping = {
            "auto_inflate_enabled": "is_auto_inflate_enabled",
            "maximum_throughput_units": "maximum_throughput_units",
            "public_network_access": "public_network_access",
            "disable_local_auth": "disable_local_auth",
            "minimum_tls_version": "minimum_tls_version",
        }
        for source, target in mapping.items():
            if source in cfg:
                properties[target] = cfg[source]
        identity, encryption = self._identity_and_encryption(cfg)
        if encryption is not None:
            properties["encryption"] = encryption
        sku = None
        if "capacity" in cfg:
            name = str(cfg.get("sku", current_sku or self._config.default_sku))
            sku = models.Sku(name=name, tier=name, capacity=int(cfg["capacity"]))
        return models.EHNamespace(
            tags=tags,
            sku=sku,
            identity=identity,
            properties=models.EHNamespaceProperties(**properties),
        )

    def _event_hub_parameters(self, spec: ProvisionSpec | UpdateSpec, *, existing: Any | None = None) -> Any:
        from azure.mgmt.eventhub import models

        cfg = spec.config or {}

        def current(name: str, default: Any) -> Any:
            return self._property(existing, name, default) if existing is not None else default

        cleanup = _enum(
            cfg.get(
                "cleanup_policy", self._nested_property(existing, "retention_description", "cleanup_policy", "Delete")
            )
        )
        retention = int(
            cfg.get(
                "retention_time_in_hours",
                self._nested_property(existing, "retention_description", "retention_time_in_hours", 24),
            ),
        )
        capture = self._capture_description(cfg, existing=existing)
        properties = models.EventhubProperties(
            partition_count=int(cfg.get("partition_count", current("partition_count", 2))),
            status=_enum(cfg.get("status", current("status", "Active"))),
            capture_description=capture,
            retention_description=models.RetentionDescription(
                cleanup_policy=cleanup,
                retention_time_in_hours=retention,
                min_compaction_lag_time_in_minutes=cfg.get(
                    "min_compaction_lag_time_in_minutes",
                    self._nested_property(
                        existing, "retention_description", "min_compaction_lag_time_in_minutes", None
                    ),
                ),
                tombstone_retention_time_in_hours=cfg.get(
                    "tombstone_retention_time_in_hours",
                    self._nested_property(existing, "retention_description", "tombstone_retention_time_in_hours", None),
                ),
            ),
            user_metadata=_metadata(spec),
        )
        return models.Eventhub(properties=properties)

    def _capture_description(self, cfg: dict[str, Any], *, existing: Any | None) -> Any | None:
        from azure.mgmt.eventhub import models

        capture_fields = {key for key in cfg if key.startswith("capture_")}
        existing_capture = self._property(existing, "capture_description", None) if existing is not None else None
        if not capture_fields:
            return existing_capture
        enabled = bool(cfg.get("capture_enabled", _field(existing_capture, "enabled", default=False)))
        if not enabled:
            return models.CaptureDescription(enabled=False)
        identity_type = str(cfg.get("capture_identity_type", "SystemAssigned"))
        capture_identity = models.CaptureIdentity(type=identity_type)
        if identity_type == "UserAssigned":
            capture_identity.user_assigned_identity = str(cfg["capture_user_assigned_identity_resource_id"])
        return models.CaptureDescription(
            enabled=True,
            encoding="Avro",
            interval_in_seconds=int(cfg.get("capture_interval_seconds", 300)),
            size_limit_in_bytes=int(cfg.get("capture_size_limit_bytes", 104857600)),
            skip_empty_archives=bool(cfg.get("capture_skip_empty_archives", True)),
            destination=models.Destination(
                name="EventHubArchive.AzureBlockBlob",
                identity=capture_identity,
                properties=models.DestinationProperties(
                    storage_account_resource_id=str(cfg["capture_storage_account_resource_id"]),
                    blob_container=str(cfg["capture_blob_container"]),
                    archive_name_format=str(cfg.get("capture_archive_name_format", _ARCHIVE_FORMAT)),
                ),
            ),
        )

    def _identity_and_encryption(self, cfg: dict[str, Any]) -> tuple[Any | None, Any | None]:
        identity_id = str(cfg.get("customer_managed_identity_resource_id") or "")
        capture_uami = str(cfg.get("capture_user_assigned_identity_resource_id") or "")
        capture_system = (
            cfg.get("capture_enabled") and cfg.get("capture_identity_type", "SystemAssigned") == "SystemAssigned"
        )
        if not identity_id and not capture_uami and not capture_system:
            return None, None
        from azure.mgmt.eventhub import models

        identities = {value: models.UserAssignedIdentity() for value in {identity_id, capture_uami} if value}
        identity_type = (
            "SystemAssigned, UserAssigned"
            if capture_system and identities
            else ("SystemAssigned" if capture_system else "UserAssigned")
        )
        identity = models.Identity(type=identity_type, user_assigned_identities=identities or None)
        if not identity_id:
            return identity, None
        key = models.KeyVaultProperties(
            key_name=str(cfg["customer_managed_key_name"]),
            key_vault_uri=str(cfg["customer_managed_key_vault_uri"]),
            key_version=str(cfg.get("customer_managed_key_version") or ""),
            identity=models.UserAssignedIdentityProperties(user_assigned_identity=identity_id),
        )
        return identity, models.Encryption(
            key_source="Microsoft.KeyVault",
            key_vault_properties=[key],
            require_infrastructure_encryption=bool(cfg.get("require_infrastructure_encryption", False)),
        )

    def _reconcile_network_rules(self, namespace_name: str, cfg: dict[str, Any]) -> None:
        if not self._network_fields_present(cfg):
            return
        from azure.mgmt.eventhub import models

        rules = models.NetworkRuleSet(
            properties=models.NetworkRuleSetProperties(
                default_action=str(cfg.get("network_default_action", "Allow")),
                public_network_access=str(cfg.get("public_network_access", "Enabled")),
                trusted_service_access_enabled=bool(cfg.get("trusted_service_access_enabled", False)),
                ip_rules=[
                    models.NWRuleSetIpRules(ip_mask=str(value), action="Allow") for value in cfg.get("ip_rules", [])
                ],
                virtual_network_rules=[
                    models.NWRuleSetVirtualNetworkRules(
                        subnet=models.Subnet(id=str(value)),
                        ignore_missing_vnet_service_endpoint=bool(
                            cfg.get("ignore_missing_vnet_service_endpoint", False),
                        ),
                    )
                    for value in cfg.get("virtual_network_rule_ids", [])
                ],
            ),
        )
        self._rpc(
            self._mgmt.namespaces.create_or_update_network_rule_set,
            resource_group_name=self._config.resource_group,
            namespace_name=namespace_name,
            parameters=rules,
        )

    def _reconcile_groups(self, target: _Target, source: object, cfg: dict[str, Any]) -> None:
        from azure.mgmt.eventhub import models

        _, existing = self._inventory(target, source)
        desired = self._desired_groups(target, cfg)
        for name in sorted(desired - set(existing)):
            self._rpc(
                self._mgmt.consumer_groups.create_or_update,
                resource_group_name=target.resource_group,
                namespace_name=target.namespace,
                event_hub_name=target.hub,
                consumer_group_name=name,
                parameters=models.ConsumerGroup(
                    properties=models.ConsumerGroupProperties(user_metadata=_metadata(source))
                ),
            )
        _, observed = self._inventory(target, source)
        if not desired <= set(observed):
            raise _Ownership("ownership_unknown", "consumer-group creation not observed")

    def _rpc(self, function: Callable[..., Any], **kwargs: Any) -> Any:
        remaining = (_DEADLINE.get() or time.monotonic()) - time.monotonic()
        if remaining <= 0:
            raise _Ownership("ownership_unknown", "bounded management observation budget exhausted")
        return function(
            **kwargs,
            retry_total=0,
            retry_connect=0,
            retry_read=0,
            retry_status=0,
            redirect_max=0,
            connection_timeout=min(5, remaining / 2),
            read_timeout=min(5, remaining / 2),
        )

    def _pages(self, function: Callable[..., Any], **kwargs: Any) -> list[Any]:
        continuation: str | None = None
        values: list[Any] = []
        for _ in range(4):
            pager = self._rpc(function, **kwargs)
            if not hasattr(pager, "by_page"):
                raise _Ownership("ownership_unknown", "SDK inventory is not a paged response")
            pages = iter(pager.by_page(continuation_token=continuation))
            page = next(pages, None)
            if page is None:
                return values
            continuation = pages.continuation_token
            if continuation:
                parsed = urlsplit(continuation)
                namespace = kwargs.get("namespace_name", kwargs.get("resource_name"))
                base = (
                    f"/subscriptions/{self._config.subscription_id}/resourceGroups/{self._config.resource_group}"
                    f"/providers/Microsoft.EventHub/namespaces/{namespace}"
                )
                suffix = (
                    "/eventhubs/" + kwargs["event_hub_name"] + "/consumergroups"
                    if "event_hub_name" in kwargs
                    else "/eventhubs"
                    if "namespace_name" in kwargs
                    else "/providers/Microsoft.Authorization/locks"
                )
                if (
                    parsed.scheme != "https"
                    or parsed.netloc != "management.azure.com"
                    or parsed.fragment
                    or unquote(parsed.path).casefold() != (base + suffix).casefold()
                ):
                    raise _Ownership("ownership_unknown", "inventory continuation changed the exact ARM collection")
            for value in page:
                values.append(value)
                if len(values) > 128:
                    raise _Ownership("ownership_unknown", "inventory item budget exceeded")
            if not continuation:
                return values
        raise _Ownership("ownership_unknown", "inventory page budget exceeded")

    def _describe_namespace(self, namespace_name: str) -> Any | None:
        try:
            return self._rpc(
                self._mgmt.namespaces.get,
                resource_group_name=self._config.resource_group,
                namespace_name=namespace_name,
            )
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _describe_event_hub(self, namespace_name: str, event_hub_name: str) -> Any | None:
        try:
            return self._rpc(
                self._mgmt.event_hubs.get,
                resource_group_name=self._config.resource_group,
                namespace_name=namespace_name,
                event_hub_name=event_hub_name,
            )
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _namespace_owned(self, namespace: Any, target: _Target, source: object) -> None:
        self._arm(namespace, target.namespace_id)
        verify_azure_ownership(
            dict(_field(namespace, "tags", default={}) or {}),
            owner_of(source),
            operation=AzureOperation.UPDATE,
            resource="recorded Event Hubs namespace",
        )

    @staticmethod
    def _arm(resource: Any, expected: str) -> None:
        actual = _field(resource, "id", default=None)
        if not isinstance(actual, str) or not actual:
            raise _Ownership("ownership_unknown", "current ARM identity is missing")
        if actual.casefold() != expected.casefold():
            raise _Ownership("ownership_refused", "current ARM identity does not match the saved target")

    def _child_owned(self, child: Any, expected: str, source: object) -> None:
        self._arm(child, expected)
        blob = self._property(child, "user_metadata", None)
        try:
            if not isinstance(blob, str) or len(blob) > 1024:
                raise ValueError
            tags = json.loads(blob, object_pairs_hook=_unique_object)
            if not isinstance(tags, dict) or any(not isinstance(v, str) for v in tags.values()):
                raise ValueError
        except (TypeError, ValueError):
            raise _Ownership("ownership_unknown", "child source/platform metadata is absent or invalid") from None
        verify_azure_ownership(
            tags, owner_of(source), operation=AzureOperation.UPDATE, resource="recorded Event Hubs child"
        )

    def _inventory(self, target: _Target, source: object) -> tuple[Any | None, dict[str, Any]]:
        locks = self._pages(
            self._locks.management_locks.list_at_resource_level,
            resource_group_name=target.resource_group,
            resource_provider_namespace="Microsoft.EventHub",
            parent_resource_path="",
            resource_type="namespaces",
            resource_name=target.namespace,
        )
        if locks:
            raise _Ownership(
                "resource_lock_present", "namespace or inherited locks require separate operator resolution"
            )
        hubs = self._pages(
            self._mgmt.event_hubs.list_by_namespace,
            resource_group_name=target.resource_group,
            namespace_name=target.namespace,
            top=128,
        )
        if len(hubs) > 1:
            raise _Ownership("ownership_refused", "namespace contains resources outside the saved target")
        hub = self._describe_event_hub(target.namespace, target.hub)
        if hub is None:
            if hubs:
                raise _Ownership("ownership_unknown", "hub lookup and complete inventory disagree")
            return None, {}
        self._child_owned(hub, target.hub_id, source)
        if len(hubs) != 1:
            raise _Ownership("ownership_unknown", "hub lookup and complete inventory disagree")
        self._child_owned(hubs[0], target.hub_id, source)
        rows = self._pages(
            self._mgmt.consumer_groups.list_by_event_hub,
            resource_group_name=target.resource_group,
            namespace_name=target.namespace,
            event_hub_name=target.hub,
            top=128,
        )
        groups: dict[str, Any] = {}
        for row in rows:
            name = _field(row, "name", default="")
            _group_name(name)
            if name.casefold() in {v.casefold() for v in groups}:
                raise _Ownership("ownership_unknown", "duplicate consumer-group inventory")
            actual = self._rpc(
                self._mgmt.consumer_groups.get,
                resource_group_name=target.resource_group,
                namespace_name=target.namespace,
                event_hub_name=target.hub,
                consumer_group_name=name,
            )
            if name == "$Default":
                self._arm(row, target.group_id(name))
                self._arm(actual, target.group_id(name))
            else:
                self._child_owned(row, target.group_id(name), source)
                self._child_owned(actual, target.group_id(name), source)
            groups[name] = actual
        if "$Default" not in groups:
            raise _Ownership("ownership_unknown", "built-in $Default group has not been observed")
        return hub, groups

    def _existing(self, target: _Target, source: object) -> tuple[Any, Any, dict[str, Any]]:
        namespace = self._describe_namespace(target.namespace)
        if namespace is None:
            raise _Ownership("ownership_unknown", "recorded namespace is absent")
        self._namespace_owned(namespace, target, source)
        hub, groups = self._inventory(target, source)
        if hub is None:
            raise _Ownership("ownership_unknown", "recorded event hub is absent")
        return namespace, hub, groups

    @staticmethod
    def _namespace_active(namespace: Any) -> bool:
        props = _field(namespace, "properties", default=None)
        status = _enum(_field(props, "status", default=""))
        generation = _enum(_field(props, "provisioning_state", default=""))
        return generation == "Succeeded" and status in {"", "Active"}

    def _desired_groups(self, target: _Target, cfg: dict[str, Any]) -> set[str]:
        groups = set(cfg.get("consumer_groups") or [])
        for name in groups:
            _group_name(name)
            if name == "$Default":
                raise _Ownership("reprovision_required", "built-in $Default is structural, not a managed group")
        if target.group not in {"-", "$Default"}:
            groups.add(target.group)
        return groups

    def _group_changes(self, target: _Target, groups: dict[str, Any], cfg: dict[str, Any]) -> None:
        desired = self._desired_groups(target, cfg)
        if "consumer_groups" in cfg and set(groups) - {"$Default"} - desired:
            raise _Ownership("reprovision_required", "consumer-group removal is not supported in place")

    def _immutable_settings(self, namespace: Any, hub: Any, target: _Target, cfg: dict[str, Any]) -> None:
        actual = {
            "namespace_name": target.namespace,
            "event_hub_name": target.hub,
            "sku": self._sku_name(namespace),
            "zone_redundant": self._property(namespace, "zone_redundant", None),
            "kafka_enabled": self._property(namespace, "kafka_enabled", None),
        }
        if hub is not None:
            actual["cleanup_policy"] = _enum(self._nested_property(hub, "retention_description", "cleanup_policy", ""))
        for name, value in actual.items():
            if name in cfg and cfg[name] != value:
                raise _Ownership(
                    "reprovision_required", f"{name} differs from the saved resource; reprovision required"
                )
        if self._profile.kafka and actual["kafka_enabled"] is not True:
            raise _Ownership("ownership_refused", "saved namespace does not enable the Kafka endpoint")

    def _provision_target(self, spec: ProvisionSpec) -> _Target:
        sid = _source_uuid(spec)
        if spec.recorded_handle:
            return self._saved_target(spec.recorded_handle, spec)
        compact = UUID(sid).hex
        cfg = spec.config or {}
        namespace = (
            cfg["namespace_name"]
            if "namespace_name" in cfg
            else _new_name(self._config.namespace_name_prefix, compact, 50)
        )
        hub = (
            cfg["event_hub_name"]
            if "event_hub_name" in cfg
            else _new_name(self._config.event_hub_name_prefix, compact, 256)
        )
        for name in (namespace, hub):
            if not isinstance(name, str) or compact not in name:
                raise _Ownership(
                    "ownership_refused", "new physical-name overrides must retain the full immutable source UUID hex"
                )
        group = (
            "-"
            if self._profile.kafka
            else (
                "$Default"
                if cfg.get("sku", self._config.default_sku) == "Basic"
                or self._config.default_consumer_group == "$Default"
                else _new_name(self._config.default_consumer_group, compact, 50)
            )
        )
        return self._coordinates(namespace, hub, group)

    def _coordinates(self, namespace: str, hub: str, group: str) -> _Target:
        subscription = _uuid(self._config.subscription_id)
        target = _Target(subscription, self._config.resource_group, namespace, hub, group)
        if not re.fullmatch(r"[A-Za-z0-9_.()\-]{1,90}", target.resource_group) or target.resource_group.endswith("."):
            raise _Ownership("ownership_unknown", "resource group is not representable")
        if not isinstance(namespace, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{4,48}[A-Za-z0-9]", namespace):
            raise _Ownership("ownership_unknown", "namespace is not representable")
        if not isinstance(hub, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}[A-Za-z0-9]|[A-Za-z0-9]", hub
        ):
            raise _Ownership("ownership_unknown", "event hub is not representable")
        if self._profile.kafka:
            if group != "-":
                raise _Ownership("ownership_unknown", "Kafka target cannot imply an ARM consumer group")
        else:
            _group_name(group)
        if len(target.handle(self._profile.kind)) > 512 or len(target.hub_id) > 512:
            raise _Ownership("ownership_unknown", "saved target exceeds backend or binding storage")
        return target

    def _saved_target(self, handle: str, source: object) -> _Target:
        _source_uuid(source)
        parts = handle.split("/")
        if len(parts) != 7 or parts[:2] != [self._profile.kind, "arm-v1"]:
            raise _Ownership(
                "ownership_unknown", "legacy or incomplete placement handle cannot establish the saved ARM target"
            )
        _, _, sub, group, ns, hub, consumer = parts
        if _uuid(sub) != _uuid(self._config.subscription_id) or group != self._config.resource_group:
            raise _Ownership(
                "ownership_refused", "saved subscription/resource group differs from current provider placement"
            )
        target = self._coordinates(ns, hub, consumer)
        if target.handle(self._profile.kind) != handle:
            raise _Ownership("ownership_unknown", "saved target encoding is not canonical")
        return target

    def _namespace_name_for(self, spec: ProvisionSpec) -> str:
        return self._provision_target(spec).namespace

    def _event_hub_name_for(self, spec: ProvisionSpec) -> str:
        return self._provision_target(spec).hub

    def _sku_name(self, namespace: Any) -> str:
        return _enum(_field(_field(namespace, "sku", default=None), "name", default=""))

    def _property(self, resource: Any, name: str, default: Any) -> Any:
        value = _field(resource, name, default=None)
        if value is None:
            value = _field(_field(resource, "properties", default=None), name, default=None)
        return default if value is None else value

    def _nested_property(self, resource: Any, parent: str, name: str, default: Any) -> Any:
        return _field(self._property(resource, parent, None), name, default=default)

    def _network_fields_present(self, cfg: dict[str, Any]) -> bool:
        return bool(
            {
                "network_default_action",
                "trusted_service_access_enabled",
                "ip_rules",
                "virtual_network_rule_ids",
                "ignore_missing_vnet_service_endpoint",
            }.intersection(cfg)
        )

    def _event_hub_fields_present(self, cfg: dict[str, Any]) -> bool:
        return bool(
            {
                "partition_count",
                "cleanup_policy",
                "retention_time_in_hours",
                "min_compaction_lag_time_in_minutes",
                "tombstone_retention_time_in_hours",
                "status",
            }.intersection(cfg)
            or any(key.startswith("capture_") for key in cfg)
        )

    def _payload_has_changes(self, payload: Any, *, ignored: set[str]) -> bool:
        data = payload.as_dict()
        return any(value is not None and value != {} for key, value in data.items() if key not in ignored)


def _field(value: Any, name: str, *, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _not_found(exc: Exception) -> bool:
    from azure.core.exceptions import ResourceNotFoundError

    return isinstance(exc, ResourceNotFoundError)


_DEADLINE: ContextVar[float | None] = ContextVar("event_hubs_management_deadline", default=None)


class _Ownership(AzureEventHubsError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _error_code(exc: Exception) -> str:
    if isinstance(exc, _Ownership):
        return exc.code
    return "ownership_refused" if isinstance(exc, AzureOwnershipError) else "ownership_unknown"


def _uuid(value: Any) -> str:
    try:
        parsed = UUID(value)
        if not parsed.int or str(parsed) != value:
            raise ValueError
        return str(parsed)
    except (ValueError, TypeError, AttributeError):
        raise _Ownership("ownership_unknown", "canonical nonzero immutable UUID is required") from None


def _source_uuid(source: object) -> str:
    return _uuid(getattr(source, "managed_service_id", ""))


def _enum(value: Any) -> str:
    return str(value.value if isinstance(value, Enum) else value or "")


def _metadata(source: object) -> str:
    return json.dumps(
        {"astrolift-managed-by": "platform", "astrolift-managed-service-id": _source_uuid(source)},
        separators=(",", ":"),
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate metadata key")
        result[key] = value
    return result


def _group_name(name: Any) -> None:
    if name == "$Default":
        return
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,48}[A-Za-z0-9]|[A-Za-z0-9]", name):
        raise _Ownership("ownership_unknown", "native consumer-group name is not representable")


def _new_name(prefix: str, compact: str, maximum: int) -> str:
    cleaned = re.sub(r"[^a-z0-9-]", "-", prefix.lower()).strip("-")
    if not cleaned or not cleaned[0].isalpha():
        raise _Ownership("ownership_unknown", "new name prefix must begin with a letter")
    return cleaned[: maximum - 33].rstrip("-") + "-" + compact


@dataclass(frozen=True)
class _Target:
    subscription: str
    resource_group: str
    namespace: str
    hub: str
    group: str

    @property
    def namespace_id(self) -> str:
        return (
            f"/subscriptions/{self.subscription}/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.EventHub/namespaces/{self.namespace}"
        )

    @property
    def hub_id(self) -> str:
        return self.namespace_id + "/eventhubs/" + self.hub

    def group_id(self, name: str) -> str:
        return self.hub_id + "/consumergroups/" + name

    def handle(self, kind: str) -> str:
        return f"{kind}/arm-v1/{self.subscription}/{self.resource_group}/{self.namespace}/{self.hub}/{self.group}"
