"""Every cloud ClusterDriver answers what an in-cluster driver asks it (#1484).

An in-cluster managed-service driver never talks to a cluster directly. It
holds a ``ClusterDriver`` -- whichever one the tenant cluster has, EKS/GKE/AKS
included since #1484 -- and calls a handful of methods on it. That indirection
is what makes an in-cluster variant portable rather than only reachable, and it
is only as good as the cloud drivers' coverage of those methods.

``_sdk.cluster.ClusterDriver`` gives several of them a permissive default:
``list_storage_classes`` returns ``[]``, ``list_csi_drivers`` returns ``[]``. A
driver that inherits those does not fail loudly, it answers "this cluster has
no StorageClasses", and the in-cluster drivers that need one refuse to
provision with a message about the cluster rather than about the driver. GKE
and AKS inherited ``list_storage_classes`` exactly that way, so OpenSearch, SQL
Server Express and ``storage_class_pvc`` were unprovisionable on them the
moment the fallback made them reachable.

The required set is read out of the in-cluster drivers themselves rather than
listed here, so a new call on ``cluster_driver`` puts every cloud driver on the
hook without anyone remembering to update a list.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _sdk.cluster import ClusterDriver

CLOUD_DRIVERS = {
    "aws": ("aws.cluster_eks", "EKSClusterDriver"),
    "gcp": ("gcp.cluster_gke", "GKEClusterDriver"),
    "azure": ("azure.cluster_aks", "AKSClusterDriver"),
    "k8s_native": ("k8s_native.cluster", "K8sNativeClusterDriver"),
}

_CALL = re.compile(r"cluster_driver\.([a-z_]+)\s*\(")
_MANAGED_DIR = Path(__file__).resolve().parents[2] / "k8s_native" / "managed"


def _driver_cls(plugin_id: str) -> type:
    module_name, class_name = CLOUD_DRIVERS[plugin_id]
    module = __import__(module_name, fromlist=[class_name])
    return getattr(module, class_name)


def required_cluster_driver_methods() -> set[str]:
    """Every ``ClusterDriver`` method the in-cluster managed drivers call."""
    calls: set[str] = set()
    for path in sorted(_MANAGED_DIR.glob("*.py")):
        calls |= set(_CALL.findall(path.read_text(encoding="utf-8")))
    return {name for name in calls if hasattr(ClusterDriver, name)}


def test_the_required_set_is_discovered_and_not_empty():
    """The scan is the whole basis of the next test; an empty set would make it
    pass by finding nothing to check."""
    required = required_cluster_driver_methods()

    assert "apply_manifests" in required
    assert "list_storage_classes" in required, (
        "the storage-class probe is the call that made GKE and AKS unable to run the "
        "stateful in-cluster drivers; if it is gone, so is the reason for this test"
    )


@pytest.mark.parametrize("plugin_id", sorted(CLOUD_DRIVERS))
def test_every_cluster_driver_implements_what_in_cluster_drivers_call(plugin_id):
    """A cloud driver that leaves one of these on the SDK default does not error,
    it answers emptily -- and an in-cluster driver reads that as a fact about
    the cluster. Overriding is the contract."""
    driver_cls = _driver_cls(plugin_id)

    inherited = sorted(
        name
        for name in required_cluster_driver_methods()
        if getattr(driver_cls, name, None) is getattr(ClusterDriver, name, None)
    )

    assert not inherited, (
        f"{driver_cls.__name__} inherits the permissive SDK default for {inherited}; "
        "an in-cluster managed service booked on this cloud will read the empty answer "
        "as the cluster's own state"
    )
