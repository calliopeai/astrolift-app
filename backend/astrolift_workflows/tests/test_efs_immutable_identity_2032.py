"""Saved EFS references reach production registry/config and native FS/AP/mount APIs."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from aws.managed.filesystem_efs import EFSDriver, _parse_resource

from astrolift_drivers.registry import PluginManifest, PluginRegistry
from astrolift_workflows.activities.managed_service_lifecycle import (
    _deprovision_sync,
    _managed_binding_for,
    _provision_sync,
    _update_sync,
)
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service
from providers.tests.aws._efs_native_2032 import native_efs, no_effects

pytestmark = pytest.mark.django_db


@pytest.fixture
def cloud(monkeypatch):
    with native_efs() as state:

        class Driver(EFSDriver):
            def __init__(self, *, config):
                super().__init__(config=config, client=state.api)

        registry = PluginRegistry()
        registry.register(
            PluginManifest(
                plugin_id="aws",
                display_name="aws",
                version="fixture",
                drivers={"managed:filesystem:efs": Driver},
            )
        )
        monkeypatch.setattr("astrolift_drivers.registry.plugins", registry)
        yield state


def service(cloud, org, app="api"):
    row = _service(org_slug=org, plugin_slug="aws", variant="efs", backend_ref="")
    row.kind, row.name, row.config = "filesystem", "files", {}
    row.registered_app.slug = app
    row.registered_app.save(update_fields=["slug"])
    row.save(update_fields=["kind", "name", "config"])
    cluster = row.app_environment.tenant_cluster
    # Actual native operator-pinned topology. Shared fallback SG naming is a
    # separately audited boundary; this fixture never attests that constructor.
    cluster.provider_config = {
        "region": cloud.cfg.region,
        "account_id": cloud.cfg.account_id,
        "efs_subnet_ids": [cloud.subnet],
        "efs_security_group_ids": [cloud.group],
        "efs_poll_delay_seconds": 0,
        "efs_max_poll_attempts": 2,
    }
    cluster.save(update_fields=["provider_config"])
    return row


def remember(row, result):
    assert result["ok"], result
    row.backend_ref = result["handle"]
    row.save(update_fields=["backend_ref"])
    return row.backend_ref


@pytest.mark.parametrize("collision", ["joined", "long"])
def test_distinct_saved_org_slug_collisions_have_distinct_root_point_and_mount(cloud, collision):
    extra = "x" * 100 if collision == "long" else ""
    first, second = (
        service(cloud, "alpha-beta", "gamma" + extra),
        service(cloud, "alpha", "beta-gamma" + extra),
    )
    handles = [remember(row, _provision_sync(row.pk)) for row in (first, second)]
    assert handles[0] != handles[1]
    for row in (first, second):
        fs_id, ap_id = _parse_resource(row.backend_ref)
        fs = cloud.api.describe_file_systems(FileSystemId=fs_id)["FileSystems"][0]
        point = cloud.api.describe_access_points(AccessPointId=ap_id)["AccessPoints"][0]
        for metadata, token in ((fs, "CreationToken"), (point, "ClientToken")):
            assert row.guid.hex in metadata[token]
            assert {t["Key"]: t["Value"] for t in metadata["Tags"]}["astrolift.io/managed_service_id"] == str(
                row.guid
            )
        assert _provision_sync(row.pk)["handle"] == row.backend_ref
    assert len(cloud.api.describe_file_systems()["FileSystems"]) == 2


def test_saved_recorded_pair_stays_literal_through_labels_binding_update_and_deletion(cloud):
    row = service(cloud, "legacy")
    handle = remember(row, _provision_sync(row.pk))
    fs_id, ap_id = _parse_resource(handle)
    row.registered_app.slug = "renamed"
    row.registered_app.save(update_fields=["slug"])
    row.name = "changed"
    row.save(update_fields=["name"])
    with (
        patch.object(cloud.api, "create_file_system", wraps=cloud.api.create_file_system) as create_fs,
        patch.object(cloud.api, "create_access_point", wraps=cloud.api.create_access_point) as create_ap,
    ):
        assert _provision_sync(row.pk)["handle"] == handle
        create_fs.assert_not_called()
        create_ap.assert_not_called()
    binding = _managed_binding_for(row)
    assert binding.env_vars["EFS_ACCESS_POINT_ID"].literal == ap_id
    row.config = {"lifecycle_policies": [{"TransitionToIA": "AFTER_30_DAYS"}]}
    row.save(update_fields=["config"])
    assert _update_sync(row.pk)["ok"]
    assert _deprovision_sync(row.pk, delete_data=True, force_destroy=True)["ok"]
    assert cloud.api.describe_file_systems()["FileSystems"] == []
    row.refresh_from_db()
    assert row.backend_ref == handle


@pytest.mark.parametrize("target", ["root", "access_point"])
def test_other_saved_org_cannot_adopt_update_or_force_delete_native_parent_tree(cloud, target):
    first, second = service(cloud, "owner"), service(cloud, "other")
    handle = remember(first, _provision_sync(first.pk))
    fs_id, ap_id = _parse_resource(handle)
    # Isolate one ownership fence; the other parent matches attempted caller.
    cloud.api.tag_resource(
        ResourceId=ap_id if target == "root" else fs_id,
        Tags=[{"Key": "astrolift.io/managed_service_id", "Value": str(second.guid)}],
    )
    second.backend_ref = handle
    second.save(update_fields=["backend_ref"])
    stack, spies = no_effects(cloud)
    with stack:
        assert not _provision_sync(second.pk)["ok"]
        update, delete = (
            _update_sync(second.pk),
            _deprovision_sync(second.pk, delete_data=True, force_destroy=True),
        )
        assert not update["ok"] and not update["retryable"]
        assert not delete["ok"] and not delete["retryable"]
        for spy in spies:
            spy.assert_not_called()
    second.refresh_from_db()
    assert second.backend_ref == handle


@pytest.mark.parametrize("target", ["root", "access_point"])
def test_saved_recorded_missing_root_or_point_never_creates_replacement(cloud, target):
    row = service(cloud, "missing")
    handle = remember(row, _provision_sync(row.pk))
    fs_id, ap_id = _parse_resource(handle)
    cloud.api.delete_access_point(AccessPointId=ap_id)
    if target == "root":
        for m in cloud.api.describe_mount_targets(FileSystemId=fs_id)["MountTargets"]:
            cloud.api.delete_mount_target(MountTargetId=m["MountTargetId"])
        cloud.api.delete_file_system(FileSystemId=fs_id)
    stack, spies = no_effects(cloud)
    with stack:
        assert not _provision_sync(row.pk)["ok"]
        assert not _update_sync(row.pk)["ok"]
        for spy in spies:
            spy.assert_not_called()
    row.refresh_from_db()
    assert row.backend_ref == handle


@pytest.mark.parametrize(
    "handle",
    [
        "topic/fs-12345678",
        "filesystem/wrong",
        "filesystem/fs-12345678/invalid",
        "filesystem/fs-12345678/fsap-12345678/extra",
    ],
)
def test_malformed_saved_reference_cannot_probe_retarget_or_delete(cloud, handle):
    row = service(cloud, "invalid")
    row.backend_ref = handle
    row.save(update_fields=["backend_ref"])
    stack, spies = no_effects(cloud)
    with (
        stack,
        patch.object(cloud.api, "describe_file_systems", wraps=cloud.api.describe_file_systems) as read,
    ):
        assert not _provision_sync(row.pk)["ok"]
        assert not _update_sync(row.pk)["ok"]
        assert not _deprovision_sync(row.pk, delete_data=True, force_destroy=True)["ok"]
        read.assert_not_called()
        for spy in spies:
            spy.assert_not_called()
