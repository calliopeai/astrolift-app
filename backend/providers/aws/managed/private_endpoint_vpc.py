"""AWS PrivateLink and gateway VPC endpoint lifecycle driver."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
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
from aws.managed._base import ManagedServiceError, handle_for, parse_handle, tags_for

KIND = "private_endpoint"
_ENDPOINT_TYPES = {
    "interface": "Interface",
    "gateway": "Gateway",
    "gatewayloadbalancer": "GatewayLoadBalancer",
    "gateway_load_balancer": "GatewayLoadBalancer",
    "resource": "Resource",
    "servicenetwork": "ServiceNetwork",
    "service_network": "ServiceNetwork",
}
_TERMINAL_STATES = {"deleted", "failed", "rejected"}
_RESERVED_CREATE = {
    "ClientToken",
    "PolicyDocument",
    "PrivateDnsEnabled",
    "ResourceConfigurationArn",
    "RouteTableIds",
    "SecurityGroupIds",
    "ServiceName",
    "ServiceNetworkArn",
    "ServiceRegion",
    "SubnetConfigurations",
    "SubnetIds",
    "TagSpecifications",
    "VpcEndpointType",
    "VpcId",
}
_RESERVED_MODIFY = {
    "AddRouteTableIds",
    "AddSecurityGroupIds",
    "AddSubnetIds",
    "DnsOptions",
    "IpAddressType",
    "PolicyDocument",
    "PrivateDnsEnabled",
    "RemoveRouteTableIds",
    "RemoveSecurityGroupIds",
    "RemoveSubnetIds",
    "ResetPolicy",
    "SubnetConfigurations",
    "VpcEndpointId",
}


@dataclass(frozen=True)
class VpcEndpointConfig:
    region: str
    vpc_id: str = ""
    subnet_ids: list[str] = field(default_factory=list)
    security_group_ids: list[str] = field(default_factory=list)
    route_table_ids: list[str] = field(default_factory=list)
    private_dns_enabled_default: bool = False
    deletion_protection_default: bool = True


class VpcEndpointDriver(ManagedServiceDriver):
    """Own consumer-side endpoints for every EC2 VPC endpoint type."""

    def __init__(self, *, config: VpcEndpointConfig, client: Any | None = None) -> None:
        self._config = config
        if client is None:
            import boto3

            client = boto3.client("ec2", region_name=config.region)
        self._ec2 = client

    @driver_op(
        cloud="aws",
        driver="vpc_endpoint",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        cfg = spec.config or {}
        error = self._validate_config(cfg)
        if error:
            return ProvisionResult(False, "", error, ["invalid_vpc_endpoint_config"])
        endpoint_id = ""
        try:
            endpoint = self._find_owned(spec, cfg)
            if endpoint is None:
                response = self._ec2.create_vpc_endpoint(**self._create_request(spec, cfg))
                endpoint = dict(response.get("VpcEndpoint") or {})
                endpoint_id = str(endpoint.get("VpcEndpointId") or "")
                if not endpoint_id:
                    raise ManagedServiceError("create_vpc_endpoint returned no VpcEndpointId")
            else:
                endpoint_id = str(endpoint["VpcEndpointId"])
            self._validate_immutable(endpoint, cfg)
            self._reconcile(endpoint, cfg)
        except Exception as exc:
            handle = handle_for(kind=KIND, resource_id=endpoint_id) if endpoint_id else ""
            return ProvisionResult(False, handle, f"provision VPC endpoint: {exc}", [str(exc)])
        state = str(endpoint.get("State") or "pending")
        return ProvisionResult(
            True,
            handle_for(kind=KIND, resource_id=endpoint_id),
            f"VPC endpoint {endpoint_id} is {state}",
            ready=state == "available",
        )

    @driver_op(cloud="aws", driver="vpc_endpoint")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        _, endpoint_id = parse_handle(spec.handle)
        cfg = spec.config or {}
        try:
            endpoint = self._describe(endpoint_id)
            error = self._validate_config(
                cfg,
                update=True,
                existing_type=str(endpoint.get("VpcEndpointType") or "Interface"),
            )
            if error:
                return UpdateResult(False, spec.handle, error, ["invalid_vpc_endpoint_config"])
            if not self._is_owned(endpoint):
                return UpdateResult(
                    False,
                    spec.handle,
                    "refusing to update a VPC endpoint not owned by Astrolift",
                    ["resource_not_owned"],
                )
            self._validate_immutable(endpoint, cfg)
            self._reconcile(endpoint, cfg)
        except Exception as exc:
            if _not_found(exc):
                return UpdateResult(False, spec.handle, f"VPC endpoint {endpoint_id} not found", ["not_found"])
            return UpdateResult(False, spec.handle, f"update VPC endpoint: {exc}", [str(exc)])
        return UpdateResult(True, spec.handle, f"VPC endpoint {endpoint_id} reconciled")

    @driver_op(
        cloud="aws",
        driver="vpc_endpoint",
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
        _, endpoint_id = parse_handle(spec.handle)
        try:
            endpoint = self._describe(endpoint_id)
        except Exception as exc:
            if _not_found(exc):
                return DeprovisionResult(True, spec.handle, f"VPC endpoint {endpoint_id} already gone")
            return DeprovisionResult(False, spec.handle, f"describe VPC endpoint: {exc}", [str(exc)])
        if not self._is_owned(endpoint) and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                "refusing to delete a VPC endpoint not owned by Astrolift",
                ["resource_not_owned"],
                retryable=False,
            )
        protected = bool(
            spec.config.get(
                "deletion_protection",
                self._config.deletion_protection_default,
            ),
        )
        if protected and not force_destroy:
            return DeprovisionResult(
                False,
                spec.handle,
                f"VPC endpoint {endpoint_id} has Astrolift deletion protection enabled",
                ["deletion_protection_enabled"],
                retryable=False,
            )
        try:
            response = self._ec2.delete_vpc_endpoints(VpcEndpointIds=[endpoint_id])
            unsuccessful = response.get("Unsuccessful") or []
            if unsuccessful:
                message = _delete_error(unsuccessful[0])
                return DeprovisionResult(False, spec.handle, message, [message])
        except Exception as exc:
            if not _not_found(exc):
                return DeprovisionResult(False, spec.handle, f"delete VPC endpoint: {exc}", [str(exc)])
        return DeprovisionResult(True, spec.handle, f"VPC endpoint {endpoint_id} deletion requested")

    @driver_op(cloud="aws", driver="vpc_endpoint")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, endpoint_id = parse_handle(handle.handle)
        try:
            endpoint = self._describe(endpoint_id)
        except Exception as exc:
            if _not_found(exc):
                return ServiceStatus(handle.handle, "deprovisioned", f"VPC endpoint {endpoint_id} is gone")
            return ServiceStatus(handle.handle, "error", f"describe VPC endpoint: {exc}")
        if not self._is_owned(endpoint):
            return ServiceStatus(handle.handle, "error", "VPC endpoint is not owned by Astrolift")
        provider_state = str(endpoint.get("State") or "unknown")
        state = {
            "pending": "provisioning",
            "pendingAcceptance": "provisioning",
            "available": "available",
            "deleting": "deprovisioning",
            "deleted": "deprovisioned",
            "failed": "error",
            "rejected": "error",
            "expired": "error",
        }.get(provider_state, "updating")
        return ServiceStatus(handle.handle, state, f"VPC endpoint is {provider_state}")

    @driver_op(cloud="aws", driver="vpc_endpoint")
    def binding(self, handle: ServiceHandle, config: dict[str, Any] | None = None) -> Binding:
        _, endpoint_id = parse_handle(handle.handle)
        endpoint = self._describe(endpoint_id)
        dns_entries = endpoint.get("DnsEntries") or []
        dns_names = [str(item.get("DnsName") or "") for item in dns_entries if item.get("DnsName")]
        ip_addresses = self._ip_addresses(endpoint)
        grants = _iam_grants(config or {})
        return Binding(
            env_vars={
                "PRIVATE_ENDPOINT_ID": ValueRef(literal=endpoint_id),
                "PRIVATE_ENDPOINT_DNS": ValueRef(literal=dns_names[0] if dns_names else ""),
                "PRIVATE_ENDPOINT_IPS": ValueRef(
                    literal=json.dumps(ip_addresses, separators=(",", ":")),
                ),
                "PRIVATE_ENDPOINT_TYPE": ValueRef(literal=str(endpoint.get("VpcEndpointType") or "")),
                "PRIVATE_ENDPOINT_SERVICE_NAME": ValueRef(literal=str(endpoint.get("ServiceName") or "")),
                "PRIVATE_ENDPOINT_DNS_NAME": ValueRef(literal=dns_names[0] if dns_names else ""),
                "PRIVATE_ENDPOINT_DNS_NAMES": ValueRef(literal=json.dumps(dns_names, separators=(",", ":"))),
                "PRIVATE_ENDPOINT_NETWORK_INTERFACE_IDS": ValueRef(
                    literal=json.dumps(endpoint.get("NetworkInterfaceIds") or [], separators=(",", ":")),
                ),
                "PRIVATE_ENDPOINT_PREFIX_LIST_ID": ValueRef(literal=str(endpoint.get("PrefixListId") or "")),
                "VPC_ENDPOINT_ID": ValueRef(literal=endpoint_id),
                "VPC_ID": ValueRef(literal=str(endpoint.get("VpcId") or "")),
                "AWS_REGION": ValueRef(literal=self._config.region),
            },
            iam_grants=grants,
            notes=(
                "Private network path only; service authorization remains enforced. "
                "Declare iam_grants when the attached workload also needs service IAM."
            ),
        )

    @driver_op(cloud="aws", driver="vpc_endpoint")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise ManagedServiceError("VPC endpoints are declarative network attachments and cannot be snapshotted")

    @driver_op(cloud="aws", driver="vpc_endpoint")
    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult:
        return ProvisionResult(
            False,
            "",
            "VPC endpoint restore is not supported; reprovision it from configuration",
            ["not_implemented"],
        )

    @driver_op(cloud="aws", driver="vpc_endpoint", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "endpoint_type": {
                    "type": "string",
                    "enum": ["Interface", "Gateway", "GatewayLoadBalancer", "Resource", "ServiceNetwork"],
                    "default": "Interface",
                },
                "vpc_id": {"type": "string"},
                "service": {"type": "string", "description": "AWS service suffix such as s3 or ecr.api."},
                "service_name": {"type": "string", "description": "Full AWS or producer endpoint-service name."},
                "service_region": {"type": "string"},
                "resource_configuration_arn": {"type": "string"},
                "service_network_arn": {"type": "string"},
                "subnet_ids": {"type": "array", "items": {"type": "string"}},
                "subnet_configurations": {"type": "array", "items": {"type": "object"}},
                "security_group_ids": {"type": "array", "items": {"type": "string"}},
                "route_table_ids": {"type": "array", "items": {"type": "string"}},
                "private_dns_enabled": {"type": "boolean"},
                "ip_address_type": {"type": "string", "enum": ["ipv4", "dualstack", "ipv6"]},
                "dns_options": {"type": "object"},
                "policy": {"description": "Endpoint policy object or serialized JSON."},
                "reset_policy": {"type": "boolean", "default": False},
                "create": {
                    "type": "object",
                    "description": "Additional native boto3 create_vpc_endpoint fields not owned by Astrolift.",
                },
                "modify": {
                    "type": "object",
                    "description": "Additional native boto3 modify_vpc_endpoint fields not owned by Astrolift.",
                },
                "iam_grants": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["resource", "actions"],
                        "properties": {
                            "resource": {"type": "string"},
                            "actions": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "deletion_protection": {"type": "boolean", "default": True},
            },
        }

    @driver_op(cloud="aws", driver="vpc_endpoint", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "PRIVATE_ENDPOINT_ID": "VPC endpoint ID",
                "PRIVATE_ENDPOINT_DNS": "Portable primary endpoint DNS name",
                "PRIVATE_ENDPOINT_IPS": "Portable JSON array of private IPv4 and IPv6 addresses",
                "PRIVATE_ENDPOINT_TYPE": "Interface, Gateway, GatewayLoadBalancer, Resource, or ServiceNetwork",
                "PRIVATE_ENDPOINT_SERVICE_NAME": "Provider endpoint-service name, when applicable",
                "PRIVATE_ENDPOINT_DNS_NAME": "First endpoint DNS name",
                "PRIVATE_ENDPOINT_DNS_NAMES": "JSON array of all endpoint DNS names",
                "PRIVATE_ENDPOINT_NETWORK_INTERFACE_IDS": "JSON array of endpoint ENI IDs",
                "PRIVATE_ENDPOINT_PREFIX_LIST_ID": "Gateway endpoint prefix-list ID",
                "VPC_ENDPOINT_ID": "AWS-native endpoint ID alias",
                "VPC_ID": "Consumer VPC ID",
                "AWS_REGION": "AWS region",
            },
        )

    def editable_fields(self) -> list[str]:
        return ["*"]

    def _validate_config(
        self,
        cfg: dict[str, Any],
        *,
        update: bool = False,
        existing_type: str = "",
    ) -> str:
        try:
            endpoint_type = _endpoint_type(cfg.get("endpoint_type") or existing_type or "Interface")
        except ValueError as exc:
            return str(exc)
        for name in (
            "subnet_ids",
            "subnet_configurations",
            "security_group_ids",
            "route_table_ids",
            "iam_grants",
        ):
            if name in cfg and not isinstance(cfg[name], list):
                return f"config.{name} must be an array"
        for name in ("dns_options", "create", "modify"):
            if name in cfg and not isinstance(cfg[name], dict):
                return f"config.{name} must be an object"
        if cfg.get("subnet_ids") and cfg.get("subnet_configurations"):
            return "subnet_ids and subnet_configurations are mutually exclusive"
        reserved = _RESERVED_CREATE.intersection((cfg.get("create") or {}).keys())
        if reserved:
            return f"config.create cannot override Astrolift-owned fields: {', '.join(sorted(reserved))}"
        reserved = _RESERVED_MODIFY.intersection((cfg.get("modify") or {}).keys())
        if reserved:
            return f"config.modify cannot override Astrolift-owned fields: {', '.join(sorted(reserved))}"
        if not update and not str(cfg.get("vpc_id") or self._config.vpc_id):
            return "vpc_id is required when the cluster has no VPC endpoint default"
        target_count = sum(
            bool(cfg.get(name))
            for name in ("service", "service_name", "resource_configuration_arn", "service_network_arn")
        )
        if not update and target_count != 1:
            return "exactly one service/service_name, resource_configuration_arn, or service_network_arn is required"
        if endpoint_type == "Resource" and (
            cfg.get("service") or cfg.get("service_name") or cfg.get("service_network_arn")
        ):
            return "Resource endpoints require only resource_configuration_arn"
        if endpoint_type == "ServiceNetwork" and (
            cfg.get("service") or cfg.get("service_name") or cfg.get("resource_configuration_arn")
        ):
            return "ServiceNetwork endpoints require only service_network_arn"
        if endpoint_type not in {"Resource", "ServiceNetwork"} and (
            cfg.get("resource_configuration_arn") or cfg.get("service_network_arn")
        ):
            return f"{endpoint_type} endpoints require service or service_name"
        if endpoint_type == "GatewayLoadBalancer":
            subnet_count = len(self._subnet_ids(cfg))
            if (not update and subnet_count != 1) or ("subnet_ids" in cfg and subnet_count != 1):
                return "GatewayLoadBalancer endpoints accept exactly one subnet"
        if cfg.get("subnet_ids") and endpoint_type not in {"Interface", "GatewayLoadBalancer"}:
            return "subnet_ids are supported only for Interface and GatewayLoadBalancer endpoints"
        if cfg.get("security_group_ids") and endpoint_type != "Interface":
            return "security_group_ids are supported only for Interface endpoints"
        if endpoint_type != "Gateway" and cfg.get("route_table_ids"):
            return "route_table_ids are supported only for Gateway endpoints"
        if cfg.get("private_dns_enabled") is not None and endpoint_type != "Interface":
            return "private_dns_enabled is supported only for Interface endpoints"
        if cfg.get("policy") is not None:
            if endpoint_type not in {"Interface", "Gateway"}:
                return "policy is supported only for Interface and Gateway endpoints"
            try:
                parsed = json.loads(_policy(cfg["policy"]))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                return f"config.policy must be valid JSON: {exc}"
            if not isinstance(parsed, dict):
                return "config.policy must encode an object"
        if cfg.get("policy") is not None and cfg.get("reset_policy"):
            return "policy and reset_policy are mutually exclusive"
        if cfg.get("reset_policy") and endpoint_type != "Gateway":
            return "reset_policy is supported only for Gateway endpoints"
        for index, declaration in enumerate(cfg.get("iam_grants") or []):
            if not isinstance(declaration, dict):
                return f"config.iam_grants[{index}] must be an object"
            if not str(declaration.get("resource") or ""):
                return f"config.iam_grants[{index}] requires resource"
            actions = declaration.get("actions")
            if not isinstance(actions, list) or not actions or not all(isinstance(item, str) for item in actions):
                return f"config.iam_grants[{index}].actions must be a non-empty string array"
        return ""

    def _create_request(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> dict[str, Any]:
        endpoint_type = _endpoint_type(cfg.get("endpoint_type") or "Interface")
        request = dict(cfg.get("create") or {})
        request.update(
            VpcId=str(cfg.get("vpc_id") or self._config.vpc_id),
            VpcEndpointType=endpoint_type,
            ClientToken=_client_token(
                spec,
                cfg,
                vpc_id=str(cfg.get("vpc_id") or self._config.vpc_id),
            ),
            TagSpecifications=[
                {
                    "ResourceType": "vpc-endpoint",
                    "Tags": [
                        *tags_for(spec),
                        {"Key": "astrolift.io/resource-hint", "Value": spec.service_handle_hint},
                    ],
                },
            ],
        )
        target = self._target(cfg)
        request.update(target)
        if cfg.get("service_region"):
            request["ServiceRegion"] = str(cfg["service_region"])
        subnet_ids = self._subnet_ids(cfg)
        if subnet_ids and endpoint_type in {"Interface", "GatewayLoadBalancer"}:
            request["SubnetIds"] = subnet_ids
        if cfg.get("subnet_configurations"):
            request["SubnetConfigurations"] = list(cfg["subnet_configurations"])
        security_group_ids = self._security_group_ids(cfg)
        if security_group_ids and endpoint_type == "Interface":
            request["SecurityGroupIds"] = security_group_ids
        route_table_ids = self._route_table_ids(cfg)
        if route_table_ids and endpoint_type == "Gateway":
            request["RouteTableIds"] = route_table_ids
        if endpoint_type == "Interface":
            request["PrivateDnsEnabled"] = bool(
                cfg.get("private_dns_enabled", self._config.private_dns_enabled_default),
            )
        if cfg.get("ip_address_type"):
            request["IpAddressType"] = str(cfg["ip_address_type"])
        if cfg.get("dns_options"):
            request["DnsOptions"] = dict(cfg["dns_options"])
        if cfg.get("policy") is not None:
            request["PolicyDocument"] = _policy(cfg["policy"])
        return request

    def _find_owned(self, spec: ProvisionSpec, cfg: dict[str, Any]) -> dict[str, Any] | None:
        filters = [
            {"Name": "vpc-id", "Values": [str(cfg.get("vpc_id") or self._config.vpc_id)]},
            {"Name": "tag:astrolift.io/managed-by", "Values": ["platform"]},
            {"Name": "tag:astrolift.io/organization", "Values": [spec.organization_slug]},
            {"Name": "tag:astrolift.io/app", "Values": [spec.app_slug]},
            {"Name": "tag:astrolift.io/environment", "Values": [spec.environment_name]},
            {"Name": "tag:astrolift.io/resource-hint", "Values": [spec.service_handle_hint]},
        ]
        candidates: list[dict[str, Any]] = []
        next_token = ""
        while True:
            request: dict[str, Any] = {"Filters": filters, "MaxResults": 1000}
            if next_token:
                request["NextToken"] = next_token
            response = self._ec2.describe_vpc_endpoints(**request)
            candidates.extend(
                dict(endpoint)
                for endpoint in response.get("VpcEndpoints") or []
                if str(endpoint.get("State") or "") not in _TERMINAL_STATES
            )
            next_token = str(response.get("NextToken") or "")
            if not next_token:
                break
        if len(candidates) > 1:
            raise ManagedServiceError("multiple owned VPC endpoints match this binding identity")
        return candidates[0] if candidates else None

    def _describe(self, endpoint_id: str) -> dict[str, Any]:
        endpoints = self._ec2.describe_vpc_endpoints(VpcEndpointIds=[endpoint_id]).get("VpcEndpoints") or []
        if not endpoints:
            raise ManagedServiceError(f"VPC endpoint {endpoint_id} not found")
        return dict(endpoints[0])

    def _ip_addresses(self, endpoint: dict[str, Any]) -> list[str]:
        interface_ids = [str(value) for value in endpoint.get("NetworkInterfaceIds") or []]
        if not interface_ids:
            return []
        response = self._ec2.describe_network_interfaces(NetworkInterfaceIds=interface_ids)
        addresses: set[str] = set()
        for interface in response.get("NetworkInterfaces") or []:
            addresses.update(
                str(item["PrivateIpAddress"])
                for item in interface.get("PrivateIpAddresses") or []
                if item.get("PrivateIpAddress")
            )
            addresses.update(
                str(item["Ipv6Address"]) for item in interface.get("Ipv6Addresses") or [] if item.get("Ipv6Address")
            )
        return sorted(addresses)

    def _validate_immutable(self, endpoint: dict[str, Any], cfg: dict[str, Any]) -> None:
        requested_type = _endpoint_type(cfg.get("endpoint_type") or endpoint.get("VpcEndpointType") or "Interface")
        requested_vpc = str(cfg.get("vpc_id") or self._config.vpc_id or endpoint.get("VpcId") or "")
        requested_target = self._target(cfg, required=False)
        checks = {
            "VpcEndpointType": requested_type,
            "VpcId": requested_vpc,
            **requested_target,
        }
        for attribute, value in checks.items():
            if value and str(endpoint.get(attribute) or "") != str(value):
                raise ManagedServiceError(
                    f"VPC endpoint {attribute} is immutable "
                    f"({endpoint.get(attribute)!r} != requested {value!r}); "
                    "reprovision is required",
                )

    def _reconcile(self, endpoint: dict[str, Any], cfg: dict[str, Any]) -> None:
        endpoint_id = str(endpoint["VpcEndpointId"])
        request: dict[str, Any] = dict(cfg.get("modify") or {})
        request["VpcEndpointId"] = endpoint_id
        endpoint_type = str(endpoint.get("VpcEndpointType") or "Interface")
        if "subnet_ids" in cfg and endpoint_type in {"Interface", "GatewayLoadBalancer"}:
            _add_remove(request, "SubnetIds", endpoint.get("SubnetIds") or [], cfg.get("subnet_ids") or [])
        if "security_group_ids" in cfg and endpoint_type == "Interface":
            current_groups = [str(item.get("GroupId") or "") for item in endpoint.get("Groups") or []]
            _add_remove(request, "SecurityGroupIds", current_groups, cfg.get("security_group_ids") or [])
        if "route_table_ids" in cfg and endpoint_type == "Gateway":
            _add_remove(request, "RouteTableIds", endpoint.get("RouteTableIds") or [], cfg.get("route_table_ids") or [])
        if "subnet_configurations" in cfg:
            request["SubnetConfigurations"] = list(cfg["subnet_configurations"])
        if "private_dns_enabled" in cfg and endpoint_type == "Interface":
            desired = bool(cfg["private_dns_enabled"])
            if bool(endpoint.get("PrivateDnsEnabled")) != desired:
                request["PrivateDnsEnabled"] = desired
        if "ip_address_type" in cfg and str(endpoint.get("IpAddressType") or "") != str(cfg["ip_address_type"]):
            request["IpAddressType"] = str(cfg["ip_address_type"])
        if "dns_options" in cfg:
            request["DnsOptions"] = dict(cfg["dns_options"])
        if cfg.get("policy") is not None and not _json_equal(endpoint.get("PolicyDocument"), cfg["policy"]):
            request["PolicyDocument"] = _policy(cfg["policy"])
        elif cfg.get("reset_policy"):
            request["ResetPolicy"] = True
        if len(request) > 1:
            self._ec2.modify_vpc_endpoint(**request)

    def _target(self, cfg: dict[str, Any], *, required: bool = True) -> dict[str, str]:
        if cfg.get("resource_configuration_arn"):
            return {"ResourceConfigurationArn": str(cfg["resource_configuration_arn"])}
        if cfg.get("service_network_arn"):
            return {"ServiceNetworkArn": str(cfg["service_network_arn"])}
        service_name = str(cfg.get("service_name") or "")
        if not service_name and cfg.get("service"):
            service_region = str(cfg.get("service_region") or self._config.region)
            service_name = f"com.amazonaws.{service_region}.{cfg['service']}"
        if service_name:
            return {"ServiceName": service_name}
        if required:
            raise ManagedServiceError("VPC endpoint target is required")
        return {}

    def _subnet_ids(self, cfg: dict[str, Any]) -> list[str]:
        values = cfg.get("subnet_ids", self._config.subnet_ids)
        return [str(value) for value in values]

    def _security_group_ids(self, cfg: dict[str, Any]) -> list[str]:
        values = cfg.get("security_group_ids", self._config.security_group_ids)
        return [str(value) for value in values]

    def _route_table_ids(self, cfg: dict[str, Any]) -> list[str]:
        values = cfg.get("route_table_ids", self._config.route_table_ids)
        return [str(value) for value in values]

    @staticmethod
    def _is_owned(endpoint: dict[str, Any]) -> bool:
        tags = {str(item.get("Key")): str(item.get("Value")) for item in endpoint.get("Tags") or []}
        return tags.get("astrolift.io/managed-by") == "platform"


def _endpoint_type(value: Any) -> str:
    raw = str(value).replace("-", "_").strip()
    normalized = _ENDPOINT_TYPES.get(raw.lower().replace(" ", "_"))
    if normalized is None:
        allowed = ", ".join(sorted(set(_ENDPOINT_TYPES.values())))
        raise ValueError(f"endpoint_type must be one of {allowed}")
    return normalized


def _client_token(spec: ProvisionSpec, cfg: dict[str, Any], *, vpc_id: str) -> str:
    identity = "|".join(
        (
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint,
            vpc_id,
            json.dumps(cfg, sort_keys=True, separators=(",", ":"), default=str),
        ),
    )
    return f"astrolift-{hashlib.sha256(identity.encode()).hexdigest()[:48]}"


def _policy(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    if isinstance(value, str):
        return value
    raise TypeError("policy must be an object or JSON string")


def _json_equal(left: Any, right: Any) -> bool:
    try:
        return bool(json.loads(_policy(left)) == json.loads(_policy(right)))
    except (TypeError, ValueError, json.JSONDecodeError):
        return False


def _add_remove(request: dict[str, Any], field: str, current: list[Any], desired: list[Any]) -> None:
    current_values = {str(value) for value in current if value}
    desired_values = {str(value) for value in desired if value}
    added = sorted(desired_values - current_values)
    removed = sorted(current_values - desired_values)
    if added:
        request[f"Add{field}"] = added
    if removed:
        request[f"Remove{field}"] = removed


def _iam_grants(cfg: dict[str, Any]) -> list[Grant]:
    return [
        Grant(
            resource=str(declaration["resource"]),
            actions=sorted({str(action) for action in declaration["actions"]}),
        )
        for declaration in cfg.get("iam_grants") or []
    ]


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {})
    code = str((response.get("Error") or {}).get("Code") or "")
    return (
        code
        in {
            "InvalidVpcEndpointId.NotFound",
            "InvalidVpcEndpointId.Malformed",
        }
        or "not found" in str(exc).lower()
    )


def _delete_error(item: dict[str, Any]) -> str:
    error = item.get("Error") or {}
    code = str(error.get("Code") or "delete_failed")
    message = str(error.get("Message") or "AWS rejected VPC endpoint deletion")
    return f"delete VPC endpoint: {code}: {message}"
