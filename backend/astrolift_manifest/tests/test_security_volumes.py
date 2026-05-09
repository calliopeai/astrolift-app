"""Tests for security context + volume rendering (#140, spec 05 §7-7.1)."""

from __future__ import annotations

import pytest

from astrolift_drivers.storage_tiers import (
    Durability,
    PerformanceTier,
    StorageError,
)
from astrolift_manifest.security_volumes import (
    SecurityContext,
    VolumeDecl,
    VolumeKind,
    parse_security_context,
    parse_volume,
    render_pod_volume,
    render_pvc,
    render_security_context,
    render_volume_mount,
)


# ---- security context defaults -------------------------------------


def test_security_context_defaults_secure():
    """Spec 05 §7: defaults should be secure. Operator must
    explicitly opt out of read-only rootfs / non-root user /
    no privilege-escalation."""
    sec = SecurityContext()
    assert sec.read_only_root_fs is True
    assert sec.run_as_non_root is True
    assert sec.allow_privilege_escalation is False
    assert sec.capabilities_drop == ("ALL",)


def test_parse_security_context_defaults():
    sec = parse_security_context({})
    assert sec.run_as_non_root is True
    assert sec.read_only_root_fs is True


def test_parse_security_context_all_fields():
    sec = parse_security_context({
        "read_only_root_fs": False,
        "run_as_non_root": False,
        "run_as_user": 1000,
        "allow_privilege_escalation": True,
        "capabilities_drop": ("ALL",),
        "capabilities_add": ("NET_BIND_SERVICE",),
    })
    assert sec.read_only_root_fs is False
    assert sec.run_as_user == 1000
    assert sec.capabilities_add == ("NET_BIND_SERVICE",)


def test_run_as_user_zero_with_non_root_rejected():
    """uid 0 is root by definition; can't satisfy non-root + uid 0
    simultaneously. Catches a foot-gun where operator sets both
    'run_as_non_root=True' (security flag) AND 'run_as_user=0'."""
    with pytest.raises(StorageError, match="root"):
        parse_security_context({
            "run_as_non_root": True,
            "run_as_user": 0,
        })


def test_run_as_user_zero_allowed_when_non_root_explicitly_false():
    """Legacy workloads that need root: must set both flags."""
    sec = parse_security_context({
        "run_as_non_root": False,
        "run_as_user": 0,
    })
    assert sec.run_as_user == 0


def test_run_as_user_must_be_int():
    with pytest.raises(StorageError, match="int"):
        parse_security_context({"run_as_user": "1000"})


def test_run_as_user_must_be_non_negative():
    with pytest.raises(StorageError, match="non-negative"):
        parse_security_context({"run_as_user": -1})


def test_parse_security_rejects_non_mapping():
    with pytest.raises(StorageError):
        parse_security_context("not a mapping")  # type: ignore[arg-type]


def test_render_security_context_shape():
    sec = SecurityContext(
        run_as_user=1000,
        capabilities_drop=("ALL",),
        capabilities_add=("NET_BIND_SERVICE",),
    )
    out = render_security_context(sec)
    assert out["readOnlyRootFilesystem"] is True
    assert out["runAsNonRoot"] is True
    assert out["runAsUser"] == 1000
    assert out["allowPrivilegeEscalation"] is False
    assert out["capabilities"]["drop"] == ["ALL"]
    assert out["capabilities"]["add"] == ["NET_BIND_SERVICE"]


def test_render_omits_run_as_user_when_unset():
    """K8s lets the image's USER directive apply when runAsUser
    is absent; we shouldn't force a specific UID unless the
    manifest declared one."""
    sec = SecurityContext()
    out = render_security_context(sec)
    assert "runAsUser" not in out


def test_render_omits_capabilities_add_when_empty():
    """K8s validates capabilities; emitting an empty 'add' list
    works but is noise. Omit when nothing to add."""
    sec = SecurityContext()
    out = render_security_context(sec)
    assert "add" not in out["capabilities"]


# ---- volume declarations -------------------------------------------


def test_pvc_volume_requires_size():
    with pytest.raises(StorageError, match="size"):
        VolumeDecl(name="data", mount_path="/data", kind=VolumeKind.PVC)


def test_pvc_volume_validates_size_format():
    with pytest.raises(StorageError):
        VolumeDecl(
            name="data", mount_path="/data",
            kind=VolumeKind.PVC, size="oops",
        )


def test_volume_requires_absolute_mount_path():
    with pytest.raises(StorageError, match="absolute"):
        VolumeDecl(
            name="data", mount_path="data",  # missing leading /
            kind=VolumeKind.PVC, size="10Gi",
        )


def test_volume_requires_name():
    with pytest.raises(StorageError, match="name"):
        VolumeDecl(name="", mount_path="/data")


def test_configmap_volume_requires_source_name():
    with pytest.raises(StorageError, match="source_name"):
        VolumeDecl(
            name="cfg", mount_path="/etc/config",
            kind=VolumeKind.CONFIG_MAP,
        )


def test_secret_volume_requires_source_name():
    with pytest.raises(StorageError, match="source_name"):
        VolumeDecl(
            name="secrets", mount_path="/etc/secrets",
            kind=VolumeKind.SECRET,
        )


def test_empty_dir_volume_no_required_fields():
    """emptyDir is the simplest volume kind."""
    vol = VolumeDecl(
        name="cache", mount_path="/cache",
        kind=VolumeKind.EMPTY_DIR,
    )
    assert vol.kind == VolumeKind.EMPTY_DIR


def test_empty_dir_with_size_limit():
    """Optional sizeLimit bounds memory-backed scratch volumes."""
    vol = VolumeDecl(
        name="cache", mount_path="/cache",
        kind=VolumeKind.EMPTY_DIR, size_limit="1Gi",
    )
    assert vol.size_limit == "1Gi"


def test_empty_dir_size_limit_validates():
    with pytest.raises(StorageError):
        VolumeDecl(
            name="cache", mount_path="/cache",
            kind=VolumeKind.EMPTY_DIR, size_limit="oops",
        )


# ---- parse_volume --------------------------------------------------


def test_parse_volume_pvc_default_kind():
    """Default kind is pvc — the most common volume."""
    vol = parse_volume({
        "name": "data", "mount_path": "/data",
        "size": "20Gi",
    })
    assert vol.kind == VolumeKind.PVC
    assert vol.size == "20Gi"


def test_parse_volume_emptydir():
    vol = parse_volume({
        "name": "cache", "mount_path": "/cache",
        "kind": "empty_dir",
    })
    assert vol.kind == VolumeKind.EMPTY_DIR


def test_parse_volume_unknown_kind():
    with pytest.raises(StorageError, match="kind"):
        parse_volume({
            "name": "x", "mount_path": "/x", "kind": "weird",
        })


def test_parse_volume_rejects_non_mapping():
    with pytest.raises(StorageError):
        parse_volume("not a mapping")  # type: ignore[arg-type]


# ---- render_pvc ----------------------------------------------------


def test_render_pvc_shape():
    vol = VolumeDecl(
        name="data", mount_path="/data",
        kind=VolumeKind.PVC, size="20Gi",
        storage_class="gp3-balanced",
        access_mode="ReadWriteOnce",
    )
    out = render_pvc(vol, namespace="acme-api")
    assert out["apiVersion"] == "v1"
    assert out["kind"] == "PersistentVolumeClaim"
    assert out["metadata"]["name"] == "data"
    assert out["metadata"]["namespace"] == "acme-api"
    assert out["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert out["spec"]["resources"]["requests"]["storage"] == "20Gi"
    assert out["spec"]["storageClassName"] == "gp3-balanced"


def test_render_pvc_omits_storage_class_when_unset():
    """Empty storageClassName lets the cluster default apply."""
    vol = VolumeDecl(
        name="data", mount_path="/data",
        kind=VolumeKind.PVC, size="20Gi",
    )
    out = render_pvc(vol, namespace="ns")
    assert "storageClassName" not in out["spec"]


def test_render_pvc_rejects_non_pvc_volume():
    """Defensive: emptyDir doesn't have a PVC representation."""
    vol = VolumeDecl(
        name="cache", mount_path="/cache",
        kind=VolumeKind.EMPTY_DIR,
    )
    with pytest.raises(StorageError):
        render_pvc(vol, namespace="ns")


# ---- render_pod_volume ---------------------------------------------


def test_render_pod_volume_pvc():
    vol = VolumeDecl(
        name="data", mount_path="/data",
        kind=VolumeKind.PVC, size="20Gi",
    )
    out = render_pod_volume(vol)
    assert out == {"name": "data", "persistentVolumeClaim": {"claimName": "data"}}


def test_render_pod_volume_empty_dir():
    vol = VolumeDecl(
        name="cache", mount_path="/cache",
        kind=VolumeKind.EMPTY_DIR,
    )
    out = render_pod_volume(vol)
    assert out["name"] == "cache"
    assert out["emptyDir"] == {}


def test_render_pod_volume_empty_dir_with_size_limit():
    vol = VolumeDecl(
        name="cache", mount_path="/cache",
        kind=VolumeKind.EMPTY_DIR, size_limit="1Gi",
    )
    out = render_pod_volume(vol)
    assert out["emptyDir"]["sizeLimit"] == "1Gi"


def test_render_pod_volume_config_map():
    vol = VolumeDecl(
        name="cfg", mount_path="/etc/config",
        kind=VolumeKind.CONFIG_MAP, source_name="app-config",
    )
    out = render_pod_volume(vol)
    assert out["configMap"] == {"name": "app-config"}


def test_render_pod_volume_secret():
    vol = VolumeDecl(
        name="secrets", mount_path="/etc/secrets",
        kind=VolumeKind.SECRET, source_name="app-secrets",
    )
    out = render_pod_volume(vol)
    assert out["secret"] == {"secretName": "app-secrets"}


def test_render_volume_mount_shape():
    vol = VolumeDecl(name="data", mount_path="/data", kind=VolumeKind.EMPTY_DIR)
    assert render_volume_mount(vol) == {
        "name": "data", "mountPath": "/data",
    }
