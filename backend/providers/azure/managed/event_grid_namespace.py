"""Azure Event Grid Standard namespace-topic managed-service driver.

Namespace topics are the durable CloudEvents broker surface of Event Grid
Standard.  MQTT topic spaces and client authorization are intentionally a
separate portable service shape: they do not share the HTTP publish/pull
contract exposed here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qsl, urlparse

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
from azure.managed.event_grid import (
    _ADVANCED_FILTER_MODELS,
    _NO_VALUE_FILTERS,
    _SENSITIVE_DELIVERY_ATTRIBUTE_NAMES,
    _SINGLE_VALUE_FILTERS,
    _field,
    _generated_name,
    _integer_in_range,
    _is_arm_id,
    _not_found,
    _slug,
    _validate_advanced_filter,
    _validate_delivery_attribute,
    _validate_resource_name,
)
from azure.managed.tags import arm_tags_for as tags_for

KIND = "event_bus"
VARIANT = "event_grid_namespace"
_MANAGED_SUBSCRIPTION_PREFIX = "astrolift-"
_STATE_MAP = {
    "Creating": "provisioning",
    "Updating": "updating",
    "Deleting": "deprovisioning",
    "Succeeded": "available",
    "Failed": "error",
    "Canceled": "error",
}
_TOP_LEVEL_FIELDS = {
    "access_mode",
    "capacity",
    "confirm_message_loss",
    "inbound_ip_rules",
    "input_schema",
    "is_zone_redundant",
    "minimum_tls_version_allowed",
    "namespace_name",
    "prune_subscriptions",
    "public_network_access",
    "subscriptions",
    "system_assigned_identity",
    "topic_name",
    "topic_retention_days",
    "user_assigned_identity_resource_ids",
}


class AzureEventGridNamespaceError(Exception):
    """An operator-actionable Event Grid namespace lifecycle error."""


@dataclass(frozen=True)
class AzureEventGridNamespaceConfig:
    subscription_id: str
    resource_group: str
    keyvault_url: str = ""
    location: str = "eastus"
    namespace_name_prefix: str = "astrolift-egns"
    topic_name_prefix: str = "events"
    secret_name_prefix: str = "event-grid-namespace"
    default_capacity: int = 1
    mgmt_client: Any | None = None
    locks_client: Any | None = None
    secret_client: Any | None = None

    def __post_init__(self) -> None:
        for field_name, value, maximum in (
            ("namespace_name_prefix", self.namespace_name_prefix, 32),
            ("topic_name_prefix", self.topic_name_prefix, 32),
            ("secret_name_prefix", self.secret_name_prefix, 40),
        ):
            normalized = _slug(value)
            if not normalized or len(normalized) > maximum:
                raise ValueError(f"Event Grid namespace {field_name} must normalize to 1-{maximum} characters")
            if field_name == "namespace_name_prefix" and _is_reserved_namespace_name(normalized):
                raise ValueError("Event Grid namespace_name_prefix uses a provider-reserved prefix")
        if not _integer_in_range(self.default_capacity, 1, 40):
            raise ValueError("Event Grid namespace default_capacity must be between 1 and 40")


class AzureEventGridNamespaceDriver(ManagedServiceDriver):
    def __init__(self, *, config: AzureEventGridNamespaceConfig) -> None:
        self._config = config
        if config.mgmt_client is not None:
            self._mgmt = config.mgmt_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.eventgrid import EventGridManagementClient

            self._mgmt = EventGridManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
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
        if config.secret_client is not None:
            self._secrets = config.secret_client
        elif config.keyvault_url:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            self._secrets = SecretClient(
                vault_url=config.keyvault_url,
                credential=DefaultAzureCredential(),
            )
        else:
            self._secrets = None

    @driver_op(
        cloud="azure",
        driver=VARIANT,
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        if self._secrets is None:
            return ProvisionResult(
                False,
                "",
                "Event Grid namespaces require Key Vault for subscription ownership and pull credentials",
                ["no_secret_backend"],
            )
        cfg = spec.config or {}
        error = self._validate(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_event_grid_namespace_config"])
        namespace_name = self._namespace_name(spec)
        topic_name = self._topic_name(spec)
        handle = self._handle(namespace_name, topic_name)
        try:
            namespace = self._namespace(namespace_name)
            if namespace is None:
                namespace = self._wait(
                    self._mgmt.namespaces.begin_create_or_update(
                        self._config.resource_group,
                        namespace_name,
                        self._namespace_parameters(spec),
                    ),
                )
            else:
                self._assert_owned(namespace, spec, AzureOperation.PROVISION, namespace_name)
                self._assert_namespace_immutable(namespace, cfg, apply_defaults=True)
                namespace = self._wait(
                    self._mgmt.namespaces.begin_update(
                        self._config.resource_group,
                        namespace_name,
                        self._namespace_update_parameters(cfg, tags=tags_for(spec), apply_defaults=True),
                    ),
                )
            topic = self._topic(namespace_name, topic_name)
            if topic is None:
                self._wait(
                    self._mgmt.namespace_topics.begin_create_or_update(
                        self._config.resource_group,
                        namespace_name,
                        topic_name,
                        self._topic_parameters(cfg),
                    ),
                )
            else:
                self._assert_topic_immutable(topic, cfg, apply_defaults=True)
                self._wait(
                    self._mgmt.namespace_topics.begin_update(
                        self._config.resource_group,
                        namespace_name,
                        topic_name,
                        self._topic_update_parameters(cfg, apply_defaults=True),
                    ),
                )
            registry = self._reconcile_subscriptions(namespace_name, topic_name, cfg)
            self._sync_access_key(namespace_name, registry)
        except AzureOwnershipError as exc:
            return ProvisionResult(False, handle, str(exc), [OWNERSHIP_ERROR_CODE])
        except Exception as exc:
            return ProvisionResult(False, handle, f"reconcile Event Grid namespace topic: {exc}", [str(exc)])
        return ProvisionResult(
            True,
            handle,
            f"Event Grid namespace topic {namespace_name}/{topic_name} available",
            ready=True,
        )

    @driver_op(cloud="azure", driver=VARIANT)
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            namespace_name, topic_name = self._parse_handle(spec.handle)
        except AzureEventGridNamespaceError as exc:
            return UpdateResult(False, spec.handle, str(exc), ["invalid_handle"])
        if self._secrets is None:
            return UpdateResult(
                False,
                spec.handle,
                "Event Grid namespaces require Key Vault for subscription ownership and pull credentials",
                ["no_secret_backend"],
            )
        cfg = spec.config or {}
        try:
            namespace = self._namespace(namespace_name)
            if namespace is None:
                return UpdateResult(
                    False, spec.handle, f"Event Grid namespace {namespace_name} not found", ["not_found"]
                )
            self._assert_owned(namespace, spec, AzureOperation.UPDATE, namespace_name)
            topic = self._topic(namespace_name, topic_name)
            if topic is None:
                return UpdateResult(
                    False, spec.handle, f"Event Grid namespace topic {topic_name} not found", ["not_found"]
                )
            effective_cfg = self._effective_partial_config(namespace, topic, cfg)
            error = self._validate(effective_cfg, partial=True)
            if error:
                return UpdateResult(False, spec.handle, error, ["invalid_event_grid_namespace_config"])
            self._assert_namespace_immutable(namespace, cfg, apply_defaults=False)
            self._assert_topic_immutable(topic, cfg, apply_defaults=False)
            if self._has_namespace_update(cfg):
                self._wait(
                    self._mgmt.namespaces.begin_update(
                        self._config.resource_group,
                        namespace_name,
                        self._namespace_update_parameters(cfg),
                    ),
                )
            if "topic_retention_days" in cfg:
                self._wait(
                    self._mgmt.namespace_topics.begin_update(
                        self._config.resource_group,
                        namespace_name,
                        topic_name,
                        self._topic_update_parameters(cfg),
                    ),
                )
            if "subscriptions" in cfg or cfg.get("prune_subscriptions"):
                registry = self._reconcile_subscriptions(namespace_name, topic_name, effective_cfg)
                self._sync_access_key(namespace_name, registry)
        except AzureOwnershipError as exc:
            return UpdateResult(False, spec.handle, str(exc), [OWNERSHIP_ERROR_CODE], retryable=False)
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Event Grid namespace topic: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"Event Grid namespace topic {namespace_name}/{topic_name} reconciled")

    @driver_op(
        cloud="azure",
        driver=VARIANT,
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
        try:
            namespace_name, topic_name = self._parse_handle(spec.handle)
        except AzureEventGridNamespaceError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        try:
            namespace = self._namespace(namespace_name)
            if namespace is None:
                self._delete_secrets(namespace_name, topic_name)
                return DeprovisionResult(True, spec.handle, f"Event Grid namespace {namespace_name} already gone")
            self._assert_owned(namespace, spec, AzureOperation.DELETE, namespace_name)
            if not delete_data:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    (
                        "Event Grid namespace topics retain queued events for up to seven days; "
                        "set delete_data=true to delete"
                    ),
                    ["delete_data_required"],
                    retryable=False,
                )
            subscriptions = self._subscriptions(namespace_name, topic_name)
            registry = self._load_registry(namespace_name, topic_name)
            external_subscriptions = [
                item for item in subscriptions if str(_field(item, "name", default="")) not in registry
            ]
            other_topics = [
                item for item in self._topics(namespace_name) if str(_field(item, "name", default="")) != topic_name
            ]
            if (external_subscriptions or other_topics) and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    (
                        f"Event Grid namespace has {len(external_subscriptions)} external subscription(s) "
                        f"and {len(other_topics)} external topic(s)"
                    ),
                    ["external_resources_present"],
                    retryable=False,
                )
            locks = self._resource_locks(namespace_name)
            if locks and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"Event Grid namespace {namespace_name} is protected by an Azure resource lock",
                    ["resource_lock_present"],
                    retryable=False,
                )
            if locks:
                self._delete_locks(namespace_name, locks)
            for item in subscriptions:
                name = str(_field(item, "name", default=""))
                if force_destroy or name in registry:
                    self._wait(
                        self._mgmt.namespace_topic_event_subscriptions.begin_delete(
                            self._config.resource_group,
                            namespace_name,
                            topic_name,
                            name,
                        ),
                    )
            if self._topic(namespace_name, topic_name) is not None:
                self._wait(
                    self._mgmt.namespace_topics.begin_delete(
                        self._config.resource_group,
                        namespace_name,
                        topic_name,
                    ),
                )
            self._wait(self._mgmt.namespaces.begin_delete(self._config.resource_group, namespace_name))
            self._delete_secrets(namespace_name, topic_name)
        except AzureOwnershipError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), [OWNERSHIP_ERROR_CODE], retryable=False)
        except Exception as exc:
            return DeprovisionResult(False, spec.handle, f"delete Event Grid namespace topic: {exc}", [str(exc)])
        return DeprovisionResult(
            True,
            spec.handle,
            f"Event Grid namespace {namespace_name} deleted (force_destroy={force_destroy})",
        )

    @driver_op(cloud="azure", driver=VARIANT)
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            namespace_name, topic_name = self._parse_handle(handle.handle)
            namespace = self._namespace(namespace_name)
            if namespace is None:
                return ServiceStatus(handle.handle, "deprovisioned", "Event Grid namespace not found")
            topic = self._topic(namespace_name, topic_name)
            if topic is None:
                return ServiceStatus(handle.handle, "error", f"Event Grid namespace topic {topic_name} not found")
            namespace_state = str(_field(namespace, "provisioning_state", default="Succeeded"))
            topic_state = str(_field(topic, "provisioning_state", default="Succeeded"))
            subscriptions = self._subscriptions(namespace_name, topic_name)
            state = topic_state if topic_state != "Succeeded" else namespace_state
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Event Grid namespace topic: {exc}")
        return ServiceStatus(
            handle.handle,
            _STATE_MAP.get(state, "updating"),
            f"Azure reports namespace={namespace_state}, topic={topic_state}; {len(subscriptions)} subscription(s)",
        )

    @driver_op(cloud="azure", driver=VARIANT)
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        namespace_name, topic_name = self._parse_handle(handle.handle)
        namespace = self._namespace(namespace_name)
        if namespace is None:
            raise AzureEventGridNamespaceError(f"binding requested for missing Event Grid namespace {namespace_name}")
        self._assert_owned(namespace, handle, AzureOperation.INSPECT, namespace_name)
        if self._topic(namespace_name, topic_name) is None:
            raise AzureEventGridNamespaceError(f"binding requested for missing Event Grid namespace topic {topic_name}")
        hostname = self._hostname(namespace)
        publish_endpoint = f"https://{hostname}/topics/{topic_name}:publish"
        cfg = config or {}
        access_mode = str(cfg.get("access_mode", "publish"))
        if access_mode not in {"publish", "pull", "publish_pull", "manage"}:
            raise AzureEventGridNamespaceError(
                "Event Grid namespace binding access_mode must be publish, pull, publish_pull, or manage",
            )
        resource_id = self._topic_resource_id(namespace_name, topic_name)
        env_vars = {
            "EVENT_BUS_NAME": ValueRef(literal=topic_name),
            "EVENT_BUS_ARN": ValueRef(literal=resource_id),
            "EVENT_BUS_REGION": ValueRef(literal=self._config.location),
            "EVENT_BUS_ENDPOINT": ValueRef(literal=publish_endpoint),
            "EVENT_GRID_NAMESPACE": ValueRef(literal=namespace_name),
            "EVENT_GRID_NAMESPACE_HOSTNAME": ValueRef(literal=hostname),
            "EVENT_GRID_NAMESPACE_TOPIC": ValueRef(literal=topic_name),
            "EVENT_GRID_NAMESPACE_PUBLISH_ENDPOINT": ValueRef(literal=publish_endpoint),
        }
        roles: list[str] = []
        notes: list[str] = []
        if access_mode in {"publish", "publish_pull"}:
            roles.append("EventGrid Data Sender")
            notes.append("publishing uses Microsoft Entra workload identity")
        if access_mode in {"pull", "publish_pull"}:
            if self._secrets is None:
                raise AzureEventGridNamespaceError("pull binding requires the configured Key Vault")
            requested = str(cfg.get("subscription_name", ""))
            if not requested:
                raise AzureEventGridNamespaceError("pull binding requires subscription_name")
            _validate_resource_name(requested, "subscription_name", max_length=64)
            subscription_name = self._subscription_name(topic_name, requested)
            registry = self._load_registry(namespace_name, topic_name)
            if registry.get(subscription_name) != "Queue":
                raise AzureEventGridNamespaceError(
                    f"pull binding subscription {requested!r} is not recorded as platform-owned",
                )
            subscription = self._subscription(namespace_name, topic_name, subscription_name)
            if subscription is None:
                raise AzureEventGridNamespaceError(
                    f"pull binding subscription {requested!r} is not declared on this namespace topic",
                )
            mode = str(_field(_field(subscription, "delivery_configuration"), "delivery_mode", default=""))
            if mode != "Queue":
                raise AzureEventGridNamespaceError(f"subscription {requested!r} is not a pull subscription")
            receive_endpoint = f"https://{hostname}/topics/{topic_name}/eventsubscriptions/{subscription_name}:receive"
            env_vars.update(
                {
                    "EVENT_GRID_NAMESPACE_SUBSCRIPTION": ValueRef(literal=subscription_name),
                    "EVENT_GRID_NAMESPACE_RECEIVE_ENDPOINT": ValueRef(literal=receive_endpoint),
                    "EVENT_GRID_NAMESPACE_ACCESS_KEY": ValueRef(
                        secret_ref=self._access_key_secret_name(namespace_name),
                    ),
                },
            )
            notes.append("pull delivery uses a Key Vault-backed namespace access key")
        if access_mode == "manage":
            roles.append("EventGrid Contributor")
            notes.append("manage binding permits Event Grid control-plane operations")
        return Binding(
            env_vars=env_vars,
            iam_grants=[Grant(resource=resource_id, actions=roles)] if roles else [],
            notes="; ".join(notes),
        )

    @driver_op(cloud="azure", driver=VARIANT)
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise AzureEventGridNamespaceError(
            "Event Grid namespace retention and dead-lettering do not provide an exact restorable topic snapshot",
        )

    @driver_op(cloud="azure", driver=VARIANT)
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        raise AzureEventGridNamespaceError("Event Grid namespace topics cannot be restored from a snapshot")

    @driver_op(cloud="azure", driver=VARIANT, heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        identity_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "user_assigned_identity_resource_id"],
            "properties": {
                "type": {"const": "UserAssigned"},
                "user_assigned_identity_resource_id": {"type": "string"},
            },
        }
        delivery_attribute_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "type"],
            "properties": {
                "name": {"type": "string"},
                "type": {"type": "string", "enum": ["static", "dynamic"]},
                "value": {"type": "string"},
                "source_field": {"type": "string"},
                "is_secret": {"const": False},
            },
        }
        destination_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["type"],
            "properties": {
                "type": {"type": "string", "enum": ["webhook", "event_hub"]},
                "endpoint_url": {"type": "string", "format": "uri"},
                "resource_id": {"type": "string"},
                "aad_tenant_id": {"type": "string"},
                "aad_application_id_or_uri": {"type": "string"},
                "minimum_tls_version_allowed": {"const": "1.2"},
                "max_events_per_batch": {"type": "integer", "minimum": 1, "maximum": 5000},
                "preferred_batch_size_in_kilobytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1024,
                },
                "delivery_attributes": {"type": "array", "items": delivery_attribute_schema},
            },
        }
        advanced_filter_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["operator", "key"],
            "properties": {
                "operator": {"type": "string", "enum": sorted(_ADVANCED_FILTER_MODELS)},
                "key": {"type": "string"},
                "value": {},
                "values": {"type": "array"},
            },
        }
        subscription_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "delivery_mode"],
            "properties": {
                "name": {"type": "string", "minLength": 3, "maxLength": 64},
                "delivery_mode": {"type": "string", "enum": ["pull", "push"]},
                "destination": destination_schema,
                "delivery_identity": identity_schema,
                "destination_preauthorized": {"type": "boolean"},
                "included_event_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                },
                "advanced_filters": {"type": "array", "items": advanced_filter_schema, "maxItems": 25},
                "receive_lock_duration_seconds": {
                    "type": "integer",
                    "minimum": 60,
                    "maximum": 300,
                },
                "max_delivery_count": {"type": "integer", "minimum": 1, "maximum": 2147483647},
                "event_time_to_live_minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
                "dead_letter": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["storage_account_resource_id", "blob_container_name", "identity", "preauthorized"],
                    "properties": {
                        "storage_account_resource_id": {"type": "string"},
                        "blob_container_name": {"type": "string"},
                        "identity": identity_schema,
                        "preauthorized": {"const": True},
                    },
                },
            },
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "namespace_name": {"type": "string", "minLength": 3, "maxLength": 50},
                "topic_name": {"type": "string", "minLength": 3, "maxLength": 50},
                "capacity": {"type": "integer", "minimum": 1, "maximum": 40},
                "input_schema": {"const": "CloudEventSchemaV1_0"},
                "topic_retention_days": {"type": "integer", "minimum": 1, "maximum": 7},
                "is_zone_redundant": {"type": "boolean", "default": False},
                "minimum_tls_version_allowed": {"const": "1.2"},
                "public_network_access": {"type": "string", "enum": ["Enabled", "Disabled"]},
                "inbound_ip_rules": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 128,
                },
                "system_assigned_identity": {"type": "boolean", "default": False},
                "user_assigned_identity_resource_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                },
                "subscriptions": {"type": "array", "items": subscription_schema, "maxItems": 500},
                "prune_subscriptions": {"type": "boolean", "default": False},
                "confirm_message_loss": {"type": "boolean", "default": False},
                "access_mode": {
                    "type": "string",
                    "enum": ["publish", "pull", "publish_pull", "manage"],
                },
            },
        }

    @driver_op(cloud="azure", driver=VARIANT, heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "EVENT_BUS_NAME": "Portable event-bus topic name",
                "EVENT_BUS_ARN": "Portable provider resource identifier",
                "EVENT_BUS_REGION": "Azure region",
                "EVENT_BUS_ENDPOINT": "CloudEvents publishing endpoint",
                "EVENT_GRID_NAMESPACE": "Event Grid namespace name",
                "EVENT_GRID_NAMESPACE_HOSTNAME": "Namespace HTTP hostname",
                "EVENT_GRID_NAMESPACE_TOPIC": "Namespace topic name",
                "EVENT_GRID_NAMESPACE_PUBLISH_ENDPOINT": "CloudEvents publishing endpoint",
                "EVENT_GRID_NAMESPACE_SUBSCRIPTION": "Resolved pull subscription name when requested",
                "EVENT_GRID_NAMESPACE_RECEIVE_ENDPOINT": "Pull-delivery receive endpoint when requested",
                "EVENT_GRID_NAMESPACE_ACCESS_KEY": "Key Vault reference for pull-delivery authentication",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "access_mode",
            "capacity",
            "confirm_message_loss",
            "inbound_ip_rules",
            "prune_subscriptions",
            "public_network_access",
            "subscriptions",
            "system_assigned_identity",
            "topic_retention_days",
            "user_assigned_identity_resource_ids",
        ]

    def _validate(self, cfg: dict[str, Any], *, partial: bool = False) -> str:
        unknown = sorted(set(cfg) - _TOP_LEVEL_FIELDS)
        if unknown:
            return f"unsupported Event Grid namespace config fields: {', '.join(unknown)}"
        for field_name in ("namespace_name", "topic_name"):
            if field_name in cfg:
                try:
                    _validate_resource_name(str(cfg[field_name]), field_name)
                except Exception as exc:
                    return str(exc)
                if partial:
                    return f"Event Grid namespace {field_name} is immutable"
                if field_name == "namespace_name" and _is_reserved_namespace_name(str(cfg[field_name])):
                    return "Event Grid namespace_name uses a provider-reserved prefix"
        if not _integer_in_range(cfg.get("capacity", self._config.default_capacity), 1, 40):
            return "Event Grid namespace capacity must be between 1 and 40 throughput units"
        if str(cfg.get("input_schema", "CloudEventSchemaV1_0")) != "CloudEventSchemaV1_0":
            return "Event Grid namespace topics accept CloudEventSchemaV1_0 only"
        retention = cfg.get("topic_retention_days", 1)
        if not _integer_in_range(retention, 1, 7):
            return "Event Grid namespace topic_retention_days must be between 1 and 7"
        if str(cfg.get("minimum_tls_version_allowed", "1.2")) != "1.2":
            return "Event Grid namespaces require TLS 1.2"
        public_access = str(cfg.get("public_network_access", "Enabled"))
        if public_access not in {"Enabled", "Disabled"}:
            return "Event Grid namespace public_network_access must be Enabled or Disabled"
        if public_access == "Disabled":
            return (
                "Event Grid namespace private-only access requires managed Private Endpoint and DNS lifecycle (#1356)"
            )
        inbound = cfg.get("inbound_ip_rules", []) or []
        if not isinstance(inbound, list) or len(inbound) > 128:
            return "Event Grid namespace inbound_ip_rules must be a list of at most 128 entries"
        import ipaddress

        for value in inbound:
            try:
                ipaddress.ip_network(str(value), strict=False)
            except ValueError:
                return f"Event Grid namespace inbound_ip_rules entry is not an IP address or CIDR: {value!r}"
        uamis = cfg.get("user_assigned_identity_resource_ids", []) or []
        if not isinstance(uamis, list) or len(uamis) != len(set(uamis)):
            return "Event Grid namespace user-assigned identity IDs must be a unique list"
        if any(not _is_arm_id(value) for value in uamis):
            return "Event Grid namespace user-assigned identities must be ARM resource IDs"
        if cfg.get("prune_subscriptions") and not cfg.get("confirm_message_loss"):
            return "prune_subscriptions requires confirm_message_loss=true"
        if str(cfg.get("access_mode", "publish")) not in {"publish", "pull", "publish_pull", "manage"}:
            return "Event Grid namespace access_mode must be publish, pull, publish_pull, or manage"
        subscriptions = cfg.get("subscriptions", []) or []
        if not isinstance(subscriptions, list) or len(subscriptions) > 500:
            return "Event Grid namespace subscriptions must be a list of at most 500 entries"
        names: list[str] = []
        for subscription in subscriptions:
            error = self._validate_subscription(subscription, cfg, int(retention))
            if error:
                return error
            names.append(str(subscription["name"]))
        if len(names) != len(set(names)):
            return "Event Grid namespace subscription names must be unique"
        return ""

    def _validate_subscription(self, sub: Any, topic_cfg: dict[str, Any], retention_days: int) -> str:
        if not isinstance(sub, dict):
            return "each Event Grid namespace subscription must be an object"
        allowed = {
            "advanced_filters",
            "dead_letter",
            "delivery_identity",
            "delivery_mode",
            "destination",
            "destination_preauthorized",
            "event_time_to_live_minutes",
            "included_event_types",
            "max_delivery_count",
            "name",
            "receive_lock_duration_seconds",
        }
        unknown = sorted(set(sub) - allowed)
        if unknown:
            return f"unsupported Event Grid namespace subscription fields: {', '.join(unknown)}"
        try:
            _validate_resource_name(str(sub.get("name", "")), "subscription name", max_length=64)
        except Exception as exc:
            return str(exc)
        mode = str(sub.get("delivery_mode", ""))
        if mode not in {"pull", "push"}:
            return "Event Grid namespace delivery_mode must be pull or push"
        if mode == "pull" and sub.get("destination"):
            return "Event Grid namespace pull subscriptions do not accept a destination"
        if mode == "pull" and (sub.get("delivery_identity") or sub.get("destination_preauthorized")):
            return "Event Grid namespace pull subscriptions do not use a delivery identity"
        if mode == "push":
            destination = sub.get("destination")
            if not isinstance(destination, dict):
                return "Event Grid namespace push subscription requires a destination"
            error = self._validate_destination(destination)
            if error:
                return error
            if "receive_lock_duration_seconds" in sub:
                return "Event Grid namespace receive_lock_duration_seconds applies to pull subscriptions only"
        if not _integer_in_range(sub.get("max_delivery_count", 10), 1, 2_147_483_647):
            return "Event Grid namespace max_delivery_count must be a positive 32-bit integer"
        ttl = sub.get("event_time_to_live_minutes", retention_days * 1440)
        if not _integer_in_range(ttl, 1, retention_days * 1440):
            return "Event Grid namespace event TTL must be between 1 minute and topic retention"
        lock = sub.get("receive_lock_duration_seconds", 60)
        if mode == "pull" and not _integer_in_range(lock, 60, 300):
            return "Event Grid namespace receive lock must be between 60 and 300 seconds"
        if mode == "pull" and int(ttl) * 60 < int(lock):
            return "Event Grid namespace event TTL cannot be shorter than the receive lock"
        filters = sub.get("advanced_filters", []) or []
        if not isinstance(filters, list) or len(filters) > 25:
            return "Event Grid namespace advanced_filters must be a list with at most 25 entries"
        for item in filters:
            error = _validate_advanced_filter(item)
            if error:
                return error
        filter_values = sum(
            len(item.get("values", [])) if "values" in item else 1 for item in filters if isinstance(item, dict)
        )
        if filter_values > 25:
            return "Event Grid namespace advanced filters support at most 25 values"
        included = sub.get("included_event_types", []) or []
        if not isinstance(included, list) or any(not isinstance(value, str) or not value for value in included):
            return "Event Grid namespace included_event_types must be non-empty strings"
        if len(included) != len(set(included)):
            return "Event Grid namespace included_event_types must be unique"
        if sub.get("delivery_identity"):
            error = self._validate_identity(
                sub["delivery_identity"],
                topic_cfg,
                preauthorized=bool(sub.get("destination_preauthorized")),
            )
            if error:
                return error
        dead_letter = sub.get("dead_letter")
        if dead_letter:
            if not isinstance(dead_letter, dict):
                return "Event Grid namespace dead_letter must be an object"
            unknown_dead = set(dead_letter) - {
                "blob_container_name",
                "identity",
                "preauthorized",
                "storage_account_resource_id",
            }
            if unknown_dead:
                return f"unsupported Event Grid namespace dead-letter fields: {', '.join(sorted(unknown_dead))}"
            if not _is_arm_id(dead_letter.get("storage_account_resource_id")):
                return "Event Grid namespace dead-letter storage account must be an ARM resource ID"
            if not dead_letter.get("blob_container_name"):
                return "Event Grid namespace dead-letter destination requires blob_container_name"
            container_name = str(dead_letter["blob_container_name"])
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?", container_name):
                return "Event Grid namespace dead-letter blob_container_name must be a valid Azure container name"
            error = self._validate_identity(
                dead_letter.get("identity"),
                topic_cfg,
                preauthorized=bool(dead_letter.get("preauthorized")),
            )
            if error:
                return error
        return ""

    def _validate_destination(self, destination: dict[str, Any]) -> str:
        kind = str(destination.get("type", ""))
        if kind == "webhook":
            allowed = {
                "aad_application_id_or_uri",
                "aad_tenant_id",
                "delivery_attributes",
                "endpoint_url",
                "max_events_per_batch",
                "minimum_tls_version_allowed",
                "preferred_batch_size_in_kilobytes",
                "type",
            }
            endpoint = str(destination.get("endpoint_url", ""))
            parsed = urlparse(endpoint)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                return "Event Grid namespace webhook must be an absolute HTTPS URL without credentials"
            sensitive_query = {
                key.lower()
                for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
                if any(token in key.lower() for token in ("key", "password", "secret", "sig", "token"))
            }
            if sensitive_query:
                return "Event Grid namespace webhook must not embed secret query parameters"
            if bool(destination.get("aad_tenant_id")) != bool(destination.get("aad_application_id_or_uri")):
                return "Event Grid namespace webhook AAD tenant and application ID/URI must be provided together"
            if str(destination.get("minimum_tls_version_allowed", "1.2")) != "1.2":
                return "Event Grid namespace webhook requires TLS 1.2"
        elif kind == "event_hub":
            allowed = {"delivery_attributes", "resource_id", "type"}
            if not _is_arm_id(destination.get("resource_id")):
                return "Event Grid namespace Event Hubs destination must be an ARM resource ID"
        else:
            return "Event Grid namespace push destination type must be webhook or event_hub"
        unknown = sorted(set(destination) - allowed)
        if unknown:
            return f"unsupported Event Grid namespace {kind} destination fields: {', '.join(unknown)}"
        if not _integer_in_range(destination.get("max_events_per_batch", 1), 1, 5000):
            return "Event Grid namespace max_events_per_batch must be between 1 and 5000"
        if not _integer_in_range(destination.get("preferred_batch_size_in_kilobytes", 64), 1, 1024):
            return "Event Grid namespace preferred_batch_size_in_kilobytes must be between 1 and 1024"
        attributes = destination.get("delivery_attributes", []) or []
        if not isinstance(attributes, list):
            return "Event Grid namespace delivery_attributes must be a list"
        names: list[str] = []
        for item in attributes:
            error = _validate_delivery_attribute(item)
            if error:
                return error
            if str(item["name"]).lower() in _SENSITIVE_DELIVERY_ATTRIBUTE_NAMES:
                return "Event Grid namespace sensitive delivery attributes require a secret-aware integration"
            names.append(str(item["name"]))
        if len(names) != len(set(names)):
            return "Event Grid namespace delivery attribute names must be unique"
        return ""

    def _validate_identity(self, identity: Any, cfg: dict[str, Any], *, preauthorized: bool) -> str:
        if not isinstance(identity, dict):
            return "Event Grid namespace delivery identity must be an object"
        unknown = set(identity) - {"type", "user_assigned_identity_resource_id"}
        if unknown:
            return f"unsupported Event Grid namespace identity fields: {', '.join(sorted(unknown))}"
        if str(identity.get("type", "")) != "UserAssigned":
            return "Event Grid namespace delivery supports a pre-authorized UserAssigned identity only (#1356)"
        resource_id = identity.get("user_assigned_identity_resource_id")
        if not preauthorized:
            return "Event Grid namespace identity delivery requires destination_preauthorized=true until #1356"
        if not _is_arm_id(resource_id):
            return "Event Grid namespace UserAssigned identity requires an ARM resource ID"
        if resource_id not in (cfg.get("user_assigned_identity_resource_ids", []) or []):
            return "Event Grid namespace delivery UAMI must also be attached to the namespace"
        return ""

    def _namespace_parameters(self, spec: ProvisionSpec) -> Any:
        from azure.mgmt.eventgrid import models

        cfg = spec.config or {}
        return models.Namespace(
            location=self._config.location,
            tags=tags_for(spec),
            sku=models.NamespaceSku(name="Standard", capacity=int(cfg.get("capacity", self._config.default_capacity))),
            identity=self._identity(cfg),
            topics_configuration=models.TopicsConfiguration(),
            is_zone_redundant=bool(cfg.get("is_zone_redundant", False)),
            public_network_access=str(cfg.get("public_network_access", "Enabled")),
            inbound_ip_rules=self._ip_rules(cfg),
            minimum_tls_version_allowed="1.2",
        )

    def _namespace_update_parameters(
        self,
        cfg: dict[str, Any],
        *,
        tags: dict[str, str] | None = None,
        apply_defaults: bool = False,
    ) -> Any:
        from azure.mgmt.eventgrid import models

        kwargs: dict[str, Any] = {"tags": tags}
        if "capacity" in cfg or apply_defaults:
            kwargs["sku"] = models.NamespaceSku(
                name="Standard",
                capacity=int(cfg.get("capacity", self._config.default_capacity)),
            )
        if "public_network_access" in cfg or apply_defaults:
            kwargs["public_network_access"] = str(cfg.get("public_network_access", "Enabled"))
        if "inbound_ip_rules" in cfg or apply_defaults:
            kwargs["inbound_ip_rules"] = self._ip_rules(cfg)
        if apply_defaults or "system_assigned_identity" in cfg or "user_assigned_identity_resource_ids" in cfg:
            kwargs["identity"] = self._identity(cfg, explicit_none=True)
        return models.NamespaceUpdateParameters(**kwargs)

    def _topic_parameters(self, cfg: dict[str, Any]) -> Any:
        from azure.mgmt.eventgrid import models

        return models.NamespaceTopic(
            publisher_type="Custom",
            input_schema="CloudEventSchemaV1_0",
            event_retention_in_days=int(cfg.get("topic_retention_days", 1)),
        )

    def _topic_update_parameters(self, cfg: dict[str, Any], *, apply_defaults: bool = False) -> Any:
        from azure.mgmt.eventgrid import models

        retention = int(cfg.get("topic_retention_days", 1)) if apply_defaults else int(cfg["topic_retention_days"])
        return models.NamespaceTopicUpdateParameters(event_retention_in_days=retention)

    def _identity(self, cfg: dict[str, Any], *, explicit_none: bool = False) -> Any | None:
        system = bool(cfg.get("system_assigned_identity", False))
        uamis = [str(value) for value in cfg.get("user_assigned_identity_resource_ids", []) or []]
        if not system and not uamis:
            if not explicit_none:
                return None
            from azure.mgmt.eventgrid import models

            return models.IdentityInfo(type="None")
        from azure.mgmt.eventgrid import models

        identity_type = (
            "SystemAssigned, UserAssigned" if system and uamis else ("SystemAssigned" if system else "UserAssigned")
        )
        return models.IdentityInfo(
            type=identity_type,
            user_assigned_identities={value: models.UserIdentityProperties() for value in uamis} or None,
        )

    def _ip_rules(self, cfg: dict[str, Any]) -> list[Any]:
        from azure.mgmt.eventgrid import models

        return [models.InboundIpRule(ip_mask=str(value), action="Allow") for value in cfg.get("inbound_ip_rules", [])]

    def _reconcile_subscriptions(self, namespace_name: str, topic_name: str, cfg: dict[str, Any]) -> dict[str, str]:
        registry = self._load_registry(namespace_name, topic_name)
        desired: dict[str, str] = {}
        for subscription in cfg.get("subscriptions", []) or []:
            name = self._subscription_name(topic_name, str(subscription["name"]))
            mode = "Queue" if subscription["delivery_mode"] == "pull" else "Push"
            desired[name] = mode

        for name in desired:
            if self._subscription(namespace_name, topic_name, name) is not None and name not in registry:
                raise AzureEventGridNamespaceError(
                    f"Event Grid namespace subscription {name!r} already exists and is not recorded as platform-owned",
                )

        # Reserve deterministic names before cloud mutation. A retry can safely finish
        # after a process failure between the registry write and Azure reconciliation.
        reserved = dict(registry)
        reserved.update(desired)
        self._store_registry(namespace_name, topic_name, reserved)
        for subscription in cfg.get("subscriptions", []) or []:
            name = self._subscription_name(topic_name, str(subscription["name"]))
            self._wait(
                self._mgmt.namespace_topic_event_subscriptions.begin_create_or_update(
                    self._config.resource_group,
                    namespace_name,
                    topic_name,
                    name,
                    self._subscription_parameters(subscription, cfg),
                ),
            )
        if cfg.get("prune_subscriptions"):
            for name in set(registry) - set(desired):
                if self._subscription(namespace_name, topic_name, name) is not None:
                    self._wait(
                        self._mgmt.namespace_topic_event_subscriptions.begin_delete(
                            self._config.resource_group,
                            namespace_name,
                            topic_name,
                            name,
                        ),
                    )
            registry = desired
        else:
            registry = reserved
        if registry != reserved:
            self._store_registry(namespace_name, topic_name, registry)
        return registry

    def _subscription_parameters(self, subscription: dict[str, Any], cfg: dict[str, Any]) -> Any:
        from azure.mgmt.eventgrid import models

        retention_minutes = int(cfg.get("topic_retention_days", 1)) * 1440
        ttl_minutes = int(subscription.get("event_time_to_live_minutes", retention_minutes))
        dead_letter = self._dead_letter(subscription.get("dead_letter"))
        if subscription["delivery_mode"] == "pull":
            delivery = models.DeliveryConfiguration(
                delivery_mode="Queue",
                queue=models.QueueInfo(
                    receive_lock_duration_in_seconds=int(subscription.get("receive_lock_duration_seconds", 60)),
                    max_delivery_count=int(subscription.get("max_delivery_count", 10)),
                    event_time_to_live=timedelta(minutes=ttl_minutes),
                    dead_letter_destination_with_resource_identity=dead_letter,
                ),
            )
        else:
            destination = self._destination(subscription["destination"])
            delivery_identity = subscription.get("delivery_identity")
            delivery_with_identity = None
            if delivery_identity:
                delivery_with_identity = models.DeliveryWithResourceIdentity(
                    identity=self._event_subscription_identity(delivery_identity),
                    destination=destination,
                )
                destination = None
            delivery = models.DeliveryConfiguration(
                delivery_mode="Push",
                push=models.PushInfo(
                    max_delivery_count=int(subscription.get("max_delivery_count", 10)),
                    event_time_to_live=_iso_minutes(ttl_minutes),
                    dead_letter_destination_with_resource_identity=dead_letter,
                    delivery_with_resource_identity=delivery_with_identity,
                    destination=destination,
                ),
            )
        return models.Subscription(
            delivery_configuration=delivery,
            event_delivery_schema="CloudEventSchemaV1_0",
            filters_configuration=models.FiltersConfiguration(
                included_event_types=subscription.get("included_event_types"),
                filters=[self._advanced_filter(item) for item in subscription.get("advanced_filters", [])],
            ),
        )

    @staticmethod
    def _effective_partial_config(namespace: Any, topic: Any, cfg: dict[str, Any]) -> dict[str, Any]:
        effective = dict(cfg)
        effective.setdefault(
            "topic_retention_days",
            int(_field(topic, "event_retention_in_days", default=1)),
        )
        identity = _field(namespace, "identity")
        attached = dict(_field(identity, "user_assigned_identities", default={}) or {})
        effective.setdefault("user_assigned_identity_resource_ids", sorted(attached))
        return effective

    def _destination(self, destination: dict[str, Any]) -> Any:
        from azure.mgmt.eventgrid import models

        attributes = self._delivery_attributes(destination)
        if destination["type"] == "webhook":
            return models.WebHookEventSubscriptionDestination(
                endpoint_url=str(destination["endpoint_url"]),
                max_events_per_batch=int(destination.get("max_events_per_batch", 1)),
                preferred_batch_size_in_kilobytes=int(
                    destination.get("preferred_batch_size_in_kilobytes", 64),
                ),
                azure_active_directory_tenant_id=destination.get("aad_tenant_id"),
                azure_active_directory_application_id_or_uri=destination.get("aad_application_id_or_uri"),
                delivery_attribute_mappings=attributes,
                minimum_tls_version_allowed="1.2",
            )
        return models.EventHubEventSubscriptionDestination(
            resource_id=str(destination["resource_id"]),
            delivery_attribute_mappings=attributes,
        )

    def _delivery_attributes(self, destination: dict[str, Any]) -> list[Any]:
        from azure.mgmt.eventgrid import models

        result: list[Any] = []
        for item in destination.get("delivery_attributes", []) or []:
            if item["type"] == "static":
                result.append(
                    models.StaticDeliveryAttributeMapping(
                        name=str(item["name"]),
                        value=str(item["value"]),
                        is_secret=False,
                    ),
                )
            else:
                result.append(
                    models.DynamicDeliveryAttributeMapping(
                        name=str(item["name"]),
                        source_field=str(item["source_field"]),
                    ),
                )
        return result

    def _advanced_filter(self, item: dict[str, Any]) -> Any:
        from azure.mgmt.eventgrid import models

        operator = str(item["operator"])
        kwargs: dict[str, Any] = {"key": str(item["key"])}
        if operator in _SINGLE_VALUE_FILTERS:
            kwargs["value"] = item["value"]
        elif operator not in _NO_VALUE_FILTERS:
            kwargs["values"] = item["values"]
        return getattr(models, _ADVANCED_FILTER_MODELS[operator])(**kwargs)

    def _dead_letter(self, config: Any) -> Any | None:
        if not config:
            return None
        from azure.mgmt.eventgrid import models

        return models.DeadLetterWithResourceIdentity(
            identity=self._event_subscription_identity(config["identity"]),
            dead_letter_destination=models.StorageBlobDeadLetterDestination(
                resource_id=str(config["storage_account_resource_id"]),
                blob_container_name=str(config["blob_container_name"]),
            ),
        )

    @staticmethod
    def _event_subscription_identity(identity: dict[str, Any]) -> Any:
        from azure.mgmt.eventgrid import models

        return models.EventSubscriptionIdentity(
            type="UserAssigned",
            user_assigned_identity=str(identity["user_assigned_identity_resource_id"]),
        )

    def _namespace_name(self, spec: ProvisionSpec) -> str:
        explicit = str((spec.config or {}).get("namespace_name") or "")
        if explicit:
            return explicit
        hint = spec.service_handle_hint or f"{spec.app_slug}-{spec.environment_name}"
        seed = "/".join(
            [
                self._config.subscription_id,
                self._config.resource_group,
                spec.organization_id,
                spec.app_id,
                spec.environment_id,
                hint,
                "namespace",
            ],
        )
        return _generated_name(self._config.namespace_name_prefix, hint, seed, 50)

    def _topic_name(self, spec: ProvisionSpec) -> str:
        explicit = str((spec.config or {}).get("topic_name") or "")
        if explicit:
            return explicit
        hint = spec.service_handle_hint or spec.app_slug
        seed = "/".join([spec.organization_id, spec.app_id, spec.environment_id, hint, "topic"])
        return _generated_name(self._config.topic_name_prefix, hint, seed, 50)

    @staticmethod
    def _subscription_name(topic_name: str, requested: str) -> str:
        return _generated_name(
            _MANAGED_SUBSCRIPTION_PREFIX.rstrip("-"),
            requested,
            f"{topic_name}/{requested}",
            64,
        )

    @staticmethod
    def _handle(namespace_name: str, topic_name: str) -> str:
        return f"{KIND}/{namespace_name}/{topic_name}"

    @staticmethod
    def _parse_handle(handle: str) -> tuple[str, str]:
        parts = handle.split("/")
        if len(parts) != 3 or parts[0] != KIND:
            raise AzureEventGridNamespaceError(f"invalid Event Grid namespace handle {handle!r}")
        _validate_resource_name(parts[1], "namespace handle")
        _validate_resource_name(parts[2], "topic handle")
        return parts[1], parts[2]

    def _namespace(self, namespace_name: str) -> Any | None:
        try:
            return self._mgmt.namespaces.get(self._config.resource_group, namespace_name)
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _topic(self, namespace_name: str, topic_name: str) -> Any | None:
        try:
            return self._mgmt.namespace_topics.get(self._config.resource_group, namespace_name, topic_name)
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _topics(self, namespace_name: str) -> list[Any]:
        return list(self._mgmt.namespace_topics.list_by_namespace(self._config.resource_group, namespace_name))

    def _subscription(self, namespace_name: str, topic_name: str, subscription_name: str) -> Any | None:
        try:
            return self._mgmt.namespace_topic_event_subscriptions.get(
                self._config.resource_group,
                namespace_name,
                topic_name,
                subscription_name,
            )
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _subscriptions(self, namespace_name: str, topic_name: str) -> list[Any]:
        if self._topic(namespace_name, topic_name) is None:
            return []
        return list(
            self._mgmt.namespace_topic_event_subscriptions.list_by_namespace_topic(
                self._config.resource_group,
                namespace_name,
                topic_name,
            ),
        )

    @staticmethod
    def _assert_owned(namespace: Any, source: object, operation: AzureOperation, namespace_name: str) -> None:
        verify_azure_ownership(
            dict(_field(namespace, "tags", default={}) or {}),
            owner_of(source),
            operation=operation,
            resource=f"Event Grid namespace {namespace_name}",
        )

    def _assert_namespace_immutable(self, namespace: Any, cfg: dict[str, Any], *, apply_defaults: bool) -> None:
        sku = _field(namespace, "sku")
        sku_name = str(_field(sku, "name", default="Standard"))
        if sku_name != "Standard":
            raise AzureEventGridNamespaceError(
                f"Event Grid namespace SKU is {sku_name}; Standard is required",
            )
        checks = (
            ("is_zone_redundant", False),
            ("minimum_tls_version_allowed", "1.2"),
        )
        for field_name, default in checks:
            if not apply_defaults and field_name not in cfg:
                continue
            desired = cfg.get(field_name, default)
            actual = _field(namespace, field_name, default=desired)
            if actual != desired:
                raise AzureEventGridNamespaceError(
                    f"Event Grid namespace {field_name} differs from existing resource; reprovision required",
                )

    @staticmethod
    def _assert_topic_immutable(topic: Any, cfg: dict[str, Any], *, apply_defaults: bool) -> None:
        if not apply_defaults and "input_schema" not in cfg:
            return
        actual = str(_field(topic, "input_schema", default="CloudEventSchemaV1_0"))
        if actual != "CloudEventSchemaV1_0":
            raise AzureEventGridNamespaceError(
                f"Event Grid namespace topic input schema is {actual}; reprovision required",
            )
        publisher_type = str(_field(topic, "publisher_type", default="Custom"))
        if publisher_type != "Custom":
            raise AzureEventGridNamespaceError(
                f"Event Grid namespace topic publisher type is {publisher_type}; Custom is required",
            )

    @staticmethod
    def _has_namespace_update(cfg: dict[str, Any]) -> bool:
        return bool(
            set(cfg)
            & {
                "capacity",
                "inbound_ip_rules",
                "public_network_access",
                "system_assigned_identity",
                "user_assigned_identity_resource_ids",
            },
        )

    def _hostname(self, namespace: Any) -> str:
        configuration = _field(namespace, "topics_configuration")
        hostname = str(_field(configuration, "hostname", default=""))
        if not hostname:
            raise AzureEventGridNamespaceError("Event Grid namespace has no HTTP topics hostname")
        return hostname.removeprefix("https://").rstrip("/")

    def _topic_resource_id(self, namespace_name: str, topic_name: str) -> str:
        return (
            f"/subscriptions/{self._config.subscription_id}/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.EventGrid/namespaces/{namespace_name}/topics/{topic_name}"
        )

    def _registry_secret_name(self, namespace_name: str, topic_name: str) -> str:
        digest = _generated_name("registry", topic_name, f"{namespace_name}/{topic_name}", 32)
        return f"{_slug(self._config.secret_name_prefix)}-{_slug(namespace_name)[:45]}-{digest}"

    def _access_key_secret_name(self, namespace_name: str) -> str:
        return f"{_slug(self._config.secret_name_prefix)}-{_slug(namespace_name)[:50]}-access-key"

    def _load_registry(self, namespace_name: str, topic_name: str) -> dict[str, str]:
        if self._secrets is None:
            return {}
        try:
            value = self._secrets.get_secret(self._registry_secret_name(namespace_name, topic_name))
        except Exception as exc:
            if _not_found(exc):
                return {}
            raise
        raw = str(_field(value, "value", default=""))
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise AzureEventGridNamespaceError(
                "Event Grid namespace subscription ownership registry is invalid JSON",
            ) from exc
        if not isinstance(parsed, dict) or any(
            not isinstance(key, str) or mode not in {"Queue", "Push"} for key, mode in parsed.items()
        ):
            raise AzureEventGridNamespaceError("Event Grid namespace subscription ownership registry is invalid")
        return {str(key): str(mode) for key, mode in parsed.items()}

    def _store_registry(self, namespace_name: str, topic_name: str, registry: dict[str, str]) -> None:
        if self._secrets is None:
            raise AzureEventGridNamespaceError("Event Grid namespace subscription ownership requires Key Vault")
        self._set_secret(
            self._registry_secret_name(namespace_name, topic_name),
            json.dumps(registry, sort_keys=True, separators=(",", ":")),
        )

    def _store_access_key(self, namespace_name: str) -> None:
        if self._secrets is None:
            raise AzureEventGridNamespaceError("Event Grid namespace pull delivery requires Key Vault")
        keys = self._mgmt.namespaces.list_shared_access_keys(self._config.resource_group, namespace_name)
        key = str(_field(keys, "key1", default=""))
        if not key:
            raise AzureEventGridNamespaceError("Event Grid namespace returned no primary access key")
        self._set_secret(self._access_key_secret_name(namespace_name), key)

    def _sync_access_key(self, namespace_name: str, registry: dict[str, str]) -> None:
        if any(mode == "Queue" for mode in registry.values()):
            self._store_access_key(namespace_name)
            return
        self._delete_secret_if_present(self._access_key_secret_name(namespace_name))

    def _set_secret(self, name: str, value: str) -> None:
        try:
            self._secrets.set_secret(name, value)
        except Exception as exc:
            if getattr(exc, "status_code", None) != 409 and type(exc).__name__ != "ResourceExistsError":
                raise
            poller = self._secrets.begin_recover_deleted_secret(name)
            self._wait(poller)
            self._secrets.set_secret(name, value)

    def _delete_secrets(self, namespace_name: str, topic_name: str) -> None:
        if self._secrets is None:
            return
        names = [
            self._access_key_secret_name(namespace_name),
            self._registry_secret_name(namespace_name, topic_name),
        ]
        for name in names:
            self._delete_secret_if_present(name)

    def _delete_secret_if_present(self, name: str) -> None:
        if self._secrets is None:
            return
        try:
            self._secrets.begin_delete_secret(name)
        except Exception as exc:
            if not _not_found(exc):
                raise

    def _resource_locks(self, namespace_name: str) -> list[Any]:
        return list(
            self._locks.management_locks.list_at_resource_level(
                resource_group_name=self._config.resource_group,
                resource_provider_namespace="Microsoft.EventGrid",
                parent_resource_path="",
                resource_type="namespaces",
                resource_name=namespace_name,
            ),
        )

    def _delete_locks(self, namespace_name: str, locks: list[Any]) -> None:
        for lock in locks:
            name = str(_field(lock, "name", default=""))
            if not name:
                raise AzureEventGridNamespaceError("Event Grid namespace lock has no name")
            self._locks.management_locks.delete_at_resource_level(
                resource_group_name=self._config.resource_group,
                resource_provider_namespace="Microsoft.EventGrid",
                parent_resource_path="",
                resource_type="namespaces",
                resource_name=namespace_name,
                lock_name=name,
            )

    @staticmethod
    def _wait(poller: Any) -> Any:
        return poller.result() if hasattr(poller, "result") else poller


def _iso_minutes(minutes: int) -> str:
    days, remainder = divmod(minutes, 1440)
    hours, mins = divmod(remainder, 60)
    date = f"{days}D" if days else ""
    time = ""
    if hours or mins or not date:
        time = f"T{hours}H{mins}M"
    return f"P{date}{time}"


def _is_reserved_namespace_name(value: str) -> bool:
    return value.lower().startswith(("microsoft", "system", "eventgrid"))
