"""Tests for CSI driver integration (#27)."""

from __future__ import annotations

from _sdk.csi import (
    PROFILES,
    profile_for,
    render_storage_class,
    render_volume_snapshot_class,
)


def test_every_cloud_has_block_storage_profile() -> None:
    """Each cloud must ship a profile for the recommended block-
    storage CSI."""
    plugin_ids = {p.plugin_id for p in PROFILES}
    assert {"aws", "gcp", "azure", "k8s_native"} <= plugin_ids
    aws = profile_for(
        plugin_id="aws",
        csi_driver_name="ebs.csi.aws.com",
    )
    assert aws is not None
    assert aws.supports_encryption
    assert aws.supports_snapshots


def test_render_storage_class_enables_encryption_by_default() -> None:
    """Profiles that support encryption should default to
    encrypted=true even without an explicit key."""
    profile = profile_for(
        plugin_id="aws",
        csi_driver_name="ebs.csi.aws.com",
    )
    assert profile is not None
    sc = render_storage_class(
        name="astrolift-default-block",
        profile=profile,
    )
    assert sc["kind"] == "StorageClass"
    assert sc["provisioner"] == "ebs.csi.aws.com"
    assert sc["parameters"]["encrypted"] == "true"
    assert sc["allowVolumeExpansion"] is True


def test_render_storage_class_uses_explicit_kms_key() -> None:
    profile = profile_for(
        plugin_id="gcp",
        csi_driver_name="pd.csi.storage.gke.io",
    )
    assert profile is not None
    sc = render_storage_class(
        name="astrolift-default-block",
        profile=profile,
        encryption_key=("projects/p/locations/l/keyRings/k/cryptoKeys/c"),
    )
    assert sc["parameters"]["disk-encryption-kms-key"] == "projects/p/locations/l/keyRings/k/cryptoKeys/c"


def test_azure_disk_uses_disk_encryption_set_param() -> None:
    profile = profile_for(
        plugin_id="azure",
        csi_driver_name="disk.csi.azure.com",
    )
    assert profile is not None
    sc = render_storage_class(
        name="x",
        profile=profile,
        encryption_key="/subscriptions/.../diskEncryptionSets/des-1",
    )
    assert "diskEncryptionSetID" in sc["parameters"]


def test_render_volume_snapshot_class_when_supported() -> None:
    profile = profile_for(
        plugin_id="aws",
        csi_driver_name="ebs.csi.aws.com",
    )
    assert profile is not None
    snap = render_volume_snapshot_class(
        name="astrolift-snapshots",
        profile=profile,
    )
    assert snap is not None
    assert snap["kind"] == "VolumeSnapshotClass"
    assert snap["driver"] == "ebs.csi.aws.com"
    assert snap["deletionPolicy"] == "Retain"


def test_render_volume_snapshot_class_returns_none_when_unsupported() -> None:
    profile = profile_for(
        plugin_id="aws",
        csi_driver_name="efs.csi.aws.com",
    )
    assert profile is not None
    snap = render_volume_snapshot_class(
        name="x",
        profile=profile,
    )
    assert snap is None


def test_efs_supports_rwx() -> None:
    profile = profile_for(
        plugin_id="aws",
        csi_driver_name="efs.csi.aws.com",
    )
    assert profile is not None
    assert "ReadWriteMany" in profile.access_modes


def test_filestore_rwx_only() -> None:
    profile = profile_for(
        plugin_id="gcp",
        csi_driver_name="filestore.csi.storage.gke.io",
    )
    assert profile is not None
    assert profile.access_modes == ("ReadWriteMany",)


def test_storage_class_uses_wait_for_first_consumer() -> None:
    """Topology-aware scheduling = better cost + perf. All
    StorageClasses use WaitForFirstConsumer by default."""
    profile = profile_for(
        plugin_id="aws",
        csi_driver_name="ebs.csi.aws.com",
    )
    sc = render_storage_class(name="x", profile=profile)
    assert sc["volumeBindingMode"] == "WaitForFirstConsumer"


def test_unknown_csi_returns_none() -> None:
    assert (
        profile_for(
            plugin_id="aws",
            csi_driver_name="never.csi.example.com",
        )
        is None
    )


def test_render_default_gp3_storage_class() -> None:
    """make_default stamps the is-default-class annotation and the EBS
    profile pins type=gp3, so #1023's autostamp picks a working default
    (#1024)."""
    profile = profile_for(
        plugin_id="aws",
        csi_driver_name="ebs.csi.aws.com",
    )
    assert profile is not None
    sc = render_storage_class(name="gp3", profile=profile, make_default=True)
    assert sc["metadata"]["annotations"]["storageclass.kubernetes.io/is-default-class"] == "true"
    assert sc["provisioner"] == "ebs.csi.aws.com"
    assert sc["parameters"]["type"] == "gp3"
    assert sc["parameters"]["encrypted"] == "true"


def test_render_storage_class_not_default_unless_requested() -> None:
    """Without make_default the is-default-class annotation must be absent
    so we never silently override an existing cluster default."""
    profile = profile_for(
        plugin_id="aws",
        csi_driver_name="ebs.csi.aws.com",
    )
    assert profile is not None
    sc = render_storage_class(name="gp3", profile=profile)
    assert "storageclass.kubernetes.io/is-default-class" not in (sc["metadata"].get("annotations", {}))
