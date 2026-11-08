from __future__ import annotations

import base64
import copy
import json
from typing import Any

import pytest

from nvke.client import Endpoint, NvkeError
from nvke.contract import Resource, resources
from nvke.datastore import NvkeDatastore
from tests.nvke.support import PUBLIC_SPECS, TOKEN, Peer

NAMESPACE = "org-owned"


def datastore(peer: Peer) -> NvkeDatastore:
    return NvkeDatastore(
        Endpoint("datastore", peer.url, token=lambda: TOKEN, allow_http=True), organization_namespace=NAMESPACE
    )


@pytest.mark.parametrize("resource", resources(), ids=lambda r: r.kind)
def test_every_management_resource_supports_complete_crudl_transport(peer: Peer, resource: Resource) -> None:
    document = {
        "metadata": {"name": "resource-a", "annotations": {"owner": "astro"}},
        "spec": PUBLIC_SPECS[resource.kind],
    }
    original = copy.deepcopy(document)
    peer.body = json.dumps(
        {
            **document,
            "apiVersion": "internal.nvke.nvidia.com/v1",
            "kind": resource.kind,
            "metadata": {
                "name": "resource-a",
                "uid": "uid-1",
                "resourceVersion": "rv-9",
                **({"namespace": NAMESPACE} if resource.namespaced else {}),
            },
        }
    ).encode()
    with datastore(peer) as client:
        assert client.create(resource.kind, document, dry_run=True).resource_version == "rv-9"
        assert client.get(resource.kind, "resource-a").data["metadata"]["uid"] == "uid-1"
        client.list(resource.kind, page_size=2, continue_token="opaque+/=", label_selector="owner=astro")
        client.replace(resource.kind, "resource-a", document, resource_version="rv-9")
        client.delete(resource.kind, "resource-a", uid="uid-1", resource_version="rv-9")
    assert document == original
    prefix = "/apis/internal.nvke.nvidia.com/v1"
    if resource.namespaced:
        prefix += f"/namespaces/{NAMESPACE}"
    prefix += f"/{resource.plural}"
    assert [(r["method"], r["path"].split("?")[0]) for r in peer.requests] == [
        ("POST", prefix),
        ("GET", prefix + "/resource-a"),
        ("GET", prefix),
        ("GET", prefix + "/resource-a"),
        ("PUT", prefix + "/resource-a"),
        ("DELETE", prefix + "/resource-a"),
    ]
    assert peer.requests[0]["path"].endswith("?fieldValidation=Strict&dryRun=All")
    created = json.loads(peer.requests[0]["body"])
    assert created["apiVersion"] == "internal.nvke.nvidia.com/v1"
    assert created["kind"] == resource.kind
    if resource.namespaced:
        assert created["metadata"]["namespace"] == NAMESPACE
    else:
        assert "namespace" not in created["metadata"]
    assert json.loads(peer.requests[4]["body"])["metadata"]["resourceVersion"] == "rv-9"
    assert json.loads(peer.requests[5]["body"])["preconditions"] == {"uid": "uid-1", "resourceVersion": "rv-9"}
    assert "continue=opaque%2B%2F%3D" in peer.requests[2]["path"]
    assert all(r["headers"]["Authorization"] == f"Bearer {TOKEN}" for r in peer.requests)


@pytest.mark.parametrize("kind", ["ClusterRelease", "NodePoolRelease"])
def test_deprecation_only_patches_the_lifecycle_flag_with_concurrency_preconditions(peer: Peer, kind: str) -> None:
    with datastore(peer) as client:
        client.set_deprecated(kind, "release-a", True, uid="uid-1", resource_version="rv-9")
    request = peer.requests.pop()
    assert request["method"] == "PATCH"
    assert request["headers"]["Content-Type"] == "application/merge-patch+json"
    assert json.loads(request["body"]) == {
        "metadata": {"resourceVersion": "rv-9", "uid": "uid-1"},
        "spec": {"lifecycle": {"deprecated": True}},
    }


@pytest.mark.parametrize(
    "kind,metadata",
    [
        ("NVKECluster", {"name": "a", "namespace": "foreign-org"}),
        ("MachineImage", {"name": "a", "namespace": NAMESPACE}),
        ("NVKECluster", {"name": "a", "uid": "untrusted"}),
        ("NVKECluster", {"name": "a", "resourceVersion": "untrusted"}),
        ("NVKECluster", {"name": "a..b"}),
    ],
)
def test_namespace_or_transport_metadata_cannot_be_injected(peer: Peer, kind: str, metadata: dict[str, Any]) -> None:
    with datastore(peer) as client, pytest.raises(NvkeError) as exc:
        client.create(kind, {"metadata": metadata, "spec": PUBLIC_SPECS[kind]})
    assert exc.value.code == "INVALID_INPUT"
    assert not peer.requests


@pytest.mark.parametrize("kind", ["Secret", "Namespace", "MachineImage/../../secrets", "Unknown"])
def test_adapter_cannot_be_used_as_an_arbitrary_kubernetes_proxy(peer: Peer, kind: str) -> None:
    with datastore(peer) as client, pytest.raises(NvkeError) as exc:
        client.get(kind, "a")
    assert exc.value.code == "INVALID_INPUT"
    assert not peer.requests


def test_datastore_does_not_fall_back_to_a_public_operator_connection(peer: Peer) -> None:
    with pytest.raises(ValueError, match="dedicated datastore"):
        NvkeDatastore(Endpoint("operator", peer.url, allow_http=True), organization_namespace=NAMESPACE)
    assert not peer.requests


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"uid": "other-uid"}, "CONFLICT"),
        ({"resourceVersion": "new-version"}, "CONFLICT"),
        ({"deletionTimestamp": "2026-11-08T00:00:00Z"}, "CONFLICT"),
        ({"namespace": "other-org"}, "INVALID_RESPONSE"),
        ({"name": "another-cluster"}, "INVALID_RESPONSE"),
        ({"uid": None}, "INVALID_RESPONSE"),
    ],
)
def test_replace_refuses_changed_or_foreign_metadata_before_writing(peer: Peer, change, expected) -> None:
    metadata = {"name": "cluster-a", "namespace": NAMESPACE, "uid": "uid-1", "resourceVersion": "rv-9", **change}
    peer.body = json.dumps(
        {"apiVersion": "internal.nvke.nvidia.com/v1", "kind": "NVKECluster", "metadata": metadata}
    ).encode()
    with datastore(peer) as client, pytest.raises(NvkeError) as error:
        client.replace(
            "NVKECluster",
            "cluster-a",
            {"metadata": {"name": "cluster-a"}, "spec": PUBLIC_SPECS["NVKECluster"]},
            resource_version="rv-9",
            uid="uid-1",
        )
    assert error.value.code == expected
    assert [request["method"] for request in peer.requests] == ["GET"]


def test_replace_rechecks_authority_after_the_metadata_read(peer: Peer) -> None:
    peer.body = json.dumps(
        {
            "apiVersion": "internal.nvke.nvidia.com/v1",
            "kind": "NVKECluster",
            "metadata": {"name": "cluster-a", "namespace": NAMESPACE, "uid": "uid-1", "resourceVersion": "rv-9"},
        }
    ).encode()
    checks = 0

    def authority():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise NvkeError("AUTHORITY_CHANGED", "Authority changed before the write")

    with (
        NvkeDatastore(
            Endpoint("datastore", peer.url, allow_http=True), organization_namespace=NAMESPACE, before_request=authority
        ) as client,
        pytest.raises(NvkeError) as error,
    ):
        client.replace(
            "NVKECluster",
            "cluster-a",
            {"metadata": {"name": "cluster-a"}, "spec": PUBLIC_SPECS["NVKECluster"]},
            resource_version="rv-9",
        )
    assert error.value.code == "AUTHORITY_CHANGED"
    assert [request["method"] for request in peer.requests] == ["GET"]


@pytest.mark.parametrize("uid,version", [("", "rv-9"), ("uid-1", ""), ("*", "rv-9"), ("uid-1", "*")])
def test_unconditional_deletion_is_not_available(peer: Peer, uid: str, version: str) -> None:
    with datastore(peer) as client, pytest.raises(NvkeError) as exc:
        client.delete("MachineImage", "image-a", uid=uid, resource_version=version)
    assert exc.value.code == "INVALID_INPUT"
    assert not peer.requests


def test_conflict_does_not_retry_as_an_unconditional_update(peer: Peer) -> None:
    peer.status = 409
    with datastore(peer) as client, pytest.raises(NvkeError) as exc:
        client.set_deprecated("ClusterRelease", "release-a", True, uid="uid-1", resource_version="old-rv")
    assert exc.value.code == "CONFLICT"
    assert not exc.value.retryable
    assert len(peer.requests) == 1


def test_bare_secret_does_not_bypass_native_organization_ownership(peer: Peer) -> None:
    peer.body = json.dumps(
        {
            "metadata": {"uid": "secret-uid", "resourceVersion": "rv-2"},
            "data": {"value": base64.b64encode(TOKEN.encode()).decode()},
        }
    ).encode()
    with datastore(peer) as client, pytest.raises(NvkeError) as error:
        client.workload_kubeconfig("cluster-a", management_namespace="nvke-system")
    assert error.value.code == "OWNERSHIP_UNVERIFIED"
    assert len(peer.requests) == 1
    assert "/secrets/" not in peer.requests[0]["path"]
    assert TOKEN not in str(error.value)
