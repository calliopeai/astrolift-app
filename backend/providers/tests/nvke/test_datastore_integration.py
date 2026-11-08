"""Real Kubernetes admission tests; opt in only to the task-owned envtest server."""

from __future__ import annotations

import base64
import copy
import hashlib
import os
import ssl
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
import yaml

from nvke.access import cluster_remote_name
from nvke.client import Endpoint, NvkeError
from nvke.contract import resources
from nvke.datastore import NvkeDatastore
from tests.nvke.support import PUBLIC_SPECS

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def live(tmp_path: Path) -> Iterator[tuple[NvkeDatastore, httpx.Client, str]]:
    path = os.environ.get("NVKE_TEST_KUBECONFIG")
    if not path:
        pytest.skip("set NVKE_TEST_KUBECONFIG to the task-owned local envtest kubeconfig")
    config = yaml.safe_load(Path(path).read_text())
    assert config["current-context"] == "astro-nvke-local", "refusing an ambient cluster"
    context = next(c["context"] for c in config["contexts"] if c["name"] == "astro-nvke-local")
    cluster = next(c["cluster"] for c in config["clusters"] if c["name"] == context["cluster"])
    user = next(c["user"] for c in config["users"] if c["name"] == context["user"])
    endpoint = cluster["server"]
    assert urlsplit(endpoint).hostname == "127.0.0.1", "refusing a non-local API server"
    files = []
    for field, source in (
        ("ca", cluster["certificate-authority-data"]),
        ("cert", user["client-certificate-data"]),
        ("key", user["client-key-data"]),
    ):
        target = tmp_path / f"{field}.pem"
        target.write_bytes(base64.b64decode(source, validate=True))
        target.chmod(0o600)
        files.append(str(target))
    tls = ssl.create_default_context(cafile=files[0])
    tls.load_cert_chain(files[1], files[2])
    namespace = "nvke-test-" + uuid4().hex[:12]
    with httpx.Client(base_url=endpoint, verify=tls, trust_env=False, timeout=30) as raw:
        response = raw.post("/api/v1/namespaces", json={"metadata": {"name": namespace}})
        response.raise_for_status()
        with NvkeDatastore(Endpoint("datastore", endpoint), organization_namespace=namespace, verify=tls) as client:
            yield client, raw, namespace


@pytest.mark.parametrize("kind", [res.kind for res in resources()])
def test_crudl_against_real_nvke_admission(live: tuple[NvkeDatastore, httpx.Client, str], kind: str) -> None:
    client, _raw, _namespace = live
    name = "nvke-" + uuid4().hex[:12]
    document = {
        "metadata": {"name": name, "annotations": {"test": "initial"}},
        "spec": copy.deepcopy(PUBLIC_SPECS[kind]),
    }
    dry_run = client.create(kind, document, dry_run=True)
    assert dry_run.data["metadata"]["name"] == name
    with pytest.raises(NvkeError) as missing:
        client.get(kind, name)
    assert missing.value.code == "NOT_FOUND"
    created = client.create(kind, document)
    uid = created.data["metadata"]["uid"]
    assert created.resource_version
    read = client.get(kind, name)
    assert read.data["metadata"]["uid"] == uid
    page = client.list(kind, page_size=1, field_selector=f"metadata.name={name}")
    assert [item["metadata"]["uid"] for item in page.data["items"]] == [uid]
    document["metadata"]["annotations"]["test"] = "updated"
    updated = client.replace(kind, name, document, resource_version=read.resource_version)
    assert updated.resource_version != read.resource_version
    assert updated.data["metadata"]["annotations"]["test"] == "updated"
    with pytest.raises(NvkeError) as stale:
        client.replace(kind, name, document, resource_version=read.resource_version)
    assert stale.value.code == "CONFLICT"
    with pytest.raises(NvkeError) as changed_identity:
        client.delete(kind, name, uid=str(uuid4()), resource_version=updated.resource_version)
    assert changed_identity.value.code == "CONFLICT"
    assert client.get(kind, name).data["metadata"]["uid"] == uid
    client.delete(kind, name, uid=uid, resource_version=updated.resource_version, propagation_policy="Background")
    with pytest.raises(NvkeError) as deleted:
        client.get(kind, name)
    assert deleted.value.code == "NOT_FOUND"


def test_replace_preserves_controller_metadata_and_replaces_the_entire_spec(live) -> None:
    client, raw, namespace = live
    name = "nvke-preserve-" + uuid4().hex[:12]
    spec = copy.deepcopy(PUBLIC_SPECS["NVKECluster"])
    spec["acceleratedRuntime"] = {"name": "none"}
    created = client.create("NVKECluster", {"metadata": {"name": name}, "spec": spec})
    owner = {"apiVersion": "v1", "kind": "ConfigMap", "name": "native-owner", "uid": str(uuid4())}
    finalizers = ["internal.nvke.nvidia.com/cluster-cleanup"]
    route = f"/apis/internal.nvke.nvidia.com/v1/namespaces/{namespace}/nvkeclusters/{name}"
    preserved = {
        "finalizers": finalizers,
        "ownerReferences": [owner],
        "annotations": {"controller.example/owned": "keep"},
        "labels": {"controller.example/owned": "keep"},
    }
    patched = raw.patch(route, json={"metadata": preserved}, headers={"Content-Type": "application/merge-patch+json"})
    patched.raise_for_status()
    fresh = client.get("NVKECluster", name)
    replacement = copy.deepcopy(spec)
    replacement.pop("acceleratedRuntime")
    replacement["controlPlaneClass"] = "replacement-class"
    updated = client.replace(
        "NVKECluster",
        name,
        {"metadata": {"name": name}, "spec": replacement},
        resource_version=fresh.resource_version,
        uid=created.data["metadata"]["uid"],
    )
    for key, value in preserved.items():
        assert updated.data["metadata"].get(key) == value
    assert updated.data["spec"] == replacement


def test_replace_keeps_the_server_version_fence_after_metadata_read(live, monkeypatch) -> None:
    client, raw, namespace = live
    name = "nvke-race-" + uuid4().hex[:12]
    spec = copy.deepcopy(PUBLIC_SPECS["NVKECluster"])
    created = client.create("NVKECluster", {"metadata": {"name": name}, "spec": spec})
    route = f"/apis/internal.nvke.nvidia.com/v1/namespaces/{namespace}/nvkeclusters/{name}"
    checks = 0

    def race():
        nonlocal checks
        checks += 1
        if checks == 2:
            changed = raw.patch(
                route,
                json={"metadata": {"annotations": {"controller.example/race": "keep"}}},
                headers={"Content-Type": "application/merge-patch+json"},
            )
            changed.raise_for_status()

    monkeypatch.setattr(client, "_before_request", race)
    replacement = {**spec, "controlPlaneClass": "replacement-class"}
    with pytest.raises(NvkeError) as error:
        client.replace(
            "NVKECluster",
            name,
            {"metadata": {"name": name}, "spec": replacement},
            resource_version=created.resource_version,
            uid=created.data["metadata"]["uid"],
        )
    assert error.value.code == "CONFLICT" and error.value.status_code == 409
    retained = client.get("NVKECluster", name).data
    assert retained["spec"] == spec
    assert retained["metadata"]["annotations"]["controller.example/race"] == "keep"


def test_release_deprecation_preserves_published_content_and_rejects_image_rewrite(
    live: tuple[NvkeDatastore, httpx.Client, str],
) -> None:
    client, _raw, _namespace = live
    name = "release-" + uuid4().hex[:12]
    document = {"metadata": {"name": name}, "spec": copy.deepcopy(PUBLIC_SPECS["NodePoolRelease"])}
    created = client.create("NodePoolRelease", document)
    uid = created.data["metadata"]["uid"]
    updated = client.set_deprecated("NodePoolRelease", name, True, uid=uid, resource_version=created.resource_version)
    assert updated.data["spec"]["lifecycle"]["deprecated"] is True
    assert updated.data["spec"]["machineInit"] == document["spec"]["machineInit"]
    document["spec"]["machineImageRef"]["name"] = "other-image"
    with pytest.raises(NvkeError) as immutable:
        client.replace("NodePoolRelease", name, document, resource_version=updated.resource_version)
    assert immutable.value.status_code == 422
    client.delete(
        "NodePoolRelease", name, uid=uid, resource_version=updated.resource_version, propagation_policy="Background"
    )


def test_pagination_retains_opaque_continue_and_does_not_lose_items(
    live: tuple[NvkeDatastore, httpx.Client, str],
) -> None:
    client, _raw, _namespace = live
    label = uuid4().hex
    created = []
    for index in range(3):
        name = f"page-{label[:12]}-{index}"
        created.append(
            client.create(
                "MachineImage",
                {
                    "metadata": {"name": name, "labels": {"astro-test": label}},
                    "spec": copy.deepcopy(PUBLIC_SPECS["MachineImage"]),
                },
            )
        )
    found = []
    token = ""
    for _ in range(4):
        page = client.list("MachineImage", page_size=1, continue_token=token, label_selector=f"astro-test={label}")
        found.extend(item["metadata"]["name"] for item in page.data["items"])
        token = page.data["metadata"].get("continue", "")
        if not token:
            break
    assert sorted(found) == sorted(item.data["metadata"]["name"] for item in created)
    assert len(found) == len(set(found)) == 3
    for item in created:
        client.delete(
            "MachineImage",
            item.data["metadata"]["name"],
            uid=item.data["metadata"]["uid"],
            resource_version=item.resource_version,
        )


def test_namespace_binding_cannot_read_a_sibling_org_cluster(live: tuple[NvkeDatastore, httpx.Client, str]) -> None:
    client, raw, _namespace = live
    other = "nvke-foreign-" + uuid4().hex[:12]
    raw.post("/api/v1/namespaces", json={"metadata": {"name": other}}).raise_for_status()
    document = {
        "apiVersion": "internal.nvke.nvidia.com/v1",
        "kind": "NVKECluster",
        "metadata": {"name": "same-name", "namespace": other},
        "spec": copy.deepcopy(PUBLIC_SPECS["NVKECluster"]),
    }
    raw.post(f"/apis/internal.nvke.nvidia.com/v1/namespaces/{other}/nvkeclusters", json=document).raise_for_status()
    with pytest.raises(NvkeError) as missing:
        client.get("NVKECluster", "same-name")
    assert missing.value.code == "NOT_FOUND"
    assert client.list("NVKECluster").data["items"] == []


def seed_access(live, resource_name="cluster-a"):
    client, raw, namespace = live
    path = Path(__file__).parent / "fixtures/cluster.x-k8s.io_clusters.yaml"
    source = path.read_bytes()
    assert hashlib.sha256(source).hexdigest() == "b72f04bb258d22fafd0c4fbba98feea08b103f1b854a98ebdb14e6a3ab8e9f24"
    manifest = yaml.safe_load(source)
    crd = "/apis/apiextensions.k8s.io/v1/customresourcedefinitions/clusters.cluster.x-k8s.io"
    if raw.get(crd).status_code == 404:
        response = raw.post(crd.rsplit("/", 1)[0], json=manifest)
        assert response.status_code in {201, 409}
    for _index in range(50):
        installed = raw.get(crd)
        installed.raise_for_status()
        if any(
            c["type"] == "Established" and c["status"] == "True"
            for c in (installed.json().get("status", {}).get("conditions") or [])
        ):
            break
        time.sleep(0.1)
    else:
        raise AssertionError("task-owned CAPI CRD did not become established")
    native = client.create(
        "NVKECluster", {"metadata": {"name": resource_name}, "spec": copy.deepcopy(PUBLIC_SPECS["NVKECluster"])}
    ).data
    cluster_id = str(uuid4())
    native["status"] = {"id": cluster_id}
    raw.put(
        f"/apis/internal.nvke.nvidia.com/v1/namespaces/{namespace}/nvkeclusters/{resource_name}/status", json=native
    ).raise_for_status()
    name = cluster_remote_name(namespace, resource_name)
    capi = raw.post(
        f"/apis/cluster.x-k8s.io/v1beta2/namespaces/{namespace}/clusters",
        json={
            "apiVersion": "cluster.x-k8s.io/v1beta2",
            "kind": "Cluster",
            "metadata": {"name": name},
            "spec": {"paused": True},
        },
    )
    assert capi.status_code == 201, capi.json().get("message")
    content = b"apiVersion: v1\nkind: Config\nclusters: [{name: a}]\nusers: [{name: a}]\ncontexts: [{name: a}]\n"
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {
            "name": name + "-admin-kubeconfig",
            "labels": {
                "nvke.nvidia.com/cluster-id": cluster_id,
                "nvke.nvidia.com/kubeconfig-mode": "admin",
                "cluster.x-k8s.io/cluster-name": name,
            },
            "ownerReferences": [
                {
                    "apiVersion": "cluster.x-k8s.io/v1beta2",
                    "kind": "Cluster",
                    "name": name,
                    "uid": capi.json()["metadata"]["uid"],
                    "controller": True,
                }
            ],
        },
        "data": {"value": base64.b64encode(content).decode()},
    }
    created = raw.post(f"/api/v1/namespaces/{namespace}/secrets", json=secret)
    created.raise_for_status()
    return name, cluster_id, content, created.json()


def test_kubeconfig_download_from_real_kubernetes_secret(live: tuple[NvkeDatastore, httpx.Client, str]) -> None:
    client, _raw, namespace = live
    name, _cluster_id, content, secret = seed_access(live)
    result = client.workload_kubeconfig(name, management_namespace=namespace)
    assert result.content == content and result.secret_uid == secret["metadata"]["uid"]


@pytest.mark.parametrize("failure", ["foreign-label", "stale-owner", "sibling-org"])
def test_real_kubernetes_refuses_foreign_or_stale_kubeconfig_ownership(live, failure, tmp_path):
    client, raw, namespace = live
    name, _cluster_id, _content, secret = seed_access(live)
    if failure == "foreign-label":
        secret["metadata"]["labels"]["nvke.nvidia.com/cluster-id"] = str(uuid4())
    elif failure == "stale-owner":
        secret["metadata"]["ownerReferences"][0]["uid"] = str(uuid4())
    if failure != "sibling-org":
        raw.put(f"/api/v1/namespaces/{namespace}/secrets/{secret['metadata']['name']}", json=secret).raise_for_status()
        with pytest.raises(NvkeError) as error:
            client.workload_kubeconfig(name, management_namespace=namespace)
        assert error.value.code == "OWNERSHIP_UNVERIFIED"
    else:
        sibling = "nvke-sibling-" + uuid4().hex[:12]
        raw.post("/api/v1/namespaces", json={"metadata": {"name": sibling}}).raise_for_status()
        # Identical privileged transport, but another org namespace cannot acquire these credentials.
        tls = ssl.create_default_context(cafile=str(tmp_path / "ca.pem"))
        tls.load_cert_chain(str(tmp_path / "cert.pem"), str(tmp_path / "key.pem"))
        with (
            NvkeDatastore(client._endpoint, organization_namespace=sibling, verify=tls) as other,
            pytest.raises(NvkeError) as error,
        ):
            other.workload_kubeconfig(name, management_namespace=namespace)
        assert error.value.code == "OWNERSHIP_UNVERIFIED"


def test_foreground_deletion_is_accepted_but_not_reported_as_completed(
    live: tuple[NvkeDatastore, httpx.Client, str],
) -> None:
    client, _raw, _namespace = live
    name = "pending-delete-" + uuid4().hex[:12]
    created = client.create(
        "ControlPlaneClass", {"metadata": {"name": name}, "spec": PUBLIC_SPECS["ControlPlaneClass"]}
    )
    client.delete(
        "ControlPlaneClass", name, uid=created.data["metadata"]["uid"], resource_version=created.resource_version
    )
    # envtest has admission but no garbage-collection controller. Foreground
    # deletion must remain pending here instead of being treated as absence.
    pending = client.get("ControlPlaneClass", name)
    assert pending.data["metadata"]["deletionTimestamp"]
    assert "foregroundDeletion" in pending.data["metadata"]["finalizers"]


def test_cel_admission_errors_are_preserved_as_input_errors(
    live: tuple[NvkeDatastore, httpx.Client, str],
) -> None:
    client, _raw, _namespace = live
    spec = copy.deepcopy(PUBLIC_SPECS["MachineImage"])
    spec["nico"]["ipxeScript"] = "https://example.com/not-an-ipxe-script"
    with pytest.raises(NvkeError) as exc:
        client.create("MachineImage", {"metadata": {"name": "invalid-" + uuid4().hex[:12]}, "spec": spec})
    assert exc.value.code == "INVALID_INPUT"
    assert exc.value.status_code == 422
