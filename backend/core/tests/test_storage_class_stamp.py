"""StatefulSet PVC storage-class autostamp (#1023).

A volumeClaimTemplate with no storageClassName only binds when the cluster
has a default-annotated StorageClass. astrolift-eks ships gp2 un-defaulted,
so the PVC hangs Pending forever. The deploy path discovers the cluster's
SC and stamps it so stateful apps bind without the operator knowing the SC
name. These tests pin the selection logic (pure, driver mocked).
"""

from __future__ import annotations

from types import SimpleNamespace

from _sdk.cluster import StorageClassInfo
from core import app_deploy


def _sts(claims):
    return {
        "kind": "StatefulSet",
        "metadata": {"name": "data"},
        "spec": {"volumeClaimTemplates": claims},
    }


def _claim(storage_class=None):
    spec = {"accessModes": ["ReadWriteOnce"]}
    if storage_class is not None:
        spec["storageClassName"] = storage_class
    return {"metadata": {"name": "data"}, "spec": spec}


def _patch_scs(monkeypatch, scs):
    driver = SimpleNamespace(list_storage_classes=lambda cluster: scs)
    monkeypatch.setattr(
        app_deploy, "driver_for_deployment", lambda d: (driver, SimpleNamespace(slug="c"), "ns")
    )


def _sc_name(resources):
    return resources[0]["spec"]["volumeClaimTemplates"][0]["spec"].get("storageClassName")


def test_sole_storage_class_is_stamped_even_if_not_default(monkeypatch):
    # The astrolift-eks case: one SC (gp2), not annotated default.
    _patch_scs(monkeypatch, [StorageClassInfo(name="gp2", is_default=False)])
    res = [_sts([_claim()])]
    app_deploy._stamp_storage_class_for_claims(object(), res)
    assert _sc_name(res) == "gp2"


def test_default_storage_class_preferred_among_several(monkeypatch):
    _patch_scs(
        monkeypatch,
        [
            StorageClassInfo(name="gp2", is_default=False),
            StorageClassInfo(name="gp3", is_default=True),
        ],
    )
    res = [_sts([_claim()])]
    app_deploy._stamp_storage_class_for_claims(object(), res)
    assert _sc_name(res) == "gp3"


def test_multiple_none_default_left_unset(monkeypatch):
    # Can't safely guess the tier → leave unset rather than risk the wrong one.
    _patch_scs(
        monkeypatch,
        [
            StorageClassInfo(name="gp2", is_default=False),
            StorageClassInfo(name="io2", is_default=False),
        ],
    )
    res = [_sts([_claim()])]
    app_deploy._stamp_storage_class_for_claims(object(), res)
    assert _sc_name(res) is None


def test_explicit_storage_class_is_not_overwritten(monkeypatch):
    _patch_scs(monkeypatch, [StorageClassInfo(name="gp2", is_default=True)])
    res = [_sts([_claim(storage_class="fast-ssd")])]
    app_deploy._stamp_storage_class_for_claims(object(), res)
    assert _sc_name(res) == "fast-ssd"  # operator's choice wins


def test_no_storage_classes_leaves_unset(monkeypatch):
    _patch_scs(monkeypatch, [])
    res = [_sts([_claim()])]
    app_deploy._stamp_storage_class_for_claims(object(), res)
    assert _sc_name(res) is None
