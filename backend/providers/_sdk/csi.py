"""CSI driver integration (#27).

CSI drivers expose persistent volumes as a typed interface. Astrolift
needs to know which CSI driver each cloud's cluster has installed
so the platform can:
- emit StorageClass with the right provisioner + KMS parameters
- enable volume snapshots (VolumeSnapshotClass)
- pick the right access modes (RWO / RWX / ROX) per workload

This module declares CsiDriverProfile per (plugin, csi_driver_name)
+ render_storage_class() that emits the encrypted-by-default
StorageClass workflows expect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CsiDriverProfile:
    plugin_id: str
    csi_driver_name: str
    """e.g. 'ebs.csi.aws.com', 'pd.csi.storage.gke.io',
    'disk.csi.azure.com', 'nfs.csi.k8s.io'."""

    supports_encryption: bool = False
    encryption_parameter_key: str = ""
    """Key name in StorageClass.parameters used to enable encryption.
    Empty when supports_encryption=False."""

    supports_snapshots: bool = False
    snapshot_class_driver: str = ""
    """driver field of VolumeSnapshotClass — usually identical to
    csi_driver_name but plugin-specific quirks apply."""

    supports_volume_expansion: bool = True
    access_modes: tuple[str, ...] = ("ReadWriteOnce",)

    default_volume_type: str = ""
    """Volume-type the platform stamps into the StorageClass ``type``
    parameter when it emits a default class for this profile (EBS
    ``gp3``). Empty when the platform doesn't pin a volume type for the
    provisioner."""


# Canonical profiles per cloud's recommended CSI.
PROFILES: tuple[CsiDriverProfile, ...] = (
    CsiDriverProfile(
        plugin_id="aws",
        csi_driver_name="ebs.csi.aws.com",
        supports_encryption=True,
        encryption_parameter_key="encrypted",
        supports_snapshots=True,
        snapshot_class_driver="ebs.csi.aws.com",
        access_modes=("ReadWriteOnce",),
        default_volume_type="gp3",
    ),
    CsiDriverProfile(
        plugin_id="aws",
        csi_driver_name="efs.csi.aws.com",
        supports_encryption=True,
        encryption_parameter_key="encrypted",
        supports_snapshots=False,
        access_modes=("ReadWriteOnce", "ReadWriteMany"),
    ),
    CsiDriverProfile(
        plugin_id="gcp",
        csi_driver_name="pd.csi.storage.gke.io",
        supports_encryption=True,
        encryption_parameter_key="disk-encryption-kms-key",
        supports_snapshots=True,
        snapshot_class_driver="pd.csi.storage.gke.io",
        access_modes=("ReadWriteOnce",),
    ),
    CsiDriverProfile(
        plugin_id="gcp",
        csi_driver_name="filestore.csi.storage.gke.io",
        supports_encryption=True,
        encryption_parameter_key="kms-key",
        supports_snapshots=True,
        snapshot_class_driver="filestore.csi.storage.gke.io",
        access_modes=("ReadWriteMany",),
    ),
    CsiDriverProfile(
        plugin_id="azure",
        csi_driver_name="disk.csi.azure.com",
        supports_encryption=True,
        encryption_parameter_key="diskEncryptionSetID",
        supports_snapshots=True,
        snapshot_class_driver="disk.csi.azure.com",
        access_modes=("ReadWriteOnce",),
    ),
    CsiDriverProfile(
        plugin_id="azure",
        csi_driver_name="file.csi.azure.com",
        supports_encryption=False,  # SSE on by default; no per-PV key
        encryption_parameter_key="",
        supports_snapshots=True,
        snapshot_class_driver="file.csi.azure.com",
        access_modes=("ReadWriteMany",),
    ),
    CsiDriverProfile(
        plugin_id="k8s_native",
        csi_driver_name="nfs.csi.k8s.io",
        supports_encryption=False,
        supports_snapshots=False,
        access_modes=("ReadWriteOnce", "ReadWriteMany"),
    ),
    CsiDriverProfile(
        plugin_id="k8s_native",
        csi_driver_name="rook-ceph.rbd.csi.ceph.com",
        supports_encryption=True,
        encryption_parameter_key="encrypted",
        supports_snapshots=True,
        snapshot_class_driver="rook-ceph.rbd.csi.ceph.com",
        access_modes=("ReadWriteOnce",),
    ),
    CsiDriverProfile(
        plugin_id="k8s_native",
        csi_driver_name="rook-ceph.cephfs.csi.ceph.com",
        supports_encryption=True,
        encryption_parameter_key="encrypted",
        supports_snapshots=True,
        snapshot_class_driver="rook-ceph.cephfs.csi.ceph.com",
        access_modes=("ReadWriteMany",),
    ),
)


def profile_for(
    *,
    plugin_id: str,
    csi_driver_name: str,
) -> CsiDriverProfile | None:
    for p in PROFILES:
        if p.plugin_id == plugin_id and p.csi_driver_name == csi_driver_name:
            return p
    return None


def render_storage_class(
    *,
    name: str,
    profile: CsiDriverProfile,
    encryption_key: str | None = None,
    parameters: dict[str, str] | None = None,
    reclaim_policy: str = "Retain",
    make_default: bool = False,
) -> dict[str, Any]:
    """Build a StorageClass manifest with encryption + expansion
    enabled per the profile.

    ``make_default=True`` stamps the
    ``storageclass.kubernetes.io/is-default-class`` annotation so the
    StatefulSet PVC autostamp (#1023) picks this class for claims that
    omit ``storageClassName`` — the cluster gets a working default
    (e.g. EBS gp3) instead of leaving PVCs Pending."""
    params = dict(parameters or {})
    if profile.default_volume_type and "type" not in params:
        params["type"] = profile.default_volume_type
    if profile.supports_encryption and encryption_key and profile.encryption_parameter_key:
        params[profile.encryption_parameter_key] = encryption_key
    elif profile.supports_encryption and profile.encryption_parameter_key:
        # When the profile takes a 'true' / 'false' string for
        # encryption (e.g. EBS 'encrypted'), default to 'true'
        # so PVs encrypt-at-rest by default.
        params.setdefault(profile.encryption_parameter_key, "true")
    metadata: dict[str, Any] = {
        "name": name,
        "labels": {
            "astrolift.io/managed-by": "platform",
        },
    }
    if make_default:
        metadata["annotations"] = {
            "storageclass.kubernetes.io/is-default-class": "true",
        }
    return {
        "apiVersion": "storage.k8s.io/v1",
        "kind": "StorageClass",
        "metadata": metadata,
        "provisioner": profile.csi_driver_name,
        "parameters": params,
        "reclaimPolicy": reclaim_policy,
        "volumeBindingMode": "WaitForFirstConsumer",
        "allowVolumeExpansion": profile.supports_volume_expansion,
    }


def render_volume_snapshot_class(
    *,
    name: str,
    profile: CsiDriverProfile,
    parameters: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Render a VolumeSnapshotClass when the CSI supports
    snapshots; otherwise return None."""
    if not profile.supports_snapshots:
        return None
    return {
        "apiVersion": "snapshot.storage.k8s.io/v1",
        "kind": "VolumeSnapshotClass",
        "metadata": {
            "name": name,
            "labels": {
                "astrolift.io/managed-by": "platform",
            },
        },
        "driver": profile.snapshot_class_driver,
        "deletionPolicy": "Retain",
        "parameters": dict(parameters or {}),
    }
