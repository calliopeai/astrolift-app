"""EFS native filesystem/access-point/mount identity and no-effect refusals."""

from __future__ import annotations

import dataclasses
from unittest.mock import patch

import pytest

from _sdk.managed_service import DeprovisionSpec, ServiceHandle, UpdateSpec
from aws.managed.filesystem_efs import EFSDriver, _handle, _parse_resource, _token
from tests.aws._efs_native_2032 import OTHER_ID, SERVICE_ID, native_efs, no_effects, spec


@pytest.fixture
def cloud():
    with native_efs() as state:
        yield state


def provision(cloud, **kwargs):
    result = cloud.driver.provision(spec(cloud, **kwargs))
    assert result.ok, result
    return result.handle


def metadata(cloud, handle):
    fs, ap = _parse_resource(handle)
    return (
        cloud.api.describe_file_systems(FileSystemId=fs)["FileSystems"][0],
        cloud.api.describe_access_points(AccessPointId=ap)["AccessPoints"][0],
    )


@pytest.mark.parametrize("collision", ["joined", "long"])
def test_distinct_orgs_create_distinct_filesystems_access_points_and_mounts(cloud, collision):
    first = spec(cloud, organization_slug="alpha-beta", app_slug="gamma" + ("x" * 120 if collision == "long" else ""))
    second = dataclasses.replace(
        first, organization_slug="alpha", app_slug="beta-" + first.app_slug, managed_service_id=OTHER_ID
    )
    assert _token("-".join((first.organization_slug, first.app_slug))) == _token(
        "-".join((second.organization_slug, second.app_slug))
    )
    results = [cloud.driver.provision(s) for s in (first, second)]
    assert all(r.ok for r in results), results
    assert results[0].handle != results[1].handle
    for s, r in zip((first, second), results, strict=True):
        fs, ap = metadata(cloud, r.handle)
        for row, token_key in ((fs, "CreationToken"), (ap, "ClientToken")):
            assert s.managed_service_id.replace("-", "") in row[token_key]
            assert row["OwnerId"] == cloud.cfg.account_id
            assert {t["Key"]: t["Value"] for t in row["Tags"]}[
                "astrolift.io/managed_service_id"
            ] == s.managed_service_id
        assert ap["FileSystemId"] == fs["FileSystemId"]
        mounts = cloud.api.describe_mount_targets(FileSystemId=fs["FileSystemId"])["MountTargets"]
        assert len(mounts) == 1 and mounts[0]["FileSystemId"] == fs["FileSystemId"]
        assert (
            cloud.driver.provision(dataclasses.replace(s, organization_slug="renamed", app_slug="renamed")).handle
            == r.handle
        )
    assert len(cloud.api.describe_file_systems()["FileSystems"]) == 2


def test_exact_recorded_legacy_pair_survives_labels_and_prefix_with_full_lifecycle(cloud):
    original = spec(cloud)
    token = "Legacy-EFS-token-2032"
    fs = cloud.api.create_file_system(**cloud.driver._create_file_system_request(token, original))
    point = cloud.api.create_access_point(
        ClientToken=_token(token + "-access-point"),
        FileSystemId=fs["FileSystemId"],
        Tags=fs["Tags"],
        PosixUser={"Uid": 1000, "Gid": 1000},
        RootDirectory={"Path": "/legacy"},
    )
    handle = _handle(fs["FileSystemId"], point["AccessPointId"])
    driver = EFSDriver(config=dataclasses.replace(cloud.cfg, creation_token_prefix="changed"), client=cloud.api)
    with (
        patch.object(cloud.api, "create_file_system", wraps=cloud.api.create_file_system) as create_fs,
        patch.object(cloud.api, "create_access_point", wraps=cloud.api.create_access_point) as create_ap,
    ):
        result = driver.provision(
            dataclasses.replace(
                original, recorded_handle=handle, organization_slug="new", service_handle_hint="changed"
            )
        )
        assert result.ok and result.handle == handle, result
        create_fs.assert_not_called()
        create_ap.assert_not_called()
    binding = driver.binding(ServiceHandle(handle, managed_service_id=SERVICE_ID))
    assert binding.env_vars["EFS_ACCESS_POINT_ID"].literal == point["AccessPointId"]
    update = driver.update(
        UpdateSpec(
            handle, config={"lifecycle_policies": [{"TransitionToIA": "AFTER_30_DAYS"}]}, managed_service_id=SERVICE_ID
        )
    )
    assert update.ok, update
    delete = driver.deprovision(
        DeprovisionSpec(handle, managed_service_id=SERVICE_ID), delete_data=True, force_destroy=True
    )
    assert delete.ok, delete
    assert cloud.api.describe_file_systems()["FileSystems"] == []


def test_guid_fresh_recovery_survives_operator_creation_prefix_change(cloud):
    handle = provision(cloud)
    driver = EFSDriver(config=dataclasses.replace(cloud.cfg, creation_token_prefix="changed"), client=cloud.api)
    with patch.object(cloud.api, "create_file_system", wraps=cloud.api.create_file_system) as create:
        result = driver.provision(spec(cloud, organization_slug="renamed"))
        assert result.ok and result.handle == handle, result
        create.assert_not_called()


@pytest.mark.parametrize("operation", ["provision", "update", "delete", "binding"])
@pytest.mark.parametrize("target", ["root", "access_point"])
def test_foreign_root_or_child_refuses_before_any_effect_including_force_delete(cloud, target, operation):
    handle = provision(cloud)
    fs, ap = metadata(cloud, handle)
    # Keep the other parent's ownership consistent to isolate the single fence.
    cloud.api.tag_resource(
        ResourceId=fs["FileSystemId"] if target == "root" else ap["AccessPointId"],
        Tags=[{"Key": "astrolift.io/managed_service_id", "Value": OTHER_ID}],
    )
    stack, spies = no_effects(cloud)
    with stack:
        if operation == "provision":
            result = cloud.driver.provision(spec(cloud, recorded_handle=handle))
        elif operation == "update":
            result = cloud.driver.update(
                UpdateSpec(handle, config={"backup_policy": "ENABLED"}, managed_service_id=SERVICE_ID)
            )
        elif operation == "delete":
            result = cloud.driver.deprovision(
                DeprovisionSpec(handle, managed_service_id=SERVICE_ID), delete_data=True, force_destroy=True
            )
        else:
            with pytest.raises(Exception, match="ownership"):
                cloud.driver.binding(ServiceHandle(handle, managed_service_id=SERVICE_ID))
            result = None
        if result:
            assert not result.ok, result
            if operation in {"update", "delete"}:
                assert not result.retryable and result.errors == ["ownership_refused"]
        for spy in spies:
            spy.assert_not_called()
    assert len(cloud.api.describe_file_systems()["FileSystems"]) == 1


@pytest.mark.parametrize("target", ["root", "access_point"])
def test_missing_recorded_root_or_access_point_never_creates_replacement(cloud, target):
    handle = provision(cloud)
    fs, ap = metadata(cloud, handle)
    if target == "access_point":
        cloud.api.delete_access_point(AccessPointId=ap["AccessPointId"])
    else:
        for m in cloud.api.describe_mount_targets(FileSystemId=fs["FileSystemId"])["MountTargets"]:
            cloud.api.delete_mount_target(MountTargetId=m["MountTargetId"])
        cloud.api.delete_access_point(AccessPointId=ap["AccessPointId"])
        cloud.api.delete_file_system(FileSystemId=fs["FileSystemId"])
    stack, spies = no_effects(cloud)
    with stack:
        assert not cloud.driver.provision(spec(cloud, recorded_handle=handle)).ok
        assert not cloud.driver.update(UpdateSpec(handle, managed_service_id=SERVICE_ID)).ok
        for spy in spies:
            spy.assert_not_called()


@pytest.mark.parametrize("target", ["access_point", "mount", "replication"])
def test_paged_foreign_child_or_source_metadata_refuses_before_any_parent_effect(cloud, target):
    handle = provision(cloud)
    fs, ap = metadata(cloud, handle)
    if target == "access_point":
        wrong = dict(ap, FileSystemId="fs-00000000")
        method = "describe_access_points"
        responses = [{"AccessPoints": [ap], "NextToken": "alpha"}, {"AccessPoints": [wrong]}]
    elif target == "mount":
        mount = cloud.api.describe_mount_targets(FileSystemId=fs["FileSystemId"])["MountTargets"][0]
        wrong = dict(mount, FileSystemId="fs-00000000")
        method = "describe_mount_targets"
        responses = [{"MountTargets": [mount], "NextMarker": "alpha"}, {"MountTargets": [wrong]}]
    else:
        method = "describe_replication_configurations"
        responses = [
            {
                "Replications": [
                    {
                        "SourceFileSystemId": "fs-00000000",
                        "SourceFileSystemArn": fs["FileSystemArn"],
                        "SourceFileSystemRegion": cloud.cfg.region,
                        "Destinations": [{"FileSystemId": "fs-99999999", "Region": "us-west-2"}],
                    }
                ]
            }
        ]
    stack, spies = no_effects(cloud)
    with stack, patch.object(cloud.api, method, side_effect=responses):
        result = cloud.driver.update(
            UpdateSpec(handle, config={"backup_policy": "ENABLED"}, managed_service_id=SERVICE_ID)
        )
        assert not result.ok and not result.retryable, result
        for spy in spies:
            spy.assert_not_called()


@pytest.mark.parametrize("target", ["filesystems", "access_points", "mount_targets", "replications"])
def test_native_pagination_cycles_are_bounded_before_any_effect(cloud, target):
    handle = provision(cloud)
    key, cursor, method = {
        "filesystems": ("FileSystems", "NextMarker", "describe_file_systems"),
        "access_points": ("AccessPoints", "NextToken", "describe_access_points"),
        "mount_targets": ("MountTargets", "NextMarker", "describe_mount_targets"),
        "replications": ("Replications", "NextToken", "describe_replication_configurations"),
    }[target]
    pages = [{key: [], cursor: "alpha"}, {key: [], cursor: "beta"}, {key: [], cursor: "alpha"}]
    stack, spies = no_effects(cloud)
    with stack, patch.object(cloud.api, method, side_effect=pages) as read:
        result = (
            cloud.driver.provision(spec(cloud))
            if target == "filesystems"
            else cloud.driver.update(
                UpdateSpec(handle, config={"backup_policy": "ENABLED"}, managed_service_id=SERVICE_ID)
            )
        )
        assert not result.ok and "pagination cursor" in result.message
        assert read.call_count == 3
        for spy in spies:
            spy.assert_not_called()


def test_many_owned_access_points_do_not_introduce_per_child_native_reads(cloud):
    handle = provision(cloud)
    fs, _ = metadata(cloud, handle)

    def reads():
        with patch.object(cloud.api, "describe_access_points", wraps=cloud.api.describe_access_points) as get:
            assert cloud.driver.provision(spec(cloud, recorded_handle=handle)).ok
            return get.call_count

    before = reads()
    for number in range(12):
        cloud.api.create_access_point(ClientToken=f"extra-{number}", FileSystemId=fs["FileSystemId"], Tags=fs["Tags"])
    after = reads()
    assert before == after == 4


def test_deleted_child_error_during_teardown_does_not_claim_parent_was_deleted(cloud):
    from botocore.exceptions import ClientError

    handle = provision(cloud)
    error = ClientError(
        {"Error": {"Code": "MountTargetNotFound", "Message": "missing mount target"}}, "DeleteMountTarget"
    )
    with (
        patch.object(cloud.api, "delete_mount_target", side_effect=error),
        patch.object(cloud.api, "delete_file_system", wraps=cloud.api.delete_file_system) as delete,
    ):
        result = cloud.driver.deprovision(
            DeprovisionSpec(handle, managed_service_id=SERVICE_ID), delete_data=True, force_destroy=True
        )
        assert not result.ok
        delete.assert_not_called()
    assert len(cloud.api.describe_file_systems()["FileSystems"]) == 1


@pytest.mark.parametrize("target", ["root", "access_point", "replication"])
def test_native_partition_identity_cannot_be_substituted_before_effects(cloud, target):
    handle = provision(cloud)
    fs, point = metadata(cloud, handle)
    if target == "root":
        row = dict(fs, FileSystemArn=fs["FileSystemArn"].replace("arn:aws:", "arn:aws-cn:"))
        method = "describe_file_systems"
        payload = {"FileSystems": [row]}
    elif target == "access_point":
        row = dict(point, AccessPointArn=point["AccessPointArn"].replace("arn:aws:", "arn:aws-cn:"))
        method = "describe_access_points"
        payload = {"AccessPoints": [row]}
    else:
        method = "describe_replication_configurations"
        payload = {
            "Replications": [
                {
                    "SourceFileSystemId": fs["FileSystemId"],
                    "SourceFileSystemArn": fs["FileSystemArn"].replace("arn:aws:", "arn:aws-cn:"),
                    "SourceFileSystemRegion": cloud.cfg.region,
                    "Destinations": [{"FileSystemId": "fs-99999999", "Region": "us-west-2"}],
                }
            ]
        }
    stack, spies = no_effects(cloud)
    with stack, patch.object(cloud.api, method, return_value=payload):
        result = cloud.driver.update(
            UpdateSpec(handle, config={"backup_policy": "ENABLED"}, managed_service_id=SERVICE_ID)
        )
        assert not result.ok and not result.retryable and "partition" in result.message, result
        for spy in spies:
            spy.assert_not_called()


@pytest.mark.parametrize(
    "region,partition,suffix",
    [
        ("us-east-1", "aws", "amazonaws.com"),
        ("cn-north-1", "aws-cn", "amazonaws.com.cn"),
        ("us-gov-west-1", "aws-us-gov", "amazonaws.com"),
        ("us-iso-east-1", "aws-iso", "c2s.ic.gov"),
        ("us-isob-east-1", "aws-iso-b", "sc2s.sgov.gov"),
        ("eu-isoe-west-1", "aws-iso-e", "cloud.adc-e.uk"),
        ("us-isof-south-1", "aws-iso-f", "csp.hci.ic.gov"),
        ("eusc-de-east-1", "aws-eusc", "amazonaws.eu"),
    ],
)
def test_current_sdk_partition_identity_and_dns_format_have_no_commercial_fallback(cloud, region, partition, suffix):
    # Offline SDK metadata/format only; no regional availability or network
    # connectivity is claimed by this parameterized validation.
    driver = EFSDriver(config=dataclasses.replace(cloud.cfg, region=region), client=cloud.api)
    driver._identity_config()
    assert driver._partition == partition
    assert driver._dns_name("fs-12345678") == f"fs-12345678.efs.{region}.{suffix}"
    driver._validate_file_system(
        {
            "FileSystemId": "fs-12345678",
            "OwnerId": cloud.cfg.account_id,
            "FileSystemArn": (
                f"arn:{partition}:elasticfilesystem:{region}:{cloud.cfg.account_id}:file-system/fs-12345678"
            ),
        },
        "fs-12345678",
    )
