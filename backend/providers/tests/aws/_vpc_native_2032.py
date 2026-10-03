"""Native EC2 endpoint API fixtures; no network traffic or external AWS effects."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import boto3
from moto import mock_aws
from moto.ec2.responses.vpcs import VPCs

from _sdk.managed_service import ProvisionSpec
from aws.managed.private_endpoint_vpc import VpcEndpointConfig, VpcEndpointDriver

SERVICE_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"
EFFECTS = ("create_vpc_endpoint", "modify_vpc_endpoint", "delete_vpc_endpoints", "create_tags", "delete_tags")


def no_effects(cloud):
    stack = ExitStack()
    spies = [stack.enter_context(patch.object(cloud.api, key, wraps=getattr(cloud.api, key))) for key in EFFECTS]
    return stack, spies


def endpoint_config(cloud, endpoint_type="Interface"):
    config = {"endpoint_type": endpoint_type, "vpc_id": cloud.vpc, "service": "s3"}
    if endpoint_type in {"Interface", "GatewayLoadBalancer"}:
        config["subnet_ids"] = [cloud.subnet]
    if endpoint_type == "Interface":
        config["security_group_ids"] = [cloud.group]
    if endpoint_type == "Gateway":
        config["route_table_ids"] = [cloud.route]
    if endpoint_type in {"Resource", "ServiceNetwork"}:
        config.pop("service")
        key, suffix = (
            ("resource_configuration_arn", "resourceconfiguration/rcfg-12345678901234567")
            if endpoint_type == "Resource"
            else ("service_network_arn", "servicenetwork/sn-12345678901234567")
        )
        # Shared/RAM target account need not equal the consumer's account.
        config[key] = "arn:aws:vpc-lattice:us-east-1:999999999999:" + suffix
    return config


def spec(cloud, endpoint_type="Interface", **overrides):
    fields = dict(
        organization_id="org",
        organization_slug="alpha",
        app_id="app",
        app_slug="api",
        environment_id="environment",
        environment_name="prod",
        tenant_cluster_id="cluster",
        service_handle_hint="private-api",
        size="small",
        managed_service_id=SERVICE_ID,
        config=endpoint_config(cloud, endpoint_type),
    )
    fields.update(overrides)
    return ProvisionSpec(**fields)


def endpoint(cloud, handle):
    return cloud.api.describe_vpc_endpoints(VpcEndpointIds=[handle.partition("/")[2]])["VpcEndpoints"][0]


@contextmanager
def native_vpc():
    original = VPCs.create_vpc_endpoint

    def capture_native_target(response):
        # Moto5.2.2 drops three documented request/response fields. Preserve them
        # on its real native model; IDs, tags, VPCs, ENIs and effects remain Moto.
        # This does not attest PrivateLink/RAM authorization or connectivity.
        result = original(response)
        model = result.result["VpcEndpoint"]
        for field, name in (
            ("ResourceConfigurationArn", "resource_configuration_arn"),
            ("ServiceNetworkArn", "service_network_arn"),
            ("ServiceRegion", "service_region"),
        ):
            setattr(model, name, response._get_param(field))
        return result

    with mock_aws(), patch.object(VPCs, "create_vpc_endpoint", capture_native_target):
        api = boto3.client("ec2", region_name="us-east-1")
        vpc = api.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
        subnet = api.create_subnet(VpcId=vpc, CidrBlock="10.0.1.0/24")["Subnet"]["SubnetId"]
        group = api.create_security_group(VpcId=vpc, GroupName="fixture", Description="test")["GroupId"]
        route = api.create_route_table(VpcId=vpc)["RouteTable"]["RouteTableId"]
        cfg = VpcEndpointConfig(region="us-east-1", account_id="123456789012", vpc_id=vpc)
        yield SimpleNamespace(
            api=api,
            vpc=vpc,
            subnet=subnet,
            group=group,
            route=route,
            cfg=cfg,
            driver=VpcEndpointDriver(config=cfg, client=api),
        )
