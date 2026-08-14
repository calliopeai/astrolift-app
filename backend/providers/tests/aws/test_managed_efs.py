from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed._networking import ensure_efs_networking
from aws.managed.filesystem_efs import EFSConfig, EFSDriver

FS_ID = "fs-12345678"
AP_ID = "fsap-12345678"
FS_ARN = f"arn:aws:elasticfilesystem:us-west-2:123456789012:file-system/{FS_ID}"
SUBNETS = ("subnet-12345678", "subnet-23456789", "subnet-34567890")
SECURITY_GROUPS = ("sg-12345678",)


def _spec(**overrides) -> ProvisionSpec:
    values = {
        "organization_id": "org-1",
        "organization_slug": "steadymd",
        "app_id": "app-1",
        "app_slug": "triage",
        "environment_id": "env-1",
        "environment_name": "prod",
        "tenant_cluster_id": "cluster-1",
        "service_handle_hint": "shared",
        "size": "small",
        "config": {},
        "tags": {"owner": "agents"},
        "isolation": "shared",
        "binding_id": "binding-1",
        "managed_service_id": "service-1",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _config(**overrides) -> EFSConfig:
    values = {
        "region": "us-west-2",
        "account_id": "123456789012",
        "subnet_ids": SUBNETS,
        "security_group_ids": SECURITY_GROUPS,
        "creation_token_prefix": "platform",
        "deletion_protection_default": True,
        "poll_delay_seconds": 0,
        "max_poll_attempts": 4,
    }
    values.update(overrides)
    return EFSConfig(**values)


def _error(code: str, operation: str, message: str | None = None) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": message or code},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        operation,
    )


def _validate(operation: str, params: dict) -> None:
    service = Session().get_service_model("efs")
    validate_parameters(params, service.operation_model(operation).input_shape)


def _client(*, existing: bool = False, managed: bool = True, state: str = "available") -> MagicMock:
    client = MagicMock()
    token = "platform-steadymd-triage-prod-shared"
    fs = {
        "CreationToken": token,
        "FileSystemId": FS_ID,
        "FileSystemArn": FS_ARN,
        "LifeCycleState": state,
        "Encrypted": True,
        "Tags": ([{"Key": "astrolift.io/managed-by", "Value": "platform"}] if managed else []),
    }
    store = {
        "exists": existing,
        "fs": fs,
        "mount_targets": [],
        "access_points": [],
        "replications": [],
    }
    client.store = store

    def describe_file_systems(**request):
        if not store["exists"]:
            if "FileSystemId" in request:
                raise _error("FileSystemNotFound", "DescribeFileSystems")
            return {"FileSystems": []}
        return {"FileSystems": [dict(store["fs"])]}

    def create_file_system(**request):
        store["exists"] = True
        store["fs"].update(
            {
                "CreationToken": request["CreationToken"],
                "Encrypted": request["Encrypted"],
                "Tags": list(request["Tags"]),
            },
        )
        return dict(store["fs"])

    def describe_mount_targets(**_request):
        return {"MountTargets": [dict(target) for target in store["mount_targets"]]}

    def create_mount_target(**request):
        target = {
            "MountTargetId": f"fsmt-{len(store['mount_targets']) + 1:08x}",
            "FileSystemId": request["FileSystemId"],
            "SubnetId": request["SubnetId"],
            "LifeCycleState": "available",
        }
        store["mount_targets"].append(target)
        return dict(target)

    def delete_mount_target(**request):
        store["mount_targets"] = [
            target for target in store["mount_targets"] if target["MountTargetId"] != request["MountTargetId"]
        ]
        return {}

    def describe_access_points(**request):
        points = store["access_points"]
        if "AccessPointId" in request:
            points = [point for point in points if point["AccessPointId"] == request["AccessPointId"]]
            if not points:
                raise _error("AccessPointNotFound", "DescribeAccessPoints")
        return {"AccessPoints": [dict(point) for point in points]}

    def create_access_point(**request):
        point = {
            "AccessPointId": AP_ID,
            "AccessPointArn": ("arn:aws:elasticfilesystem:us-west-2:123456789012:access-point/" + AP_ID),
            "ClientToken": request["ClientToken"],
            "FileSystemId": request["FileSystemId"],
            "LifeCycleState": "available",
            "Tags": list(request.get("Tags") or []),
        }
        store["access_points"].append(point)
        return dict(point)

    def delete_access_point(**request):
        store["access_points"] = [
            point for point in store["access_points"] if point["AccessPointId"] != request["AccessPointId"]
        ]
        return {}

    def delete_file_system(**_request):
        store["exists"] = False
        return {}

    def describe_replication_configurations(**_request):
        return {"Replications": [dict(replication) for replication in store["replications"]]}

    def create_replication_configuration(**request):
        replication = {
            "SourceFileSystemId": request["SourceFileSystemId"],
            "Destinations": [
                {
                    "Region": destination.get("Region") or str(destination.get("AvailabilityZoneName") or "")[:-1],
                    "FileSystemId": destination.get("FileSystemId", "fs-87654321"),
                    "RoleArn": destination.get("RoleArn", ""),
                    "Status": "ENABLED",
                }
                for destination in request["Destinations"]
            ],
        }
        store["replications"] = [replication]
        return dict(replication)

    def delete_replication_configuration(**_request):
        store["replications"] = []
        return {}

    client.describe_file_systems.side_effect = describe_file_systems
    client.create_file_system.side_effect = create_file_system
    client.describe_mount_targets.side_effect = describe_mount_targets
    client.create_mount_target.side_effect = create_mount_target
    client.delete_mount_target.side_effect = delete_mount_target
    client.describe_access_points.side_effect = describe_access_points
    client.create_access_point.side_effect = create_access_point
    client.delete_access_point.side_effect = delete_access_point
    client.delete_file_system.side_effect = delete_file_system
    client.describe_replication_configurations.side_effect = describe_replication_configurations
    client.create_replication_configuration.side_effect = create_replication_configuration
    client.delete_replication_configuration.side_effect = delete_replication_configuration
    return client


def test_provision_creates_encrypted_filesystem_mount_targets_and_access_point():
    client = _client()
    driver = EFSDriver(config=_config(kms_key_id="alias/platform-efs"), client=client, sleep=lambda _seconds: None)

    result = driver.provision(
        _spec(
            config={
                "file_system": {"PerformanceMode": "generalPurpose", "ThroughputMode": "elastic"},
                "root_path": "/triage",
                "posix_uid": 2000,
                "posix_gid": 3000,
            },
        ),
    )

    assert result.ok and result.ready and result.handle == f"filesystem/{FS_ID}/{AP_ID}"
    create = client.create_file_system.call_args.kwargs
    assert create["Encrypted"] is True
    assert create["KmsKeyId"] == "alias/platform-efs"
    assert create["Tags"] and any(tag["Key"] == "astrolift.io/binding" for tag in create["Tags"])
    _validate("CreateFileSystem", create)
    assert [call.kwargs["SubnetId"] for call in client.create_mount_target.call_args_list] == list(SUBNETS)
    assert all(
        call.kwargs["SecurityGroups"] == list(SECURITY_GROUPS) for call in client.create_mount_target.call_args_list
    )
    access = client.create_access_point.call_args.kwargs
    assert access["RootDirectory"]["Path"] == "/triage"
    assert access["PosixUser"] == {"Uid": 2000, "Gid": 3000}
    _validate("CreateAccessPoint", access)


def test_provision_can_use_native_mount_targets_and_skip_access_point():
    client = _client()
    driver = EFSDriver(config=_config(), client=client, sleep=lambda _seconds: None)
    target = {
        "SubnetId": "subnet-99999999",
        "SecurityGroups": ["sg-99999999"],
        "IpAddressType": "IPV4_ONLY",
    }

    result = driver.provision(
        _spec(config={"mount_targets": [target], "create_access_point": False}),
    )

    assert result.ok and result.handle == f"filesystem/{FS_ID}"
    request = client.create_mount_target.call_args.kwargs
    assert request["IpAddressType"] == "IPV4_ONLY"
    assert request["SecurityGroups"] == ["sg-99999999"]
    client.create_access_point.assert_not_called()
    _validate("CreateMountTarget", request)


def test_existing_managed_filesystem_is_reconciled_not_recreated():
    client = _client(existing=True)
    client.store["mount_targets"] = [
        {
            "MountTargetId": f"fsmt-{index:08x}",
            "FileSystemId": FS_ID,
            "SubnetId": subnet,
            "LifeCycleState": "available",
        }
        for index, subnet in enumerate(SUBNETS, start=1)
    ]
    client.store["access_points"] = [
        {
            "AccessPointId": AP_ID,
            "ClientToken": "platform-steadymd-triage-prod-shared-access-point",
            "FileSystemId": FS_ID,
            "LifeCycleState": "available",
            "Tags": [{"Key": "astrolift.io/managed-by", "Value": "platform"}],
        },
    ]
    driver = EFSDriver(config=_config(), client=client)

    result = driver.provision(_spec())

    assert result.ok and result.handle == f"filesystem/{FS_ID}/{AP_ID}"
    client.create_file_system.assert_not_called()
    client.create_mount_target.assert_not_called()
    client.create_access_point.assert_not_called()
    client.tag_resource.assert_called_once()


def test_creation_token_collision_is_never_adopted():
    client = _client(existing=True, managed=False)
    driver = EFSDriver(config=_config(), client=client)

    result = driver.provision(_spec())

    assert not result.ok and "outside this declaration" in result.message
    client.tag_resource.assert_not_called()
    client.create_mount_target.assert_not_called()


def test_update_applies_native_throughput_lifecycle_backup_and_protection():
    client = _client(existing=True)
    client.store["mount_targets"] = [
        {
            "MountTargetId": "fsmt-12345678",
            "FileSystemId": FS_ID,
            "SubnetId": SUBNETS[0],
            "LifeCycleState": "available",
        },
    ]
    driver = EFSDriver(config=_config(subnet_ids=(SUBNETS[0],)), client=client)
    config = {
        "file_system_update": {"ThroughputMode": "provisioned", "ProvisionedThroughputInMibps": 64.0},
        "lifecycle_policies": [{"TransitionToIA": "AFTER_30_DAYS"}],
        "backup_policy": "ENABLED",
        "replication_overwrite_protection": "ENABLED",
        "file_system_policy": {
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Principal": {"AWS": "*"}, "Action": "elasticfilesystem:ClientMount"}],
        },
        "mount_target_security_group_ids": ["sg-99999999"],
    }

    result = driver.update(UpdateSpec(f"filesystem/{FS_ID}", config=config))

    assert result.ok
    update = client.update_file_system.call_args.kwargs
    _validate("UpdateFileSystem", update)
    client.put_lifecycle_configuration.assert_called_once_with(
        FileSystemId=FS_ID,
        LifecyclePolicies=[{"TransitionToIA": "AFTER_30_DAYS"}],
    )
    client.put_backup_policy.assert_called_once_with(
        FileSystemId=FS_ID,
        BackupPolicy={"Status": "ENABLED"},
    )
    client.update_file_system_protection.assert_called_once_with(
        FileSystemId=FS_ID,
        ReplicationOverwriteProtection="ENABLED",
    )
    policy_call = client.put_file_system_policy.call_args.kwargs
    assert policy_call["FileSystemId"] == FS_ID
    assert '"elasticfilesystem:ClientMount"' in policy_call["Policy"]
    assert policy_call["BypassPolicyLockoutSafetyCheck"] is False
    client.modify_mount_target_security_groups.assert_called_once_with(
        MountTargetId="fsmt-12345678",
        SecurityGroups=["sg-99999999"],
    )


def test_provision_creates_and_idempotently_recognizes_cross_region_replication():
    client = _client()
    driver = EFSDriver(config=_config(), client=client, sleep=lambda _seconds: None)
    config = {
        "replication_configuration": {
            "Destinations": [{"Region": "us-east-1", "KmsKeyId": "alias/efs-replica"}],
        },
    }

    first = driver.provision(_spec(config=config))
    second = driver.provision(_spec(config=config))

    assert first.ok and second.ok
    client.create_replication_configuration.assert_called_once()
    request = client.create_replication_configuration.call_args.kwargs
    assert request["SourceFileSystemId"] == FS_ID
    _validate("CreateReplicationConfiguration", request)


def test_replication_destination_drift_requires_reprovision():
    client = _client(existing=True)
    client.store["replications"] = [
        {
            "SourceFileSystemId": FS_ID,
            "Destinations": [{"Region": "us-east-1", "FileSystemId": "fs-87654321"}],
        },
    ]
    driver = EFSDriver(config=_config(), client=client)

    result = driver.provision(
        _spec(config={"replication_configuration": {"Destinations": [{"Region": "eu-west-1"}]}}),
    )

    assert not result.ok and "immutable live configuration" in result.message


@pytest.mark.parametrize(
    ("provider_state", "expected"),
    [
        ("creating", "provisioning"),
        ("available", "available"),
        ("updating", "updating"),
        ("deleting", "deprovisioning"),
        ("error", "error"),
    ],
)
def test_status_maps_filesystem_lifecycle(provider_state, expected):
    client = _client(existing=True, state=provider_state)
    if provider_state == "available":
        client.store["mount_targets"] = [
            {
                "MountTargetId": "fsmt-12345678",
                "FileSystemId": FS_ID,
                "SubnetId": SUBNETS[0],
                "LifeCycleState": "available",
            },
        ]
    driver = EFSDriver(config=_config(), client=client)

    assert driver.status(ServiceHandle(f"filesystem/{FS_ID}")).state == expected


def test_available_filesystem_without_ready_mount_target_is_not_reported_ready():
    client = _client(existing=True)
    driver = EFSDriver(config=_config(), client=client)

    assert driver.status(ServiceHandle(f"filesystem/{FS_ID}")).state == "provisioning"


def test_binding_emits_portable_csi_envelope_and_scoped_client_grants():
    client = _client(existing=True)
    driver = EFSDriver(config=_config(), client=client)

    binding = driver.binding(
        ServiceHandle(f"filesystem/{FS_ID}/{AP_ID}"),
        {"mount_path": "/workspace", "client_root_access": True},
    )

    assert binding.env_vars["FILESYSTEM_HANDLE"].literal == FS_ID
    assert binding.env_vars["FILESYSTEM_MOUNT_PATH"].literal == "/workspace"
    assert binding.env_vars["FILESYSTEM_TLS"].literal == "true"
    assert binding.env_vars["FILESYSTEM_ENDPOINT"].literal == f"{FS_ID}.efs.us-west-2.amazonaws.com"
    options = binding.env_vars["FILESYSTEM_MOUNT_OPTIONS"].literal.split(",")
    assert options == ["tls", f"accesspoint={AP_ID}", "iam"]
    assert binding.iam_grants[0].resource == FS_ARN
    assert binding.iam_grants[0].actions == [
        "elasticfilesystem:ClientMount",
        "elasticfilesystem:ClientWrite",
        "elasticfilesystem:ClientRootAccess",
    ]


def test_read_only_binding_does_not_grant_client_write():
    client = _client(existing=True)
    driver = EFSDriver(config=_config(), client=client)

    binding = driver.binding(ServiceHandle(f"filesystem/{FS_ID}"), {"read_only": True})

    assert binding.iam_grants[0].actions == ["elasticfilesystem:ClientMount"]


def test_binding_and_delete_refuse_an_external_filesystem_even_with_force():
    client = _client(existing=True, managed=False)
    driver = EFSDriver(config=_config(), client=client)
    handle = f"filesystem/{FS_ID}"

    with pytest.raises(ManagedServiceError, match="not owned"):
        driver.binding(ServiceHandle(handle))
    result = driver.deprovision(
        DeprovisionSpec(handle),
        delete_data=True,
        force_destroy=True,
    )

    assert not result.ok and result.errors == ["external_resource_collision"]
    client.delete_file_system.assert_not_called()


def test_mount_target_pruning_is_explicit_and_convergent():
    client = _client(existing=True)
    client.store["mount_targets"] = [
        {
            "MountTargetId": f"fsmt-{index:08x}",
            "FileSystemId": FS_ID,
            "SubnetId": subnet,
            "LifeCycleState": "available",
        }
        for index, subnet in enumerate(SUBNETS, start=1)
    ]
    driver = EFSDriver(config=_config(), client=client)

    result = driver.update(
        UpdateSpec(
            f"filesystem/{FS_ID}",
            config={
                "mount_targets": [{"SubnetId": SUBNETS[0]}],
                "prune_mount_targets": True,
            },
        ),
    )

    assert result.ok
    assert {target["SubnetId"] for target in client.store["mount_targets"]} == {SUBNETS[0]}
    assert client.delete_mount_target.call_count == 2


def test_deprovision_requires_guard_and_data_loss_ack_then_removes_dependencies():
    client = _client(existing=True)
    client.store["mount_targets"] = [
        {
            "MountTargetId": "fsmt-12345678",
            "FileSystemId": FS_ID,
            "SubnetId": SUBNETS[0],
            "LifeCycleState": "available",
        },
    ]
    client.store["access_points"] = [
        {
            "AccessPointId": AP_ID,
            "FileSystemId": FS_ID,
            "LifeCycleState": "available",
            "Tags": [{"Key": "astrolift.io/managed-by", "Value": "platform"}],
        },
    ]
    client.store["replications"] = [
        {
            "SourceFileSystemId": FS_ID,
            "Destinations": [{"Region": "us-east-1", "FileSystemId": "fs-87654321"}],
        },
    ]
    driver = EFSDriver(config=_config(), client=client)
    handle = f"filesystem/{FS_ID}/{AP_ID}"

    protected = driver.deprovision(DeprovisionSpec(handle))
    retained = driver.deprovision(
        DeprovisionSpec(handle, config={"deletion_protection": False}),
    )
    deleted = driver.deprovision(
        DeprovisionSpec(handle),
        delete_data=True,
        force_destroy=True,
    )

    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]
    assert not retained.ok and retained.errors == ["retained_filesystem_data_requires_delete_data"]
    assert deleted.ok
    client.delete_access_point.assert_called_once_with(AccessPointId=AP_ID)
    client.delete_replication_configuration.assert_called_once_with(
        SourceFileSystemId=FS_ID,
        DeletionMode="ALL_CONFIGURATIONS",
    )
    client.delete_mount_target.assert_called_once_with(MountTargetId="fsmt-12345678")
    client.delete_file_system.assert_called_once_with(FileSystemId=FS_ID)


def test_missing_status_update_and_delete_converge_honestly():
    client = _client(existing=False)
    driver = EFSDriver(config=_config(), client=client)
    handle = f"filesystem/{FS_ID}"

    assert driver.status(ServiceHandle(handle)).state == "deprovisioned"
    assert not driver.update(UpdateSpec(handle, config={})).ok
    assert driver.deprovision(DeprovisionSpec(handle)).ok


def test_snapshot_contract_points_to_aws_backup_instead_of_faking_efs_snapshot():
    driver = EFSDriver(config=_config(), client=_client(existing=True))

    with pytest.raises(ManagedServiceError, match="AWS Backup"):
        driver.snapshot(ServiceHandle(f"filesystem/{FS_ID}"))


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"unknown": True}, "unsupported EFS config fields"),
        ({"file_system": []}, "file_system must be an object"),
        ({"mount_targets": []}, "non-empty array"),
        ({"mount_targets": [{}]}, "requires an AWS SubnetId"),
        (
            {"mount_targets": [{"SubnetId": SUBNETS[0]}, {"SubnetId": SUBNETS[0]}]},
            "cannot repeat a subnet",
        ),
        ({"mount_path": "relative"}, "absolute path"),
        ({"mount_options": [""]}, "non-empty strings"),
        ({"lifecycle_policies": [{}, {}, {}, {}]}, "at most three"),
        ({"tls": "yes"}, "must be a boolean"),
        ({"tls": False}, "access points require tls=true"),
        ({"create_access_point": False, "root_path": "/ignored"}, "require create_access_point=true"),
        ({"backup_policy": "MAYBE"}, "backup_policy"),
        ({"replication_configuration": {}}, "non-empty object"),
        (
            {"file_system_policy": {}, "delete_file_system_policy": True},
            "mutually exclusive",
        ),
        (
            {"file_system": {"AvailabilityZoneName": "us-west-2a"}},
            "exactly one explicit mount target",
        ),
    ],
)
def test_invalid_config_is_rejected_before_cloud(config, message):
    client = _client()
    driver = EFSDriver(config=_config(), client=client)

    result = driver.provision(_spec(config=config))

    assert not result.ok and message in result.message
    client.create_file_system.assert_not_called()


def test_efs_networking_discovers_three_azs_and_nfs_security_group():
    ec2 = MagicMock()
    eks = MagicMock()
    cluster = SimpleNamespace(slug="aws-prod", provider_config={}, auth_config={})
    eks.describe_cluster.return_value = {"cluster": {"resourcesVpcConfig": {"vpcId": "vpc-1"}}}
    ec2.describe_vpcs.return_value = {"Vpcs": [{"CidrBlock": "10.0.0.0/16"}]}
    ec2.describe_subnets.return_value = {
        "Subnets": [
            {
                "SubnetId": f"subnet-{index:08x}",
                "AvailabilityZone": f"us-west-2{az}",
                "MapPublicIpOnLaunch": False,
            }
            for index, az in enumerate(("a", "b", "c", "d"), start=1)
        ],
    }
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    ec2.create_security_group.return_value = {"GroupId": "sg-12345678"}

    subnets, groups = ensure_efs_networking(
        cluster,
        region="us-west-2",
        clients=(ec2, eks),
    )

    assert len(subnets) == 3
    assert groups == ["sg-12345678"]
    permission = ec2.authorize_security_group_ingress.call_args.kwargs["IpPermissions"][0]
    assert permission["FromPort"] == permission["ToPort"] == 2049


def test_config_plugin_cost_and_catalogue_are_wired():
    from core.cluster_observability import managed_config_for

    from _sdk.availability import MATRIX
    from aws.cost import SERVICE_CODE_BY_VARIANT
    from aws.plugin import PLUGIN

    cluster = SimpleNamespace(
        slug="aws-prod",
        region="us-west-2",
        auth_config={},
        provider_config={
            "account_id": "123456789012",
            "efs_subnet_ids": list(SUBNETS),
            "efs_security_group_ids": list(SECURITY_GROUPS),
            "efs_kms_key_id": "alias/platform-efs",
        },
    )

    config = managed_config_for("aws", cluster, kind="filesystem", variant="efs")

    assert config.subnet_ids == SUBNETS
    assert config.security_group_ids == SECURITY_GROUPS
    assert config.kms_key_id == "alias/platform-efs"
    assert PLUGIN.managed_service_drivers[("filesystem", "efs")] is EFSDriver
    assert SERVICE_CODE_BY_VARIANT[("filesystem", "efs")] == "AmazonEFS"
    entry = next(item for item in MATRIX.managed_services if item.plugin_id == "aws" and item.variant == "efs")
    assert entry.status == "preview"
    assert "FILESYSTEM_HANDLE" in entry.binding_envs
