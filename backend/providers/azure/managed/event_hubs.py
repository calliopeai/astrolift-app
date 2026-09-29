"""Azure Event Hubs native-stream and Kafka-compatible managed services."""

from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.azure_ownership import (
    OWNERSHIP_ERROR_CODE,
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
    "SendDisabled": "available",
    "ReceiveDisabled": "available",
    "Unknown": "updating",
}
_ARCHIVE_FORMAT = "{Namespace}/{EventHub}/{PartitionId}/{Year}/{Month}/{Day}/{Hour}/{Minute}/{Second}"


class AzureEventHubsError(Exception):
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
        cfg = spec.config or {}
        validation = self._validate(cfg)
        if validation:
            return ProvisionResult(False, "", validation, ["invalid_runtime_controls"])
        namespace_name = self._namespace_name_for(spec)
        event_hub_name = self._event_hub_name_for(spec)
        handle = self._handle_for(namespace_name, event_hub_name)
        try:
            namespace = self._describe_namespace(namespace_name)
            if namespace is None:
                self._mgmt.namespaces.begin_create_or_update(
                    resource_group_name=self._config.resource_group,
                    namespace_name=namespace_name,
                    parameters=self._namespace_create_parameters(spec),
                ).result()
            else:
                existing_validation = self._validate({"sku": self._sku_name(namespace), **cfg})
                if existing_validation:
                    raise AzureEventHubsError(existing_validation)
                self._assert_owned(namespace, spec, AzureOperation.PROVISION, namespace_name)
                self._assert_create_only(namespace, cfg)
                update = self._namespace_update_parameters(
                    cfg,
                    tags=tags_for(spec),
                    current_sku=self._sku_name(namespace),
                )
                if self._payload_has_changes(update, ignored={"tags"}):
                    self._mgmt.namespaces.update(
                        resource_group_name=self._config.resource_group,
                        namespace_name=namespace_name,
                        parameters=update,
                    )
            self._reconcile_network_rules(namespace_name, cfg)
            existing_hub = self._describe_event_hub(namespace_name, event_hub_name)
            if existing_hub is None or self._event_hub_fields_present(cfg):
                self._mgmt.event_hubs.create_or_update(
                    resource_group_name=self._config.resource_group,
                    namespace_name=namespace_name,
                    event_hub_name=event_hub_name,
                    parameters=self._event_hub_parameters(spec, existing=existing_hub),
                )
            effective_sku = str(
                cfg.get(
                    "sku",
                    self._sku_name(namespace) if namespace is not None else self._config.default_sku,
                ),
            )
            self._reconcile_consumer_groups(
                namespace_name,
                event_hub_name,
                cfg,
                sku=effective_sku,
            )
        except AzureOwnershipError as exc:
            return ProvisionResult(False, handle, str(exc), [OWNERSHIP_ERROR_CODE])
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Event Hubs resource: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle,
            f"Azure {self._config.variant} {namespace_name}/{event_hub_name} is ready",
            ready=True,
        )

    @driver_op(cloud="azure", driver="event_hubs")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        namespace_name, event_hub_name = self._parse_handle(spec.handle)
        cfg = spec.config or {}
        validation = self._validate(cfg)
        if validation:
            return UpdateResult(False, spec.handle, validation, ["invalid_runtime_controls"])
        immutable = sorted(
            key
            for key in ("namespace_name", "event_hub_name", "sku", "zone_redundant", "kafka_enabled", "cleanup_policy")
            if key in cfg
        )
        if immutable:
            return UpdateResult(
                False,
                spec.handle,
                f"Event Hubs fields require reprovision: {', '.join(immutable)}",
                ["reprovision_required"],
            )
        namespace = self._describe_namespace(namespace_name)
        existing_hub = self._describe_event_hub(namespace_name, event_hub_name)
        if namespace is None or existing_hub is None:
            return UpdateResult(False, spec.handle, "Event Hubs resource does not exist", ["not_found"])
        try:
            self._assert_owned(namespace, spec, AzureOperation.UPDATE, namespace_name)
        except AzureOwnershipError as exc:
            return UpdateResult(False, spec.handle, str(exc), [OWNERSHIP_ERROR_CODE], retryable=False)
        existing_validation = self._validate({"sku": self._sku_name(namespace), **cfg})
        if existing_validation:
            return UpdateResult(
                False,
                spec.handle,
                existing_validation,
                ["invalid_runtime_controls"],
            )
        if "partition_count" in cfg and self._sku_name(namespace) not in {"Premium"}:
            current = int(self._property(existing_hub, "partition_count", 0) or 0)
            if current != int(cfg["partition_count"]):
                return UpdateResult(
                    False,
                    spec.handle,
                    "partition_count can only change in-place on Premium; reprovision required",
                    ["reprovision_required"],
                )
        try:
            namespace_fields = {
                "capacity",
                "auto_inflate_enabled",
                "maximum_throughput_units",
                "public_network_access",
                "disable_local_auth",
                "minimum_tls_version",
                "customer_managed_key_name",
                "customer_managed_key_vault_uri",
                "customer_managed_key_version",
                "customer_managed_identity_resource_id",
                "require_infrastructure_encryption",
            }
            namespace_cfg = dict(cfg)
            if spec.size and "capacity" not in namespace_cfg:
                namespace_cfg["capacity"] = _SIZE_TO_CAPACITY.get(
                    spec.size,
                    self._config.default_capacity,
                )
            if namespace_fields.intersection(namespace_cfg):
                self._mgmt.namespaces.update(
                    resource_group_name=self._config.resource_group,
                    namespace_name=namespace_name,
                    parameters=self._namespace_update_parameters(
                        namespace_cfg,
                        current_sku=self._sku_name(namespace),
                    ),
                )
            if self._network_fields_present(cfg):
                self._reconcile_network_rules(namespace_name, cfg)
            if self._event_hub_fields_present(cfg):
                target = ProvisionSpec(
                    organization_id="",
                    organization_slug="",
                    app_id="",
                    app_slug="",
                    environment_id="",
                    environment_name="",
                    tenant_cluster_id="",
                    service_handle_hint="",
                    size=spec.size or "small",
                    config=cfg,
                )
                self._mgmt.event_hubs.create_or_update(
                    resource_group_name=self._config.resource_group,
                    namespace_name=namespace_name,
                    event_hub_name=event_hub_name,
                    parameters=self._event_hub_parameters(target, existing=existing_hub),
                )
            self._reconcile_consumer_groups(
                namespace_name,
                event_hub_name,
                cfg,
                sku=self._sku_name(namespace),
            )
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Event Hubs resource: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Azure {self._config.variant} updated")

    @driver_op(
        cloud="azure",
        driver="event_hubs",
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
        namespace_name, event_hub_name = self._parse_handle(spec.handle)
        namespace = self._describe_namespace(namespace_name)
        if namespace is None:
            return DeprovisionResult(True, spec.handle, f"Event Hubs namespace {namespace_name} already gone")
        try:
            self._assert_owned(namespace, spec, AzureOperation.DELETE, namespace_name)
        except AzureOwnershipError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), [OWNERSHIP_ERROR_CODE], retryable=False)
        if not delete_data:
            return DeprovisionResult(
                False,
                spec.handle,
                (
                    f"Event Hubs stream {namespace_name}/{event_hub_name} has no exact snapshot primitive; "
                    "configure downstream durability and pass delete_data=True"
                ),
                ["delete_data_required"],
                retryable=False,
            )
        try:
            locks = self._list_locks(namespace_name)
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"list Event Hubs resource locks: {exc}", [str(exc)])
        if locks and not force_destroy:
            names = ", ".join(str(_field(lock, "name", default="?")) for lock in locks)
            return DeprovisionResult(
                False,
                spec.handle,
                f"Event Hubs namespace has resource locks ({names}); pass force_destroy=True",
                ["resource_lock_present"],
            )
        try:
            for lock in locks:
                self._delete_lock(namespace_name, str(_field(lock, "name", default="")))
            self._mgmt.namespaces.begin_delete(
                resource_group_name=self._config.resource_group,
                namespace_name=namespace_name,
            ).result()
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"delete Event Hubs namespace: {exc}", [str(exc)])
        return DeprovisionResult(
            True,
            spec.handle,
            f"Event Hubs namespace {namespace_name} deleted (force_destroy={force_destroy})",
        )

    @driver_op(cloud="azure", driver="event_hubs")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        namespace_name, event_hub_name = self._parse_handle(handle.handle)
        namespace = self._describe_namespace(namespace_name)
        if namespace is None:
            return ServiceStatus(handle.handle, "deprovisioned", "Event Hubs namespace not found")
        event_hub = self._describe_event_hub(namespace_name, event_hub_name)
        if event_hub is None:
            return ServiceStatus(handle.handle, "error", f"event hub {event_hub_name} is missing")
        namespace_state = str(self._property(namespace, "status", "Active"))
        hub_state = str(self._property(event_hub, "status", "Active"))
        state = hub_state if hub_state != "Active" else namespace_state
        return ServiceStatus(handle.handle, _STATE_MAP.get(state, "updating"), f"Azure reports {state}")

    @driver_op(cloud="azure", driver="event_hubs")
    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        del config
        namespace_name, event_hub_name = self._parse_handle(handle.handle)
        namespace = self._describe_namespace(namespace_name)
        if namespace is None or self._describe_event_hub(namespace_name, event_hub_name) is None:
            raise AzureEventHubsError(f"binding requested for missing event hub {namespace_name}/{event_hub_name}")
        resource_id = self._event_hub_resource_id(namespace_name, event_hub_name)
        fqdn = f"{namespace_name}.servicebus.windows.net"
        common = {
            "EVENTHUB_NAMESPACE": ValueRef(literal=namespace_name),
            "EVENTHUB_NAME": ValueRef(literal=event_hub_name),
            "EVENTHUB_FULLY_QUALIFIED_NAMESPACE": ValueRef(literal=fqdn),
            "EVENTHUB_RESOURCE_ID": ValueRef(literal=resource_id),
        }
        if self._profile.kafka:
            common.update(
                EVENT_STREAM_BROKERS=ValueRef(literal=f"{fqdn}:9093"),
                EVENT_STREAM_TLS=ValueRef(literal="true"),
                EVENT_STREAM_AUTH_MECHANISM=ValueRef(literal="OAUTHBEARER"),
                EVENTHUB_KAFKA_TOPIC=ValueRef(literal=event_hub_name),
            )
        else:
            common.update(
                STREAM_NAME=ValueRef(literal=event_hub_name),
                STREAM_ARN=ValueRef(literal=resource_id),
                STREAM_ENDPOINT=ValueRef(literal=f"sb://{fqdn}/{event_hub_name}"),
                STREAM_REGION=ValueRef(literal=self._config.location),
                EVENTHUB_CONSUMER_GROUP=ValueRef(
                    literal="$Default" if self._sku_name(namespace) == "Basic" else self._config.default_consumer_group,
                ),
            )
        return Binding(
            env_vars=common,
            iam_grants=[
                Grant(resource_id, ["Azure Event Hubs Data Sender"]),
                Grant(resource_id, ["Azure Event Hubs Data Receiver"]),
            ],
            notes="Microsoft Entra workload identity binding; no SAS connection string is emitted.",
        )

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
                "cleanup_policy": {"type": "string", "enum": ["Delete", "Compact", "DeleteOrCompact"]},
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
        if cleanup not in {"Delete", "Compact", "DeleteOrCompact"}:
            return "unsupported Event Hubs cleanup policy"
        if cleanup in {"Compact", "DeleteOrCompact"} and sku == "Basic":
            return "Event Hubs log compaction requires Standard or Premium"
        try:
            retention = int(cfg.get("retention_time_in_hours", 24))
        except (TypeError, ValueError):
            return "Event Hubs retention_time_in_hours must be an integer"
        retention_limit = {"Basic": 24, "Standard": 168, "Premium": 2160}[sku]
        if retention != -1 and not 1 <= retention <= retention_limit:
            return f"Event Hubs {sku} retention must be -1 or between 1 and {retention_limit} hours"
        if retention == -1 and cleanup == "Delete":
            return "infinite Event Hubs retention requires Compact or DeleteOrCompact"
        groups = cfg.get("consumer_groups", []) or []
        if not isinstance(groups, list) or any(not isinstance(value, str) or not value for value in groups):
            return "Event Hubs consumer_groups must be a list of non-empty strings"
        if len(groups) != len(set(groups)):
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

    def _event_hub_parameters(self, spec: ProvisionSpec, *, existing: Any | None = None) -> Any:
        from azure.mgmt.eventhub import models

        cfg = spec.config or {}

        def current(name: str, default: Any) -> Any:
            return self._property(existing, name, default) if existing is not None else default

        cleanup = str(
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
            status=str(cfg.get("status", current("status", "Active"))),
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
            user_metadata=current("user_metadata", "astrolift-managed"),
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
        self._mgmt.namespaces.create_or_update_network_rule_set(
            resource_group_name=self._config.resource_group,
            namespace_name=namespace_name,
            parameters=rules,
        )

    def _reconcile_consumer_groups(
        self,
        namespace_name: str,
        event_hub_name: str,
        cfg: dict[str, Any],
        *,
        sku: str,
    ) -> None:
        from azure.mgmt.eventhub import models

        if sku == "Basic" or self._profile.kafka:
            return
        groups = list(cfg.get("consumer_groups") or [])
        if not self._profile.kafka and self._config.default_consumer_group not in groups:
            groups.append(self._config.default_consumer_group)
        for group_name in groups:
            self._mgmt.consumer_groups.create_or_update(
                resource_group_name=self._config.resource_group,
                namespace_name=namespace_name,
                event_hub_name=event_hub_name,
                consumer_group_name=str(group_name),
                parameters=models.ConsumerGroup(
                    properties=models.ConsumerGroupProperties(user_metadata="astrolift-managed"),
                ),
            )

    def _describe_namespace(self, namespace_name: str) -> Any | None:
        try:
            return self._mgmt.namespaces.get(
                resource_group_name=self._config.resource_group,
                namespace_name=namespace_name,
            )
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _describe_event_hub(self, namespace_name: str, event_hub_name: str) -> Any | None:
        try:
            return self._mgmt.event_hubs.get(
                resource_group_name=self._config.resource_group,
                namespace_name=namespace_name,
                event_hub_name=event_hub_name,
            )
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    @staticmethod
    def _assert_owned(namespace: Any, source: object, operation: AzureOperation, namespace_name: str) -> None:
        verify_azure_ownership(
            dict(_field(namespace, "tags", default={}) or {}),
            owner_of(source),
            operation=operation,
            resource=f"Event Hubs namespace {namespace_name}",
        )

    def _assert_create_only(self, namespace: Any, cfg: dict[str, Any]) -> None:
        if "sku" in cfg and self._sku_name(namespace) != str(cfg["sku"]):
            raise AzureEventHubsError("Event Hubs sku differs from existing namespace; reprovision required")
        if "kafka_enabled" in cfg and bool(self._property(namespace, "kafka_enabled", False)) != bool(
            cfg["kafka_enabled"],
        ):
            raise AzureEventHubsError("Event Hubs kafka_enabled differs from existing namespace; reprovision required")
        if self._profile.kafka and not bool(self._property(namespace, "kafka_enabled", False)):
            raise AzureEventHubsError(
                "Event Hubs existing namespace does not expose Kafka; reprovision required",
            )
        if "zone_redundant" in cfg and bool(self._property(namespace, "zone_redundant", False)) != bool(
            cfg["zone_redundant"],
        ):
            raise AzureEventHubsError("Event Hubs zone_redundant differs from existing namespace; reprovision required")

    def _list_locks(self, namespace_name: str) -> list[Any]:
        return list(
            self._locks.management_locks.list_at_resource_level(
                resource_group_name=self._config.resource_group,
                resource_provider_namespace="Microsoft.EventHub",
                parent_resource_path="",
                resource_type="namespaces",
                resource_name=namespace_name,
            ),
        )

    def _delete_lock(self, namespace_name: str, lock_name: str) -> None:
        self._locks.management_locks.delete_at_resource_level(
            resource_group_name=self._config.resource_group,
            resource_provider_namespace="Microsoft.EventHub",
            parent_resource_path="",
            resource_type="namespaces",
            resource_name=namespace_name,
            lock_name=lock_name,
        )

    def _namespace_name_for(self, spec: ProvisionSpec) -> str:
        explicit = str((spec.config or {}).get("namespace_name") or "")
        if explicit:
            return _bounded_name(explicit, 50, entropy=explicit)
        base = "-".join(
            filter(
                None,
                (
                    self._config.namespace_name_prefix,
                    spec.organization_slug,
                    spec.app_slug,
                    spec.environment_name,
                    spec.service_handle_hint or "events",
                ),
            ),
        )
        entropy = spec.managed_service_id or spec.app_id or spec.environment_id or base
        return _bounded_name(base, 50, entropy=entropy)

    def _event_hub_name_for(self, spec: ProvisionSpec) -> str:
        explicit = str((spec.config or {}).get("event_hub_name") or "")
        base = explicit or "-".join(
            filter(None, (self._config.event_hub_name_prefix, spec.service_handle_hint or "events"))
        )
        return _safe_name(base, 256)

    def _handle_for(self, namespace_name: str, event_hub_name: str) -> str:
        return f"{self._profile.kind}/{namespace_name}/{event_hub_name}"

    def _parse_handle(self, handle: str) -> tuple[str, str]:
        parts = handle.split("/")
        if len(parts) != 3 or parts[0] != self._profile.kind or not parts[1] or not parts[2]:
            raise AzureEventHubsError(f"invalid {self._config.variant} handle {handle!r}")
        return parts[1], parts[2]

    def _event_hub_resource_id(self, namespace_name: str, event_hub_name: str) -> str:
        return (
            f"/subscriptions/{self._config.subscription_id}/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.EventHub/namespaces/{namespace_name}/eventhubs/{event_hub_name}"
        )

    def _sku_name(self, namespace: Any) -> str:
        sku = _field(namespace, "sku", default=None)
        return str(_field(sku, "name", default=""))

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
        return bool(set(data) - ignored)


def _field(value: Any, name: str, *, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _not_found(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 404 or type(exc).__name__ == "ResourceNotFoundError"


def _safe_name(value: str, max_length: int) -> str:
    clean = "".join(char if char.isascii() and (char.isalnum() or char in {"-", "_", "."}) else "-" for char in value)
    while "--" in clean:
        clean = clean.replace("--", "-")
    clean = clean.strip("-.")
    if not clean:
        raise AzureEventHubsError("Event Hubs resource name cannot be empty")
    return clean[:max_length].rstrip("-.")


def _bounded_name(value: str, max_length: int, *, entropy: str) -> str:
    clean = _safe_name(value.lower(), max(len(value), max_length)).replace("_", "-").replace(".", "-")
    digest = hashlib.sha256(entropy.encode()).hexdigest()[:10]
    prefix = clean[: max_length - len(digest) - 1].rstrip("-")
    return f"{prefix}-{digest}"
