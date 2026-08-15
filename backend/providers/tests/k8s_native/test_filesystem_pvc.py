from __future__ import annotations

from types import SimpleNamespace

from astrolift_manifest.env_injection import envelope_keys_for

from _sdk.availability import MATRIX
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, VolumeSourceKind
from k8s_native.managed.filesystem_pvc import PVCConfig, RookCephFSDriver, StorageClassPVCDriver
from k8s_native.plugin import PLUGIN


class _ClusterDriver:
    def __init__(self, *, storage_classes=(), csi_drivers=(), provisioner=""):
        self.storage_classes = list(storage_classes)
        self.csi_drivers = list(csi_drivers)
        self.provisioner = provisioner

    def list_storage_classes(self, _cluster):
        return [SimpleNamespace(name=value, provisioner=self.provisioner) for value in self.storage_classes]

    def list_csi_drivers(self, _cluster):
        return list(self.csi_drivers)


def _spec(**overrides):
    values = {
        "organization_id": "org-1",
        "organization_slug": "acme",
        "app_id": "project-1",
        "app_slug": "payments",
        "environment_id": "cluster-1",
        "environment_name": "shared",
        "tenant_cluster_id": "cluster-1",
        "service_handle_hint": "workspace",
        "size": "medium",
        "config": {"storage_class_name": "fast-rwx"},
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def test_storage_class_driver_validates_template_without_eager_claim() -> None:
    cluster = _ClusterDriver(storage_classes=["fast-rwx"])
    driver = StorageClassPVCDriver(config=PVCConfig(cluster_driver=cluster))

    result = driver.provision(_spec())

    assert result.ok is True
    assert result.ready is True
    assert result.handle == "filesystem/cluster-1/acme-payments/pvc-payments-shared-workspace"
    assert "consumer-local claims" in result.message


def test_storage_class_driver_fails_closed_for_missing_class() -> None:
    driver = StorageClassPVCDriver(
        config=PVCConfig(cluster_driver=_ClusterDriver(storage_classes=["standard"])),
    )

    result = driver.provision(_spec())

    assert result.ok is False
    assert result.errors == ["storage_preflight_failed"]
    assert "fast-rwx" in result.message


def test_rook_driver_requires_cephfs_csi_and_emits_rwx_binding() -> None:
    cluster = _ClusterDriver(
        storage_classes=["rook-cephfs"],
        csi_drivers=["rook-ceph.cephfs.csi.ceph.com"],
        provisioner="rook-ceph.cephfs.csi.ceph.com",
    )
    driver = RookCephFSDriver(
        config=PVCConfig(
            storage_class_name="rook-cephfs",
            csi_driver="rook-ceph.cephfs.csi.ceph.com",
            default_access_modes=("ReadWriteMany",),
            cluster_driver=cluster,
        ),
    )

    provisioned = driver.provision(_spec(config={}))
    binding = driver.binding(ServiceHandle(provisioned.handle), {"mount_path": "/workspace"})

    assert provisioned.ok is True
    volume = binding.pod_volume_mounts[0]
    assert volume.source_kind is VolumeSourceKind.DYNAMIC_PVC
    assert volume.storage_class_name == "rook-cephfs"
    assert volume.csi_driver == "rook-ceph.cephfs.csi.ceph.com"
    assert volume.access_modes == ("ReadWriteMany",)
    assert volume.capacity == "10Gi"
    assert volume.mount_path == "/workspace"


def test_rook_driver_reports_missing_csi_driver() -> None:
    driver = RookCephFSDriver(
        config=PVCConfig(
            storage_class_name="rook-cephfs",
            csi_driver="rook-ceph.cephfs.csi.ceph.com",
            default_access_modes=("ReadWriteMany",),
            cluster_driver=_ClusterDriver(
                storage_classes=["rook-cephfs"],
                provisioner="rook-ceph.cephfs.csi.ceph.com",
            ),
        ),
    )

    result = driver.provision(_spec(config={}))

    assert result.ok is False
    assert "CSI driver" in result.message


def test_rook_driver_rejects_storage_class_with_wrong_provisioner() -> None:
    driver = RookCephFSDriver(
        config=PVCConfig(
            storage_class_name="rook-cephfs",
            csi_driver="rook-ceph.cephfs.csi.ceph.com",
            default_access_modes=("ReadWriteMany",),
            cluster_driver=_ClusterDriver(
                storage_classes=["rook-cephfs"],
                csi_drivers=["rook-ceph.cephfs.csi.ceph.com"],
                provisioner="ebs.csi.aws.com",
            ),
        ),
    )

    result = driver.provision(_spec(config={}))

    assert result.ok is False
    assert "ebs.csi.aws.com" in result.message
    assert "rook-ceph.cephfs.csi.ceph.com" in result.message


def test_dynamic_pvc_binding_rejects_unsafe_capacity_and_access_mode() -> None:
    driver = StorageClassPVCDriver(config=PVCConfig(storage_class_name="standard"))
    handle = ServiceHandle("filesystem/cluster-1/acme-payments/pvc-payments-shared-workspace")

    try:
        driver.binding(handle, {"capacity": "one disk"})
    except ValueError as exc:
        assert "capacity" in str(exc)
    else:
        raise AssertionError("invalid capacity was accepted")

    try:
        driver.binding(handle, {"access_modes": ["ReadWriteEverywhere"]})
    except ValueError as exc:
        assert "access mode" in str(exc)
    else:
        raise AssertionError("invalid access mode was accepted")

    try:
        driver.binding(handle, {"access_modes": ["ReadWriteOnce", "ReadOnlyMany"]})
    except ValueError as exc:
        assert "exactly one" in str(exc)
    else:
        raise AssertionError("multiple PVC access modes were accepted")

    try:
        driver.binding(handle, {"access_modes": []})
    except ValueError as exc:
        assert "access mode" in str(exc)
    else:
        raise AssertionError("empty PVC access modes were accepted")


def test_dynamic_pvc_exposes_only_live_safe_editable_fields() -> None:
    driver = StorageClassPVCDriver(config=PVCConfig(storage_class_name="standard"))

    editable = driver.editable_fields()

    assert "capacity" in editable
    assert "mount_path" in editable
    assert "storage_class_name" not in editable
    assert "access_modes" not in editable


def test_dynamic_pvc_rejects_overlong_storage_class_dns_label() -> None:
    driver = StorageClassPVCDriver(config=PVCConfig())

    result = driver.provision(
        _spec(config={"storage_class_name": f"{'a' * 64}.example"}),
    )

    assert result.ok is False
    assert "DNS labels" in result.message


def test_dynamic_pvc_deprovision_is_data_safe_and_rejects_legacy_handle() -> None:
    driver = StorageClassPVCDriver(config=PVCConfig(storage_class_name="standard"))
    handle = "filesystem/cluster-1/acme-payments/pvc-payments-shared-workspace"

    retained = driver.deprovision(DeprovisionSpec(handle=handle))
    unconfirmed_delete = driver.deprovision(
        DeprovisionSpec(handle=handle),
        delete_data=True,
    )
    legacy = driver.deprovision(DeprovisionSpec(handle="filesystem/workspace"))

    assert retained.ok is True
    assert "retained" in retained.message
    assert unconfirmed_delete.ok is False
    assert unconfirmed_delete.retryable is False
    assert legacy.ok is False
    assert legacy.retryable is False


def test_dynamic_pvc_binding_reports_transport_security_conservatively() -> None:
    """#1398 — both dynamic variants must emit the canonical FILESYSTEM_TLS.

    The value is ``"false"`` because the StorageClass's provisioner owns
    in-transit encryption and the driver cannot observe it. Reporting
    ``"true"`` for an unknown backend would tell a consumer its traffic is
    protected when it may not be.
    """
    handle = ServiceHandle("filesystem/cluster-1/acme-payments/pvc-payments-shared-workspace")

    for driver in (
        StorageClassPVCDriver(config=PVCConfig(storage_class_name="standard")),
        RookCephFSDriver(
            config=PVCConfig(
                storage_class_name="rook-cephfs",
                default_access_modes=("ReadWriteMany",),
            ),
        ),
    ):
        binding = driver.binding(handle)

        assert binding.env_vars["FILESYSTEM_TLS"].literal == "false"
        assert binding.env_vars["FILESYSTEM_TLS"].secret_ref in (None, "")
        # The declared schema has to keep pace with what binding() emits,
        # otherwise the availability matrix and the docs go on lying.
        assert set(driver.binding_schema().env_vars) == set(binding.env_vars)


def test_dynamic_pvc_matrix_entries_declare_the_full_filesystem_envelope() -> None:
    entries = {
        entry.variant: entry
        for entry in MATRIX.managed_services
        if entry.kind == "filesystem" and entry.plugin_id == "k8s_native"
    }

    for variant in ("storage_class_pvc", "rook_cephfs"):
        assert set(envelope_keys_for("filesystem")) <= set(entries[variant].binding_envs)


def test_plugin_registers_both_dynamic_filesystem_variants() -> None:
    assert PLUGIN.managed_service_drivers[("filesystem", "storage_class_pvc")] is StorageClassPVCDriver
    assert PLUGIN.managed_service_drivers[("filesystem", "rook_cephfs")] is RookCephFSDriver
