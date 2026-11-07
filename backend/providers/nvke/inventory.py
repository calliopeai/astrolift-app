"""Complete, bounded native inventory reads without changing native list semantics."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from nvke.client import NvkeClient, NvkeError, NvkeResponse
from nvke.contract import controller_spec, resources
from nvke.operations import Action, action, actions, execute_action, validate_request

MAX_INVENTORY_BYTES = 48 * 1024 * 1024
MAX_INVENTORY_ITEMS = 100_000
MAX_INVENTORY_PAGES = 2000


@dataclass(frozen=True)
class Collection:
    key: str
    surface: str
    kind: str
    item_field: str
    namespaced: bool

    @property
    def permission(self) -> str:
        return action(self.key).permission


@lru_cache(maxsize=1)
def collections() -> tuple[Collection, ...]:
    spec = controller_spec()
    schemas = spec["components"]["schemas"]
    namespace = {item.kind: item.namespaced for item in resources()}
    result = []
    for selected in actions():
        if selected.http and selected.http.id.split(".")[-1].startswith("list"):
            if selected.surface == "controller":
                operation = spec["paths"][selected.http.path]["get"]
                schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
                schema = schemas[schema["$ref"].split("/")[-1]]
                arrays = [(key, value) for key, value in schema["properties"].items() if value.get("type") == "array"]
                if len(arrays) != 1:
                    raise ValueError("pinned inventory response must contain exactly one resource array")
                field, values = arrays[0]
                kind = values["items"]["$ref"].split("/")[-1]
            else:
                kind, field = selected.http.resource, "items"
            result.append(Collection(selected.key, selected.surface, kind, field, namespace.get(kind, False)))
        elif selected.surface == "datastore" and selected.verb == "list":
            result.append(Collection(selected.key, selected.surface, selected.kind, "items", namespace[selected.kind]))
    return tuple(result)


def collection(key: str) -> Collection:
    for candidate in collections():
        if candidate.key == key:
            return candidate
    raise NvkeError("INVALID_INPUT", "Choose a supported NVKE inventory collection")


def resource_actions(selected: Collection) -> tuple[Action, ...]:
    base = action(selected.key)
    if base.http:
        path = re.compile(
            re.escape(base.http.path)
            + r"/\{[^}]+\}"
            + (r"(?:/[^{}]+)*" if selected.surface == "controller" else "")
            + r"\Z"
        )
        return tuple(
            item
            for item in actions()
            if item.http
            and item.surface == selected.surface
            and (item.http.path == base.http.path or path.fullmatch(item.http.path))
        )
    return tuple(item for item in actions() if item.surface == selected.surface and item.kind == selected.kind)


def validate_inventory_request(key: str, parameters: dict[str, Any]) -> Collection:
    selected = collection(key)
    if not isinstance(parameters, dict) or not isinstance(parameters.get("query", {}), dict):
        raise NvkeError("INVALID_INPUT", "Use native NVKE inventory parameters")
    token = (
        parameters.get("continue_token")
        if selected.surface == "datastore"
        else parameters.get("query", {}).get("pageKey")
    )
    if token:
        raise NvkeError("INVALID_INPUT", "Complete inventory reads must start at the first native page")
    return selected


def identity(selected: Collection, item: dict[str, Any]) -> str:
    metadata = item.get("metadata", {})
    value = (
        item.get("id", item.get("name"))
        if selected.surface == "controller"
        else metadata.get("name")
        if isinstance(metadata, dict)
        else None
    )
    if not isinstance(value, str) or not value or len(value) > 512:
        raise NvkeError("INVALID_RESPONSE", "NVKE inventory returned an invalid resource identity")
    return value


def collect(client: NvkeClient, key: str, parameters: dict[str, Any], namespace: str) -> NvkeResponse:
    selected = validate_inventory_request(key, parameters)
    validate_request(key, parameters, namespace)
    native = action(key)
    request = copy.deepcopy(parameters)
    if selected.surface == "datastore":
        request.setdefault("page_size", 500)
    elif selected.surface != "controller":
        request.setdefault("query", {}).setdefault("pageSize", 500)
    items: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    tokens: set[str] = set()
    resource_version: str | None = None
    encoded_bytes = 0
    for page in range(1, MAX_INVENTORY_PAGES + 1):
        response = execute_action(client, native, request)
        data = response.data
        if not isinstance(data, dict) or not isinstance(data.get(selected.item_field), list):
            raise NvkeError("INVALID_RESPONSE", "NVKE inventory returned an invalid resource page")
        for item in data[selected.item_field]:
            if not isinstance(item, dict):
                raise NvkeError("INVALID_RESPONSE", "NVKE inventory returned an invalid resource")
            identifier = identity(selected, item)
            if identifier in identifiers:
                raise NvkeError("INVALID_RESPONSE", "NVKE inventory repeated a resource identity")
            if (
                selected.namespaced
                and selected.surface == "datastore"
                and item.get("metadata", {}).get("namespace") != namespace
            ):
                raise NvkeError("INVALID_RESPONSE", "NVKE inventory returned a resource outside its bound namespace")
            try:
                encoded_bytes += len(
                    json.dumps(item, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
                )
            except (ValueError, TypeError, UnicodeError):
                raise NvkeError("INVALID_RESPONSE", "NVKE inventory returned an invalid native value") from None
            if len(items) >= MAX_INVENTORY_ITEMS or encoded_bytes > MAX_INVENTORY_BYTES:
                raise NvkeError("INVENTORY_LIMIT", "Inventory exceeds its snapshot budget; narrow native filters")
            identifiers.add(identifier)
            items.append(item)
        if selected.surface == "datastore":
            metadata = data.get("metadata", {})
            if (
                not isinstance(metadata, dict)
                or not isinstance(metadata.get("resourceVersion"), str)
                or not metadata["resourceVersion"]
            ):
                raise NvkeError("INVALID_RESPONSE", "NVKE inventory lacks its Kubernetes snapshot version")
            if resource_version is not None and resource_version != metadata["resourceVersion"]:
                raise NvkeError("SOURCE_CHANGED", "Kubernetes inventory changed between native snapshot pages")
            resource_version = metadata["resourceVersion"]
            token = metadata.get("continue", "")
        elif selected.surface == "controller":
            token = ""
        else:
            token = data.get("pageKey")
            if type(data.get("hasMore")) is not bool or not isinstance(token, str) or data["hasMore"] != bool(token):
                raise NvkeError("INVALID_RESPONSE", "NVKE inventory returned invalid continuation metadata")
        if not isinstance(token, str) or len(token) > 16384:
            raise NvkeError("INVALID_RESPONSE", "NVKE inventory returned an invalid continuation token")
        if not token:
            return NvkeResponse(
                200,
                {
                    "inventory_format": 1,
                    "collection": key,
                    "items": items,
                    "complete": True,
                    "native_page_count": page,
                    "resource_version": resource_version or "",
                },
                resource_version,
            )
        if token in tokens:
            raise NvkeError("INVALID_RESPONSE", "NVKE inventory repeated a continuation token")
        tokens.add(token)
        if selected.surface == "datastore":
            request["continue_token"] = token
        else:
            request["query"]["pageKey"] = token
    raise NvkeError("INVENTORY_LIMIT", "Inventory exceeds its page budget; increase native page size or narrow filters")
