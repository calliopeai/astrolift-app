from unittest.mock import patch

import pytest
from _sdk.managed_service import (
    Binding,
    ValueRef,
    VolumeMount,
    VolumeSourceKind,
)

from astrolift_services.models import (
    ManagedService,
    ManagedServiceBinding,
    ManagedServiceVolumeBinding,
)
from astrolift_workflows.activities.managed_service_lifecycle import _sync_binding_rows
from astrolift_workflows.tests.test_provision_managed_service_ready import _make_service

pytestmark = pytest.mark.django_db


def _filesystem_service() -> ManagedService:
    service = _make_service()
    service.kind = ManagedService.Kind.FILESYSTEM
    service.name = "shared-files"
    service.save(update_fields=["kind", "name", "updated_at", "version"])
    return service


def _binding(*volumes: VolumeMount) -> Binding:
    return Binding(
        env_vars={
            "FILESYSTEM_ENDPOINT": ValueRef(literal="files.internal"),
            "FILESYSTEM_PASSWORD": ValueRef(secret_ref="secret/fsx#password"),
        },
        pod_volume_mounts=list(volumes),
    )


def test_sync_persists_portable_volume_contract_without_secret_values() -> None:
    service = _filesystem_service()
    volume = VolumeMount(
        name="shared-files",
        mount_path="/workspace",
        source_kind=VolumeSourceKind.CSI,
        protocol="smb3",
        csi_driver="smb.csi.k8s.io",
        volume_handle="files.internal##share",
        volume_attributes={"source": "//files.internal/share"},
        secret_refs={
            "username": "secret/fsx#username",
            "password": "secret/fsx#password",
        },
        secret_literals={"account": "files-account"},
        mount_options=["vers=3.0"],
        capacity="100Gi",
    )

    with patch(
        "astrolift_workflows.activities.managed_service_lifecycle._managed_binding_for",
        return_value=_binding(volume),
    ):
        _sync_binding_rows(service)

    persisted = ManagedServiceVolumeBinding.objects.get(managed_service=service)
    assert persisted.csi_driver == "smb.csi.k8s.io"
    assert persisted.volume_attributes == {"source": "//files.internal/share"}
    assert persisted.secret_refs == {
        "username": "secret/fsx#username",
        "password": "secret/fsx#password",
    }
    assert persisted.secret_literals == {"account": "files-account"}
    assert "agent-user" not in str(persisted.secret_refs)
    password = ManagedServiceBinding.objects.get(
        managed_service=service,
        env_key="FILESYSTEM_PASSWORD",
    )
    assert password.is_secret is True
    assert password.env_value_ref == "secret/fsx#password"


def test_sync_persists_dynamic_storage_class_template() -> None:
    service = _filesystem_service()
    volume = VolumeMount(
        name="shared-files",
        mount_path="/workspace",
        source_kind=VolumeSourceKind.DYNAMIC_PVC,
        protocol="cephfs",
        storage_class_name="rook-cephfs",
        csi_driver="rook-ceph.cephfs.csi.ceph.com",
        capacity="100Gi",
        access_modes=("ReadWriteMany",),
    )

    with patch(
        "astrolift_workflows.activities.managed_service_lifecycle._managed_binding_for",
        return_value=_binding(volume),
    ):
        _sync_binding_rows(service)

    persisted = ManagedServiceVolumeBinding.objects.get(managed_service=service)
    assert persisted.source_kind == "dynamic_pvc"
    assert persisted.storage_class_name == "rook-cephfs"
    assert persisted.csi_driver == "rook-ceph.cephfs.csi.ceph.com"
    assert persisted.claim_name == ""
    assert persisted.volume_handle == ""


def test_invalid_driver_reconcile_does_not_erase_last_good_bindings() -> None:
    service = _filesystem_service()
    first = VolumeMount(
        name="shared-files",
        mount_path="/workspace",
        source_kind=VolumeSourceKind.CSI,
        protocol="nfs4",
        csi_driver="efs.csi.aws.com",
        volume_handle="fs-12345678",
    )
    with patch(
        "astrolift_workflows.activities.managed_service_lifecycle._managed_binding_for",
        return_value=_binding(first),
    ):
        _sync_binding_rows(service)

    with (
        patch(
            "astrolift_workflows.activities.managed_service_lifecycle._managed_binding_for",
            return_value=_binding(first, first),
        ),
        pytest.raises(ValueError, match="duplicate volume name"),
    ):
        _sync_binding_rows(service)

    assert list(
        ManagedServiceVolumeBinding.objects.filter(managed_service=service).values_list(
            "name",
            flat=True,
        ),
    ) == ["shared-files"]
    assert ManagedServiceBinding.objects.filter(managed_service=service).count() == 2
