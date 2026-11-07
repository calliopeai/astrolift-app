"""Bind breakglass downloads to native organization and cluster ownership."""

from __future__ import annotations

import base64
import hashlib
from typing import Any
from uuid import UUID

import yaml

from nvke.client import NvkeError
from nvke.contract import Operation
from nvke.datastore import _NAMESPACE, Kubeconfig, NvkeDatastore, _name, _version


def _refused() -> NvkeError:
    return NvkeError("OWNERSHIP_UNVERIFIED", "NVKE workload credential ownership could not be verified")


def _uuid(value: Any) -> str:
    try:
        parsed = UUID(value)
        if not parsed.int or str(parsed) != value:
            raise ValueError
        return str(parsed)
    except (ValueError, TypeError, AttributeError):
        raise _refused() from None


def cluster_remote_name(namespace: str, name: str) -> str:
    """Pinned management-engine/controllers.ClusterRemoteName algorithm."""
    suffix = "-" + hashlib.sha256(f"{namespace}/{name}".encode()).hexdigest()[:8] + "-mgmt-eng"
    return name.replace(".", "-")[: 55 - len(suffix)].rstrip("-") + suffix


def _get(client: NvkeDatastore, path: str, namespace: str, name: str) -> dict[str, Any]:
    result = client._call(
        Operation("datastore.workloadAccessProof", "datastore", "GET", path),
        client._endpoint,
        path={"namespace": namespace, "name": name},
        query={},
        body=None,
    )
    if not isinstance(result.data, dict):
        raise _refused()
    return result.data


def _metadata(document: dict[str, Any], kind: str, namespace: str, name: str) -> dict[str, Any]:
    meta = document.get("metadata")
    if (
        document.get("kind") != kind
        or document.get("apiVersion")
        != {"Cluster": "cluster.x-k8s.io/v1beta2", "NVKECluster": "internal.nvke.nvidia.com/v1", "Secret": "v1"}[kind]
        or not isinstance(meta, dict)
        or meta.get("namespace") != namespace
        or meta.get("name") != name
        or meta.get("deletionTimestamp")
    ):
        raise _refused()
    _uuid(meta.get("uid"))
    if not isinstance(meta.get("resourceVersion"), str):
        raise _refused()
    _version(meta["resourceVersion"])
    return meta


def _native_owner(client: NvkeDatastore, name: str) -> tuple[str, str, str]:
    token = ""
    tokens: set[str] = set()
    found: tuple[str, str, str] | None = None
    version: str | None = None
    for _page in range(2000):
        response = client.list("NVKECluster", page_size=500, continue_token=token)
        data = response.data
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise _refused()
        for item in data["items"]:
            if not isinstance(item, dict) or not isinstance(item.get("metadata"), dict):
                raise _refused()
            resource_name = _name(item["metadata"].get("name"))
            if cluster_remote_name(client._namespace, resource_name) != name:
                continue
            meta = _metadata(item, "NVKECluster", client._namespace, resource_name)
            status = item.get("status")
            if not isinstance(status, dict) or not status.get("id"):
                raise NvkeError(
                    "ACCESS_NOT_READY", "NVKE has not recorded the workload cluster identity", retryable=True
                )
            candidate = (resource_name, meta["uid"], _uuid(status["id"]))
            if found is not None:
                raise _refused()
            found = candidate
        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            raise _refused()
        current_version = metadata.get("resourceVersion")
        if (
            not isinstance(current_version, str)
            or not current_version
            or (version is not None and version != current_version)
        ):
            raise _refused()
        version = current_version
        token = metadata.get("continue", "")
        if not isinstance(token, str) or len(token) > 16384 or token in tokens:
            raise _refused()
        if not token:
            if found is None:
                raise _refused()
            return found
        tokens.add(token)
    raise NvkeError("OWNERSHIP_LIMIT", "NVKE workload ownership exceeds its native page budget")


def _label(labels: dict[str, Any], name: str) -> str:
    current, legacy = labels.get("nvke.nvidia.com/" + name), labels.get("nke.nvidia.com/" + name)
    if current is not None and legacy is not None and current != legacy:
        raise _refused()
    value = current if current is not None else legacy
    if not isinstance(value, str) or not value:
        raise _refused()
    return value


def workload_kubeconfig(
    client: NvkeDatastore,
    cluster_name: str,
    *,
    management_namespace: str,
    trusted_cluster_ids: frozenset[str] = frozenset(),
) -> Kubeconfig:
    cluster_name = _name(cluster_name)
    if not isinstance(management_namespace, str) or _NAMESPACE.fullmatch(management_namespace) is None:
        raise NvkeError("INVALID_INPUT", "Use the configured NVKE management namespace")
    native = None if trusted_cluster_ids else _native_owner(client, cluster_name)
    expected_ids = {_uuid(value) for value in trusted_cluster_ids}
    if native is not None:
        expected_ids.add(native[2])
    capi_path = "/apis/cluster.x-k8s.io/v1beta2/namespaces/{namespace}/clusters/{name}"
    capi = _get(client, capi_path, management_namespace, cluster_name)
    capi_meta = _metadata(capi, "Cluster", management_namespace, cluster_name)
    secret_name = _name(cluster_name + "-admin-kubeconfig")
    secret = _get(client, "/api/v1/namespaces/{namespace}/secrets/{name}", management_namespace, secret_name)
    meta = _metadata(secret, "Secret", management_namespace, secret_name)
    labels, owners = meta.get("labels"), meta.get("ownerReferences")
    if not isinstance(labels, dict) or not isinstance(owners, list) or len(owners) != 1:
        raise _refused()
    owner = owners[0]
    if (
        not isinstance(owner, dict)
        or owner.get("apiVersion") not in {"cluster.x-k8s.io/v1beta1", "cluster.x-k8s.io/v1beta2"}
        or owner.get("kind") != "Cluster"
        or owner.get("name") != cluster_name
        or owner.get("uid") != capi_meta["uid"]
        or owner.get("controller") is not True
        or labels.get("cluster.x-k8s.io/cluster-name") != cluster_name
        or _label(labels, "cluster-id") not in expected_ids
        or _label(labels, "kubeconfig-mode") != "admin"
        or secret.get("type") != "Opaque"
    ):
        raise _refused()
    current = _get(client, capi_path, management_namespace, cluster_name)
    if _metadata(current, "Cluster", management_namespace, cluster_name)["uid"] != capi_meta["uid"]:
        raise _refused()
    if native is not None:
        latest = client.get("NVKECluster", native[0]).data
        if not isinstance(latest, dict):
            raise _refused()
        latest_meta = _metadata(latest, "NVKECluster", client._namespace, native[0])
        latest_status = latest.get("status")
        if (
            latest_meta["uid"] != native[1]
            or not isinstance(latest_status, dict)
            or latest_status.get("id") != native[2]
        ):
            raise _refused()
    try:
        content = base64.b64decode(secret["data"]["value"], validate=True)
        config = yaml.safe_load(content)
        if not isinstance(config, dict) or config.get("kind") != "Config" or config.get("apiVersion") != "v1":
            raise ValueError
        if not config.get("clusters") or not config.get("users") or not config.get("contexts"):
            raise ValueError
    except (KeyError, TypeError, ValueError, yaml.YAMLError):
        raise NvkeError("INVALID_RESPONSE", "NVKE admin kubeconfig Secret is missing valid kubeconfig data") from None
    return Kubeconfig(content, meta["uid"], meta["resourceVersion"])
