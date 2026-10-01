"""Azure Event Grid Standard namespace-topic managed-service driver.

Namespace topics are the durable CloudEvents broker surface of Event Grid
Standard.  MQTT topic spaces and client authorization are intentionally a
separate portable service shape: they do not share the HTTP publish/pull
contract exposed here.
"""

from __future__ import annotations

import hashlib
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
from azure._event_grid_namespace_observation import Observation, bounded
from azure._event_grid_namespace_ownership import (
    OwnershipUnknown,
    Receipts,
    Target,
    assert_identity,
    child_name,
    contains,
    namespace_identity_tags,
    service_uuid,
)
from azure._managed_identities import unlisted_identity
from azure.managed.event_grid import (
    _ADVANCED_FILTER_MODELS,
    _NO_VALUE_FILTERS,
    _SENSITIVE_DELIVERY_ATTRIBUTE_NAMES,
    _SINGLE_VALUE_FILTERS,
    _field,
    _integer_in_range,
    _is_arm_id,
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
    # User-assigned identities a namespace may attach and deliver or
    # dead-letter with. Empty refuses every one (#2087).
    allowed_identity_resource_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name, value, maximum in (
            ("namespace_name_prefix", self.namespace_name_prefix, 17),
            ("topic_name_prefix", self.topic_name_prefix, 17),
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
        self._observation = Observation()
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

    @bounded
    @driver_op(cloud="azure", driver=VARIANT, audit=True, sensitive_kind="managed_service_provision")
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        handle = spec.recorded_handle
        try:
            self._require_vault()
            cfg = spec.config or {}
            error = self._validate(cfg)
            if error:
                return ProvisionResult(False, handle, error, ["invalid_event_grid_namespace_config"])
            target = self._provision_target(spec)
            handle = target.handle
            desired = self._desired_children(cfg, spec)
            receipts = self._load_receipts(target, spec)
            namespace, topic, children = self._inventory(target, spec, receipts)
            self._assert_unlocked(target, namespace is not None, topic is not None, children)
            if namespace is None:
                if spec.recorded_handle or any(r["state"] != "reserved" for r in receipts.resources.values()):
                    raise OwnershipUnknown("recorded namespace is missing; implicit recreation is refused")
                accepted = self._observation.begin(
                    self._mgmt.namespaces.begin_create_or_update,
                    target.resource_group,
                    target.namespace,
                    self._namespace_parameters(spec),
                )
                self._assert_owned(accepted, target, spec)
                namespace = self._namespace(target.namespace)
                if namespace is None or _state(namespace) != "Succeeded":
                    return ProvisionResult(
                        False, handle, "namespace creation is not yet observed ready", ["provision_pending"]
                    )
            self._assert_owned(namespace, target, spec)
            self._immutable_settings(namespace, topic, target, cfg, apply_defaults=True)
            if _state(namespace) != "Succeeded":
                return ProvisionResult(
                    False, handle, "namespace is not in an observed stable state", ["provision_pending"]
                )
            namespace, topic, children = self._inventory(target, spec, receipts)
            self._assert_unlocked(target, True, topic is not None, children)
            self._preflight_children(
                target, desired, children, receipts, spec, prune=bool(cfg.get("prune_subscriptions"))
            )
            if topic is None:
                if spec.recorded_handle:
                    raise OwnershipUnknown("recorded topic is missing; implicit recreation is refused")
                if target.topic_id.casefold() in receipts.resources:
                    if receipts.resources[target.topic_id.casefold()]["state"] != "reserved":
                        raise OwnershipUnknown("recorded topic is missing; implicit recreation is refused")
                else:
                    receipts.reserve(target.topic_id, self._topic_parameters(cfg).serialize())
                    self._store_receipts(receipts, spec)
                self._current_namespace(target, spec)
                accepted = self._observation.begin(
                    self._mgmt.namespace_topics.begin_create_or_update,
                    target.resource_group,
                    target.namespace,
                    target.topic,
                    self._topic_parameters(cfg),
                )
                receipts.accept(target.topic_id, accepted, self._topic_parameters(cfg).serialize())
                self._store_receipts(receipts, spec)
                topic = self._topic(target.namespace, target.topic)
                if topic is None or not receipts.observe(target.topic_id, topic):
                    return ProvisionResult(
                        False, handle, "topic creation is not yet observed ready", ["provision_pending"]
                    )
            self._apply_update(target, cfg, spec, receipts, apply_defaults=True)
            self._reconcile_owned_children(target, cfg, desired, spec, receipts)
            if not self._observed(target, cfg, spec, receipts, apply_defaults=True):
                return ProvisionResult(
                    False, handle, "namespace, topic or subscriptions remain pending", ["provision_pending"]
                )
            self._store_receipts(receipts, spec)
            self._sync_owned_key(target, spec, receipts)
            return ProvisionResult(True, handle, "owned Standard target observed reconciled", ready=True)
        except Exception as exc:
            return ProvisionResult(False, handle, _failure_message(exc), [_failure_code(exc)])

    @bounded
    @driver_op(cloud="azure", driver=VARIANT)
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            self._require_vault()
            target = self._saved_target(spec.handle, spec)
            receipts = self._load_receipts(target, spec)
            namespace, topic, children = self._inventory(target, spec, receipts)
            if namespace is None or topic is None:
                raise OwnershipUnknown("the exact saved Standard target is missing")
            cfg = spec.config or {}
            effective = self._effective_partial_config(namespace, topic, cfg)
            error = self._validate(effective, partial=True)
            if error:
                return UpdateResult(False, spec.handle, error, ["invalid_event_grid_namespace_config"], retryable=False)
            desired = self._desired_children(effective, spec)
            self._immutable_settings(namespace, topic, target, cfg, apply_defaults=False)
            self._assert_unlocked(target, True, True, children)
            self._preflight_children(
                target, desired, children, receipts, spec, prune=bool(cfg.get("prune_subscriptions"))
            )
            if _state(namespace) != "Succeeded" or _state(topic) != "Succeeded":
                return UpdateResult(False, spec.handle, "Standard target update remains pending", ["update_pending"])
            self._apply_update(target, cfg, spec, receipts, apply_defaults=False)
            if "subscriptions" in cfg or cfg.get("prune_subscriptions"):
                self._reconcile_owned_children(target, effective, desired, spec, receipts)
            if not self._observed(target, cfg, spec, receipts, apply_defaults=False):
                return UpdateResult(
                    False, spec.handle, "Standard update is not yet observed reconciled", ["update_pending"]
                )
            self._store_receipts(receipts, spec)
            self._sync_owned_key(target, spec, receipts)
            return UpdateResult(True, spec.handle, "owned Standard update observed reconciled")
        except Exception as exc:
            return UpdateResult(
                False,
                spec.handle,
                _failure_message(exc),
                [_failure_code(exc)],
                retryable=not isinstance(exc, (OwnershipUnknown, AzureOwnershipError, AzureEventGridNamespaceError)),
            )

    @bounded
    @driver_op(cloud="azure", driver=VARIANT, audit=True, sensitive_kind="managed_service_deprovision")
    def deprovision(
        self, spec: DeprovisionSpec, *, delete_data: bool = False, force_destroy: bool = False
    ) -> DeprovisionResult:
        del force_destroy
        try:
            self._require_vault()
            target = self._saved_target(spec.handle, spec)
            receipts = self._load_receipts(target, spec)
            namespace, topic, children = self._inventory(target, spec, receipts)
            if namespace is not None:
                if not delete_data:
                    return DeprovisionResult(
                        False,
                        spec.handle,
                        "queued events have no exact restorable snapshot; "
                        "destructive deletion requires delete_data=true",
                        ["delete_data_required"],
                        retryable=False,
                    )
                self._assert_unlocked(target, True, topic is not None, children)
                for name in children:
                    self._current_namespace(target, spec)
                    current = self._subscription(target.namespace, target.topic, name)
                    if current is not None:
                        receipts.assert_authority(target.child_id(name), current)
                        self._observation.begin(
                            self._mgmt.namespace_topic_event_subscriptions.begin_delete,
                            target.resource_group,
                            target.namespace,
                            target.topic,
                            name,
                        )
                    if self._subscription(target.namespace, target.topic, name) is not None:
                        return DeprovisionResult(
                            False, spec.handle, "subscription deletion remains pending", ["delete_pending"]
                        )
                namespace, topic, children = self._inventory(target, spec, receipts)
                if children:
                    raise OwnershipUnknown("subscription absence is not observed")
                self._assert_unlocked(target, True, topic is not None, children)
                if topic is not None:
                    receipts.assert_authority(target.topic_id, topic)
                    self._observation.begin(
                        self._mgmt.namespace_topics.begin_delete, target.resource_group, target.namespace, target.topic
                    )
                    if self._topic(target.namespace, target.topic) is not None:
                        return DeprovisionResult(
                            False, spec.handle, "topic deletion remains pending", ["delete_pending"]
                        )
                namespace, topic, children = self._inventory(target, spec, receipts)
                self._assert_unlocked(target, True, False, children)
                self._current_namespace(target, spec)
                self._observation.begin(self._mgmt.namespaces.begin_delete, target.resource_group, target.namespace)
                if self._namespace(target.namespace) is not None:
                    return DeprovisionResult(
                        False, spec.handle, "namespace deletion remains pending", ["delete_pending"]
                    )
            self._inventory(target, spec, receipts)
            self._delete_owned_key(target, spec, receipts)
            self._delete_receipt_secret(receipts, spec)
            return DeprovisionResult(True, spec.handle, "saved Standard target observed absent")
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                _failure_message(exc),
                [_failure_code(exc)],
                retryable=not isinstance(exc, (OwnershipUnknown, AzureOwnershipError, AzureEventGridNamespaceError)),
            )

    @bounded
    @driver_op(cloud="azure", driver=VARIANT)
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            self._require_vault()
            target = self._saved_target(handle.handle, handle)
            receipts = self._load_receipts(target, handle)
            namespace, topic, children = self._inventory(target, handle, receipts)
            if namespace is None:
                return ServiceStatus(handle.handle, "deprovisioned", "saved namespace absence independently observed")
            if topic is None:
                return ServiceStatus(handle.handle, "error", "saved namespace topic is missing")
            self._require_receipt_children(target, receipts, children)
            ready = _state(namespace) == "Succeeded" and receipts.observe(target.topic_id, topic)
            ready = all(receipts.observe(target.child_id(name), item) for name, item in children.items()) and ready
            state = (
                "available"
                if ready
                else "error"
                if any(
                    _state(item) in {"Failed", "Canceled", "DeleteFailed", "CreateFailed", "UpdatedFailed"}
                    for item in [namespace, topic, *children.values()]
                )
                else "updating"
            )
            return ServiceStatus(
                handle.handle,
                state,
                f"Azure reports namespace={_state(namespace) or 'unknown'}, "
                f"topic={_state(topic) or 'unknown'}; {len(children)} owned subscription(s)",
            )
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", _failure_message(exc))

    @bounded
    @driver_op(cloud="azure", driver=VARIANT)
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        self._require_vault()
        target = self._saved_target(handle.handle, handle)
        receipts = self._load_receipts(target, handle)
        namespace, topic, children = self._inventory(target, handle, receipts)
        if (
            namespace is None
            or topic is None
            or _state(namespace) != "Succeeded"
            or not receipts.observe(target.topic_id, topic)
        ):
            raise OwnershipUnknown("binding requires an exact owned, observed ready namespace topic")
        if not all(receipts.observe(target.child_id(name), item) for name, item in children.items()):
            raise OwnershipUnknown("subscription completion is not observed")
        self._require_receipt_children(target, receipts, children)
        namespace_name, topic_name = target.namespace, target.topic
        hostname = self._hostname(namespace)
        if not re.fullmatch(
            re.escape(namespace_name) + r"\.[a-z0-9-]+\.eventgrid\.azure\.net", hostname, flags=re.IGNORECASE
        ):
            raise OwnershipUnknown("namespace hostname is not observed on the exact public Azure authority")
        publish_endpoint = f"https://{hostname}/topics/{topic_name}:publish"
        cfg = config or {}
        access_mode = str(cfg.get("access_mode", "publish"))
        if access_mode not in {"publish", "pull", "publish_pull", "manage"}:
            raise AzureEventGridNamespaceError("binding access_mode must be publish, pull, publish_pull, or manage")
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
            requested = cfg.get("subscription_name", "")
            name = child_name(handle, requested)
            subscription = children.get(name)
            if (
                subscription is None
                or _field(_field(subscription, "delivery_configuration"), "delivery_mode") != "Queue"
            ):
                raise OwnershipUnknown("pull binding requires the exact observed owned Queue subscription")
            if self._owned_key(target, handle, receipts) is None:
                raise OwnershipUnknown("pull binding requires an observed source-bound cached key")
            env_vars.update(
                {
                    "EVENT_GRID_NAMESPACE_SUBSCRIPTION": ValueRef(literal=name),
                    "EVENT_GRID_NAMESPACE_RECEIVE_ENDPOINT": ValueRef(
                        literal=f"https://{hostname}/topics/{topic_name}/eventsubscriptions/{name}:receive"
                    ),
                    "EVENT_GRID_NAMESPACE_ACCESS_KEY": ValueRef(secret_ref=self._key_secret_name(receipts)),
                }
            )
            notes.append("pull delivery uses a source-bound Key Vault namespace access key")
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
                "name": {"type": "string", "minLength": 3, "maxLength": 50},
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
                "subscriptions": {"type": "array", "items": subscription_schema, "maxItems": 128},
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
        for value in uamis:
            unlisted = unlisted_identity(value, self._config.allowed_identity_resource_ids)
            if unlisted:
                return (
                    f"Event Grid namespace identity {unlisted!r} is not allowed by the cluster install policy "
                    "eventgrid_namespace_allowed_identity_resource_ids"
                )
        if cfg.get("prune_subscriptions") and not cfg.get("confirm_message_loss"):
            return "prune_subscriptions requires confirm_message_loss=true"
        if str(cfg.get("access_mode", "publish")) not in {"publish", "pull", "publish_pull", "manage"}:
            return "Event Grid namespace access_mode must be publish, pull, publish_pull, or manage"
        subscriptions = cfg.get("subscriptions", []) or []
        if not isinstance(subscriptions, list) or len(subscriptions) > 128:
            return "Event Grid namespace subscriptions must be a list of at most 128 entries"
        names: list[str] = []
        for subscription in subscriptions:
            error = self._validate_subscription(subscription, cfg, int(retention))
            if error:
                return error
            names.append(str(subscription["name"]))
        if len(names) != len({name.casefold() for name in names}):
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
            _validate_resource_name(str(sub.get("name", "")), "subscription name", max_length=50)
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
        if len(names) != len({name.casefold() for name in names}):
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
        unlisted = unlisted_identity(resource_id, self._config.allowed_identity_resource_ids)
        if unlisted:
            return (
                f"Event Grid namespace delivery identity {unlisted!r} is not allowed by the cluster install "
                "policy eventgrid_namespace_allowed_identity_resource_ids"
            )
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

    def _require_vault(self) -> None:
        if self._secrets is None:
            raise _NoVault("Standard ownership receipts require the configured Key Vault")

    def _provision_target(self, spec: ProvisionSpec) -> Target:
        if spec.recorded_handle:
            return self._saved_target(spec.recorded_handle, spec)
        return Target.new(
            subscription=self._config.subscription_id,
            resource_group=self._config.resource_group,
            namespace_prefix=self._config.namespace_name_prefix,
            topic_prefix=self._config.topic_name_prefix,
            source=spec,
            config=spec.config or {},
        )

    def _saved_target(self, handle: str, source: object) -> Target:
        return Target.saved(
            handle, subscription=self._config.subscription_id, resource_group=self._config.resource_group, source=source
        )

    def _namespace_name(self, spec: ProvisionSpec) -> str:
        return self._provision_target(spec).namespace

    def _topic_name(self, spec: ProvisionSpec) -> str:
        return self._provision_target(spec).topic

    def _handle(self, namespace_name: str, topic_name: str) -> str:
        return Target(self._config.subscription_id, self._config.resource_group, namespace_name, topic_name).handle

    def _parse_handle(self, handle: str) -> tuple[str, str]:
        parts = handle.split("/")
        if len(parts) != 6 or parts[:2] != [KIND, "arm-v1"]:
            raise OwnershipUnknown("historical Standard placement is not recorded unambiguously")
        target = Target(parts[2], parts[3], parts[4], parts[5])
        if target.handle != handle or (target.subscription, target.resource_group) != (
            self._config.subscription_id,
            self._config.resource_group,
        ):
            raise OwnershipUnknown("saved placement differs from the current provider")
        return target.namespace, target.topic

    @staticmethod
    def _subscription_name(source: object, requested: str) -> str:
        return child_name(source, requested)

    def _desired_children(self, cfg: dict[str, Any], source: object) -> dict[str, dict[str, Any]]:
        result = {}
        logical_names = set()
        for sub in cfg.get("subscriptions", []) or []:
            name = child_name(source, sub["name"])
            if name in result or sub["name"].casefold() in logical_names:
                raise OwnershipUnknown("logical or generated subscription names collide")
            result[name] = sub
            logical_names.add(sub["name"].casefold())
        return result

    def _read(self, function: Any, *args: Any) -> Any | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self._observation.call(function, *args)
        except ResourceNotFoundError as exc:
            if exc.status_code == 404:
                return None
            raise

    def _namespace(self, namespace_name: str) -> Any | None:
        return self._read(self._mgmt.namespaces.get, self._config.resource_group, namespace_name)

    def _topic(self, namespace_name: str, topic_name: str) -> Any | None:
        return self._read(self._mgmt.namespace_topics.get, self._config.resource_group, namespace_name, topic_name)

    def _subscription(self, namespace_name: str, topic_name: str, name: str) -> Any | None:
        return self._read(
            self._mgmt.namespace_topic_event_subscriptions.get,
            self._config.resource_group,
            namespace_name,
            topic_name,
            name,
        )

    def _assert_owned(self, namespace: Any, target: Target, source: object) -> None:
        try:
            tags = namespace_identity_tags(namespace, target, source)
        except OwnershipUnknown as exc:
            raise AzureOwnershipError(str(exc)) from exc
        verify_azure_ownership(
            tags, owner_of(source), operation=AzureOperation.UPDATE, resource="saved Event Grid Standard namespace"
        )
        if _enum(_field(_field(namespace, "sku"), "name")) != "Standard":
            raise AzureEventGridNamespaceError("namespace SKU is not observed Standard; Standard is required")
        if _enum(_field(namespace, "minimum_tls_version_allowed")) != "1.2":
            raise OwnershipUnknown("namespace minimum TLS version is not observed as 1.2")
        observed = _field(namespace, "location")
        if (
            not isinstance(observed, str)
            or observed.replace(" ", "").casefold() != self._config.location.replace(" ", "").casefold()
        ):
            raise OwnershipUnknown("namespace location differs from the current provider")

    def _current_namespace(self, target: Target, source: object) -> Any:
        namespace = self._namespace(target.namespace)
        if namespace is None:
            raise OwnershipUnknown("namespace disappeared during the operation")
        self._assert_owned(namespace, target, source)
        return namespace

    def _load_receipts(self, target: Target, source: object) -> Receipts:
        self._require_vault()
        receipts = Receipts.empty(target, source)
        secret = self._read(self._secrets.get_secret, self._receipt_secret_name(receipts))
        if secret is None:
            return receipts
        return Receipts.load(_field(secret, "value"), target, source)

    def _store_receipts(self, receipts: Receipts, source: object) -> None:
        self._load_receipts(receipts.target, source)
        raw = receipts.dump()
        self._observation.call(
            self._secrets.set_secret, self._receipt_secret_name(receipts), raw, tags=self._secret_metadata(receipts)
        )
        actual = self._load_receipts(receipts.target, source)
        if actual.dump() != raw:
            raise OwnershipUnknown("receipt persistence is not observed; cloud effects are refused")

    @staticmethod
    def _secret_metadata(receipts: Receipts) -> dict[str, str]:
        return {
            "astrolift-managed-by": "platform",
            "astrolift-managed-service-id": receipts.owner,
            "astrolift-topic-sha256": hashlib.sha256(receipts.target.topic_id.casefold().encode()).hexdigest(),
        }

    def _receipt_secret_name(self, receipts: Receipts) -> str:
        return receipts.secret_name_for(_slug(self._config.secret_name_prefix) + "-v2")

    def _key_secret_name(self, receipts: Receipts) -> str:
        return self._receipt_secret_name(receipts) + "-key"

    def _owned_key(self, target: Target, source: object, receipts: Receipts) -> Any | None:
        service_uuid(source)
        secret = self._read(self._secrets.get_secret, self._key_secret_name(receipts))
        if secret is None:
            return None
        tags = _field(_field(secret, "properties"), "tags")
        wanted = self._secret_metadata(receipts)
        if not isinstance(tags, dict) or any(tags.get(key) != value for key, value in wanted.items()):
            raise OwnershipUnknown("cached key source and placement metadata are not observed")
        for key, value in tags.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            for name, expected in wanted.items():
                if normalized == re.sub(r"[^a-z0-9]", "", name) and value != expected:
                    raise OwnershipUnknown("cached key identity aliases disagree")
        if target != receipts.target or not isinstance(_field(secret, "value"), str) or not _field(secret, "value"):
            raise OwnershipUnknown("cached key is unavailable on the exact saved target")
        return secret

    def _delete_owned_key(self, target: Target, source: object, receipts: Receipts) -> None:
        if self._owned_key(target, source, receipts) is not None:
            name = self._key_secret_name(receipts)
            self._observation.call(self._secrets.begin_delete_secret, name)
            if self._read(self._secrets.get_secret, name) is not None:
                raise _Pending("cached key deletion remains pending")

    def _delete_receipt_secret(self, receipts: Receipts, source: object) -> None:
        self._load_receipts(receipts.target, source)
        if self._read(self._secrets.get_secret, self._receipt_secret_name(receipts)) is not None:
            self._observation.call(self._secrets.begin_delete_secret, self._receipt_secret_name(receipts))
            if self._read(self._secrets.get_secret, self._receipt_secret_name(receipts)) is not None:
                raise _Pending("receipt secret deletion remains pending")

    def _sync_owned_key(self, target: Target, source: object, receipts: Receipts) -> None:
        namespace, topic, children = self._inventory(target, source, receipts)
        if (
            namespace is None
            or topic is None
            or _state(namespace) != "Succeeded"
            or not receipts.observe(target.topic_id, topic)
        ):
            raise OwnershipUnknown("credential publication requires an observed ready exclusive target")
        if not all(receipts.observe(target.child_id(name), child) for name, child in children.items()):
            raise OwnershipUnknown("credential publication requires observed ready children")
        self._require_receipt_children(target, receipts, children)
        if any(
            _field(_field(child, "delivery_configuration"), "delivery_mode") == "Queue" for child in children.values()
        ):
            self._assert_unlocked(target, True, True, children)
            self._owned_key(target, source, receipts)
            keys = self._observation.call(
                self._mgmt.namespaces.list_shared_access_keys, target.resource_group, target.namespace
            )
            key = _field(keys, "key1")
            if not isinstance(key, str) or not key:
                raise OwnershipUnknown("namespace primary key is unavailable")
            self._observation.call(
                self._secrets.set_secret, self._key_secret_name(receipts), key, tags=self._secret_metadata(receipts)
            )
            persisted = self._owned_key(target, source, receipts)
            if persisted is None or _field(persisted, "value") != key:
                raise OwnershipUnknown("cached key persistence is not observed")
        else:
            self._delete_owned_key(target, source, receipts)

    def _inventory(self, target: Target, source: object, receipts: Receipts) -> tuple[Any, Any, dict[str, Any]]:
        namespace = self._namespace(target.namespace)
        if namespace is None:
            collection = (
                f"/subscriptions/{target.subscription}/resourceGroups/{target.resource_group}"
                "/providers/Microsoft.EventGrid/namespaces"
            )
            rows = self._observation.pages(
                self._mgmt.namespaces.list_by_resource_group, collection, target.resource_group
            )
            observed_names = set()
            for row in rows:
                name = _field(row, "name")
                if not isinstance(name, str) or "/" in name or not name:
                    raise OwnershipUnknown("namespace inventory identity is unavailable")
                expected = collection + "/" + name
                assert_identity(row, expected)
                if name.casefold() in observed_names or name.casefold() == target.namespace.casefold():
                    raise OwnershipUnknown("namespace absence conflicts with the complete parent inventory")
                observed_names.add(name.casefold())
            return None, None, {}
        self._assert_owned(namespace, target, source)
        topics = self._observation.pages(
            self._mgmt.namespace_topics.list_by_namespace,
            target.namespace_id + "/topics",
            target.resource_group,
            target.namespace,
        )
        topic = None
        for row in topics:
            if topic is not None or _field(row, "name") != target.topic:
                raise OwnershipUnknown("namespace contains unrelated or duplicate topic resources")
            receipts.assert_authority(target.topic_id, row)
            topic = self._topic(target.namespace, target.topic)
            if topic is None:
                raise OwnershipUnknown("topic inventory conflicts with its direct read")
            receipts.assert_authority(target.topic_id, topic)
        direct = self._topic(target.namespace, target.topic)
        if (topic is None) != (direct is None):
            raise OwnershipUnknown("topic absence conflicts with the complete parent inventory")
        if direct is not None:
            receipts.assert_authority(target.topic_id, direct)
            topic = direct
        children: dict[str, Any] = {}
        if topic is not None:
            rows = self._observation.pages(
                self._mgmt.namespace_topic_event_subscriptions.list_by_namespace_topic,
                target.topic_id + "/eventSubscriptions",
                target.resource_group,
                target.namespace,
                target.topic,
            )
            for row in rows:
                name = _field(row, "name")
                if not isinstance(name, str) or name in children:
                    raise OwnershipUnknown("subscription inventory identity is missing or duplicated")
                receipts.assert_authority(target.child_id(name), row)
                current = self._subscription(target.namespace, target.topic, name)
                if current is None:
                    raise OwnershipUnknown("subscription inventory conflicts with its direct read")
                receipts.assert_authority(target.child_id(name), current)
                children[name] = current
        for attr, path in (
            ("clients", "clients"),
            ("client_groups", "clientGroups"),
            ("topic_spaces", "topicSpaces"),
            ("permission_bindings", "permissionBindings"),
        ):
            operation = getattr(self._mgmt, attr)
            rows = self._observation.pages(
                operation.list_by_namespace, target.namespace_id + "/" + path, target.resource_group, target.namespace
            )
            seen = set()
            for row in rows:
                name = _field(row, "name")
                if attr != "client_groups" or name != "$all" or name in seen:
                    raise OwnershipUnknown("namespace contains MQTT resources outside this service shape")
                expected = target.namespace_id + "/clientGroups/$all"
                assert_identity(row, expected)
                current = self._observation.call(operation.get, target.resource_group, target.namespace, "$all")
                assert_identity(current, expected)
                seen.add(name)
        mqtt = _field(namespace, "topic_spaces_configuration")
        if mqtt is not None and (_enum(_field(mqtt, "state")) != "Disabled" or _field(mqtt, "route_topic_resource_id")):
            raise OwnershipUnknown("namespace MQTT configuration is outside this service shape")
        return namespace, topic, children

    def _assert_unlocked(
        self, target: Target, namespace_exists: bool, topic_exists: bool, children: dict[str, Any]
    ) -> None:
        scopes = [
            f"/subscriptions/{target.subscription}",
            f"/subscriptions/{target.subscription}/resourceGroups/{target.resource_group}",
        ]
        calls = [
            (self._locks.management_locks.list_at_subscription_level, scopes[0], {}),
            (
                self._locks.management_locks.list_at_resource_group_level,
                scopes[1],
                {"resource_group_name": target.resource_group},
            ),
        ]
        resource_scopes = []
        if namespace_exists:
            resource_scopes.append((target.namespace_id, "", "namespaces", target.namespace))
        if topic_exists:
            resource_scopes.append((target.topic_id, "namespaces/" + target.namespace, "topics", target.topic))
        resource_scopes.extend(
            (
                target.child_id(name),
                "namespaces/" + target.namespace + "/topics/" + target.topic,
                "eventSubscriptions",
                name,
            )
            for name in children
        )
        for identity, parent, kind, name in resource_scopes:
            calls.append(
                (
                    self._locks.management_locks.list_at_resource_level,
                    identity,
                    {
                        "resource_group_name": target.resource_group,
                        "resource_provider_namespace": "Microsoft.EventGrid",
                        "parent_resource_path": parent,
                        "resource_type": kind,
                        "resource_name": name,
                    },
                )
            )
        for function, scope, options in calls:
            rows = self._observation.pages(function, scope + "/providers/Microsoft.Authorization/locks", **options)
            for row in rows:
                identity = _field(row, "id")
                if not isinstance(identity, str) or not identity.startswith("/"):
                    raise OwnershipUnknown("lock identity is unavailable")
                lock_scope, separator, name = identity.casefold().partition("/providers/microsoft.authorization/locks/")
                if (
                    not separator
                    or not name
                    or "/" in name
                    or not (lock_scope == scope.casefold() or lock_scope.startswith(scope.casefold() + "/"))
                ):
                    raise OwnershipUnknown("lock inventory returned an unrelated or malformed identity")
                if (
                    lock_scope in {s.casefold() for s in scopes}
                    or lock_scope == target.namespace_id.casefold()
                    or lock_scope.startswith(target.namespace_id.casefold() + "/")
                ):
                    raise OwnershipUnknown("an inherited or target lock requires separate operator resolution")

    def _immutable_settings(
        self, namespace: Any, topic: Any, target: Target, cfg: dict[str, Any], *, apply_defaults: bool
    ) -> None:
        for name, expected in (("namespace_name", target.namespace), ("topic_name", target.topic)):
            if name in cfg and cfg[name] != expected:
                raise AzureEventGridNamespaceError(f"{name} differs from the saved resource; reprovision required")
        self._assert_namespace_immutable(namespace, cfg, apply_defaults=apply_defaults)
        if topic is not None:
            self._assert_topic_immutable(topic, cfg, apply_defaults=apply_defaults)

    @staticmethod
    def _require_receipt_children(target: Target, receipts: Receipts, children: dict[str, Any]) -> None:
        observed = {target.child_id(name).casefold() for name in children}
        expected = set(receipts.resources) - {target.topic_id.casefold()}
        if not expected <= observed:
            raise OwnershipUnknown("recorded subscription completion or presence is not observed")

    def _preflight_children(
        self,
        target: Target,
        desired: dict[str, dict[str, Any]],
        children: dict[str, Any],
        receipts: Receipts,
        source: object,
        *,
        prune: bool,
    ) -> None:
        for name in desired:
            current = self._subscription(target.namespace, target.topic, name)
            if current is not None:
                receipts.assert_authority(target.child_id(name), current)
                if name not in children:
                    raise OwnershipUnknown("desired subscription direct read conflicts with its complete inventory")
        changed = False
        for key, record in list(receipts.resources.items()):
            if key == target.topic_id.casefold():
                continue
            name = record["id"].rsplit("/", 1)[1]
            if name in children:
                continue
            if self._subscription(target.namespace, target.topic, name) is not None:
                raise OwnershipUnknown("recorded subscription direct read conflicts with its complete inventory")
            if prune and name not in desired:
                receipts.resources.pop(key)
                changed = True
            elif record["state"] != "reserved" or name not in desired:
                raise OwnershipUnknown("recorded subscription is missing; implicit recreation is refused")
        if changed:
            self._store_receipts(receipts, source)

    def _apply_update(
        self, target: Target, cfg: dict[str, Any], source: object, receipts: Receipts, *, apply_defaults: bool
    ) -> None:
        namespace, topic, children = self._inventory(target, source, receipts)
        if namespace is None or topic is None:
            raise OwnershipUnknown("the exact saved target is unavailable for update")
        self._assert_unlocked(target, True, True, children)
        if self._has_namespace_update(cfg) or apply_defaults:
            self._current_namespace(target, source)
            actual = self._observation.begin(
                self._mgmt.namespaces.begin_update,
                target.resource_group,
                target.namespace,
                self._namespace_update_parameters(cfg, apply_defaults=apply_defaults),
            )
            self._assert_owned(actual, target, source)
        if "topic_retention_days" in cfg or apply_defaults:
            self._current_namespace(target, source)
            current = self._topic(target.namespace, target.topic)
            receipts.assert_authority(target.topic_id, current)
            actual = self._observation.begin(
                self._mgmt.namespace_topics.begin_update,
                target.resource_group,
                target.namespace,
                target.topic,
                self._topic_update_parameters(cfg, apply_defaults=apply_defaults),
            )
            parameters = self._topic_parameters(
                {**cfg, "topic_retention_days": cfg.get("topic_retention_days", 1)}
            ).serialize()
            receipts.accept(target.topic_id, actual, parameters)
            self._store_receipts(receipts, source)

    def _reconcile_owned_children(
        self,
        target: Target,
        cfg: dict[str, Any],
        desired: dict[str, dict[str, Any]],
        source: object,
        receipts: Receipts,
    ) -> None:
        namespace, topic, children = self._inventory(target, source, receipts)
        if namespace is None or topic is None:
            raise OwnershipUnknown("subscription reconciliation requires the exact owned parent")
        self._assert_unlocked(target, True, True, children)
        for name, sub in desired.items():
            identity = target.child_id(name)
            parameters = self._subscription_parameters(sub, cfg)
            self._current_namespace(target, source)
            current = self._subscription(target.namespace, target.topic, name)
            if current is not None:
                receipts.assert_authority(identity, current)
                if _state(current) != "Succeeded":
                    raise _Pending("subscription update remains pending")
            elif identity.casefold() in receipts.resources:
                if receipts.resources[identity.casefold()]["state"] != "reserved":
                    raise OwnershipUnknown("recorded subscription is missing; recreation is refused")
            else:
                receipts.reserve(identity, parameters.serialize())
                self._store_receipts(receipts, source)
            actual = self._observation.begin(
                self._mgmt.namespace_topic_event_subscriptions.begin_create_or_update,
                target.resource_group,
                target.namespace,
                target.topic,
                name,
                parameters,
            )
            receipts.accept(identity, actual, parameters.serialize())
            self._store_receipts(receipts, source)
        if cfg.get("prune_subscriptions"):
            for name, current in children.items():
                if name in desired:
                    continue
                identity = target.child_id(name)
                self._current_namespace(target, source)
                current = self._subscription(target.namespace, target.topic, name)
                if current is not None:
                    receipts.assert_authority(identity, current)
                    self._observation.begin(
                        self._mgmt.namespace_topic_event_subscriptions.begin_delete,
                        target.resource_group,
                        target.namespace,
                        target.topic,
                        name,
                    )
                if self._subscription(target.namespace, target.topic, name) is not None:
                    raise _Pending("subscription pruning remains pending")
                receipts.resources.pop(identity.casefold(), None)
                self._store_receipts(receipts, source)

    def _observed(
        self, target: Target, cfg: dict[str, Any], source: object, receipts: Receipts, *, apply_defaults: bool
    ) -> bool:
        namespace, topic, children = self._inventory(target, source, receipts)
        if (
            namespace is None
            or topic is None
            or _state(namespace) != "Succeeded"
            or not receipts.observe(target.topic_id, topic)
        ):
            return False
        self._require_receipt_children(target, receipts, children)
        expected = self._namespace_update_parameters(cfg, apply_defaults=apply_defaults).serialize()
        if not contains(namespace.serialize(), expected):
            return False
        if not all(receipts.observe(target.child_id(name), child) for name, child in children.items()):
            return False
        if "subscriptions" in cfg:
            desired = self._desired_children(cfg, source)
            if not set(desired) <= set(children):
                return False
            if cfg.get("prune_subscriptions") and set(desired) != set(children):
                return False
        return True

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


class _Pending(Exception):
    pass


class _NoVault(OwnershipUnknown):
    pass


def _enum(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _state(value: Any) -> str:
    return _enum(_field(value, "provisioning_state"))


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, _NoVault):
        return "no_secret_backend"
    if isinstance(exc, AzureOwnershipError):
        return OWNERSHIP_ERROR_CODE
    if isinstance(exc, _Pending):
        return "operation_pending"
    if isinstance(exc, OwnershipUnknown):
        return "ownership_unknown"
    if isinstance(exc, AzureEventGridNamespaceError):
        return "invalid_event_grid_namespace_config"
    return "ownership_unknown"


def _failure_message(exc: Exception) -> str:
    if isinstance(exc, (_Pending, OwnershipUnknown, AzureOwnershipError, AzureEventGridNamespaceError)):
        return str(exc)
    return "Standard observation failed; current ownership or completion is unknown"
