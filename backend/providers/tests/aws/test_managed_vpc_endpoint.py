"""Lifecycle, endpoint-type, safety, and request-shape tests for AWS VPC endpoints."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import ClassVar

import pytest
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed.private_endpoint_vpc import VpcEndpointConfig, VpcEndpointDriver


class NotFound(Exception):
    response: ClassVar[dict] = {"Error": {"Code": "InvalidVpcEndpointId.NotFound"}}


class FakeEC2:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.endpoints: dict[str, dict] = {}
        self.sequence = 0
        self.delete_unsuccessful: list[dict] = []

    def _call(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs))

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def kwargs_for(self, name: str, occurrence: int = -1) -> dict:
        return [kwargs for call, kwargs in self.calls if call == name][occurrence]

    def create_vpc_endpoint(self, **kwargs):
        self._call("create_vpc_endpoint", kwargs)
        self.sequence += 1
        endpoint_id = f"vpce-{self.sequence:017d}"
        endpoint = {
            "VpcEndpointId": endpoint_id,
            "VpcEndpointType": kwargs.get("VpcEndpointType", "Gateway"),
            "VpcId": kwargs["VpcId"],
            "ServiceName": kwargs.get("ServiceName", ""),
            "ServiceRegion": kwargs.get("ServiceRegion", ""),
            "ResourceConfigurationArn": kwargs.get("ResourceConfigurationArn", ""),
            "ServiceNetworkArn": kwargs.get("ServiceNetworkArn", ""),
            "State": "pending",
            "SubnetIds": list(kwargs.get("SubnetIds") or []),
            "RouteTableIds": list(kwargs.get("RouteTableIds") or []),
            "Groups": [{"GroupId": value} for value in kwargs.get("SecurityGroupIds") or []],
            "PrivateDnsEnabled": bool(kwargs.get("PrivateDnsEnabled", False)),
            "IpAddressType": kwargs.get("IpAddressType", "ipv4"),
            "DnsOptions": dict(kwargs.get("DnsOptions") or {}),
            "PolicyDocument": kwargs.get("PolicyDocument", ""),
            "DnsEntries": [
                {
                    "DnsName": f"{endpoint_id}.service.us-east-1.vpce.amazonaws.com",
                    "HostedZoneId": "Z123",
                },
            ],
            "NetworkInterfaceIds": ["eni-123"] if kwargs.get("VpcEndpointType") != "Gateway" else [],
            "PrefixListId": "pl-123" if kwargs.get("VpcEndpointType") == "Gateway" else "",
            "Tags": list((kwargs.get("TagSpecifications") or [{}])[0].get("Tags") or []),
        }
        self.endpoints[endpoint_id] = endpoint
        return {"VpcEndpoint": dict(endpoint), "ClientToken": kwargs.get("ClientToken")}

    def describe_vpc_endpoints(self, **kwargs):
        self._call("describe_vpc_endpoints", kwargs)
        if kwargs.get("VpcEndpointIds"):
            endpoints = []
            for endpoint_id in kwargs["VpcEndpointIds"]:
                endpoint = self.endpoints.get(endpoint_id)
                if endpoint is None:
                    raise NotFound(endpoint_id)
                endpoints.append(dict(endpoint))
            return {"VpcEndpoints": endpoints}
        endpoints = list(self.endpoints.values())
        for declaration in kwargs.get("Filters") or []:
            name = declaration["Name"]
            values = {str(value) for value in declaration["Values"]}
            if name == "vpc-id":
                endpoints = [item for item in endpoints if str(item.get("VpcId")) in values]
            elif name.startswith("tag:"):
                key = name.removeprefix("tag:")
                endpoints = [
                    item
                    for item in endpoints
                    if {str(tag["Key"]): str(tag["Value"]) for tag in item.get("Tags") or []}.get(key) in values
                ]
        return {"VpcEndpoints": [dict(item) for item in endpoints]}

    def modify_vpc_endpoint(self, **kwargs):
        self._call("modify_vpc_endpoint", kwargs)
        endpoint = self.endpoints[kwargs["VpcEndpointId"]]
        for key, field in (
            ("SubnetIds", "SubnetIds"),
            ("RouteTableIds", "RouteTableIds"),
        ):
            values = set(endpoint.get(field) or [])
            values.update(kwargs.get(f"Add{key}") or [])
            values.difference_update(kwargs.get(f"Remove{key}") or [])
            endpoint[field] = sorted(values)
        groups = {str(item["GroupId"]) for item in endpoint.get("Groups") or []}
        groups.update(kwargs.get("AddSecurityGroupIds") or [])
        groups.difference_update(kwargs.get("RemoveSecurityGroupIds") or [])
        endpoint["Groups"] = [{"GroupId": value} for value in sorted(groups)]
        for request_key, endpoint_key in (
            ("PrivateDnsEnabled", "PrivateDnsEnabled"),
            ("IpAddressType", "IpAddressType"),
            ("DnsOptions", "DnsOptions"),
            ("PolicyDocument", "PolicyDocument"),
            ("SubnetConfigurations", "SubnetConfigurations"),
        ):
            if request_key in kwargs:
                endpoint[endpoint_key] = kwargs[request_key]
        if kwargs.get("ResetPolicy"):
            endpoint["PolicyDocument"] = ""
        return {"Return": True}

    def describe_network_interfaces(self, **kwargs):
        self._call("describe_network_interfaces", kwargs)
        return {
            "NetworkInterfaces": [
                {
                    "NetworkInterfaceId": interface_id,
                    "PrivateIpAddresses": [{"PrivateIpAddress": "10.0.1.10"}],
                    "Ipv6Addresses": [{"Ipv6Address": "2001:db8::10"}],
                }
                for interface_id in kwargs["NetworkInterfaceIds"]
            ],
        }

    def delete_vpc_endpoints(self, **kwargs):
        self._call("delete_vpc_endpoints", kwargs)
        if self.delete_unsuccessful:
            return {"Unsuccessful": self.delete_unsuccessful}
        for endpoint_id in kwargs["VpcEndpointIds"]:
            self.endpoints.pop(endpoint_id, None)
        return {"Unsuccessful": []}

    def seed(self, *, owned: bool = True, **overrides) -> dict:
        tags = [{"Key": "astrolift.io/managed-by", "Value": "platform"}] if owned else []
        endpoint = {
            "VpcEndpointId": overrides.pop("VpcEndpointId", f"vpce-seed{len(self.endpoints)}"),
            "VpcEndpointType": "Interface",
            "VpcId": "vpc-123",
            "ServiceName": "com.amazonaws.us-east-1.s3",
            "State": "available",
            "SubnetIds": ["subnet-a"],
            "RouteTableIds": [],
            "Groups": [{"GroupId": "sg-a"}],
            "PrivateDnsEnabled": False,
            "IpAddressType": "ipv4",
            "DnsEntries": [],
            "NetworkInterfaceIds": [],
            "Tags": tags,
            **overrides,
        }
        self.endpoints[endpoint["VpcEndpointId"]] = endpoint
        return endpoint


def _config() -> VpcEndpointConfig:
    return VpcEndpointConfig(
        region="us-east-1",
        vpc_id="vpc-123",
        subnet_ids=["subnet-a"],
        security_group_ids=["sg-a"],
        route_table_ids=["rtb-a"],
    )


def _driver(client: FakeEC2 | None = None) -> tuple[VpcEndpointDriver, FakeEC2]:
    fake = client or FakeEC2()
    return VpcEndpointDriver(config=_config(), client=fake), fake


def _spec(config: dict | None = None, *, hint: str = "private-api") -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-id",
        organization_slug="steady",
        app_id="app-id",
        app_slug="triage",
        environment_id="env-id",
        environment_name="production",
        tenant_cluster_id="cluster-id",
        service_handle_hint=hint,
        size="small",
        config=config if config is not None else {"endpoint_type": "Interface", "service": "execute-api"},
        binding_id="binding-id",
        managed_service_id="service-id",
    )


def test_interface_endpoint_full_lifecycle_and_binding() -> None:
    driver, client = _driver()
    policy = {"Version": "2012-10-17", "Statement": []}
    config = {
        "endpoint_type": "Interface",
        "service": "ecr.api",
        "service_region": "us-west-2",
        "subnet_ids": ["subnet-a", "subnet-b"],
        "security_group_ids": ["sg-a", "sg-b"],
        "private_dns_enabled": True,
        "ip_address_type": "dualstack",
        "dns_options": {"DnsRecordIpType": "dualstack"},
        "policy": policy,
        "create": {"DryRun": False},
    }
    result = driver.provision(_spec(config))
    assert result.ok and not result.ready and result.handle.startswith("private_endpoint/vpce-")
    request = client.kwargs_for("create_vpc_endpoint")
    assert request["VpcEndpointType"] == "Interface"
    assert request["VpcId"] == "vpc-123"
    assert request["ServiceName"] == "com.amazonaws.us-west-2.ecr.api"
    assert request["ServiceRegion"] == "us-west-2"
    assert request["SubnetIds"] == ["subnet-a", "subnet-b"]
    assert request["SecurityGroupIds"] == ["sg-a", "sg-b"]
    assert request["PrivateDnsEnabled"] is True
    assert request["IpAddressType"] == "dualstack"
    assert json.loads(request["PolicyDocument"]) == policy
    assert request["ClientToken"].startswith("astrolift-")
    tags = {item["Key"]: item["Value"] for item in request["TagSpecifications"][0]["Tags"]}
    assert tags["astrolift.io/resource-hint"] == "private-api"
    assert tags["astrolift.io/binding"] == "binding-id"

    endpoint_id = result.handle.split("/", 1)[1]
    client.endpoints[endpoint_id]["State"] = "available"
    status = driver.status(ServiceHandle(result.handle))
    binding = driver.binding(ServiceHandle(result.handle))
    assert status.state == "available"
    assert binding.env_vars["PRIVATE_ENDPOINT_DNS_NAME"].literal == (
        f"{endpoint_id}.service.us-east-1.vpce.amazonaws.com"
    )
    assert binding.env_vars["PRIVATE_ENDPOINT_DNS"].literal == (f"{endpoint_id}.service.us-east-1.vpce.amazonaws.com")
    assert json.loads(binding.env_vars["PRIVATE_ENDPOINT_IPS"].literal or "[]") == [
        "10.0.1.10",
        "2001:db8::10",
    ]
    assert binding.env_vars["VPC_ID"].literal == "vpc-123"


def test_provision_is_idempotent_and_recovers_by_binding_tags() -> None:
    driver, client = _driver()
    first = driver.provision(_spec())
    second = driver.provision(_spec())
    assert first.ok and second.ok and first.handle == second.handle
    assert client.names().count("create_vpc_endpoint") == 1


def test_update_reconciles_mutable_interface_fields() -> None:
    driver, client = _driver()
    created = driver.provision(_spec())
    update = driver.update(
        UpdateSpec(
            created.handle,
            config={
                "subnet_ids": ["subnet-b"],
                "security_group_ids": ["sg-b"],
                "private_dns_enabled": True,
                "ip_address_type": "ipv6",
                "dns_options": {"DnsRecordIpType": "ipv6"},
                "policy": {"Statement": []},
                "modify": {"DryRun": False},
            },
        ),
    )
    assert update.ok
    request = client.kwargs_for("modify_vpc_endpoint")
    assert request["AddSubnetIds"] == ["subnet-b"]
    assert request["RemoveSubnetIds"] == ["subnet-a"]
    assert request["AddSecurityGroupIds"] == ["sg-b"]
    assert request["RemoveSecurityGroupIds"] == ["sg-a"]
    assert request["PrivateDnsEnabled"] is True
    assert request["IpAddressType"] == "ipv6"
    assert request["DryRun"] is False


def test_gateway_endpoint_uses_route_tables_policy_and_prefix_list() -> None:
    driver, client = _driver()
    result = driver.provision(
        _spec(
            {
                "endpoint_type": "Gateway",
                "service": "s3",
                "route_table_ids": ["rtb-a", "rtb-b"],
                "policy": {"Statement": []},
            },
        ),
    )
    assert result.ok
    request = client.kwargs_for("create_vpc_endpoint")
    assert request["RouteTableIds"] == ["rtb-a", "rtb-b"]
    assert "SubnetIds" not in request and "SecurityGroupIds" not in request
    binding = driver.binding(ServiceHandle(result.handle))
    assert binding.env_vars["PRIVATE_ENDPOINT_PREFIX_LIST_ID"].literal == "pl-123"

    updated = driver.update(UpdateSpec(result.handle, config={"route_table_ids": ["rtb-b", "rtb-c"]}))
    assert updated.ok
    modify = client.kwargs_for("modify_vpc_endpoint")
    assert modify["AddRouteTableIds"] == ["rtb-c"]
    assert modify["RemoveRouteTableIds"] == ["rtb-a"]


@pytest.mark.parametrize(
    ("endpoint_type", "target", "expected_key"),
    [
        ("GatewayLoadBalancer", {"service_name": "com.amazonaws.vpce.us-east-1.vpce-svc-1"}, "ServiceName"),
        (
            "Resource",
            {"resource_configuration_arn": "arn:aws:vpc-lattice:us-east-1:123:resourceconfiguration/x"},
            "ResourceConfigurationArn",
        ),
        (
            "ServiceNetwork",
            {"service_network_arn": "arn:aws:vpc-lattice:us-east-1:123:servicenetwork/x"},
            "ServiceNetworkArn",
        ),
    ],
)
def test_all_privatelink_endpoint_types_are_supported(endpoint_type: str, target: dict, expected_key: str) -> None:
    driver, client = _driver()
    config = {"endpoint_type": endpoint_type, **target}
    if endpoint_type == "GatewayLoadBalancer":
        config["subnet_ids"] = ["subnet-a"]
    result = driver.provision(_spec(config))
    assert result.ok
    request = client.kwargs_for("create_vpc_endpoint")
    assert request["VpcEndpointType"] == endpoint_type
    assert expected_key in request


def test_binding_exposes_only_explicit_service_iam_grants() -> None:
    driver, _ = _driver()
    result = driver.provision(_spec())
    plain = driver.binding(ServiceHandle(result.handle))
    assert plain.iam_grants == []
    bound = driver.binding(
        ServiceHandle(result.handle),
        {
            "iam_grants": [
                {
                    "resource": "arn:aws:execute-api:us-east-1:123:api/*",
                    "actions": ["execute-api:Invoke", "execute-api:Invoke"],
                },
            ],
        },
    )
    assert bound.iam_grants[0].actions == ["execute-api:Invoke"]


@pytest.mark.parametrize(
    ("provider_state", "platform_state"),
    [
        ("pending", "provisioning"),
        ("pendingAcceptance", "provisioning"),
        ("available", "available"),
        ("deleting", "deprovisioning"),
        ("failed", "error"),
        ("rejected", "error"),
    ],
)
def test_status_mapping(provider_state: str, platform_state: str) -> None:
    driver, client = _driver()
    endpoint = client.seed(State=provider_state)
    status = driver.status(ServiceHandle(f"private_endpoint/{endpoint['VpcEndpointId']}"))
    assert status.state == platform_state


def test_delete_requires_ownership_and_deletion_protection_bypass() -> None:
    driver, client = _driver()
    owned = client.seed()
    handle = f"private_endpoint/{owned['VpcEndpointId']}"
    protected = driver.deprovision(DeprovisionSpec(handle, {}))
    assert not protected.ok and not protected.retryable
    deleted = driver.deprovision(DeprovisionSpec(handle, {}), force_destroy=True)
    assert deleted.ok and owned["VpcEndpointId"] not in client.endpoints

    foreign = client.seed(owned=False)
    foreign_handle = f"private_endpoint/{foreign['VpcEndpointId']}"
    refused = driver.deprovision(
        DeprovisionSpec(foreign_handle, {"deletion_protection": False}),
    )
    assert not refused.ok and refused.errors == ["resource_not_owned"]


def test_delete_surfaces_unsuccessful_item_and_is_idempotent() -> None:
    driver, client = _driver()
    endpoint = client.seed()
    handle = f"private_endpoint/{endpoint['VpcEndpointId']}"
    client.delete_unsuccessful = [{"Error": {"Code": "DependencyViolation", "Message": "still used"}}]
    failed = driver.deprovision(
        DeprovisionSpec(handle, {"deletion_protection": False}),
    )
    assert not failed.ok and "DependencyViolation" in failed.message
    client.delete_unsuccessful = []
    assert driver.deprovision(DeprovisionSpec(handle, {}), force_destroy=True).ok
    assert driver.deprovision(DeprovisionSpec(handle, {}), force_destroy=True).ok


def test_update_rejects_immutable_identity_changes() -> None:
    driver, client = _driver()
    endpoint = client.seed()
    handle = f"private_endpoint/{endpoint['VpcEndpointId']}"
    for config in (
        {"vpc_id": "vpc-other"},
        {"endpoint_type": "Gateway"},
        {"service": "dynamodb"},
    ):
        result = driver.update(UpdateSpec(handle, config=config))
        assert not result.ok and "immutable" in result.message
    assert "modify_vpc_endpoint" not in client.names()


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({}, "exactly one"),
        ({"endpoint_type": "magic", "service": "s3"}, "endpoint_type"),
        ({"endpoint_type": "Interface", "service": "s3", "service_name": "duplicate"}, "exactly one"),
        ({"endpoint_type": "Resource", "service": "s3"}, "resource_configuration_arn"),
        ({"endpoint_type": "ServiceNetwork", "service": "s3"}, "service_network_arn"),
        ({"endpoint_type": "Gateway", "service": "s3", "subnet_ids": ["subnet-a"]}, "subnet_ids"),
        ({"endpoint_type": "Resource", "resource_configuration_arn": "arn:x", "subnet_ids": ["a"]}, "subnet_ids"),
        (
            {
                "endpoint_type": "GatewayLoadBalancer",
                "service_name": "svc",
                "security_group_ids": ["sg-a"],
            },
            "security_group_ids",
        ),
        ({"endpoint_type": "Interface", "service": "s3", "route_table_ids": ["rtb-a"]}, "Gateway"),
        ({"endpoint_type": "GatewayLoadBalancer", "service_name": "svc", "subnet_ids": ["a", "b"]}, "one subnet"),
        ({"endpoint_type": "Gateway", "service": "s3", "private_dns_enabled": True}, "Interface"),
        ({"endpoint_type": "Resource", "resource_configuration_arn": "arn:x", "policy": {}}, "policy"),
        ({"endpoint_type": "Interface", "service": "s3", "reset_policy": True}, "reset_policy"),
        ({"endpoint_type": "Interface", "service": "s3", "subnet_ids": "bad"}, "array"),
        (
            {
                "endpoint_type": "Interface",
                "service": "s3",
                "subnet_ids": ["a"],
                "subnet_configurations": [{}],
            },
            "mutually",
        ),
        ({"endpoint_type": "Interface", "service": "s3", "policy": "bad-json"}, "valid JSON"),
        ({"endpoint_type": "Interface", "service": "s3", "policy": {}, "reset_policy": True}, "mutually"),
        ({"endpoint_type": "Interface", "service": "s3", "create": {"VpcId": "escape"}}, "Astrolift-owned"),
        ({"endpoint_type": "Interface", "service": "s3", "modify": {"VpcEndpointId": "escape"}}, "Astrolift-owned"),
        (
            {
                "endpoint_type": "Interface",
                "service": "s3",
                "iam_grants": [{"resource": "*", "actions": []}],
            },
            "non-empty",
        ),
    ],
)
def test_invalid_config_is_rejected_before_mutation(config: dict, message: str) -> None:
    driver, client = _driver()
    result = driver.provision(_spec(config))
    assert not result.ok and message in result.message
    assert "create_vpc_endpoint" not in client.names()


def test_missing_runtime_vpc_is_rejected() -> None:
    client = FakeEC2()
    driver = VpcEndpointDriver(config=VpcEndpointConfig(region="us-east-1"), client=client)
    result = driver.provision(_spec({"endpoint_type": "Interface", "service": "s3"}))
    assert not result.ok and "vpc_id is required" in result.message


def test_foreign_endpoint_is_not_updated_or_bound_as_owned_status() -> None:
    driver, client = _driver()
    endpoint = client.seed(owned=False)
    handle = f"private_endpoint/{endpoint['VpcEndpointId']}"
    update = driver.update(UpdateSpec(handle, config={"private_dns_enabled": True}))
    status = driver.status(ServiceHandle(handle))
    assert not update.ok and update.errors == ["resource_not_owned"]
    assert status.state == "error"


def test_snapshot_and_restore_are_explicitly_unsupported() -> None:
    driver, _ = _driver()
    with pytest.raises(Exception, match="cannot be snapshotted"):
        driver.snapshot(ServiceHandle("private_endpoint/vpce-1"))
    restored = driver.restore(
        SimpleNamespace(handle="snapshot/x", snapshot_id="x", created_at="now"),
        _spec(),
    )
    assert not restored.ok and restored.errors == ["not_implemented"]


def test_native_create_and_modify_requests_match_botocore_shapes() -> None:
    driver, client = _driver()
    result = driver.provision(
        _spec(
            {
                "endpoint_type": "Interface",
                "service": "s3",
                "subnet_ids": ["subnet-a"],
                "security_group_ids": ["sg-a"],
                "private_dns_enabled": False,
                "ip_address_type": "ipv4",
                "dns_options": {"DnsRecordIpType": "ipv4"},
            },
        ),
    )
    assert result.ok
    update = driver.update(UpdateSpec(result.handle, config={"private_dns_enabled": True}))
    assert update.ok
    model = Session().get_service_model("ec2")
    validate_parameters(
        client.kwargs_for("create_vpc_endpoint"),
        model.operation_model("CreateVpcEndpoint").input_shape,
    )
    validate_parameters(
        client.kwargs_for("modify_vpc_endpoint"),
        model.operation_model("ModifyVpcEndpoint").input_shape,
    )


def test_registration_catalogue_cost_and_runtime_config() -> None:
    from core.cluster_observability import managed_config_for

    from _sdk.availability import MATRIX
    from aws.cost import SERVICE_CODE_BY_VARIANT
    from aws.plugin import PLUGIN

    assert ("private_endpoint", "vpc_endpoint") in PLUGIN.managed_service_drivers
    assert SERVICE_CODE_BY_VARIANT[("private_endpoint", "vpc_endpoint")] == "AmazonVPC"
    entry = next(
        item
        for item in MATRIX.managed_services
        if item.plugin_id == "aws" and item.kind == "private_endpoint" and item.variant == "vpc_endpoint"
    )
    assert entry.status == "preview" and "PRIVATE_ENDPOINT_ID" in entry.binding_envs

    cluster = SimpleNamespace(
        provider_config={
            "region": "us-west-2",
            "vpc_endpoint_vpc_id": "vpc-config",
            "vpc_endpoint_subnet_ids": ["subnet-config"],
            "vpc_endpoint_security_group_ids": ["sg-config"],
            "vpc_endpoint_route_table_ids": ["rtb-config"],
            "vpc_endpoint_private_dns_enabled_default": True,
            "vpc_endpoint_deletion_protection_default": False,
        },
        agent_config={},
        auth_config={},
        region="us-west-2",
    )
    config = managed_config_for("aws", cluster, kind="private_endpoint", variant="vpc_endpoint")
    assert config.region == "us-west-2"
    assert config.vpc_id == "vpc-config"
    assert config.subnet_ids == ["subnet-config"]
    assert config.private_dns_enabled_default is True
    assert config.deletion_protection_default is False


def test_runtime_config_discovers_cluster_vpc_when_not_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    import boto3
    from core.cluster_observability import managed_config_for

    from aws.managed import _networking

    clients: list[tuple[str, str]] = []

    def fake_client(service: str, *, region_name: str):
        clients.append((service, region_name))
        return SimpleNamespace(service=service)

    def fake_discover(cluster, *, region: str, ec2, eks):
        assert cluster.slug == "cluster"
        assert region == "us-east-2"
        assert ec2.service == "ec2" and eks.service == "eks"
        return "vpc-discovered", ["subnet-a", "subnet-b"], "10.0.0.0/16"

    monkeypatch.setattr(boto3, "client", fake_client)
    monkeypatch.setattr(_networking, "discover_vpc", fake_discover)
    cluster = SimpleNamespace(
        slug="cluster",
        region="us-east-2",
        provider_config={},
        auth_config={},
    )
    config = managed_config_for("aws", cluster, kind="private_endpoint", variant="vpc_endpoint")
    assert config.vpc_id == "vpc-discovered"
    assert config.subnet_ids == ["subnet-a", "subnet-b"]
    assert clients == [("ec2", "us-east-2"), ("eks", "us-east-2")]
