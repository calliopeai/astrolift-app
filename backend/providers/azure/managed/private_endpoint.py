"""Azure Private Endpoint and Private DNS zone-group lifecycle."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from _sdk import UnsupportedOperationError
from _sdk._telemetry import driver_op
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
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
    ValueRef,
)
from azure.managed.tags import arm_tags_for

KIND = "private_endpoint"
VARIANT = "private_link"
_MANAGED_BY = "astrolift-managed-by"
_SERVICE_ID = "astrolift-managed-service-id"
_PROTECTION = "astrolift-deletion-protection"
_ZONE_GROUP = "astrolift"
_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,78}[a-z0-9])?$")
_GROUP_ID = re.compile(r"^[A-Za-z0-9](?:[-A-Za-z0-9_.]{0,78}[A-Za-z0-9])?$")
_RESOURCE_ID = re.compile(
    r"^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/[^/]+/[^/]+/[^/]+(?:/[^/]+/[^/]+)*$",
)
_RESOURCE_SCOPE_ID = re.compile(
    r"^/subscriptions/[^/]+/resourceGroups/[^/]+"
    r"(?:/providers/[^/]+(?:/[^/]+(?:/[^/]+(?:/[^/]+/[^/]+)*)?)?)?$",
)
_SUBNET_ID = re.compile(
    r"^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.Network/"
    r"virtualNetworks/[^/]+/subnets/[^/]+$",
)
_DNS_ZONE_ID = re.compile(
    r"^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.Network/"
    r"privateDnsZones/[^/]+$",
)
_DNS_ZONE_SCOPE_ID = re.compile(
    r"^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.Network/"
    r"privateDnsZones(?:/[^/]+)?$",
)
_CONFIG_FIELDS = {
    "private_link_service_id",
    "group_ids",
    "subnet_id",
    "manual_approval",
    "request_message",
    "private_dns_zone_ids",
    "deletion_protection",
}


@dataclass(frozen=True)
class AzurePrivateEndpointConfig:
    subscription_id: str
    resource_group: str
    location: str = "eastus"
    name_prefix: str = "astrolift-pe"
    default_subnet_id: str = ""
    allowed_subnet_ids: tuple[str, ...] = ()
    allowed_service_id_prefixes: tuple[str, ...] = ()
    allowed_private_dns_zone_id_prefixes: tuple[str, ...] = ()
    allow_manual_approval: bool = False
    max_group_ids: int = 8
    max_private_dns_zones: int = 8
    deletion_protection_default: bool = True
    network_client: Any = None


class AzurePrivateEndpointDriver(ManagedServiceDriver):
    """Own a consumer Private Endpoint and its optional DNS zone group."""

    def __init__(self, *, config: AzurePrivateEndpointConfig, client: Any | None = None) -> None:
        self._config = config
        if client is None:
            client = config.network_client
        if client is None:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.network import NetworkManagementClient

            client = NetworkManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
            )
        self._network = client

    @driver_op(
        cloud="azure",
        driver="private_link",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        name = self._name(spec)
        handle = _handle(name)
        try:
            cfg = self._normalize(spec.config)
            existing = self._get(name)
            if existing is not None:
                self._assert_owned(existing, spec.managed_service_id)
                self._assert_immutable(existing, cfg)
            resource = self._put(
                name,
                self._parameters(
                    cfg,
                    tags=arm_tags_for(
                        spec,
                        platform_tags={
                            "deletion-protection": str(cfg["deletion_protection"]).lower(),
                        },
                    ),
                ),
            )
            self._reconcile_dns(name, cfg["private_dns_zone_ids"])
        except (TypeError, ValueError) as exc:
            return ProvisionResult(False, handle, str(exc), ["invalid_azure_private_endpoint_config"])
        except Exception as exc:
            return ProvisionResult(False, handle, f"provision Azure Private Endpoint: {exc}", [str(exc)])
        state, ready = self._state(resource)
        return ProvisionResult(True, handle, f"Azure Private Endpoint {name} is {state}", ready=ready)

    @driver_op(cloud="azure", driver="private_link")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        try:
            name = _parse_handle(spec.handle)
            cfg = self._normalize(spec.config)
            existing = self._get(name)
            if existing is None:
                return UpdateResult(False, spec.handle, "Azure Private Endpoint not found", ["not_found"])
            self._assert_owned(existing)
            self._assert_immutable(existing, cfg)
            tags = self._tags(existing)
            tags[_PROTECTION] = str(cfg["deletion_protection"]).lower()
            resource = self._put(name, self._parameters(cfg, tags=tags))
            self._reconcile_dns(name, cfg["private_dns_zone_ids"])
        except (TypeError, ValueError) as exc:
            return UpdateResult(
                False,
                spec.handle,
                str(exc),
                ["invalid_azure_private_endpoint_config"],
            )
        except Exception as exc:
            return UpdateResult(False, spec.handle, f"update Azure Private Endpoint: {exc}", [str(exc)])
        state, _ = self._state(resource)
        return UpdateResult(True, spec.handle, f"Azure Private Endpoint {name} reconciled ({state})")

    @driver_op(
        cloud="azure",
        driver="private_link",
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
        del delete_data
        try:
            name = _parse_handle(spec.handle)
            existing = self._get(name)
            if existing is None:
                return DeprovisionResult(True, spec.handle, f"Azure Private Endpoint {name} already absent")
            self._assert_owned(existing)
            protected = self._tags(existing).get(_PROTECTION, "true").lower() == "true"
            if protected and not force_destroy:
                return DeprovisionResult(
                    False,
                    spec.handle,
                    f"Azure Private Endpoint {name} has deletion protection enabled",
                    ["deletion_protection_enabled"],
                    retryable=False,
                )
            self._network.private_endpoints.begin_delete(self._config.resource_group, name).result()
        except ValueError as exc:
            return DeprovisionResult(False, spec.handle, str(exc), ["invalid_handle"], retryable=False)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, f"Azure Private Endpoint {name} already absent")
            return DeprovisionResult(False, spec.handle, f"delete Azure Private Endpoint: {exc}", [str(exc)])
        return DeprovisionResult(True, spec.handle, f"Azure Private Endpoint {name} deleted")

    @driver_op(cloud="azure", driver="private_link")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        try:
            name = _parse_handle(handle.handle)
            resource = self._get(name)
            if resource is None:
                return ServiceStatus(handle.handle, "deprovisioned", f"Azure Private Endpoint {name} is absent")
            self._assert_owned(resource)
            state, ready = self._state(resource)
        except Exception as exc:
            return ServiceStatus(handle.handle, "error", f"describe Azure Private Endpoint: {exc}")
        if ready:
            return ServiceStatus(handle.handle, "available", f"Azure Private Endpoint {name} is approved")
        if state.lower() in {"failed", "rejected", "disconnected"}:
            return ServiceStatus(handle.handle, "error", f"Azure Private Endpoint {name} is {state}")
        return ServiceStatus(handle.handle, "provisioning", f"Azure Private Endpoint {name} is {state}")

    @driver_op(cloud="azure", driver="private_link")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        del config
        name = _parse_handle(handle.handle)
        resource = self._get(name)
        if resource is None:
            raise ValueError(f"Azure Private Endpoint {name} does not exist")
        self._assert_owned(resource)
        properties = _properties(resource)
        resource_id = str(_value(resource, "id") or self._resource_id(name))
        dns = _list(properties, "customDnsConfigs", "custom_dns_configs")
        dns_names = sorted({str(_value(row, "fqdn")) for row in dns if _value(row, "fqdn")})
        ips = sorted(
            {str(ip) for row in dns for ip in (_value(row, "ipAddresses", "ip_addresses") or []) if ip},
        )
        nic_ids = sorted(
            {
                str(_value(row, "id"))
                for row in _list(properties, "networkInterfaces", "network_interfaces")
                if _value(row, "id")
            },
        )
        rows, _manual = _connection_rows(properties)
        target = str(
            _value(_properties(rows[0]) if rows else {}, "privateLinkServiceId", "private_link_service_id") or "",
        )
        return Binding(
            env_vars={
                "PRIVATE_ENDPOINT_ID": ValueRef(literal=resource_id),
                "PRIVATE_ENDPOINT_NAME": ValueRef(literal=name),
                "PRIVATE_ENDPOINT_DNS": ValueRef(literal=dns_names[0] if dns_names else ""),
                "PRIVATE_ENDPOINT_IPS": ValueRef(literal=_json_array(ips)),
                "PRIVATE_ENDPOINT_TYPE": ValueRef(literal="private_link"),
                "PRIVATE_ENDPOINT_SERVICE_NAME": ValueRef(literal=target),
                "PRIVATE_ENDPOINT_DNS_NAME": ValueRef(literal=dns_names[0] if dns_names else ""),
                "PRIVATE_ENDPOINT_DNS_NAMES": ValueRef(literal=_json_array(dns_names)),
                "PRIVATE_ENDPOINT_NETWORK_INTERFACE_IDS": ValueRef(literal=_json_array(nic_ids)),
                "AZURE_SUBSCRIPTION_ID": ValueRef(literal=self._config.subscription_id),
                "AZURE_RESOURCE_GROUP": ValueRef(literal=self._config.resource_group),
            },
            notes=(
                "Traffic uses the private IP and Private DNS path. No Azure control-plane key is injected; "
                "application authentication remains on the target service binding."
            ),
        )

    @driver_op(cloud="azure", driver="private_link")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        del handle
        raise UnsupportedOperationError("Azure Private Endpoints are declarative network resources without snapshots")

    @driver_op(cloud="azure", driver="private_link")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        del snapshot, target
        raise UnsupportedOperationError("reconcile the Private Endpoint declaration instead of restoring a snapshot")

    @driver_op(cloud="azure", driver="private_link", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        required = ["private_link_service_id", "group_ids"]
        if not self._config.default_subnet_id:
            required.append("subnet_id")
        return {
            "type": "object",
            "additionalProperties": False,
            "required": required,
            "properties": {
                "private_link_service_id": {
                    "type": "string",
                    "pattern": _RESOURCE_ID.pattern,
                    "allOf": [{"pattern": _allowlist_pattern(self._config.allowed_service_id_prefixes)}],
                    "x-astrolift-allowed-resource-prefixes": list(self._config.allowed_service_id_prefixes),
                },
                "group_ids": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": self._config.max_group_ids,
                    "uniqueItems": True,
                    "items": {"type": "string", "pattern": _GROUP_ID.pattern},
                },
                "subnet_id": {
                    "type": "string",
                    "enum": list(self._config.allowed_subnet_ids),
                    **({"default": self._config.default_subnet_id} if self._config.default_subnet_id else {}),
                },
                "manual_approval": {
                    "type": "boolean",
                    "default": False,
                    **({} if self._config.allow_manual_approval else {"const": False}),
                },
                "request_message": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 140,
                    "pattern": r"^[^\u0000-\u001f]+$",
                },
                "private_dns_zone_ids": {
                    "type": "array",
                    "maxItems": (
                        self._config.max_private_dns_zones if self._config.allowed_private_dns_zone_id_prefixes else 0
                    ),
                    "uniqueItems": True,
                    "items": {
                        "type": "string",
                        "pattern": _DNS_ZONE_ID.pattern,
                        "allOf": [
                            {
                                "pattern": _allowlist_pattern(
                                    self._config.allowed_private_dns_zone_id_prefixes,
                                ),
                            },
                        ],
                    },
                    "x-astrolift-allowed-resource-prefixes": list(
                        self._config.allowed_private_dns_zone_id_prefixes,
                    ),
                },
                "deletion_protection": {
                    "type": "boolean",
                    "default": self._config.deletion_protection_default,
                },
            },
        }

    @driver_op(cloud="azure", driver="private_link", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "PRIVATE_ENDPOINT_ID": "Azure resource id",
                "PRIVATE_ENDPOINT_NAME": "Private Endpoint name",
                "PRIVATE_ENDPOINT_DNS": "Portable primary endpoint DNS name",
                "PRIVATE_ENDPOINT_IPS": "Portable JSON array of endpoint private IP addresses",
                "PRIVATE_ENDPOINT_TYPE": "Always private_link for Azure Private Endpoints",
                "PRIVATE_ENDPOINT_SERVICE_NAME": "Target Private Link resource id",
                "PRIVATE_ENDPOINT_DNS_NAME": "First endpoint DNS name",
                "PRIVATE_ENDPOINT_DNS_NAMES": "JSON array of all endpoint DNS names",
                "PRIVATE_ENDPOINT_NETWORK_INTERFACE_IDS": "JSON array of endpoint NIC resource ids",
                "AZURE_SUBSCRIPTION_ID": "Azure subscription hosting the endpoint",
                "AZURE_RESOURCE_GROUP": "Azure resource group hosting the endpoint",
            },
        )

    @driver_op(cloud="azure", driver="private_link", heartbeat=False)
    def editable_fields(self) -> list[str]:
        return ["deletion_protection", "private_dns_zone_ids"]

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        self._validate_install_policy()
        if not isinstance(raw, dict):
            raise ValueError("Azure Private Endpoint config must be an object")
        unknown = sorted(set(raw) - _CONFIG_FIELDS)
        if unknown:
            raise ValueError(f"Azure Private Endpoint config contains unsupported fields: {unknown}")
        service_id = str(raw.get("private_link_service_id") or "")
        if not _RESOURCE_ID.fullmatch(service_id):
            raise ValueError("private_link_service_id must be a complete Azure resource id")
        if not _allowed(service_id, self._config.allowed_service_id_prefixes):
            raise ValueError("private_link_service_id is outside the service resource-id allowlist")
        group_ids = raw.get("group_ids")
        if (
            not isinstance(group_ids, list)
            or not group_ids
            or len(group_ids) > self._config.max_group_ids
            or any(not isinstance(value, str) or not _GROUP_ID.fullmatch(value) for value in group_ids)
            or len(set(group_ids)) != len(group_ids)
        ):
            raise ValueError("group_ids must be a unique bounded array of Azure Private Link group ids")
        subnet_id = str(raw.get("subnet_id") or self._config.default_subnet_id)
        if not _SUBNET_ID.fullmatch(subnet_id):
            raise ValueError("subnet_id must be a complete Azure virtual-network subnet id")
        if subnet_id not in self._config.allowed_subnet_ids:
            raise ValueError("subnet_id is outside the install allowlist")
        manual = raw.get("manual_approval", False)
        if not isinstance(manual, bool):
            raise ValueError("manual_approval must be a boolean")
        if manual and not self._config.allow_manual_approval:
            raise ValueError("manual Private Link approval is blocked by install policy")
        message = str(raw.get("request_message") or "Astrolift managed private endpoint")
        if not message or len(message) > 140 or any(ord(char) < 32 for char in message):
            raise ValueError("request_message must be printable and at most 140 characters")
        zones = raw.get("private_dns_zone_ids") or []
        if (
            not isinstance(zones, list)
            or len(zones) > self._config.max_private_dns_zones
            or len(set(zones)) != len(zones)
            or any(not isinstance(zone, str) or not _DNS_ZONE_ID.fullmatch(zone) for zone in zones)
        ):
            raise ValueError("private_dns_zone_ids must be unique complete Azure Private DNS zone ids")
        for zone in zones:
            if not _allowed(zone, self._config.allowed_private_dns_zone_id_prefixes):
                raise ValueError("private DNS zone id is outside the install allowlist")
        protected = raw.get("deletion_protection", self._config.deletion_protection_default)
        if not isinstance(protected, bool):
            raise ValueError("deletion_protection must be a boolean")
        return {
            "private_link_service_id": service_id,
            "group_ids": list(group_ids),
            "subnet_id": subnet_id,
            "manual_approval": manual,
            "request_message": message,
            "private_dns_zone_ids": list(zones),
            "deletion_protection": protected,
        }

    def _validate_install_policy(self) -> None:
        if not self._config.subscription_id or not self._config.resource_group or not self._config.location:
            raise ValueError("Azure Private Endpoint requires subscription, resource group, and location")
        if not _NAME.fullmatch(self._config.name_prefix):
            raise ValueError("Azure Private Endpoint name_prefix is invalid")
        if not self._config.allowed_service_id_prefixes or any(
            not _RESOURCE_SCOPE_ID.fullmatch(value.rstrip("/")) for value in self._config.allowed_service_id_prefixes
        ):
            raise ValueError("Azure Private Endpoint requires an explicit service resource-id allowlist")
        if not self._config.allowed_subnet_ids or any(
            not _SUBNET_ID.fullmatch(value) for value in self._config.allowed_subnet_ids
        ):
            raise ValueError("Azure Private Endpoint requires valid explicitly allowed subnet ids")
        if any(
            not _DNS_ZONE_SCOPE_ID.fullmatch(value.rstrip("/"))
            for value in self._config.allowed_private_dns_zone_id_prefixes
        ):
            raise ValueError("Azure Private Endpoint private DNS zone allowlist is invalid")
        if not 1 <= self._config.max_group_ids <= 64 or not 0 <= self._config.max_private_dns_zones <= 64:
            raise ValueError("Azure Private Endpoint install limits are invalid")

    def _parameters(self, cfg: dict[str, Any], *, tags: dict[str, str]) -> dict[str, Any]:
        connection = {
            "name": "astrolift",
            "properties": {
                "privateLinkServiceId": cfg["private_link_service_id"],
                "groupIds": cfg["group_ids"],
                "requestMessage": cfg["request_message"],
            },
        }
        key = "manualPrivateLinkServiceConnections" if cfg["manual_approval"] else "privateLinkServiceConnections"
        return {
            "location": self._config.location,
            "tags": tags,
            "properties": {
                "subnet": {"id": cfg["subnet_id"]},
                key: [connection],
            },
        }

    def _assert_immutable(self, existing: Any, cfg: dict[str, Any]) -> None:
        properties = _properties(existing)
        rows, manual = _connection_rows(properties)
        row_properties = _properties(rows[0] if rows else {})
        actual_service = str(_value(row_properties, "privateLinkServiceId", "private_link_service_id") or "")
        actual_groups = list(_value(row_properties, "groupIds", "group_ids") or [])
        actual_subnet = str(_value(_value(properties, "subnet") or {}, "id") or "")
        if (
            actual_service.casefold() != cfg["private_link_service_id"].casefold()
            or actual_subnet.casefold() != cfg["subnet_id"].casefold()
            or actual_groups != cfg["group_ids"]
            or manual != cfg["manual_approval"]
        ):
            raise ValueError("Azure Private Endpoint target, groups, subnet, and approval mode are immutable")

    def _assert_owned(self, resource: Any, managed_service_id: str = "") -> None:
        tags = self._tags(resource)
        if tags.get(_MANAGED_BY) != "platform":
            raise ValueError("refusing to operate an Azure Private Endpoint not owned by Astrolift")
        if managed_service_id and tags.get(_SERVICE_ID) != managed_service_id:
            raise ValueError("Azure Private Endpoint name collides with another Astrolift managed service")

    def _get(self, name: str) -> Any | None:
        try:
            return self._network.private_endpoints.get(self._config.resource_group, name)
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _put(self, name: str, parameters: dict[str, Any]) -> Any:
        return self._network.private_endpoints.begin_create_or_update(
            self._config.resource_group,
            name,
            parameters,
        ).result()

    def _reconcile_dns(self, name: str, zone_ids: list[str]) -> None:
        if zone_ids:
            self._network.private_dns_zone_groups.begin_create_or_update(
                self._config.resource_group,
                name,
                _ZONE_GROUP,
                {
                    "properties": {
                        "privateDnsZoneConfigs": [
                            {
                                "name": f"zone-{index + 1}",
                                "properties": {"privateDnsZoneId": zone_id},
                            }
                            for index, zone_id in enumerate(zone_ids)
                        ],
                    },
                },
            ).result()
            return
        if not self._dns_group_exists(name):
            return
        try:
            self._network.private_dns_zone_groups.begin_delete(
                self._config.resource_group,
                name,
                _ZONE_GROUP,
            ).result()
        except Exception as exc:
            if not _not_found(exc):
                raise

    def _name(self, spec: ProvisionSpec) -> str:
        readable = re.sub(
            r"-+",
            "-",
            re.sub(
                r"[^a-z0-9-]+",
                "-",
                "-".join(
                    (
                        self._config.name_prefix,
                        spec.organization_slug,
                        spec.app_slug,
                        spec.environment_name,
                        spec.service_handle_hint or "private",
                    ),
                ).lower(),
            ),
        ).strip("-")
        digest = hashlib.sha256(
            f"{spec.organization_id}:{spec.app_id}:{spec.environment_id}:{spec.managed_service_id}".encode(),
        ).hexdigest()[:10]
        return f"{readable[:69].rstrip('-')}-{digest}"

    def _tags(self, resource: Any) -> dict[str, str]:
        value = _value(resource, "tags") or {}
        if not isinstance(value, dict):
            return {}
        return {str(key): str(item) for key, item in value.items()}

    def _resource_id(self, name: str) -> str:
        return (
            f"/subscriptions/{self._config.subscription_id}/resourceGroups/{self._config.resource_group}/"
            f"providers/Microsoft.Network/privateEndpoints/{name}"
        )

    @staticmethod
    def _state(resource: Any) -> tuple[str, bool]:
        properties = _properties(resource)
        provisioning = str(_value(properties, "provisioningState", "provisioning_state") or "Unknown")
        connections = [
            *_list(properties, "privateLinkServiceConnections", "private_link_service_connections"),
            *_list(properties, "manualPrivateLinkServiceConnections", "manual_private_link_service_connections"),
        ]
        states = [
            str(
                _value(
                    _value(
                        _properties(row),
                        "privateLinkServiceConnectionState",
                        "private_link_service_connection_state",
                    )
                    or {},
                    "status",
                )
                or "Pending",
            )
            for row in connections
        ]
        connection = states[0] if states else "Pending"
        ready = provisioning.casefold() == "succeeded" and connection.casefold() == "approved"
        return ("Approved" if ready else connection if provisioning.casefold() == "succeeded" else provisioning), ready

    def _dns_group_exists(self, name: str) -> bool:
        try:
            self._network.private_dns_zone_groups.get(
                self._config.resource_group,
                name,
                _ZONE_GROUP,
            )
        except Exception as exc:
            if _not_found(exc):
                return False
            raise
        return True


def _handle(name: str) -> str:
    return f"{KIND}/{name}"


def _parse_handle(handle: str) -> str:
    prefix = f"{KIND}/"
    if not handle.startswith(prefix):
        raise ValueError("invalid Azure Private Endpoint handle")
    name = handle[len(prefix) :]
    if not _NAME.fullmatch(name):
        raise ValueError("invalid Azure Private Endpoint handle")
    return name


def _connection_rows(properties: Any) -> tuple[list[Any], bool]:
    """Return the endpoint's service connections and whether they are manual."""
    manual = _list(
        properties,
        "manualPrivateLinkServiceConnections",
        "manual_private_link_service_connections",
    )
    if manual:
        return manual, True
    automatic = _list(
        properties,
        "privateLinkServiceConnections",
        "private_link_service_connections",
    )
    return automatic, False


def _json_array(values: list[str]) -> str:
    return json.dumps(values, separators=(",", ":"))


def _allowed(resource_id: str, prefixes: tuple[str, ...]) -> bool:
    normalized = resource_id.rstrip("/")
    return any(
        normalized == prefix.rstrip("/") or normalized.startswith(prefix.rstrip("/") + "/") for prefix in prefixes
    )


def _allowlist_pattern(prefixes: tuple[str, ...]) -> str:
    if not prefixes:
        return r"a^"
    alternatives = "|".join(re.escape(prefix.rstrip("/")) for prefix in prefixes)
    return rf"^(?:{alternatives})(?:/.*)?$"


def _properties(value: Any) -> Any:
    return _value(value, "properties") or value


def _list(value: Any, *names: str) -> list[Any]:
    result = _value(value, *names) or []
    return list(result) if isinstance(result, (list, tuple)) else []


def _value(value: Any, *names: str) -> Any:
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _not_found(exc: Exception) -> bool:
    return type(exc).__name__ == "ResourceNotFoundError" or "not found" in str(exc).casefold()
