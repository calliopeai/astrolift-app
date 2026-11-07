"""Explicit management-Kubernetes adapter; never a fallback from public APIs."""

from __future__ import annotations

import base64
import copy
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from nvke.client import _NAME, _VERSION, Endpoint, NvkeClient, NvkeError, NvkeResponse, _strict_object, _validate
from nvke.contract import Operation, Resource, resource_schema, resources

_NAMESPACE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?\Z")


def _name(value: str) -> str:
    if not isinstance(value, str) or len(value) > 253 or _NAME.fullmatch(value) is None:
        raise NvkeError("INVALID_INPUT", "Use a valid NVKE resource name")
    return value


def _version(value: str) -> str:
    if not isinstance(value, str) or not value or _VERSION.fullmatch(value) is None:
        raise NvkeError("INVALID_INPUT", "Use the current opaque Kubernetes resource version")
    return value


@dataclass(frozen=True)
class Kubeconfig:
    content: bytes = field(repr=False)
    secret_uid: str
    secret_resource_version: str


class NvkeDatastore(NvkeClient):
    def __init__(self, endpoint: Endpoint, *, organization_namespace: str, **kwargs: Any) -> None:
        if endpoint.surface != "datastore":
            raise ValueError("NVKE management Kubernetes requires a dedicated datastore endpoint")
        if _NAMESPACE.fullmatch(organization_namespace) is None:
            raise ValueError("NVKE organization namespace must be a valid Kubernetes namespace")
        super().__init__([endpoint], **kwargs)
        self._endpoint = endpoint
        # The namespace is bound by the site's trusted org mapping, not by an
        # operation's caller-supplied document, selectors or resource name.
        self._namespace = organization_namespace
        self._resources = {res.kind: res for res in resources()}

    def _resource(self, kind: str) -> Resource:
        resource = self._resources.get(kind)
        if resource is None:
            raise NvkeError("INVALID_INPUT", "Unknown NVKE management resource kind")
        return resource

    def _route(self, kind: str, name: str | None = None) -> tuple[str, dict[str, str]]:
        resource = self._resource(kind)
        base = f"/apis/{resource.group}/{resource.version}"
        path = {}
        if resource.namespaced:
            base += "/namespaces/{namespace}"
            path["namespace"] = self._namespace
        base += f"/{resource.plural}"
        if name is not None:
            base += "/{name}"
            path["name"] = _name(name)
        return base, path

    def _request(
        self,
        kind: str,
        method: str,
        *,
        name: str | None = None,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        content_type: str | None = None,
    ) -> NvkeResponse:
        route, path = self._route(kind, name)
        response = self._call(
            Operation(f"datastore.{method.lower()}{kind}", "datastore", method, route, kind),
            self._endpoint,
            path=path,
            query=query or {},
            body=body,
            headers={"Content-Type": content_type} if content_type else None,
        )
        if not isinstance(response.data, dict):
            raise NvkeError(
                "INVALID_RESPONSE", "NVKE management Kubernetes returned an invalid object", ambiguous=method != "GET"
            )
        metadata = response.data.get("metadata", {})
        if not isinstance(metadata, dict):
            raise NvkeError(
                "INVALID_RESPONSE", "NVKE management Kubernetes returned invalid metadata", ambiguous=method != "GET"
            )
        version = metadata.get("resourceVersion")
        if version is not None and (not isinstance(version, str) or _VERSION.fullmatch(version) is None):
            raise NvkeError(
                "INVALID_RESPONSE",
                "NVKE management Kubernetes returned an invalid resource version",
                ambiguous=method != "GET",
            )
        return NvkeResponse(response.status_code, response.data, version)

    def get(self, kind: str, name: str) -> NvkeResponse:
        return self._request(kind, "GET", name=name)

    def list(
        self,
        kind: str,
        *,
        page_size: int = 100,
        continue_token: str = "",
        label_selector: str = "",
        field_selector: str = "",
    ) -> NvkeResponse:
        _validate(
            {
                "type": "object",
                "properties": {
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 500},
                    "continue_token": {"type": "string"},
                    "label_selector": {"type": "string"},
                    "field_selector": {"type": "string"},
                },
            },
            {
                "page_size": page_size,
                "continue_token": continue_token,
                "label_selector": label_selector,
                "field_selector": field_selector,
            },
        )
        query: dict[str, Any] = {"limit": page_size}
        for name, value in (
            ("continue", continue_token),
            ("labelSelector", label_selector),
            ("fieldSelector", field_selector),
        ):
            if value:
                query[name] = value
        return self._request(kind, "GET", query=query)

    def _document(self, kind: str, document: dict[str, Any]) -> dict[str, Any]:
        resource = self._resource(kind)
        version = f"{resource.group}/{resource.version}"
        _validate(
            {
                "type": "object",
                "required": ["metadata", "spec"],
                "additionalProperties": False,
                "properties": {
                    "apiVersion": {"enum": [version]},
                    "kind": {"enum": [kind]},
                    "metadata": {
                        "type": "object",
                        "required": ["name"],
                        "additionalProperties": False,
                        "properties": {
                            "name": {"type": "string", "pattern": _NAME.pattern, "maxLength": 253},
                            "namespace": {"enum": [self._namespace]} if resource.namespaced else {"enum": [""]},
                            "annotations": {"type": "object", "additionalProperties": {"type": "string"}},
                            "labels": {"type": "object", "additionalProperties": {"type": "string"}},
                        },
                    },
                    "spec": _strict_object(resource_schema(kind)["properties"]["spec"]),
                },
            },
            document,
        )
        body = copy.deepcopy(document)
        body["apiVersion"] = version
        body["kind"] = kind
        if resource.namespaced:
            body["metadata"]["namespace"] = self._namespace
        return body

    def create(self, kind: str, document: dict[str, Any], *, dry_run: bool = False) -> NvkeResponse:
        body = self._document(kind, document)
        query = {"fieldValidation": "Strict"}
        if dry_run:
            query["dryRun"] = "All"
        return self._request(kind, "POST", body=body, query=query)

    def replace(
        self, kind: str, name: str, document: dict[str, Any], *, resource_version: str, dry_run: bool = False
    ) -> NvkeResponse:
        body = self._document(kind, document)
        if body["metadata"]["name"] != _name(name):
            raise NvkeError("INVALID_INPUT", "NVKE document name must match the resource path")
        body["metadata"]["resourceVersion"] = _version(resource_version)
        query = {"fieldValidation": "Strict"}
        if dry_run:
            query["dryRun"] = "All"
        return self._request(kind, "PUT", name=name, body=body, query=query)

    def set_deprecated(
        self, kind: str, name: str, deprecated: bool, *, resource_version: str, uid: str
    ) -> NvkeResponse:
        if kind not in {"ClusterRelease", "NodePoolRelease"} or not isinstance(deprecated, bool):
            raise NvkeError("INVALID_INPUT", "Only cluster and node-pool releases support deprecation")
        body = {
            "metadata": {"resourceVersion": _version(resource_version), "uid": _version(uid)},
            "spec": {"lifecycle": {"deprecated": deprecated}},
        }
        return self._request(
            kind,
            "PATCH",
            name=name,
            body=body,
            query={"fieldValidation": "Strict"},
            content_type="application/merge-patch+json",
        )

    def delete(
        self, kind: str, name: str, *, uid: str, resource_version: str, propagation_policy: str = "Foreground"
    ) -> NvkeResponse:
        if propagation_policy not in {"Foreground", "Background"}:
            raise NvkeError("INVALID_INPUT", "Use Foreground or Background propagation")
        body = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {"uid": _version(uid), "resourceVersion": _version(resource_version)},
            "propagationPolicy": propagation_policy,
        }
        return self._request(kind, "DELETE", name=name, body=body)

    def workload_kubeconfig(self, cluster_name: str, *, management_namespace: str) -> Kubeconfig:
        cluster_name = _name(cluster_name)
        if _NAMESPACE.fullmatch(management_namespace) is None:
            raise NvkeError("INVALID_INPUT", "Use the configured NVKE management namespace")
        secret_name = _name(f"{cluster_name}-admin-kubeconfig")
        result = self._call(
            Operation(
                "datastore.getWorkloadKubeconfig",
                "datastore",
                "GET",
                "/api/v1/namespaces/{namespace}/secrets/{name}",
            ),
            self._endpoint,
            path={"namespace": management_namespace, "name": secret_name},
            query={},
            body=None,
        )
        try:
            content = base64.b64decode(result.data["data"]["value"], validate=True)
            config = yaml.safe_load(content)
            metadata = result.data["metadata"]
            uid = _version(metadata["uid"])
            version = _version(metadata["resourceVersion"])
            if not isinstance(config, dict) or config.get("kind") != "Config" or config.get("apiVersion") != "v1":
                raise ValueError("invalid kubeconfig")
            if not config.get("clusters") or not config.get("users") or not config.get("contexts"):
                raise ValueError("incomplete kubeconfig")
        except (KeyError, TypeError, ValueError, yaml.YAMLError, NvkeError):
            raise NvkeError(
                "INVALID_RESPONSE", "NVKE admin kubeconfig Secret is missing valid kubeconfig data"
            ) from None
        return Kubeconfig(content, uid, version)
