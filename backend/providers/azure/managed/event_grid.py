"""Azure Event Grid custom-topic managed-service driver.

This driver intentionally targets Event Grid Basic custom topics. Event Grid
namespaces have different durability, throughput, pull-delivery, and MQTT
semantics and are tracked as a separate variant in #1355.
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, unquote, urlparse, urlsplit
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
from azure.core.exceptions import ResourceNotFoundError
from azure.managed.tags import arm_tags_for as tags_for

if TYPE_CHECKING:
    from collections.abc import Callable

KIND = "event_bus"
VARIANT = "event_grid"
_MANAGED_SUBSCRIPTION_PREFIX = "astrolift-"
_MANAGED_SUBSCRIPTION_LABEL = "astrolift-managed"
_STATE_MAP = {
    "Creating": "provisioning",
    "Updating": "updating",
    "Deleting": "deprovisioning",
    "Succeeded": "available",
    "Failed": "error",
    "Canceled": "error",
}
_DESTINATION_MODELS = {
    "azure_function": "AzureFunctionEventSubscriptionDestination",
    "event_hub": "EventHubEventSubscriptionDestination",
    "hybrid_connection": "HybridConnectionEventSubscriptionDestination",
    "namespace_topic": "NamespaceTopicEventSubscriptionDestination",
    "service_bus_queue": "ServiceBusQueueEventSubscriptionDestination",
    "service_bus_topic": "ServiceBusTopicEventSubscriptionDestination",
}
_ADVANCED_FILTER_MODELS = {
    "bool_equals": "BoolEqualsAdvancedFilter",
    "is_not_null": "IsNotNullAdvancedFilter",
    "is_null_or_undefined": "IsNullOrUndefinedAdvancedFilter",
    "number_greater_than": "NumberGreaterThanAdvancedFilter",
    "number_greater_than_or_equals": "NumberGreaterThanOrEqualsAdvancedFilter",
    "number_in": "NumberInAdvancedFilter",
    "number_in_range": "NumberInRangeAdvancedFilter",
    "number_less_than": "NumberLessThanAdvancedFilter",
    "number_less_than_or_equals": "NumberLessThanOrEqualsAdvancedFilter",
    "number_not_in": "NumberNotInAdvancedFilter",
    "number_not_in_range": "NumberNotInRangeAdvancedFilter",
    "string_begins_with": "StringBeginsWithAdvancedFilter",
    "string_contains": "StringContainsAdvancedFilter",
    "string_ends_with": "StringEndsWithAdvancedFilter",
    "string_in": "StringInAdvancedFilter",
    "string_not_begins_with": "StringNotBeginsWithAdvancedFilter",
    "string_not_contains": "StringNotContainsAdvancedFilter",
    "string_not_ends_with": "StringNotEndsWithAdvancedFilter",
    "string_not_in": "StringNotInAdvancedFilter",
}
_SINGLE_VALUE_FILTERS = {
    "bool_equals",
    "number_greater_than",
    "number_greater_than_or_equals",
    "number_less_than",
    "number_less_than_or_equals",
}
_NO_VALUE_FILTERS = {"is_not_null", "is_null_or_undefined"}
_SENSITIVE_DELIVERY_ATTRIBUTE_NAMES = {
    "api-key",
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-functions-key",
}
_ALLOWED_TOP_LEVEL = {
    "access_mode",
    "confirm_message_loss",
    "data_residency_boundary",
    "disable_local_auth",
    "inbound_ip_rules",
    "input_schema",
    "minimum_tls_version_allowed",
    "prune_subscriptions",
    "public_network_access",
    "subscriptions",
    "system_assigned_identity",
    "topic_name",
    "topic_user_assigned_identity_resource_ids",
}


_DEADLINE: ContextVar[float | None] = ContextVar("eventgrid_basic_deadline", default=None)


def _bounded[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def bounded(*args: P.args, **kwargs: P.kwargs) -> R:
        token = _DEADLINE.set(time.monotonic() + 20)
        try:
            return function(*args, **kwargs)
        finally:
            _DEADLINE.reset(token)

    return bounded


class AzureEventGridError(Exception):
    """An operator-actionable Event Grid lifecycle error."""


@dataclass(frozen=True)
class AzureEventGridConfig:
    subscription_id: str
    resource_group: str
    location: str = "eastus"
    topic_name_prefix: str = "astrolift-eg"
    default_input_schema: str = "CloudEventSchemaV1_0"
    public_network_access_default: str = "Enabled"
    mgmt_client: Any | None = None
    locks_client: Any | None = None
    # User-assigned identities a topic may attach and deliver or dead-letter
    # with. Empty refuses every one (#2087).
    allowed_identity_resource_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.default_input_schema not in {"CloudEventSchemaV1_0", "EventGridSchema"}:
            raise ValueError(f"unsupported Event Grid input schema {self.default_input_schema!r}")
        if self.public_network_access_default not in {"Enabled", "Disabled"}:
            raise ValueError(
                f"unsupported Event Grid public-network default {self.public_network_access_default!r}",
            )
        prefix = _slug(self.topic_name_prefix)
        if not prefix or len(prefix) > 32:
            raise ValueError("Event Grid topic_name_prefix must normalize to between 1 and 32 characters")


class AzureEventGridDriver(ManagedServiceDriver):
    def __init__(self, *, config: AzureEventGridConfig) -> None:
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

    @_bounded
    @driver_op(cloud="azure", driver="event_grid", audit=True, sensitive_kind="managed_service_provision")
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        handle = spec.recorded_handle
        try:
            cfg = spec.config or {}
            error = self._validate(cfg)
            if error:
                return ProvisionResult(False, handle, error, ["invalid_event_grid_config"])
            target = self._provision_target(spec)
            handle = target.handle
            desired = self._desired_children(cfg, spec)
            topic = self._topic(target.topic)
            if topic is not None:
                self._assert_owned(topic, spec, AzureOperation.PROVISION, target.topic)
            self._assert_unlocked(target, exists=topic is not None)
            if topic is None:
                if spec.recorded_handle:
                    raise _Ownership("ownership_unknown", "recorded Event Grid topic is missing; recreation refused")
                if self._missing_parent_children(target):
                    raise _Ownership("ownership_unknown", "new parent has remaining subscription observations")
                self._rpc(
                    self._mgmt.topics.begin_create_or_update,
                    self._config.resource_group,
                    target.topic,
                    self._topic_create_parameters(spec),
                    polling=False,
                ).result()
                topic = self._topic(target.topic)
                if topic is None:
                    return ProvisionResult(False, handle, "topic creation is not yet observed", ["provision_pending"])
            self._assert_owned(topic, spec, AzureOperation.PROVISION, target.topic)
            children = self._inventory(target, spec)
            self._immutable_settings(topic, target, cfg)
            if _enum(_field(topic, "provisioning_state")) != "Succeeded":
                return ProvisionResult(False, handle, "topic is not in a stable observed state", ["provision_pending"])
            self._assert_unlocked(target, exists=True)
            self._update_topic(target, cfg, spec, apply_defaults=True)
            self._reconcile_subscriptions(target, desired, children, cfg, spec)
            if not self._observed(target, cfg, spec, apply_defaults=True):
                return ProvisionResult(
                    False, handle, "topic or subscriptions are not yet reconciled", ["provision_pending"]
                )
            return ProvisionResult(True, handle, "owned Event Grid target observed reconciled", ready=True)
        except Exception as exc:
            return ProvisionResult(False, handle, _error_message(exc), [_error_code(exc)])

    @_bounded
    @driver_op(cloud="azure", driver="event_grid")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            target = self._saved_target(spec.handle, spec)
            cfg = spec.config or {}
            error = self._validate(cfg, partial=True)
            if error:
                return UpdateResult(False, spec.handle, error, ["invalid_event_grid_config"], retryable=False)
            desired = self._desired_children(cfg, spec)
            topic = self._required_topic(target, spec, AzureOperation.UPDATE)
            children = self._inventory(target, spec)
            self._immutable_settings(topic, target, cfg)
            self._assert_unlocked(target, exists=True)
            if _enum(_field(topic, "provisioning_state")) != "Succeeded":
                return UpdateResult(False, spec.handle, "topic update is pending", ["update_pending"])
            self._update_topic(target, cfg, spec)
            if "subscriptions" in cfg or cfg.get("prune_subscriptions"):
                self._reconcile_subscriptions(target, desired, children, cfg, spec)
            if not self._observed(target, cfg, spec):
                return UpdateResult(False, spec.handle, "update is not yet observed reconciled", ["update_pending"])
            return UpdateResult(True, spec.handle, "owned Event Grid update observed reconciled")
        except Exception as exc:
            return UpdateResult(
                False,
                spec.handle,
                _error_message(exc),
                [_error_code(exc)],
                retryable=not isinstance(exc, (AzureOwnershipError, AzureEventGridError)),
            )

    @_bounded
    @driver_op(cloud="azure", driver="event_grid", audit=True, sensitive_kind="managed_service_deprovision")
    def deprovision(
        self, spec: DeprovisionSpec, *, delete_data: bool = False, force_destroy: bool = False
    ) -> DeprovisionResult:
        del force_destroy
        try:
            target = self._saved_target(spec.handle, spec)
            topic = self._topic(target.topic)
            if topic is None:
                # A deleted parent does not prove its independent subscription inventory is empty.
                if self._missing_parent_children(target):
                    raise _Ownership("ownership_unknown", "missing parent has remaining subscription observations")
                return DeprovisionResult(True, spec.handle, "exact recorded topic and subscription collection absent")
            self._assert_owned(topic, spec, AzureOperation.DELETE, target.topic)
            if not delete_data:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    "Event Grid has no exact snapshot and may hold in-flight retries; set delete_data=true to delete",
                    ["delete_data_required"],
                    retryable=False,
                )
            children = self._inventory(target, spec)
            self._assert_unlocked(target, exists=True)
            for child in children:
                with contextlib.suppress(ResourceNotFoundError):
                    self._rpc(
                        self._mgmt.topic_event_subscriptions.begin_delete,
                        target.resource_group,
                        target.topic,
                        str(_field(child, "name")),
                        polling=False,
                    ).result()
            if self._inventory(target, spec):
                return DeprovisionResult(False, spec.handle, "child deletion remains pending", ["delete_pending"])
            topic = self._topic(target.topic)
            if topic is not None:
                self._assert_owned(topic, spec, AzureOperation.DELETE, target.topic)
                self._assert_unlocked(target, exists=True)
                with contextlib.suppress(ResourceNotFoundError):
                    self._rpc(
                        self._mgmt.topics.begin_delete, target.resource_group, target.topic, polling=False
                    ).result()
            if self._topic(target.topic) is not None:
                return DeprovisionResult(False, spec.handle, "topic deletion remains pending", ["delete_pending"])
            if self._missing_parent_children(target):
                raise _Ownership("ownership_unknown", "deleted parent still has subscription observations")
            return DeprovisionResult(True, spec.handle, "exact owned Event Grid target observed absent")
        except Exception as exc:
            return DeprovisionResult(
                False,
                spec.handle,
                _error_message(exc),
                [_error_code(exc)],
                retryable=not isinstance(exc, (AzureOwnershipError, AzureEventGridError)),
            )

    @_bounded
    @driver_op(cloud="azure", driver="event_grid")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            target = self._saved_target(handle.handle, handle)
            topic = self._topic(target.topic)
            if topic is None:
                if self._missing_parent_children(target):
                    raise _Ownership("ownership_unknown", "missing parent has remaining subscription observations")
                return ServiceStatus(handle.handle, "deprovisioned", "exact recorded topic absent")
            self._assert_owned(topic, handle, AzureOperation.INSPECT, target.topic)
            children = self._inventory(target, handle)
            state = _enum(_field(topic, "provisioning_state"))
            child_states = [_enum(_field(item, "provisioning_state")) for item in children]
            available = state == "Succeeded" and all(value == "Succeeded" for value in child_states)
            observed = "available" if available else _STATE_MAP.get(state, "error")
            if not available and observed == "available":
                pending = {
                    "Creating",
                    "Updating",
                    "Deleting",
                    "AwaitingManualAction",
                    "AwaitingIdentityOperation",
                    "Succeeded",
                }
                observed = "error" if any(value not in pending for value in child_states) else "updating"
            return ServiceStatus(handle.handle, observed, "current owned topic and subscriptions observed")
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", _error_message(exc))

    @_bounded
    @driver_op(cloud="azure", driver="event_grid")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        try:
            return self._binding(handle, config)
        except AzureEventGridError:
            raise
        except Exception as exc:
            raise _Ownership("ownership_unknown", "Event Grid binding observation failed") from exc

    def _binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        target = self._saved_target(handle.handle, handle)
        topic = self._required_topic(target, handle, AzureOperation.INSPECT)
        self._inventory(target, handle)
        resource_id = target.topic_id
        endpoint = str(_field(topic, "endpoint", default=""))
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".eventgrid.azure.net")
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.port not in {None, 443}
            or parsed.path != "/api/events"
            or len(endpoint) > 512
        ):
            raise _Ownership("ownership_unknown", "topic publishing endpoint is not a bounded Azure endpoint")
        input_schema = _enum(_field(topic, "input_schema"))
        if input_schema not in {"CloudEventSchemaV1_0", "EventGridSchema"}:
            raise _Ownership("ownership_unknown", "topic input schema is not observed")
        location = _field(topic, "location", default=None)
        if not isinstance(location, str) or not location or len(location) > 512:
            raise _Ownership("ownership_unknown", "topic region is not observed within binding limits")
        access_mode = str((config or {}).get("access_mode", "publish"))
        if access_mode not in {"publish", "manage"}:
            raise AzureEventGridError("unsupported binding access mode")
        roles = ["EventGrid Data Sender"]
        if access_mode == "manage":
            roles.append("EventGrid EventSubscription Contributor")
        return Binding(
            env_vars={
                "EVENT_BUS_NAME": ValueRef(literal=target.topic),
                "EVENT_BUS_ARN": ValueRef(literal=resource_id),
                "EVENT_BUS_REGION": ValueRef(literal=location),
                "EVENT_BUS_ENDPOINT": ValueRef(literal=endpoint),
                "EVENT_GRID_TOPIC_NAME": ValueRef(literal=target.topic),
                "EVENT_GRID_TOPIC_ENDPOINT": ValueRef(literal=endpoint),
                "EVENT_GRID_TOPIC_RESOURCE_ID": ValueRef(literal=resource_id),
                "EVENT_GRID_INPUT_SCHEMA": ValueRef(literal=input_schema),
            },
            iam_grants=[Grant(resource=resource_id, actions=roles)],
            notes="Microsoft Entra workload identity binding; no topic key or SAS token is emitted.",
        )

    @driver_op(cloud="azure", driver="event_grid")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise AzureEventGridError(
            "Event Grid custom topics expose no snapshot; dead-letter storage is a downstream failure sink",
        )

    @driver_op(cloud="azure", driver="event_grid")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        raise AzureEventGridError("Event Grid custom topics cannot be restored from a snapshot")

    @driver_op(cloud="azure", driver="event_grid", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        destination_types = ["webhook", "storage_queue", "monitor_alert", *_DESTINATION_MODELS]
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
                "type": {"type": "string", "enum": destination_types},
                "resource_id": {"type": "string"},
                "endpoint_url": {"type": "string", "format": "uri"},
                "aad_tenant_id": {"type": "string"},
                "aad_application_id_or_uri": {"type": "string"},
                "minimum_tls_version_allowed": {"type": "string", "enum": ["1.0", "1.1", "1.2"]},
                "max_events_per_batch": {"type": "integer", "minimum": 1, "maximum": 5000},
                "preferred_batch_size_in_kilobytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1024,
                },
                "queue_name": {"type": "string"},
                "queue_message_time_to_live_in_seconds": {"type": "integer", "minimum": -1},
                "severity": {"type": "string", "enum": ["Sev0", "Sev1", "Sev2", "Sev3", "Sev4"]},
                "description": {"type": "string"},
                "action_groups": {"type": "array", "items": {"type": "string"}},
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
            "required": ["name", "destination"],
            "properties": {
                "name": {"type": "string", "minLength": 3, "maxLength": 64},
                "destination": destination_schema,
                "included_event_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                },
                "subject_begins_with": {"type": "string"},
                "subject_ends_with": {"type": "string"},
                "is_subject_case_sensitive": {"type": "boolean"},
                "advanced_filtering_on_arrays": {"type": "boolean"},
                "advanced_filters": {"type": "array", "maxItems": 25, "items": advanced_filter_schema},
                "event_delivery_schema": {
                    "type": "string",
                    "enum": ["CloudEventSchemaV1_0", "EventGridSchema", "CustomInputSchema"],
                },
                "labels": {"type": "array", "maxItems": 10, "items": {"type": "string"}},
                "retry": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "max_delivery_attempts": {"type": "integer", "minimum": 1, "maximum": 30},
                        "event_time_to_live_in_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
                    },
                },
                "delivery_identity": identity_schema,
                "destination_preauthorized": {"type": "boolean"},
                "dead_letter": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["storage_account_resource_id", "blob_container_name"],
                    "properties": {
                        "storage_account_resource_id": {"type": "string"},
                        "blob_container_name": {"type": "string"},
                        "identity": identity_schema,
                        "preauthorized": {"type": "boolean"},
                    },
                },
            },
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "topic_name": {"type": "string", "minLength": 3, "maxLength": 50},
                "input_schema": {
                    "type": "string",
                    "enum": ["CloudEventSchemaV1_0", "EventGridSchema"],
                },
                "minimum_tls_version_allowed": {
                    "type": "string",
                    "enum": ["1.0", "1.1", "1.2"],
                    "default": "1.2",
                },
                "public_network_access": {
                    "type": "string",
                    "enum": ["Enabled", "Disabled"],
                },
                "disable_local_auth": {"type": "boolean", "default": True},
                "data_residency_boundary": {
                    "type": "string",
                    "enum": ["WithinGeopair", "WithinRegion"],
                },
                "inbound_ip_rules": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 128,
                },
                "system_assigned_identity": {"type": "boolean", "default": False},
                "topic_user_assigned_identity_resource_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                },
                "subscriptions": {
                    "type": "array",
                    "maxItems": 128,
                    "items": subscription_schema,
                },
                "prune_subscriptions": {"type": "boolean", "default": False},
                "confirm_message_loss": {"type": "boolean", "default": False},
                "access_mode": {"type": "string", "enum": ["publish", "manage"]},
            },
        }

    @driver_op(cloud="azure", driver="event_grid", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "EVENT_BUS_NAME": "Portable event-bus name",
                "EVENT_BUS_ARN": "Portable provider resource identifier",
                "EVENT_BUS_REGION": "Azure region",
                "EVENT_BUS_ENDPOINT": "Event publishing endpoint",
                "EVENT_GRID_TOPIC_NAME": "Azure Event Grid custom-topic name",
                "EVENT_GRID_TOPIC_ENDPOINT": "Azure Event Grid publishing endpoint",
                "EVENT_GRID_TOPIC_RESOURCE_ID": "Azure Resource Manager topic ID",
                "EVENT_GRID_INPUT_SCHEMA": "Accepted event input schema",
            },
        )

    def editable_fields(self) -> list[str]:
        return [
            "access_mode",
            "confirm_message_loss",
            "data_residency_boundary",
            "disable_local_auth",
            "inbound_ip_rules",
            "minimum_tls_version_allowed",
            "prune_subscriptions",
            "public_network_access",
            "subscriptions",
            "system_assigned_identity",
            "topic_user_assigned_identity_resource_ids",
        ]

    def _validate(self, cfg: dict[str, Any], *, partial: bool = False) -> str:
        unknown = sorted(set(cfg) - _ALLOWED_TOP_LEVEL)
        if unknown:
            return f"unsupported Event Grid config fields: {', '.join(unknown)}"
        if "topic_name" in cfg:
            try:
                _validate_resource_name(str(cfg["topic_name"]), "topic_name")
            except AzureEventGridError as exc:
                return str(exc)
        for flag in ("confirm_message_loss", "prune_subscriptions", "disable_local_auth", "system_assigned_identity"):
            if flag in cfg and type(cfg[flag]) is not bool:
                return f"Event Grid {flag} must be boolean"
        input_schema = str(cfg.get("input_schema", self._config.default_input_schema))
        if input_schema not in {"CloudEventSchemaV1_0", "EventGridSchema"}:
            return "Event Grid supports CloudEventSchemaV1_0 or EventGridSchema; custom mapping is not implemented"
        tls = str(cfg.get("minimum_tls_version_allowed", "1.2"))
        if tls not in {"1.0", "1.1", "1.2"}:
            return f"unsupported Event Grid minimum TLS version {tls!r}"
        public_access = str(cfg.get("public_network_access", self._config.public_network_access_default))
        if public_access not in {"Enabled", "Disabled"}:
            return f"unsupported Event Grid public_network_access {public_access!r}"
        if public_access == "Disabled":
            return "Event Grid private-only access requires managed Private Endpoint and Private DNS lifecycle (#1356)"
        if cfg.get("prune_subscriptions") and not cfg.get("confirm_message_loss"):
            return "prune_subscriptions requires confirm_message_loss=true"
        if str(cfg.get("access_mode", "publish")) not in {"publish", "manage"}:
            return "Event Grid access_mode must be publish or manage"
        for value in cfg.get("inbound_ip_rules", []) or []:
            try:
                ipaddress.ip_network(str(value), strict=False)
            except ValueError:
                return f"Event Grid inbound_ip_rules entry is not an IP address or CIDR: {value!r}"
        uamis = cfg.get("topic_user_assigned_identity_resource_ids", []) or []
        if not isinstance(uamis, list) or len(uamis) != len(set(uamis)):
            return "Event Grid topic user-assigned identity IDs must be a unique list"
        if any(not _is_arm_id(value) for value in uamis):
            return "Event Grid topic user-assigned identities must be ARM resource IDs"
        for value in uamis:
            unlisted = unlisted_identity(value, self._config.allowed_identity_resource_ids)
            if unlisted:
                return (
                    f"Event Grid topic identity {unlisted!r} is not allowed by the cluster install policy "
                    "eventgrid_allowed_identity_resource_ids"
                )
        subscriptions = cfg.get("subscriptions", []) or []
        if not isinstance(subscriptions, list):
            return "Event Grid subscriptions must be a list"
        if len(subscriptions) > 128:
            return "Event Grid bounded reconciliation supports at most 128 declared subscriptions"
        names: list[str] = []
        for subscription in subscriptions:
            error = self._validate_subscription(subscription, cfg)
            if error:
                return error
            names.append(str(subscription["name"]))
        if len(names) != len({name.casefold() for name in names}):
            return "Event Grid subscription names must be unique"
        return ""

    def _validate_subscription(self, sub: Any, topic_cfg: dict[str, Any]) -> str:
        if not isinstance(sub, dict):
            return "each Event Grid subscription must be an object"
        allowed = {
            "advanced_filtering_on_arrays",
            "advanced_filters",
            "dead_letter",
            "delivery_identity",
            "destination",
            "destination_preauthorized",
            "event_delivery_schema",
            "included_event_types",
            "is_subject_case_sensitive",
            "labels",
            "name",
            "retry",
            "subject_begins_with",
            "subject_ends_with",
        }
        unknown = sorted(set(sub) - allowed)
        if unknown:
            return f"unsupported Event Grid subscription fields: {', '.join(unknown)}"
        if not sub.get("name"):
            return "Event Grid subscription requires a non-empty name"
        try:
            _validate_resource_name(str(sub["name"]), "subscription name", max_length=64)
        except AzureEventGridError as exc:
            return str(exc)
        destination = sub.get("destination")
        if not isinstance(destination, dict):
            return f"Event Grid subscription {sub['name']!r} requires a destination object"
        error = self._validate_destination(destination)
        if error:
            return f"Event Grid subscription {sub['name']!r}: {error}"
        delivery_schema = str(sub.get("event_delivery_schema", "CloudEventSchemaV1_0"))
        if delivery_schema not in {"CloudEventSchemaV1_0", "EventGridSchema", "CustomInputSchema"}:
            return f"unsupported Event Grid delivery schema {delivery_schema!r}"
        included_types = sub.get("included_event_types", []) or []
        if not isinstance(included_types, list) or any(
            not isinstance(value, str) or not value for value in included_types
        ):
            return "Event Grid included_event_types must be a list of non-empty strings"
        if len(included_types) != len(set(included_types)):
            return "Event Grid included_event_types must be unique"
        labels = sub.get("labels", []) or []
        if not isinstance(labels, list) or len(labels) > 8 or any(not isinstance(value, str) for value in labels):
            return "Event Grid labels must be a list of at most 8 strings (two labels are reserved for ownership)"
        if any(re.sub(r"[^a-z0-9]", "", label.lower()).startswith(("astrolift", "xastrolift")) for label in labels):
            return "Event Grid astrolift subscription labels are reserved for platform ownership"
        for field_name in ("subject_begins_with", "subject_ends_with"):
            if field_name in sub and not isinstance(sub[field_name], str):
                return f"Event Grid {field_name} must be a string"
        retry = sub.get("retry", {}) or {}
        if not isinstance(retry, dict) or set(retry) - {"max_delivery_attempts", "event_time_to_live_in_minutes"}:
            return "Event Grid retry accepts max_delivery_attempts and event_time_to_live_in_minutes"
        if not _integer_in_range(retry.get("max_delivery_attempts", 30), 1, 30):
            return "Event Grid max_delivery_attempts must be between 1 and 30"
        if not _integer_in_range(retry.get("event_time_to_live_in_minutes", 1440), 1, 1440):
            return "Event Grid event_time_to_live_in_minutes must be between 1 and 1440"
        filters = sub.get("advanced_filters", []) or []
        if not isinstance(filters, list) or len(filters) > 25:
            return "Event Grid advanced_filters must be a list with at most 25 entries"
        for item in filters:
            error = _validate_advanced_filter(item)
            if error:
                return error
        filter_values = sum(
            len(item.get("values", [])) if "values" in item else 1 for item in filters if isinstance(item, dict)
        )
        if filter_values > 25:
            return "Event Grid advanced filters support at most 25 values across the subscription"
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
                return "Event Grid dead_letter must be an object"
            if not _is_arm_id(dead_letter.get("storage_account_resource_id")):
                return "Event Grid dead-letter storage account must be an ARM resource ID"
            if not dead_letter.get("blob_container_name"):
                return "Event Grid dead-letter destination requires blob_container_name"
            container = str(dead_letter["blob_container_name"])
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?", container):
                return "Event Grid dead-letter blob_container_name must be a valid Azure container name"
            unknown_dead = set(dead_letter) - {
                "blob_container_name",
                "identity",
                "preauthorized",
                "storage_account_resource_id",
            }
            if unknown_dead:
                return f"unsupported Event Grid dead-letter fields: {', '.join(sorted(unknown_dead))}"
            if dead_letter.get("identity"):
                error = self._validate_identity(
                    dead_letter["identity"],
                    topic_cfg,
                    preauthorized=bool(dead_letter.get("preauthorized")),
                )
                if error:
                    return error
        return ""

    def _validate_destination(self, destination: dict[str, Any]) -> str:
        kind = str(destination.get("type", ""))
        allowed_types = {"webhook", "storage_queue", "monitor_alert", *_DESTINATION_MODELS}
        if kind not in allowed_types:
            return f"unsupported destination type {kind!r}"
        common = {"type"}
        with_attributes = common | {"delivery_attributes"}
        if kind == "webhook":
            allowed = with_attributes | {
                "aad_application_id_or_uri",
                "aad_tenant_id",
                "endpoint_url",
                "max_events_per_batch",
                "minimum_tls_version_allowed",
                "preferred_batch_size_in_kilobytes",
            }
            endpoint = str(destination.get("endpoint_url", ""))
            parsed = urlparse(endpoint)
            if parsed.scheme != "https" or not parsed.netloc:
                return "webhook endpoint_url must be an absolute HTTPS URL"
            if parsed.username or parsed.password:
                return "webhook endpoint_url must not contain credentials"
            sensitive_query = {
                key.lower()
                for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
                if any(token in key.lower() for token in ("key", "password", "secret", "sig", "token"))
            }
            if sensitive_query:
                return "webhook endpoint_url must not embed secret query parameters"
            if bool(destination.get("aad_tenant_id")) != bool(destination.get("aad_application_id_or_uri")):
                return "webhook AAD tenant and application ID/URI must be provided together"
            if str(destination.get("minimum_tls_version_allowed", "1.2")) not in {"1.0", "1.1", "1.2"}:
                return "webhook minimum_tls_version_allowed must be 1.0, 1.1, or 1.2"
        elif kind == "storage_queue":
            allowed = common | {
                "queue_message_time_to_live_in_seconds",
                "queue_name",
                "resource_id",
            }
            if not destination.get("queue_name"):
                return "storage_queue destination requires queue_name"
            if not _is_arm_id(destination.get("resource_id")):
                return "storage_queue destination resource_id must be an ARM ID"
            ttl = destination.get("queue_message_time_to_live_in_seconds")
            if ttl is not None and not (ttl == -1 or _integer_in_range(ttl, 1, 604800)):
                return "storage_queue message TTL must be -1 or between 1 and 604800 seconds"
        elif kind == "monitor_alert":
            allowed = common | {"action_groups", "description", "severity"}
            if str(destination.get("severity", "Sev3")) not in {"Sev0", "Sev1", "Sev2", "Sev3", "Sev4"}:
                return "monitor_alert severity must be Sev0 through Sev4"
            if any(not _is_arm_id(value) for value in destination.get("action_groups", []) or []):
                return "monitor_alert action_groups must be ARM resource IDs"
        else:
            allowed = with_attributes | {"resource_id"}
            if kind == "azure_function":
                allowed |= {"max_events_per_batch", "preferred_batch_size_in_kilobytes"}
            if kind == "namespace_topic":
                allowed = common | {"resource_id"}
            if not _is_arm_id(destination.get("resource_id")):
                return f"{kind} destination resource_id must be an ARM ID"
        unknown = sorted(set(destination) - allowed)
        if unknown:
            return f"unsupported {kind} destination fields: {', '.join(unknown)}"
        if not _integer_in_range(destination.get("max_events_per_batch", 1), 1, 5000):
            return "max_events_per_batch must be between 1 and 5000"
        if not _integer_in_range(destination.get("preferred_batch_size_in_kilobytes", 64), 1, 1024):
            return "preferred_batch_size_in_kilobytes must be between 1 and 1024"
        mappings = destination.get("delivery_attributes", []) or []
        if not isinstance(mappings, list):
            return "delivery_attributes must be a list"
        names: list[str] = []
        for mapping in mappings:
            error = _validate_delivery_attribute(mapping)
            if error:
                return error
            names.append(str(mapping["name"]))
        if len(names) != len({name.casefold() for name in names}):
            return "delivery attribute names must be unique"
        return ""

    def _validate_identity(
        self,
        identity: Any,
        topic_cfg: dict[str, Any],
        *,
        preauthorized: bool,
    ) -> str:
        if not isinstance(identity, dict):
            return "Event Grid delivery identity must be an object"
        unknown = set(identity) - {"type", "user_assigned_identity_resource_id"}
        if unknown:
            return f"unsupported Event Grid identity fields: {', '.join(sorted(unknown))}"
        identity_type = str(identity.get("type", ""))
        if identity_type not in {"SystemAssigned", "UserAssigned"}:
            return "Event Grid delivery identity type must be SystemAssigned or UserAssigned"
        if not preauthorized:
            return "Event Grid identity delivery requires destination_preauthorized=true until #1356"
        if identity_type == "SystemAssigned":
            return (
                "Event Grid SystemAssigned delivery requires managed destination-role lifecycle (#1356); "
                "use a preauthorized UserAssigned identity"
            )
        if identity_type == "UserAssigned":
            resource_id = identity.get("user_assigned_identity_resource_id")
            if not _is_arm_id(resource_id):
                return "Event Grid UserAssigned delivery identity requires an ARM resource ID"
            unlisted = unlisted_identity(resource_id, self._config.allowed_identity_resource_ids)
            if unlisted:
                return (
                    f"Event Grid delivery identity {unlisted!r} is not allowed by the cluster install policy "
                    "eventgrid_allowed_identity_resource_ids"
                )
            if resource_id not in (topic_cfg.get("topic_user_assigned_identity_resource_ids", []) or []):
                return "Event Grid delivery UAMI must also be attached to the topic"
        return ""

    def _topic_create_parameters(self, spec: ProvisionSpec) -> Any:
        from azure.mgmt.eventgrid import models

        cfg = spec.config or {}
        return models.Topic(
            location=self._config.location,
            tags=tags_for(spec),
            identity=self._identity(cfg),
            input_schema=str(cfg.get("input_schema", self._config.default_input_schema)),
            minimum_tls_version_allowed=str(cfg.get("minimum_tls_version_allowed", "1.2")),
            public_network_access=str(
                cfg.get("public_network_access", self._config.public_network_access_default),
            ),
            inbound_ip_rules=self._ip_rules(cfg),
            disable_local_auth=bool(cfg.get("disable_local_auth", True)),
            data_residency_boundary=cfg.get("data_residency_boundary"),
        )

    def _topic_update_parameters(
        self,
        cfg: dict[str, Any],
        *,
        tags: dict[str, str] | None = None,
        apply_defaults: bool = False,
    ) -> Any:
        from azure.mgmt.eventgrid import models

        kwargs: dict[str, Any] = {"tags": tags}
        field_map = {
            "minimum_tls_version_allowed": "minimum_tls_version_allowed",
            "public_network_access": "public_network_access",
            "disable_local_auth": "disable_local_auth",
            "data_residency_boundary": "data_residency_boundary",
        }
        for source, target in field_map.items():
            if source in cfg:
                kwargs[target] = cfg[source]
        if apply_defaults:
            kwargs.update(
                minimum_tls_version_allowed=str(cfg.get("minimum_tls_version_allowed", "1.2")),
                public_network_access=str(
                    cfg.get("public_network_access", self._config.public_network_access_default),
                ),
                disable_local_auth=bool(cfg.get("disable_local_auth", True)),
            )
        if "inbound_ip_rules" in cfg or apply_defaults:
            kwargs["inbound_ip_rules"] = self._ip_rules(cfg)
        if apply_defaults or "system_assigned_identity" in cfg or "topic_user_assigned_identity_resource_ids" in cfg:
            kwargs["identity"] = self._identity(cfg, explicit_none=True)
        return models.TopicUpdateParameters(**kwargs)

    def _identity(self, cfg: dict[str, Any], *, explicit_none: bool = False) -> Any | None:
        system = bool(cfg.get("system_assigned_identity", False))
        uamis = [str(value) for value in cfg.get("topic_user_assigned_identity_resource_ids", []) or []]
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

    def _desired_children(self, cfg: dict[str, Any], source: object) -> dict[str, dict[str, Any]]:
        desired: dict[str, dict[str, Any]] = {}
        for sub in cfg.get("subscriptions", []) or []:
            name = self._subscription_name(_owner_uuid(source).hex, str(sub["name"]))
            if name.casefold() in {key.casefold() for key in desired}:
                raise AzureEventGridError("declared physical subscription names collide")
            desired[name] = sub
        return desired

    def _reconcile_subscriptions(
        self,
        target: _Target,
        desired: dict[str, dict[str, Any]],
        existing: list[Any],
        cfg: dict[str, Any],
        source: object,
    ) -> None:
        for name, sub in desired.items():
            current = self._subscription(target.topic, name)
            if current is not None:
                self._assert_child_owned(current, target, source)
            parameters = self._subscription_parameters(sub, source)
            if current is None or not _contains(current.serialize(), parameters.serialize()):
                self._rpc(
                    self._mgmt.topic_event_subscriptions.begin_create_or_update,
                    target.resource_group,
                    target.topic,
                    name,
                    parameters,
                    polling=False,
                ).result()
        if cfg.get("prune_subscriptions"):
            for item in existing:
                name = str(_field(item, "name"))
                if name not in desired:
                    current = self._subscription(target.topic, name)
                    if current is not None:
                        self._assert_child_owned(current, target, source)
                        with contextlib.suppress(ResourceNotFoundError):
                            self._rpc(
                                self._mgmt.topic_event_subscriptions.begin_delete,
                                target.resource_group,
                                target.topic,
                                name,
                                polling=False,
                            ).result()

    def _subscription_parameters(self, sub: dict[str, Any], source: object | None = None) -> Any:
        from azure.mgmt.eventgrid import models

        destination = self._destination(sub["destination"])
        delivery_identity = sub.get("delivery_identity")
        delivery_with_identity = None
        if delivery_identity:
            delivery_with_identity = models.DeliveryWithResourceIdentity(
                identity=self._event_subscription_identity(delivery_identity),
                destination=destination,
            )
            destination = None
        dead_letter = sub.get("dead_letter") or {}
        dead_letter_destination = None
        dead_letter_with_identity = None
        if dead_letter:
            sink = models.StorageBlobDeadLetterDestination(
                resource_id=str(dead_letter["storage_account_resource_id"]),
                blob_container_name=str(dead_letter["blob_container_name"]),
            )
            if dead_letter.get("identity"):
                dead_letter_with_identity = models.DeadLetterWithResourceIdentity(
                    identity=self._event_subscription_identity(dead_letter["identity"]),
                    dead_letter_destination=sink,
                )
            else:
                dead_letter_destination = sink
        retry = sub.get("retry", {}) or {}
        return models.EventSubscription(
            destination=destination,
            delivery_with_resource_identity=delivery_with_identity,
            filter=models.EventSubscriptionFilter(
                subject_begins_with=sub.get("subject_begins_with"),
                subject_ends_with=sub.get("subject_ends_with"),
                included_event_types=sub.get("included_event_types"),
                is_subject_case_sensitive=bool(sub.get("is_subject_case_sensitive", False)),
                enable_advanced_filtering_on_arrays=bool(sub.get("advanced_filtering_on_arrays", False)),
                advanced_filters=[self._advanced_filter(item) for item in sub.get("advanced_filters", [])],
            ),
            labels=[
                _MANAGED_SUBSCRIPTION_LABEL,
                *([f"astrolift-owner-{_owner_uuid(source).hex}"] if source is not None else []),
                *[str(value) for value in sub.get("labels", [])],
            ],
            event_delivery_schema=str(sub.get("event_delivery_schema", "CloudEventSchemaV1_0")),
            retry_policy=models.RetryPolicy(
                max_delivery_attempts=int(retry.get("max_delivery_attempts", 30)),
                event_time_to_live_in_minutes=int(retry.get("event_time_to_live_in_minutes", 1440)),
            ),
            dead_letter_destination=dead_letter_destination,
            dead_letter_with_resource_identity=dead_letter_with_identity,
        )

    def _destination(self, destination: dict[str, Any]) -> Any:
        from azure.mgmt.eventgrid import models

        kind = str(destination["type"])
        attributes = self._delivery_attributes(destination)
        if kind == "webhook":
            return models.WebHookEventSubscriptionDestination(
                endpoint_url=str(destination["endpoint_url"]),
                max_events_per_batch=int(destination.get("max_events_per_batch", 1)),
                preferred_batch_size_in_kilobytes=int(
                    destination.get("preferred_batch_size_in_kilobytes", 64),
                ),
                azure_active_directory_tenant_id=destination.get("aad_tenant_id"),
                azure_active_directory_application_id_or_uri=destination.get("aad_application_id_or_uri"),
                delivery_attribute_mappings=attributes,
                minimum_tls_version_allowed=str(destination.get("minimum_tls_version_allowed", "1.2")),
            )
        if kind == "storage_queue":
            return models.StorageQueueEventSubscriptionDestination(
                resource_id=str(destination["resource_id"]),
                queue_name=str(destination["queue_name"]),
                queue_message_time_to_live_in_seconds=destination.get("queue_message_time_to_live_in_seconds"),
            )
        if kind == "monitor_alert":
            return models.MonitorAlertEventSubscriptionDestination(
                severity=str(destination.get("severity", "Sev3")),
                description=destination.get("description"),
                action_groups=[str(value) for value in destination.get("action_groups", [])],
            )
        model = getattr(models, _DESTINATION_MODELS[kind])
        kwargs: dict[str, Any] = {
            "resource_id": str(destination["resource_id"]),
        }
        if kind in {"azure_function"}:
            kwargs.update(
                max_events_per_batch=int(destination.get("max_events_per_batch", 1)),
                preferred_batch_size_in_kilobytes=int(
                    destination.get("preferred_batch_size_in_kilobytes", 64),
                ),
            )
        if kind != "namespace_topic":
            kwargs["delivery_attribute_mappings"] = attributes
        return model(**kwargs)

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

    def _event_subscription_identity(self, identity: dict[str, Any]) -> Any:
        from azure.mgmt.eventgrid import models

        return models.EventSubscriptionIdentity(
            type=str(identity["type"]),
            user_assigned_identity=identity.get("user_assigned_identity_resource_id"),
        )

    def _provision_target(self, spec: ProvisionSpec) -> _Target:
        if spec.recorded_handle:
            return self._saved_target(spec.recorded_handle, spec)
        identity = _owner_uuid(spec)
        if "topic_name" in (spec.config or {}):
            name = spec.config["topic_name"]
            if not isinstance(name, str) or identity.hex not in name.casefold():
                raise _Ownership("ownership_refused", "new topic_name must retain the full canonical service UUID hex")
        else:
            prefix = self._config.topic_name_prefix
            _validate_resource_name(prefix, "topic_name_prefix", max_length=17)
            name = f"{prefix}-{identity.hex}"
        return self._target(name)

    def _topic_name(self, spec: ProvisionSpec) -> str:
        return self._provision_target(spec).topic

    def _target(self, name: str) -> _Target:
        _validate_resource_name(name, "topic name")
        subscription = _uuid(self._config.subscription_id)
        group = self._config.resource_group
        if not isinstance(group, str) or not re.fullmatch(r"[\w.()\-]{1,90}", group) or group.endswith("."):
            raise _Ownership("ownership_unknown", "provider resource group cannot be represented safely")
        target = _Target(str(subscription), group, name)
        if len(target.handle) > 512 or len(target.topic_id) > 512:
            raise _Ownership("ownership_unknown", "recorded target or binding exceeds storage limits")
        return target

    def _saved_target(self, handle: str, source: object) -> _Target:
        _owner_uuid(source)
        parts = handle.split("/")
        if len(parts) != 5 or parts[:2] != [KIND, "arm-v1"]:
            raise _Ownership("ownership_unknown", "historical Event Grid placement is not recorded unambiguously")
        target = self._target(parts[4])
        if handle != target.handle or parts[2] != target.subscription or parts[3] != target.resource_group:
            raise _Ownership("ownership_refused", "saved Event Grid coordinates differ from the current provider")
        return target

    def _parse_handle(self, handle: str) -> str:
        parts = handle.split("/")
        if len(parts) != 5 or parts[:2] != [KIND, "arm-v1"]:
            raise _Ownership("ownership_unknown", "historical Event Grid placement is not recorded unambiguously")
        target = self._target(parts[4])
        if target.handle != handle:
            raise _Ownership("ownership_refused", "saved Event Grid coordinates differ from the current provider")
        return target.topic

    def _subscription_name(self, identity: str, requested: str) -> str:
        return f"astrolift-{identity}-{hashlib.sha256(requested.encode()).hexdigest()[:16]}"

    def _rpc(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        deadline = _DEADLINE.get()
        remaining = (deadline - time.monotonic()) if deadline is not None else 0
        if remaining <= 0:
            raise _Ownership("ownership_unknown", "bounded Event Grid observation budget exhausted")
        return function(
            *args,
            connection_timeout=min(5, remaining),
            read_timeout=min(5, remaining),
            retry_total=0,
            redirect_max=0,
            **kwargs,
        )

    def _paged(self, function: Callable[..., Any], collection: str, *args: Any, **kwargs: Any) -> list[Any]:
        page_count = 0

        def check_next(response: Any) -> None:
            nonlocal page_count
            import json

            page_count += 1

            raw = response.http_response.body()
            if len(raw) > 2 * 1024 * 1024:
                raise _Ownership("ownership_unknown", "inventory response exceeds observation limit")
            payload = json.loads(raw)
            following = payload.get("nextLink")
            if following:
                if page_count >= 4:
                    raise _Ownership("ownership_unknown", "inventory exceeds four page observation budget")
                parsed = urlsplit(following)
                if (
                    parsed.scheme != "https"
                    or parsed.hostname != "management.azure.com"
                    or parsed.port not in {None, 443}
                    or parsed.username
                    or parsed.password
                    or parsed.fragment
                    or unquote(parsed.path).casefold() != collection.casefold()
                ):
                    raise _Ownership("ownership_unknown", "inventory continuation leaves the exact trusted collection")

        pager = self._rpc(function, *args, raw_response_hook=check_next, **kwargs)
        result: list[Any] = []
        if not hasattr(pager, "by_page"):
            raise _Ownership("ownership_unknown", "complete paged SDK inventory is unavailable")
        pages = pager.by_page()
        for page_number, page in enumerate(pages):
            if page_number >= 4:
                raise _Ownership("ownership_unknown", "inventory exceeds four page observation budget")
            for item in page:
                if len(result) >= 128:
                    raise _Ownership("ownership_unknown", "inventory exceeds 128 item observation budget")
                if _DEADLINE.get() is None or time.monotonic() >= (_DEADLINE.get() or 0):
                    raise _Ownership("ownership_unknown", "inventory observation budget exhausted")
                result.append(item)
        return result

    def _topic(self, topic_name: str) -> Any | None:
        try:
            return self._rpc(self._mgmt.topics.get, self._config.resource_group, topic_name)
        except ResourceNotFoundError:
            return None

    def _subscription(self, topic_name: str, child: str) -> Any | None:
        try:
            return self._rpc(self._mgmt.topic_event_subscriptions.get, self._config.resource_group, topic_name, child)
        except ResourceNotFoundError:
            return None

    def _subscriptions(self, topic_name: str) -> list[Any]:
        collection = self._target(topic_name).topic_id + "/eventSubscriptions"
        return self._paged(
            self._mgmt.topic_event_subscriptions.list, collection, self._config.resource_group, topic_name
        )

    def _missing_parent_children(self, target: _Target) -> list[Any]:
        try:
            return self._subscriptions(target.topic)
        except ResourceNotFoundError:
            if self._topic(target.topic) is not None:
                raise _Ownership("ownership_unknown", "parent changed during absent collection observation") from None
            return []

    def _assert_owned(self, topic: Any, source: object, operation: AzureOperation, topic_name: str) -> None:
        _owner_uuid(source)
        expected = self._target(topic_name).topic_id
        if (
            not _same_arm(_field(topic, "id"), expected)
            or str(_field(topic, "name", default="")).casefold() != topic_name.casefold()
        ):
            raise _Ownership("ownership_unknown", "actual topic ARM identity does not match recorded coordinates")
        tags = dict(_field(topic, "tags", default={}) or {})
        wanted = str(_owner_uuid(source))
        source_observed = False
        for key, value in tags.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in {"astroliftmanagedserviceid", "astroliftiomanagedserviceid", "xastroliftmanagedserviceid"}:
                source_observed = True
                if value != wanted:
                    raise _Ownership("ownership_refused", "conflicting source identity tag spelling")
            if (
                normalized in {"astroliftmanagedby", "astroliftiomanagedby", "xastroliftmanagedby"}
                and value != "platform"
            ):
                raise _Ownership("ownership_refused", "conflicting platform identity tag spelling")
        if not source_observed:
            raise _Ownership("ownership_refused", "current topic source identity is not observed")
        try:
            verify_azure_ownership(
                dict(_field(topic, "tags", default={}) or {}),
                owner_of(source),
                operation=operation,
                resource="recorded Event Grid topic",
            )
        except AzureOwnershipError as exc:
            raise _Ownership("ownership_refused", "topic platform/source ownership does not match") from exc

    def _assert_child_owned(self, item: Any, target: _Target, source: object) -> None:
        name = str(_field(item, "name", default=""))
        identity = _owner_uuid(source).hex
        if not re.fullmatch(r"astrolift-" + identity + r"-[a-f0-9]{16}", name):
            raise _Ownership("ownership_refused", "subscription name does not carry the current source UUID")
        expected = target.topic_id + "/eventSubscriptions/" + name
        if not _same_arm(_field(item, "id"), expected) or not _same_arm(_field(item, "topic"), target.topic_id):
            raise _Ownership("ownership_unknown", "subscription ARM parent/identity is not observed coherently")
        labels = _field(item, "labels", default=None)
        platform = [_MANAGED_SUBSCRIPTION_LABEL, f"astrolift-owner-{identity}"]
        if (
            not isinstance(labels, list)
            or any(labels.count(value) != 1 for value in platform)
            or any(not isinstance(value, str) for value in labels)
            or any(
                re.sub(r"[^a-z0-9]", "", value.lower()).startswith(("astrolift", "xastrolift"))
                and value not in platform
                for value in labels
            )
        ):
            raise _Ownership("ownership_refused", "subscription platform/source labels do not match")

    def _inventory(self, target: _Target, source: object) -> list[Any]:
        rows = self._subscriptions(target.topic)
        names: set[str] = set()
        for row in rows:
            self._assert_child_owned(row, target, source)
            name = str(_field(row, "name")).casefold()
            if name in names:
                raise _Ownership("ownership_unknown", "duplicate subscription inventory identity")
            names.add(name)
            current = self._subscription(target.topic, str(_field(row, "name")))
            if current is None:
                raise _Ownership("ownership_unknown", "subscription inventory changed during observation")
            self._assert_child_owned(current, target, source)
        return rows

    def _assert_unlocked(self, target: _Target, *, exists: bool) -> None:
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
        if exists:
            calls.append(
                (
                    self._locks.management_locks.list_at_resource_level,
                    target.topic_id,
                    {
                        "resource_group_name": target.resource_group,
                        "resource_provider_namespace": "Microsoft.EventGrid",
                        "parent_resource_path": "",
                        "resource_type": "topics",
                        "resource_name": target.topic,
                    },
                )
            )
        for function, scope, options in calls:
            rows = self._paged(function, scope + "/providers/Microsoft.Authorization/locks", **options)
            for row in rows:
                identity = str(_field(row, "id", default=""))
                if (
                    not identity.startswith("/")
                    or "/providers/microsoft.authorization/locks/" not in identity.casefold()
                ):
                    raise _Ownership("ownership_unknown", "lock scope is not observed unambiguously")
                lock_scope, _, lock_name = identity.casefold().partition("/providers/microsoft.authorization/locks/")
                if (
                    not lock_name
                    or "/" in lock_name
                    or not (lock_scope == scope.casefold() or lock_scope.startswith(scope.casefold() + "/"))
                ):
                    raise _Ownership(
                        "ownership_unknown", "lock inventory returned an unrelated or malformed ARM identity"
                    )
                if (
                    lock_scope in {value.casefold() for value in scopes}
                    or lock_scope == target.topic_id.casefold()
                    or lock_scope.startswith(target.topic_id.casefold() + "/")
                ):
                    raise _Ownership(
                        "resource_lock_present",
                        "an inherited or target resource lock requires separate operator resolution",
                    )

    def _required_topic(self, target: _Target, source: object, operation: AzureOperation) -> Any:
        topic = self._topic(target.topic)
        if topic is None:
            raise _Ownership("ownership_unknown", "recorded Event Grid topic is missing")
        self._assert_owned(topic, source, operation, target.topic)
        return topic

    def _immutable_settings(self, topic: Any, target: _Target, cfg: dict[str, Any]) -> None:
        if "topic_name" in cfg and cfg["topic_name"] != target.topic:
            raise AzureEventGridError("topic_name change requires reprovision")
        if "input_schema" in cfg and _enum(_field(topic, "input_schema")) != cfg["input_schema"]:
            raise AzureEventGridError("input_schema change requires reprovision")

    def _update_topic(
        self, target: _Target, cfg: dict[str, Any], source: object, *, apply_defaults: bool = False
    ) -> None:
        desired = self._topic_update_parameters(cfg, apply_defaults=apply_defaults)
        if not desired.serialize():
            return
        current = self._topic(target.topic)
        if current is None:
            raise _Ownership("ownership_unknown", "topic disappeared before update")
        self._assert_owned(current, source, AzureOperation.UPDATE, target.topic)
        if _contains(current.serialize(), desired.serialize()):
            return
        self._rpc(self._mgmt.topics.begin_update, target.resource_group, target.topic, desired, polling=False).result()

    def _observed(self, target: _Target, cfg: dict[str, Any], source: object, *, apply_defaults: bool = False) -> bool:
        topic = self._required_topic(target, source, AzureOperation.INSPECT)
        if _enum(_field(topic, "provisioning_state")) != "Succeeded":
            return False
        if not _contains(
            topic.serialize(), self._topic_update_parameters(cfg, apply_defaults=apply_defaults).serialize()
        ):
            return False
        children = self._inventory(target, source)
        by_name = {str(_field(row, "name")): row for row in children}
        desired = self._desired_children(cfg, source)
        for name, sub in desired.items():
            current = by_name.get(name)
            if current is None or _enum(_field(current, "provisioning_state")) != "Succeeded":
                return False
            if not _contains(current.serialize(), self._subscription_parameters(sub, source).serialize()):
                return False
        if cfg.get("prune_subscriptions") and set(by_name) != set(desired):
            return False
        return all(_enum(_field(row, "provisioning_state")) == "Succeeded" for row in children)


@dataclass(frozen=True)
class _Target:
    subscription: str
    resource_group: str
    topic: str

    @property
    def handle(self) -> str:
        return f"{KIND}/arm-v1/{self.subscription}/{self.resource_group}/{self.topic}"

    @property
    def topic_id(self) -> str:
        return (
            f"/subscriptions/{self.subscription}/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.EventGrid/topics/{self.topic}"
        )


class _Ownership(AzureEventGridError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _uuid(value: str) -> UUID:
    try:
        parsed = UUID(value)
        if not parsed.int or str(parsed) != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError) as exc:
        raise _Ownership("ownership_unknown", "canonical nonzero immutable UUID is required") from exc


def _owner_uuid(source: object) -> UUID:
    return _uuid(getattr(source, "managed_service_id", ""))


def _same_arm(value: Any, expected: str) -> bool:
    return isinstance(value, str) and value.startswith("/") and value.casefold() == expected.casefold()


def _enum(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _contains(actual: dict[str, Any], desired: dict[str, Any]) -> bool:
    for key, value in desired.items():
        if key == "identity" and value == {"type": "None"} and actual.get(key) in (None, {"type": "None"}):
            continue
        if key not in actual:
            return False
        if isinstance(value, dict):
            if not isinstance(actual[key], dict) or not _contains(actual[key], value):
                return False
        elif actual[key] != value:
            return False
    return True


def _error_code(exc: Exception) -> str:
    if isinstance(exc, _Ownership):
        return exc.code
    if isinstance(exc, AzureEventGridError):
        return "invalid_event_grid_config"
    return "ownership_unknown"


def _error_message(exc: Exception) -> str:
    if isinstance(exc, _Ownership):
        return str(exc)
    if isinstance(exc, AzureEventGridError):
        return "Event Grid configuration cannot be applied in place"
    return "Event Grid observation failed; current ownership or completion is unknown"


def _generated_name(prefix: str, hint: str, seed: str, max_length: int) -> str:
    safe_prefix = _slug(prefix) or "astrolift"
    safe_hint = _slug(hint) or "resource"
    digest = hashlib.sha256(seed.encode()).hexdigest()[:10]
    suffix = f"-{digest}"
    available = max_length - len(safe_prefix) - len(suffix) - 1
    name = f"{safe_prefix}-{safe_hint[: max(1, available)].strip('-')}{suffix}"
    _validate_resource_name(name, "generated Event Grid resource", max_length=max_length)
    return name


def _slug(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", value.lower())).strip("-")


def _validate_resource_name(value: str, field: str, *, max_length: int = 50) -> None:
    if not 3 <= len(value) <= max_length:
        raise AzureEventGridError(f"Event Grid {field} must be between 3 and {max_length} characters")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9]", value):
        raise AzureEventGridError(f"Event Grid {field} must contain only alphanumerics and interior hyphens")


def _validate_advanced_filter(item: Any) -> str:
    if not isinstance(item, dict):
        return "Event Grid advanced filter must be an object"
    operator = str(item.get("operator", ""))
    if operator not in _ADVANCED_FILTER_MODELS:
        return f"unsupported Event Grid advanced filter operator {operator!r}"
    allowed = {"key", "operator"}
    if operator in _SINGLE_VALUE_FILTERS:
        allowed.add("value")
        if "value" not in item:
            return f"Event Grid advanced filter {operator} requires value"
    elif operator not in _NO_VALUE_FILTERS:
        allowed.add("values")
        if not isinstance(item.get("values"), list) or not item["values"]:
            return f"Event Grid advanced filter {operator} requires non-empty values"
    if not item.get("key"):
        return "Event Grid advanced filter requires key"
    unknown = sorted(set(item) - allowed)
    if unknown:
        return f"unsupported Event Grid advanced filter fields: {', '.join(unknown)}"
    return ""


def _validate_delivery_attribute(item: Any) -> str:
    if not isinstance(item, dict) or not item.get("name"):
        return "Event Grid delivery attribute requires a name"
    if str(item["name"]).lower() in _SENSITIVE_DELIVERY_ATTRIBUTE_NAMES:
        return "sensitive Event Grid delivery attributes must use an external secret-aware integration"
    mapping_type = str(item.get("type", ""))
    if mapping_type == "static":
        if "value" not in item:
            return "static Event Grid delivery attribute requires value"
        if item.get("is_secret"):
            return "secret delivery attributes must not be stored in managed-service config"
        unknown = set(item) - {"name", "type", "value", "is_secret"}
    elif mapping_type == "dynamic":
        if not item.get("source_field"):
            return "dynamic Event Grid delivery attribute requires source_field"
        unknown = set(item) - {"name", "type", "source_field"}
    else:
        return "Event Grid delivery attribute type must be static or dynamic"
    if unknown:
        return f"unsupported Event Grid delivery attribute fields: {', '.join(sorted(unknown))}"
    return ""


def _integer_in_range(value: Any, minimum: int, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def _is_arm_id(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("/subscriptions/") and "/providers/" in value


def _field(value: Any, name: str, *, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _not_found(exc: Exception) -> bool:
    return type(exc).__name__ in {"ResourceNotFoundError", "NotFound"} or getattr(exc, "status_code", None) == 404
