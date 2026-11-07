"""Executable HTTP and management-Kubernetes catalog used by API/UI/CLI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nvke.client import Endpoint, NvkeClient, NvkeError, NvkeResponse, _validate
from nvke.contract import Operation, operations, resources
from nvke.datastore import _NAMESPACE, NvkeDatastore, _name, _version


@dataclass(frozen=True)
class Action:
    key: str
    surface: str
    verb: str
    kind: str = ""
    http: Operation | None = None

    @property
    def mutates(self) -> bool:
        return self.http.mutates if self.http else self.verb not in {"get", "list", "kubeconfig"}

    @property
    def permission(self) -> str:
        if self.surface == "operator" or (self.surface == "datastore" and self.kind != "NVKECluster"):
            return "nvke.admin"
        if self.key in {"listConfigProfiles", "updateClusterSpireServer"}:
            return "nvke.admin"
        return "nvke.manage" if self.mutates else "nvke.read"


def actions() -> tuple[Action, ...]:
    result = [Action(op.id, op.surface, op.method, http=op) for op in operations()]
    for resource in resources():
        for verb in ("get", "list", "create", "replace", "delete"):
            result.append(Action(f"datastore.{verb}{resource.kind}", "datastore", verb, resource.kind))
    for kind in ("ClusterRelease", "NodePoolRelease"):
        result.append(Action(f"datastore.deprecate{kind}", "datastore", "deprecate", kind))
    result.append(Action("datastore.getWorkloadKubeconfig", "datastore", "kubeconfig"))
    return tuple(result)


def action(key: str) -> Action:
    for candidate in actions():
        if candidate.key == key:
            return candidate
    raise NvkeError("INVALID_INPUT", "Unknown NVKE operation")


def validate_request(key: str, request: dict[str, Any], namespace: str) -> Action:
    selected = action(key)
    if selected.http:
        _validate(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "object"},
                    "query": {"type": "object"},
                    "body": {"type": ["object", "null"]},
                    "resource_version": {"type": ["string", "null"]},
                },
            },
            request,
        )
        with NvkeClient([]) as client:
            client.prepare(key, **request)
        return selected
    properties: dict[str, Any] = {}
    required = []
    if selected.verb in {"get", "replace", "delete", "deprecate"}:
        properties["name"] = {"type": "string"}
        required.append("name")
    if selected.verb in {"replace", "delete", "deprecate"}:
        properties["resource_version"] = {"type": "string"}
        required.append("resource_version")
    if selected.verb in {"delete", "deprecate"}:
        properties["uid"] = {"type": "string"}
        required.append("uid")
    if selected.verb in {"create", "replace"}:
        properties.update(document={"type": "object"}, dry_run={"type": "boolean"})
        required.append("document")
    if selected.verb == "delete":
        properties["propagation_policy"] = {"enum": ["Foreground", "Background"]}
    if selected.verb == "deprecate":
        properties["deprecated"] = {"type": "boolean"}
        required.append("deprecated")
    if selected.verb == "list":
        properties.update(
            page_size={"type": "integer", "minimum": 1, "maximum": 500},
            continue_token={"type": "string"},
            label_selector={"type": "string"},
            field_selector={"type": "string"},
        )
    if selected.verb == "kubeconfig":
        properties.update(cluster_name={"type": "string"}, management_namespace={"type": "string"})
        required.extend(["cluster_name", "management_namespace"])
    _validate(
        {"type": "object", "properties": properties, "required": required, "additionalProperties": False}, request
    )
    if "management_namespace" in request and _NAMESPACE.fullmatch(request["management_namespace"]) is None:
        raise NvkeError("INVALID_INPUT", "Use the configured NVKE management namespace")
    for name in ("name", "cluster_name"):
        if name in request:
            _name(request[name])
    for name in ("uid", "resource_version"):
        if name in request:
            _version(request[name])
    if "document" in request:
        with NvkeDatastore(
            Endpoint("datastore", "https://validation.invalid"), organization_namespace=namespace
        ) as datastore:
            body = datastore._document(selected.kind, request["document"])
            if "name" in request and request["name"] != body["metadata"]["name"]:
                raise NvkeError("INVALID_INPUT", "NVKE document name must match the resource path")
    return selected


def execute_action(
    client: NvkeClient,
    selected: Action,
    request: dict[str, Any],
    *,
    trusted_cluster_ids: frozenset[str] = frozenset(),
) -> NvkeResponse:
    if selected.http:
        return client.execute(selected.key, **request)
    if not isinstance(client, NvkeDatastore):
        raise NvkeError("SURFACE_UNAVAILABLE", "NVKE datastore connection is required")
    if selected.verb == "kubeconfig":
        value = client.workload_kubeconfig(**request, trusted_cluster_ids=trusted_cluster_ids)
        return NvkeResponse(200, {"kubeconfig": value.content.decode("utf-8")}, value.secret_resource_version)
    method = "set_deprecated" if selected.verb == "deprecate" else selected.verb
    result: NvkeResponse = getattr(client, method)(selected.kind, **request)
    return result
