from __future__ import annotations

import base64
import copy
from uuid import uuid4

import httpx
import pytest

from nvke.access import cluster_remote_name
from nvke.client import Endpoint, NvkeError
from nvke.datastore import NvkeDatastore
from nvke.operations import validate_request

CONFIG = (
    b"apiVersion: v1\nkind: Config\nclusters: [{name: a}]\n"
    b"users: [{name: a, token: PRIVATE_NATIVE_ACCESS}]\ncontexts: [{name: a}]\n"
)


def documents(namespace="org-owned", resource_name="desired-cluster"):
    name = cluster_remote_name(namespace, resource_name)
    cluster_id, native_uid, capi_uid, secret_uid = (str(uuid4()) for _ in range(4))
    native = {
        "apiVersion": "internal.nvke.nvidia.com/v1",
        "kind": "NVKECluster",
        "metadata": {"name": resource_name, "namespace": namespace, "uid": native_uid, "resourceVersion": "7"},
        "status": {"id": cluster_id},
    }
    capi = {
        "apiVersion": "cluster.x-k8s.io/v1beta2",
        "kind": "Cluster",
        "metadata": {"name": name, "namespace": "nvke-system", "uid": capi_uid, "resourceVersion": "9"},
    }
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {
            "name": name + "-admin-kubeconfig",
            "namespace": "nvke-system",
            "uid": secret_uid,
            "resourceVersion": "11",
            "labels": {
                "nvke.nvidia.com/cluster-id": cluster_id,
                "nvke.nvidia.com/kubeconfig-mode": "admin",
                "cluster.x-k8s.io/cluster-name": name,
            },
            "ownerReferences": [
                {"apiVersion": capi["apiVersion"], "kind": "Cluster", "name": name, "uid": capi_uid, "controller": True}
            ],
        },
        "data": {"value": base64.b64encode(CONFIG).decode()},
    }
    return name, native, capi, secret


def transport(native, capi, secret, *, change_native=None, change_capi=None):
    calls = []

    def respond(request):
        calls.append(request.url.path)
        path = request.url.path
        if path.endswith("/nvkeclusters"):
            return httpx.Response(
                200, json={"items": [native] if native else [], "metadata": {"resourceVersion": "7", "continue": ""}}
            )
        if "/nvkeclusters/" in path:
            return httpx.Response(200, json=change_native or native)
        if "/secrets/" in path:
            return httpx.Response(200, json=secret)
        if "/clusters/" in path:
            count = sum("/clusters/" in call for call in calls)
            return httpx.Response(200, json=change_capi if change_capi and count > 1 else capi)
        raise AssertionError("unexpected native path")

    return httpx.MockTransport(respond), calls


@pytest.mark.parametrize("source", ["native", "receipt"])
def test_download_requires_live_matching_native_ownership(source):
    name, native, capi, secret = documents()
    mock, calls = transport(native, capi, secret)
    with NvkeDatastore(
        Endpoint("datastore", "https://native.example.test"), organization_namespace="org-owned", transport=mock
    ) as client:
        result = client.workload_kubeconfig(
            name,
            management_namespace="nvke-system",
            trusted_cluster_ids=frozenset([native["status"]["id"]]) if source == "receipt" else frozenset(),
        )
    assert result.content == CONFIG and result.secret_uid == secret["metadata"]["uid"]
    assert "PRIVATE_NATIVE_ACCESS" not in repr(result)
    assert sum("/secrets/" in path for path in calls) == 1
    assert sum("/clusters/" in path for path in calls) == 2
    assert (any("/nvkeclusters" in path for path in calls)) is (source == "native")


@pytest.mark.parametrize(
    "failure",
    [
        "missing-label",
        "foreign-id",
        "conflicting-legacy",
        "wrong-owner-uid",
        "wrong-owner-kind",
        "wrong-owner-name",
        "wrong-owner-api",
        "not-controller",
        "missing-owner",
        "foreign-namespace",
        "deleting-secret",
        "wrong-mode",
        "wrong-type",
        "replaced-native",
        "replaced-capi",
        "foreign-native",
        "missing-native",
        "pending-native",
    ],
)
def test_unknown_stale_or_foreign_access_material_never_returns_credentials(failure):
    name, native, capi, secret = documents()
    changed_native, changed_capi = copy.deepcopy(native), copy.deepcopy(capi)
    meta = secret["metadata"]
    if failure == "missing-label":
        meta["labels"].pop("nvke.nvidia.com/cluster-id")
    elif failure == "foreign-id":
        meta["labels"]["nvke.nvidia.com/cluster-id"] = str(uuid4())
    elif failure == "conflicting-legacy":
        meta["labels"]["nke.nvidia.com/cluster-id"] = str(uuid4())
    elif failure == "wrong-owner-uid":
        meta["ownerReferences"][0]["uid"] = str(uuid4())
    elif failure == "wrong-owner-kind":
        meta["ownerReferences"][0]["kind"] = "Other"
    elif failure == "wrong-owner-name":
        meta["ownerReferences"][0]["name"] = "foreign"
    elif failure == "wrong-owner-api":
        meta["ownerReferences"][0]["apiVersion"] = "foreign/v1"
    elif failure == "not-controller":
        meta["ownerReferences"][0]["controller"] = False
    elif failure == "missing-owner":
        meta.pop("ownerReferences")
    elif failure == "foreign-namespace":
        meta["namespace"] = "foreign"
    elif failure == "deleting-secret":
        meta["deletionTimestamp"] = "2026-11-07T00:00:00Z"
    elif failure == "wrong-mode":
        meta["labels"]["nvke.nvidia.com/kubeconfig-mode"] = "spire"
    elif failure == "wrong-type":
        secret["type"] = "kubernetes.io/tls"
    elif failure == "replaced-native":
        changed_native["metadata"]["uid"] = str(uuid4())
    elif failure == "replaced-capi":
        changed_capi["metadata"]["uid"] = str(uuid4())
    elif failure == "foreign-native":
        native["metadata"]["namespace"] = "foreign"
    elif failure == "missing-native":
        native = None
    elif failure == "pending-native":
        native["status"] = {}
    mock, calls = transport(native, capi, secret, change_native=changed_native, change_capi=changed_capi)
    with (
        NvkeDatastore(
            Endpoint("datastore", "https://native.example.test"), organization_namespace="org-owned", transport=mock
        ) as client,
        pytest.raises(NvkeError) as error,
    ):
        client.workload_kubeconfig(name, management_namespace="nvke-system")
    assert error.value.code in {"OWNERSHIP_UNVERIFIED", "ACCESS_NOT_READY"}
    assert "PRIVATE_NATIVE_ACCESS" not in str(error.value)
    if failure in {"foreign-native", "missing-native", "pending-native"}:
        assert not any("/secrets/" in path for path in calls)


def test_caller_json_cannot_supply_the_server_receipt_ownership_override():
    with pytest.raises(NvkeError) as error:
        validate_request(
            "datastore.getWorkloadKubeconfig",
            {"cluster_name": "foreign", "management_namespace": "nvke-system", "trusted_cluster_ids": [str(uuid4())]},
            "org-owned",
        )
    assert error.value.code == "INVALID_INPUT"


def test_legacy_label_domain_and_capi_owner_version_keep_strong_identity_checks():
    name, native, capi, secret = documents()
    labels = secret["metadata"]["labels"]
    for key in ("cluster-id", "kubeconfig-mode"):
        labels["nke.nvidia.com/" + key] = labels.pop("nvke.nvidia.com/" + key)
    secret["metadata"]["ownerReferences"][0]["apiVersion"] = "cluster.x-k8s.io/v1beta1"
    mock, _calls = transport(native, capi, secret)
    with NvkeDatastore(
        Endpoint("datastore", "https://native.example.test"), organization_namespace="org-owned", transport=mock
    ) as client:
        assert client.workload_kubeconfig(name, management_namespace="nvke-system").content == CONFIG


@pytest.mark.parametrize(
    "content",
    [
        "??bad-base64",
        base64.b64encode(b"PRIVATE_MALFORMED_DOCUMENT").decode(),
        base64.b64encode(b"apiVersion: v1\nkind: Config").decode(),
    ],
)
def test_owned_but_malformed_kubeconfig_keeps_payloads_out_of_errors(content):
    name, native, capi, secret = documents()
    secret["data"]["value"] = content
    mock, _calls = transport(native, capi, secret)
    with (
        NvkeDatastore(
            Endpoint("datastore", "https://native.example.test"), organization_namespace="org-owned", transport=mock
        ) as client,
        pytest.raises(NvkeError) as error,
    ):
        client.workload_kubeconfig(name, management_namespace="nvke-system")
    assert error.value.code == "INVALID_RESPONSE"
    assert "PRIVATE" not in str(error.value)


def test_current_authority_is_rechecked_before_secret_access():
    name, native, capi, secret = documents()
    mock, calls = transport(native, capi, secret)

    def checkpoint():
        if len(calls) == 2:
            raise NvkeError("AUTHORITY_REVOKED", "NVKE caller was revoked")

    with (
        NvkeDatastore(
            Endpoint("datastore", "https://native.example.test"),
            organization_namespace="org-owned",
            transport=mock,
            before_request=checkpoint,
        ) as client,
        pytest.raises(NvkeError) as error,
    ):
        client.workload_kubeconfig(name, management_namespace="nvke-system")
    assert error.value.code == "AUTHORITY_REVOKED"
    assert not any("/secrets/" in path for path in calls)
