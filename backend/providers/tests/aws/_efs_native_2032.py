"""Actual Moto EFS filesystems/access points and EC2-backed mount targets."""

from __future__ import annotations

import json
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import boto3
from moto import mock_aws
from moto.efs.responses import EFSResponse

from _sdk.managed_service import ProvisionSpec
from aws.managed.filesystem_efs import EFSConfig, EFSDriver

SERVICE_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"
EFFECTS = (
    "create_file_system",
    "update_file_system",
    "delete_file_system",
    "tag_resource",
    "untag_resource",
    "create_access_point",
    "delete_access_point",
    "create_mount_target",
    "delete_mount_target",
    "modify_mount_target_security_groups",
    "put_lifecycle_configuration",
    "put_backup_policy",
    "update_file_system_protection",
    "put_file_system_policy",
    "delete_file_system_policy",
    "create_replication_configuration",
    "delete_replication_configuration",
)


def no_effects(cloud):
    stack = ExitStack()
    spies = [stack.enter_context(patch.object(cloud.api, key, wraps=getattr(cloud.api, key))) for key in EFFECTS]
    return stack, spies


def spec(cloud, **overrides):
    fields = dict(
        organization_id="org",
        organization_slug="alpha",
        app_id="app",
        app_slug="api",
        environment_id="environment",
        environment_name="prod",
        tenant_cluster_id="cluster",
        service_handle_hint="files",
        size="small",
        managed_service_id=SERVICE_ID,
    )
    fields.update(overrides)
    return ProvisionSpec(**fields)


@contextmanager
def native_efs():
    def describe_no_replications(response):
        # Moto5.2.2 has no replication API. This explicit empty native-model
        # fixture is only for unreplicated source filesystems; configured
        # replication needs separate contract proof, never a data-plane claim.
        assert not getattr(response.efs_backend, "fixture_replications", {}), "unsupported replication fixture"
        file_system_id = response._get_param("FileSystemId")
        response.efs_backend.describe_file_systems(
            file_system_id=file_system_id, creation_token=None, marker=None, max_items=100
        )
        return json.dumps({"Replications": []}), {"Content-Type": "application/json"}

    with (
        mock_aws(),
        patch.object(EFSResponse, "describe_replication_configurations", describe_no_replications, create=True),
    ):
        ec2 = boto3.client("ec2", region_name="us-east-1")
        api = boto3.client("efs", region_name="us-east-1")
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
        subnet = ec2.create_subnet(VpcId=vpc, CidrBlock="10.0.1.0/24")["Subnet"]["SubnetId"]
        group = ec2.create_security_group(VpcId=vpc, GroupName="fixture", Description="test")["GroupId"]
        cfg = EFSConfig(
            region="us-east-1",
            account_id="123456789012",
            subnet_ids=(subnet,),
            security_group_ids=(group,),
            poll_delay_seconds=0,
            max_poll_attempts=2,
        )
        yield SimpleNamespace(
            api=api, ec2=ec2, vpc=vpc, subnet=subnet, group=group, cfg=cfg, driver=EFSDriver(config=cfg, client=api)
        )
