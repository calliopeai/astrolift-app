from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from aws.managed._base import ManagedServiceError
from aws.managed._networking import ensure_fsx_networking
from aws.managed.filesystem_fsx import (
    FSxConfig,
    FSxLustreDriver,
    FSxOpenZFSDriver,
    FSxWindowsDriver,
)

FS_ID = "fs-12345678"
RESTORED_FS_ID = "fs-87654321"
BACKUP_ID = "backup-12345678"
ROOT_VOLUME_ID = "fsvol-1234567890abcdef0"
SUBNETS = ("subnet-12345678", "subnet-23456789", "subnet-34567890")
GROUPS = ("sg-12345678",)


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


def _config(**overrides) -> FSxConfig:
    values = {
        "region": "us-west-2",
        "account_id": "123456789012",
        "subnet_ids": SUBNETS,
        "security_group_ids": GROUPS,
        "client_token_prefix": "platform",
        "deletion_protection_default": True,
        "poll_delay_seconds": 0,
        "max_poll_attempts": 4,
    }
    values.update(overrides)
    return FSxConfig(**values)


def _error(code: str, operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": code},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        operation,
    )


def _validate(operation: str, params: dict) -> None:
    service = Session().get_service_model("fsx")
    validate_parameters(params, service.operation_model(operation).input_shape)


def _file_system(file_system_type: str, *, managed: bool = True, lifecycle: str = "AVAILABLE") -> dict:
    engine = {
        "LUSTRE": {"DeploymentType": "PERSISTENT_2", "MountName": "abc123mv"},
        "OPENZFS": {"DeploymentType": "SINGLE_AZ_1", "RootVolumeId": ROOT_VOLUME_ID},
        "WINDOWS": {"DeploymentType": "SINGLE_AZ_2", "ActiveDirectoryId": "d-1234567890"},
    }[file_system_type]
    key = {
        "LUSTRE": "LustreConfiguration",
        "OPENZFS": "OpenZFSConfiguration",
        "WINDOWS": "WindowsConfiguration",
    }[file_system_type]
    variant = {
        "LUSTRE": "fsx_lustre",
        "OPENZFS": "fsx_openzfs",
        "WINDOWS": "fsx_windows",
    }[file_system_type]
    tags = [
        {
            "Key": "astrolift.io/resource-token",
            "Value": f"platform-steadymd-triage-prod-shared-{variant}",
        },
    ]
    if managed:
        tags += [
            {"Key": "astrolift.io/managed-by", "Value": "platform"},
            {"Key": "astrolift.io/organization", "Value": "steadymd"},
            {"Key": "astrolift.io/app", "Value": "triage"},
        ]
    return {
        "FileSystemId": FS_ID,
        "FileSystemType": file_system_type,
        "Lifecycle": lifecycle,
        "DNSName": f"{FS_ID}.fsx.us-west-2.amazonaws.com",
        "ResourceARN": f"arn:aws:fsx:us-west-2:123456789012:file-system/{FS_ID}",
        "Tags": tags,
        key: engine,
        "AdministrativeActions": [],
    }


def _client(file_system_type: str, *, existing: bool = False, managed: bool = True) -> MagicMock:
    client = MagicMock()
    store = {
        "exists": existing,
        "fs": _file_system(file_system_type, managed=managed),
        "backups": {},
    }
    client.store = store

    def describe_file_systems(**request):
        if not store["exists"]:
            if request.get("FileSystemIds"):
                raise _error("FileSystemNotFound", "DescribeFileSystems")
            return {"FileSystems": []}
        return {"FileSystems": [dict(store["fs"])]}

    def create_file_system(**request):
        store["exists"] = True
        fs = _file_system(file_system_type)
        fs["Tags"] = list(request["Tags"])
        fs["SubnetIds"] = list(request["SubnetIds"])
        key = {
            "LUSTRE": "LustreConfiguration",
            "OPENZFS": "OpenZFSConfiguration",
            "WINDOWS": "WindowsConfiguration",
        }[file_system_type]
        fs[key].update(request.get(key) or {})
        store["fs"] = fs
        return {"FileSystem": dict(fs)}

    def tag_resource(**request):
        tags = {tag["Key"]: tag["Value"] for tag in store["fs"]["Tags"]}
        tags.update({tag["Key"]: tag["Value"] for tag in request["Tags"]})
        store["fs"]["Tags"] = [{"Key": key, "Value": value} for key, value in tags.items()]
        return {}

    def update_file_system(**request):
        store["fs"]["StorageCapacity"] = request.get(
            "StorageCapacity",
            store["fs"].get("StorageCapacity", 64),
        )
        return {"FileSystem": dict(store["fs"])}

    def delete_file_system(**_request):
        store["exists"] = False
        return {"FileSystemId": FS_ID, "Lifecycle": "DELETING"}

    def create_backup(**_request):
        backup = {
            "BackupId": BACKUP_ID,
            "Lifecycle": "AVAILABLE",
            "CreationTime": datetime(2026, 8, 14, tzinfo=UTC),
            "FileSystem": dict(store["fs"]),
        }
        store["backups"][BACKUP_ID] = backup
        return {"Backup": dict(backup)}

    def describe_backups(**request):
        backups = list(store["backups"].values())
        if request.get("BackupIds"):
            backups = [backup for backup in backups if backup["BackupId"] in request["BackupIds"]]
            if not backups:
                raise _error("BackupNotFound", "DescribeBackups")
        return {"Backups": [dict(backup) for backup in backups]}

    def create_file_system_from_backup(**request):
        fs = _file_system(file_system_type)
        fs["FileSystemId"] = RESTORED_FS_ID
        fs["ResourceARN"] = f"arn:aws:fsx:us-west-2:123456789012:file-system/{RESTORED_FS_ID}"
        fs["Tags"] = list(request["Tags"])
        store["fs"] = fs
        store["exists"] = True
        return {"FileSystem": dict(fs)}

    client.describe_file_systems.side_effect = describe_file_systems
    client.create_file_system.side_effect = create_file_system
    client.tag_resource.side_effect = tag_resource
    client.update_file_system.side_effect = update_file_system
    client.delete_file_system.side_effect = delete_file_system
    client.create_backup.side_effect = create_backup
    client.describe_backups.side_effect = describe_backups
    client.create_file_system_from_backup.side_effect = create_file_system_from_backup
    client.describe_volumes.return_value = {
        "Volumes": [
            {
                "VolumeId": ROOT_VOLUME_ID,
                "OpenZFSConfiguration": {"VolumePath": "/fsx"},
            },
        ],
    }
    return client


@pytest.mark.parametrize(
    ("driver_class", "file_system_type", "config", "engine_key"),
    [
        (FSxLustreDriver, "LUSTRE", {}, "LustreConfiguration"),
        (FSxOpenZFSDriver, "OPENZFS", {}, "OpenZFSConfiguration"),
        (
            FSxWindowsDriver,
            "WINDOWS",
            {"file_system": {"WindowsConfiguration": {"ActiveDirectoryId": "d-1234567890"}}},
            "WindowsConfiguration",
        ),
    ],
)
def test_provision_creates_each_encrypted_fsx_variant(driver_class, file_system_type, config, engine_key):
    client = _client(file_system_type)
    driver = driver_class(config=_config(kms_key_id="alias/platform-fsx"), client=client)

    result = driver.provision(_spec(config=config))

    assert result.ok and result.ready and result.handle == f"filesystem/{FS_ID}"
    request = client.create_file_system.call_args.kwargs
    _validate("CreateFileSystem", request)
    assert request["FileSystemType"] == file_system_type
    assert request["KmsKeyId"] == "alias/platform-fsx"
    assert request[engine_key]
    assert len(request["SubnetIds"]) == 1
    assert any(tag["Key"] == "astrolift.io/managed_service_id" for tag in request["Tags"])


def test_provision_is_idempotent_and_refuses_external_collision():
    managed = _client("LUSTRE", existing=True)
    driver = FSxLustreDriver(config=_config(), client=managed)
    assert driver.provision(_spec()).ok
    managed.create_file_system.assert_not_called()

    external = _client("LUSTRE", existing=True, managed=False)
    result = FSxLustreDriver(config=_config(), client=external).provision(_spec())
    assert not result.ok and "not owned" in result.message
    external.create_file_system.assert_not_called()


def test_native_fragments_are_validated_and_platform_fields_win():
    client = _client("OPENZFS")
    driver = FSxOpenZFSDriver(config=_config(), client=client)
    result = driver.provision(
        _spec(
            size="custom",
            config={
                "file_system": {
                    "FileSystemType": "WINDOWS",
                    "StorageCapacity": 512,
                    "StorageType": "SSD",
                    "OpenZFSConfiguration": {
                        "DeploymentType": "SINGLE_AZ_2",
                        "ThroughputCapacity": 128,
                    },
                },
            },
        ),
    )
    assert result.ok
    request = client.create_file_system.call_args.kwargs
    assert request["FileSystemType"] == "OPENZFS"
    assert request["StorageCapacity"] == 512
    assert request["OpenZFSConfiguration"]["ThroughputCapacity"] == 128


def test_native_fragments_cannot_mix_fsx_engines():
    driver = FSxOpenZFSDriver(config=_config(), client=_client("OPENZFS"))
    result = driver.provision(
        _spec(
            config={
                "file_system": {
                    "WindowsConfiguration": {
                        "ActiveDirectoryId": "d-1234567890",
                        "ThroughputCapacity": 32,
                    },
                },
            },
        ),
    )
    assert not result.ok and "cannot contain engine configuration" in result.message


def test_lustre_scratch_uses_aws_owned_encryption_and_rejects_explicit_cmek():
    client = _client("LUSTRE")
    driver = FSxLustreDriver(config=_config(kms_key_id="alias/platform-fsx"), client=client)
    scratch = {
        "file_system": {
            "LustreConfiguration": {"DeploymentType": "SCRATCH_2"},
        },
    }
    assert driver.provision(_spec(config=scratch)).ok
    assert "KmsKeyId" not in client.create_file_system.call_args.kwargs

    invalid = FSxLustreDriver(config=_config(), client=_client("LUSTRE")).provision(
        _spec(
            config={
                "file_system": {
                    "KmsKeyId": "alias/not-supported",
                    "LustreConfiguration": {"DeploymentType": "SCRATCH_2"},
                },
            },
        ),
    )
    assert not invalid.ok and "scratch deployments cannot use KmsKeyId" in invalid.message


@pytest.mark.parametrize("field", ["Password", "ServiceAccountPassword", "FsxAdminPassword"])
def test_plaintext_passwords_are_rejected_recursively(field):
    driver = FSxWindowsDriver(config=_config(), client=_client("WINDOWS"))
    result = driver.provision(
        _spec(
            config={
                "file_system": {
                    "WindowsConfiguration": {
                        "SelfManagedActiveDirectoryConfiguration": {
                            "DomainName": "corp.example.com",
                            "DnsIps": ["10.0.0.10"],
                            field: "plaintext",
                        },
                    },
                },
            },
        ),
    )
    assert not result.ok and "plaintext password" in result.message


def test_windows_self_managed_ad_materializes_only_a_secret_reference():
    client = _client("WINDOWS")
    secrets = MagicMock()
    secrets.get_secret_value.return_value = {"SecretString": '{"password":"Not-Actually-Secret-123!"}'}
    driver = FSxWindowsDriver(config=_config(), client=client, secrets_client=secrets)
    source = {
        "file_system": {
            "WindowsConfiguration": {
                "SelfManagedActiveDirectoryConfiguration": {
                    "DomainName": "corp.example.com",
                    "DnsIps": ["10.0.0.10", "10.0.1.10"],
                    "UserName": "fsx-service",
                },
            },
        },
        "active_directory_password_secret_ref": "astrolift/fsx/ad#password",
    }
    result = driver.provision(_spec(config=source))
    assert result.ok
    ad = client.create_file_system.call_args.kwargs["WindowsConfiguration"]["SelfManagedActiveDirectoryConfiguration"]
    assert ad["Password"] == "Not-Actually-Secret-123!"
    assert "Password" not in source["file_system"]["WindowsConfiguration"]["SelfManagedActiveDirectoryConfiguration"]
    secrets.get_secret_value.assert_called_once_with(SecretId="astrolift/fsx/ad")


def test_windows_ad_secret_reference_cannot_be_silently_unused():
    driver = FSxWindowsDriver(config=_config(), client=_client("WINDOWS"))
    result = driver.provision(
        _spec(
            config={
                "file_system": {"WindowsConfiguration": {"ActiveDirectoryId": "d-1234567890"}},
                "active_directory_password_secret_ref": "astrolift/fsx/ad#password",
            },
        ),
    )
    assert not result.ok and "requires file_system.WindowsConfiguration" in result.message


def test_lustre_efa_requires_an_explicit_compatible_security_group():
    driver = FSxLustreDriver(config=_config(), client=_client("LUSTRE"))
    config = {
        "file_system": {
            "StorageCapacity": 4800,
            "LustreConfiguration": {
                "DeploymentType": "PERSISTENT_2",
                "EfaEnabled": True,
            },
        },
    }
    blocked = driver.provision(_spec(config=config))
    assert not blocked.ok and "EFA-compatible security_group_ids" in blocked.message
    config["security_group_ids"] = [GROUPS[0]]
    assert driver.provision(_spec(config=config)).ok


def test_windows_managed_domain_join_secret_arn_never_reads_secret_value():
    client = _client("WINDOWS")
    secrets = MagicMock()
    driver = FSxWindowsDriver(config=_config(), client=client, secrets_client=secrets)
    result = driver.provision(
        _spec(
            config={
                "file_system": {
                    "WindowsConfiguration": {
                        "SelfManagedActiveDirectoryConfiguration": {
                            "DomainName": "corp.example.com",
                            "DnsIps": ["10.0.0.10"],
                            "DomainJoinServiceAccountSecret": (
                                "arn:aws:secretsmanager:us-west-2:123456789012:secret:fsx-ad-AbCdEf"
                            ),
                        },
                    },
                },
            },
        ),
    )
    assert result.ok
    secrets.get_secret_value.assert_not_called()


def test_windows_multi_az_requires_two_subnets_and_sets_preferred():
    client = _client("WINDOWS")
    driver = FSxWindowsDriver(config=_config(), client=client)
    config = {
        "file_system": {
            "WindowsConfiguration": {
                "ActiveDirectoryId": "d-1234567890",
                "DeploymentType": "MULTI_AZ_1",
            },
        },
    }
    assert driver.provision(_spec(config=config)).ok
    request = client.create_file_system.call_args.kwargs
    assert request["SubnetIds"] == list(SUBNETS[:2])
    assert request["WindowsConfiguration"]["PreferredSubnetId"] == SUBNETS[0]

    too_few = FSxWindowsDriver(config=_config(subnet_ids=(SUBNETS[0],)), client=_client("WINDOWS"))
    result = too_few.provision(_spec(config=config))
    assert not result.ok and "2 distinct subnet" in result.message


def test_openzfs_preferred_subnet_selects_the_native_single_subnet():
    client = _client("OPENZFS")
    driver = FSxOpenZFSDriver(config=_config(), client=client)
    result = driver.provision(
        _spec(
            config={
                "file_system": {
                    "OpenZFSConfiguration": {
                        "DeploymentType": "MULTI_AZ_1",
                        "ThroughputCapacity": 320,
                        "PreferredSubnetId": SUBNETS[1],
                    },
                },
            },
        ),
    )
    assert result.ok
    assert client.create_file_system.call_args.kwargs["SubnetIds"] == [SUBNETS[1]]


def test_update_is_native_validated_and_plan_hash_is_idempotent():
    client = _client("OPENZFS", existing=True)
    driver = FSxOpenZFSDriver(config=_config(), client=client)
    spec = UpdateSpec(
        f"filesystem/{FS_ID}",
        config={
            "file_system_update": {
                "StorageCapacity": 128,
                "OpenZFSConfiguration": {"ThroughputCapacity": 128},
            },
        },
    )
    assert driver.update(spec).ok
    request = client.update_file_system.call_args.kwargs
    _validate("UpdateFileSystem", request)
    assert request["FileSystemId"] == FS_ID
    assert driver.update(spec).ok
    client.update_file_system.assert_called_once()


def test_size_only_update_scales_storage_and_is_idempotent():
    client = _client("OPENZFS", existing=True)
    client.store["fs"]["StorageCapacity"] = 64
    driver = FSxOpenZFSDriver(config=_config(), client=client)
    spec = UpdateSpec(f"filesystem/{FS_ID}", size="medium")
    assert driver.update(spec).ok
    assert client.update_file_system.call_args.kwargs["StorageCapacity"] == 256
    assert driver.update(spec).ok
    client.update_file_system.assert_called_once()


@pytest.mark.parametrize(
    ("driver_class", "file_system_type", "protocol", "source_suffix"),
    [
        (FSxLustreDriver, "LUSTRE", "lustre", "@tcp:/abc123mv"),
        (FSxOpenZFSDriver, "OPENZFS", "nfs4.1", ":/fsx"),
        (FSxWindowsDriver, "WINDOWS", "smb3", "\\share"),
    ],
)
def test_binding_is_portable_and_protocol_specific(driver_class, file_system_type, protocol, source_suffix):
    client = _client(file_system_type, existing=True)
    driver = driver_class(config=_config(), client=client)
    config = (
        {
            "mount_username_secret_ref": "astrolift/fsx/client#username",
            "mount_password_secret_ref": "astrolift/fsx/client#password",
        }
        if file_system_type == "WINDOWS"
        else None
    )
    binding = driver.binding(ServiceHandle(f"filesystem/{FS_ID}"), config)
    assert binding.env_vars["FILESYSTEM_PROTOCOL"].literal == protocol
    assert binding.env_vars["FILESYSTEM_MOUNT_SOURCE"].literal.endswith(source_suffix)
    assert binding.env_vars["FSX_FILE_SYSTEM_TYPE"].literal == file_system_type
    assert binding.env_vars["FILESYSTEM_TLS"].literal == "true"
    volume = binding.pod_volume_mounts[0]
    assert volume.protocol == protocol
    assert (
        volume.csi_driver
        == {
            "LUSTRE": "fsx.csi.aws.com",
            "OPENZFS": "nfs.csi.k8s.io",
            "WINDOWS": "smb.csi.k8s.io",
        }[file_system_type]
    )


def test_windows_binding_can_reference_mount_credentials_without_reading_them():
    client = _client("WINDOWS", existing=True)
    driver = FSxWindowsDriver(config=_config(), client=client)
    binding = driver.binding(
        ServiceHandle(f"filesystem/{FS_ID}"),
        {
            "mount_username_secret_ref": "astrolift/fsx/client#username",
            "mount_password_secret_ref": "astrolift/fsx/client#password",
        },
    )
    assert binding.env_vars["FILESYSTEM_USERNAME"].secret_ref == "astrolift/fsx/client#username"
    assert binding.env_vars["FILESYSTEM_PASSWORD"].secret_ref == "astrolift/fsx/client#password"
    volume = binding.pod_volume_mounts[0]
    assert volume.volume_attributes == {
        "source": f"//{FS_ID}.fsx.us-west-2.amazonaws.com/share",
    }
    assert volume.secret_refs == {
        "username": "astrolift/fsx/client#username",
        "password": "astrolift/fsx/client#password",
    }


def test_windows_binding_refuses_unmountable_credentials() -> None:
    driver = FSxWindowsDriver(config=_config(), client=_client("WINDOWS", existing=True))

    with pytest.raises(ManagedServiceError, match="workload attachment requires"):
        driver.binding(ServiceHandle(f"filesystem/{FS_ID}"))


def test_status_includes_administrative_action_state_and_failures():
    client = _client("LUSTRE", existing=True)
    driver = FSxLustreDriver(config=_config(), client=client)
    client.store["fs"]["AdministrativeActions"] = [{"Status": "IN_PROGRESS"}]
    assert driver.status(ServiceHandle(f"filesystem/{FS_ID}")).state == "updating"
    client.store["fs"]["AdministrativeActions"] = [{"Status": "FAILED"}]
    assert driver.status(ServiceHandle(f"filesystem/{FS_ID}")).state == "error"
    client.store["exists"] = False
    assert driver.status(ServiceHandle(f"filesystem/{FS_ID}")).state == "deprovisioned"


@pytest.mark.parametrize(
    ("driver_class", "file_system_type", "target_config"),
    [
        (FSxLustreDriver, "LUSTRE", {}),
        (FSxOpenZFSDriver, "OPENZFS", {}),
        (
            FSxWindowsDriver,
            "WINDOWS",
            {"restore_file_system": {"WindowsConfiguration": {"ActiveDirectoryId": "d-1234567890"}}},
        ),
    ],
)
def test_snapshot_and_restore_use_native_fsx_backups(driver_class, file_system_type, target_config):
    client = _client(file_system_type, existing=True)
    driver = driver_class(config=_config(), client=client)
    snapshot = driver.snapshot(ServiceHandle(f"filesystem/{FS_ID}"))
    assert snapshot.snapshot_id == BACKUP_ID
    assert snapshot.created_at.startswith("2026-08-14")
    client.store["exists"] = False
    result = driver.restore(snapshot, _spec(config=target_config))
    assert result.ok and result.handle == f"filesystem/{RESTORED_FS_ID}"
    _validate("CreateFileSystemFromBackup", client.create_file_system_from_backup.call_args.kwargs)


def test_restore_refuses_a_backup_from_another_fsx_engine():
    client = _client("OPENZFS", existing=True)
    driver = FSxOpenZFSDriver(config=_config(), client=client)
    snapshot = driver.snapshot(ServiceHandle(f"filesystem/{FS_ID}"))
    client.store["backups"][BACKUP_ID]["FileSystem"]["FileSystemType"] = "WINDOWS"
    client.store["exists"] = False
    result = driver.restore(snapshot, _spec())
    assert not result.ok and "not OPENZFS" in result.message


def test_restore_does_not_adopt_the_ordinary_provisioned_filesystem():
    client = _client("OPENZFS", existing=True)
    driver = FSxOpenZFSDriver(config=_config(), client=client)
    snapshot = driver.snapshot(ServiceHandle(f"filesystem/{FS_ID}"))
    result = driver.restore(snapshot, _spec())
    assert result.ok and result.handle == f"filesystem/{RESTORED_FS_ID}"
    restore_request = client.create_file_system_from_backup.call_args.kwargs
    assert restore_request["ClientRequestToken"] != "platform-steadymd-triage-prod-shared-fsx_openzfs"


def test_lustre_scratch_refuses_backup_and_safe_delete():
    client = _client("LUSTRE", existing=True)
    client.store["fs"]["LustreConfiguration"]["DeploymentType"] = "SCRATCH_2"
    driver = FSxLustreDriver(config=_config(deletion_protection_default=False), client=client)
    with pytest.raises(ManagedServiceError, match="do not support backups"):
        driver.snapshot(ServiceHandle(f"filesystem/{FS_ID}"))
    result = driver.deprovision(DeprovisionSpec(f"filesystem/{FS_ID}"))
    assert not result.ok and result.errors == ["retained_filesystem_data_requires_delete_data"]
    assert driver.deprovision(DeprovisionSpec(f"filesystem/{FS_ID}"), delete_data=True).ok
    assert client.delete_file_system.call_args.kwargs["LustreConfiguration"]["SkipFinalBackup"] is True


def test_deprovision_respects_guard_and_final_backup_policy():
    client = _client("OPENZFS", existing=True)
    driver = FSxOpenZFSDriver(config=_config(), client=client)
    handle = f"filesystem/{FS_ID}"
    blocked = driver.deprovision(DeprovisionSpec(handle))
    assert not blocked.ok and blocked.errors == ["deletion_protection_enabled"]

    retained = driver.deprovision(DeprovisionSpec(handle), force_destroy=True)
    assert retained.ok
    request = client.delete_file_system.call_args.kwargs
    _validate("DeleteFileSystem", request)
    assert request["OpenZFSConfiguration"]["SkipFinalBackup"] is False
    assert request["OpenZFSConfiguration"]["Options"] == ["DELETE_CHILD_VOLUMES_AND_SNAPSHOTS"]


def test_deprovision_refuses_external_resource_even_when_forced():
    client = _client("WINDOWS", existing=True, managed=False)
    driver = FSxWindowsDriver(config=_config(), client=client)
    result = driver.deprovision(
        DeprovisionSpec(f"filesystem/{FS_ID}"),
        delete_data=True,
        force_destroy=True,
    )
    assert not result.ok and result.errors == ["external_resource_collision"]
    client.delete_file_system.assert_not_called()


def test_networking_pinned_values_short_circuit_cloud_discovery():
    cluster = SimpleNamespace(
        provider_config={
            "fsx_openzfs_subnet_ids": ["subnet-a"],
            "fsx_openzfs_security_group_ids": ["sg-a"],
        },
    )
    assert ensure_fsx_networking(cluster, region="us-west-2", variant="fsx_openzfs") == (
        ["subnet-a"],
        ["sg-a"],
    )


@pytest.mark.parametrize(
    ("variant", "expected"),
    [
        ("fsx_lustre", {("tcp", 988, 988), ("tcp", 1018, 1023)}),
        ("fsx_openzfs", {("tcp", 2049, 2049), ("udp", 20001, 20003)}),
        ("fsx_windows", {("tcp", 445, 445)}),
    ],
)
def test_networking_discovers_private_subnets_and_protocol_rules(variant, expected):
    ec2 = MagicMock()
    eks = MagicMock()
    cluster = SimpleNamespace(slug="prod", provider_config={}, auth_config={})
    eks.describe_cluster.return_value = {
        "cluster": {"resourcesVpcConfig": {"vpcId": "vpc-12345678"}},
    }
    ec2.describe_vpcs.return_value = {"Vpcs": [{"CidrBlock": "10.0.0.0/16"}]}
    ec2.describe_subnets.return_value = {
        "Subnets": [
            {"SubnetId": SUBNETS[0], "AvailabilityZone": "us-west-2a", "MapPublicIpOnLaunch": False},
            {"SubnetId": SUBNETS[1], "AvailabilityZone": "us-west-2b", "MapPublicIpOnLaunch": False},
        ],
    }
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    ec2.create_security_group.return_value = {"GroupId": GROUPS[0]}

    subnets, groups = ensure_fsx_networking(
        cluster,
        region="us-west-2",
        variant=variant,
        clients=(ec2, eks),
    )
    assert subnets == list(SUBNETS[:2]) and groups == list(GROUPS)
    rules = {
        (permission["IpProtocol"], permission["FromPort"], permission["ToPort"])
        for call in ec2.authorize_security_group_ingress.call_args_list
        for permission in call.kwargs["IpPermissions"]
    }
    assert expected <= rules


def test_networking_converges_remaining_rules_when_one_rule_already_exists():
    ec2 = MagicMock()
    eks = MagicMock()
    cluster = SimpleNamespace(slug="prod", provider_config={}, auth_config={})
    eks.describe_cluster.return_value = {
        "cluster": {"resourcesVpcConfig": {"vpcId": "vpc-12345678"}},
    }
    ec2.describe_vpcs.return_value = {"Vpcs": [{"CidrBlock": "10.0.0.0/16"}]}
    ec2.describe_subnets.return_value = {
        "Subnets": [
            {"SubnetId": SUBNETS[0], "AvailabilityZone": "us-west-2a", "MapPublicIpOnLaunch": False},
        ],
    }
    ec2.describe_security_groups.return_value = {"SecurityGroups": [{"GroupId": GROUPS[0]}]}
    ec2.authorize_security_group_ingress.side_effect = [
        _error("InvalidPermission.Duplicate", "AuthorizeSecurityGroupIngress"),
        {},
    ]
    ensure_fsx_networking(
        cluster,
        region="us-west-2",
        variant="fsx_lustre",
        clients=(ec2, eks),
    )
    assert ec2.authorize_security_group_ingress.call_count == 2


def test_config_plugin_cost_encryption_and_catalogue_are_wired():
    from core.cluster_observability import managed_config_for

    from _sdk.availability import MATRIX
    from _sdk.storage_encryption import POLICIES
    from aws.cost import SERVICE_CODE_BY_VARIANT
    from aws.plugin import PLUGIN

    provider_config = {
        "account_id": "123456789012",
        "fsx_subnet_ids": list(SUBNETS),
        "fsx_security_group_ids": list(GROUPS),
        "fsx_kms_key_id": "alias/platform-fsx",
    }
    cluster = SimpleNamespace(
        slug="aws-prod",
        region="us-west-2",
        auth_config={},
        provider_config=provider_config,
    )
    expected = {
        "fsx_lustre": FSxLustreDriver,
        "fsx_openzfs": FSxOpenZFSDriver,
        "fsx_windows": FSxWindowsDriver,
    }
    for variant, driver_class in expected.items():
        config = managed_config_for("aws", cluster, kind="filesystem", variant=variant)
        assert config.subnet_ids == SUBNETS and config.security_group_ids == GROUPS
        assert config.kms_key_id == "alias/platform-fsx"
        assert PLUGIN.managed_service_drivers[("filesystem", variant)] is driver_class
        assert SERVICE_CODE_BY_VARIANT[("filesystem", variant)] == "AmazonFSx"
        assert POLICIES[("aws", "filesystem", variant)].cmek_supported
        entry = next(item for item in MATRIX.managed_services if item.plugin_id == "aws" and item.variant == variant)
        assert entry.status == "preview"
        assert "FILESYSTEM_MOUNT_SOURCE" in entry.binding_envs


def test_a_platform_filesystem_of_another_org_is_not_adopted():
    """Platform-made is not enough; it must be this service's (#1961)."""
    client = _client("LUSTRE", existing=True)
    client.store["fs"]["Tags"] = [
        {"Key": t["Key"], "Value": "globex" if t["Key"] == "astrolift.io/organization" else t["Value"]}
        for t in client.store["fs"]["Tags"]
    ]
    driver = FSxLustreDriver(config=_config(), client=client)

    result = driver.provision(_spec())

    assert not result.ok and "refusing to adopt" in result.message
    client.tag_resource.assert_not_called()
