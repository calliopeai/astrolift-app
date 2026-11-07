"""Actor-bound pages over complete encrypted native observations."""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.core import signing
from nvke.client import NvkeError
from nvke.inventory import collection, identity
from nvke.operations import action

from astrolift_clusters import nvke_operations as operations
from astrolift_clusters import nvke_sites as sites
from core.permissions import Permission

_SALT = "astrolift.nvke.inventory.v1"
_CONDITION = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")


@dataclass(frozen=True)
class InventoryItem:
    key: str
    identity: str
    name: str
    uid: str
    namespace: str
    resource_version: str
    generation: str
    kubernetes_version: str
    desired_count: str
    conditions: list[tuple[str, str]]
    conditions_omitted: int


def _actor(expected_actor_id):
    org, actor = sites.authorized(Permission.NVKE_READ)
    if type(expected_actor_id) is not int or actor.pk != expected_actor_id:
        raise NvkeError("SOURCE_CHANGED", "The reviewed NVKE caller changed")
    return org, actor


def _snapshot(operation_id, expected_actor_id):
    org, actor = _actor(expected_actor_id)
    row = operations.read(operation_id)
    sites.authorized(Permission(action(row.operation_key).permission))
    if row.authority.get("inventory") is not True or row.status != "succeeded":
        raise NvkeError(
            "SNAPSHOT_UNAVAILABLE", "A complete successful NVKE inventory observation is required"
        )
    selected = collection(row.operation_key)
    data = operations.decode_result(row).get("response", {}).get("data", {})
    if (
        not isinstance(data, dict)
        or data.get("inventory_format") != 1
        or data.get("collection") != selected.key
        or data.get("complete") is not True
        or not isinstance(data.get("items"), list)
    ):
        raise NvkeError("SNAPSHOT_UNAVAILABLE", "The complete NVKE inventory observation is unavailable")
    return row, selected, data, org, actor


def _load(token):
    if not isinstance(token, str) or len(token) > 4096:
        raise NvkeError("INVALID_CURSOR", "Use a cursor from this NVKE inventory observation")
    try:
        value = signing.Signer(salt=_SALT).unsign_object(token)
    except (signing.BadSignature, ValueError, TypeError):
        raise NvkeError("INVALID_CURSOR", "Use a cursor from this NVKE inventory observation") from None
    if not isinstance(value, list):
        raise NvkeError("INVALID_CURSOR", "Use a cursor from this NVKE inventory observation")
    return value


def _text(value, limit=512):
    return value[:limit] if isinstance(value, str) else ""


def _number(value):
    return str(value) if type(value) is int else ""


def _object(value):
    return value if isinstance(value, dict) else {}


def _item(row, selected, resource, index):
    if not isinstance(resource, dict):
        raise NvkeError("SNAPSHOT_UNAVAILABLE", "The NVKE inventory resource is unavailable")
    metadata, spec, status = (_object(resource.get(key)) for key in ("metadata", "spec", "status"))
    native_id = identity(selected, resource)
    raw_conditions = resource.get("conditions", status.get("conditions", []))
    entries = (
        raw_conditions.items()
        if isinstance(raw_conditions, dict)
        else ((entry.get("type"), entry) for entry in raw_conditions if isinstance(entry, dict))
        if isinstance(raw_conditions, list)
        else []
    )
    conditions = [
        (name, value["status"])
        for name, value in entries
        if isinstance(name, str)
        and _CONDITION.fullmatch(name)
        and isinstance(value, dict)
        and isinstance(value.get("status"), str)
        and value.get("status") in {"True", "False", "Unknown"}
    ]
    key = signing.Signer(salt=_SALT).sign_object(["resource", str(row.guid), row.version, index])
    return InventoryItem(
        key=key,
        identity=native_id,
        name=_text(resource.get("name", metadata.get("name", native_id))),
        uid=_text(metadata.get("uid")),
        namespace=_text(metadata.get("namespace")),
        resource_version=_text(metadata.get("resourceVersion")),
        generation=_number(metadata.get("generation")),
        kubernetes_version=_text(
            resource.get(
                "kubernetesVersion",
                resource.get("controlPlaneKubernetesVersion", spec.get("kubernetesVersion")),
            )
        ),
        desired_count=_number(
            resource.get("desiredNodeCount", resource.get("desiredCount", spec.get("replicas")))
        ),
        conditions=conditions[:64],
        conditions_omitted=max(0, len(conditions) - 64),
    )


def page(*, operation_id, expected_actor_id, after=None, limit=50, search=""):
    row, selected, data, org, actor = _snapshot(operation_id, expected_actor_id)
    if type(limit) is not int or not 1 <= limit <= 200 or not isinstance(search, str) or len(search) > 200:
        raise NvkeError(
            "INVALID_INPUT", "Use an inventory page size 1–200 and a search of at most 200 characters"
        )
    binding = ["page", str(org.guid), actor.pk, str(row.guid), row.version, selected.key, search, limit]
    offset = 0
    if after is not None:
        cursor = _load(after)
        if (
            len(cursor) != len(binding) + 1
            or cursor[:-1] != binding
            or type(cursor[-1]) is not int
            or cursor[-1] < 0
        ):
            raise NvkeError("INVALID_CURSOR", "Use a cursor from this NVKE inventory observation and search")
        offset = cursor[-1]
    # Filtering happens across the complete native snapshot before server pagination.
    needle = search.casefold()
    indices = [
        index
        for index, resource in enumerate(data["items"])
        if not needle
        or needle in identity(selected, resource).casefold()
        or needle in _text(resource.get("name", _object(resource.get("metadata")).get("name"))).casefold()
    ]
    if offset > len(indices):
        raise NvkeError("INVALID_CURSOR", "The NVKE inventory cursor is outside this observation")
    selected_indices = indices[offset : offset + limit]
    items = [_item(row, selected, data["items"][index], index) for index in selected_indices]
    next_offset = offset + len(items)
    cursor = (
        signing.Signer(salt=_SALT).sign_object([*binding, next_offset])
        if next_offset < len(indices)
        else None
    )
    return row, selected, items, cursor, len(indices), data


def resource(*, operation_id, expected_actor_id, resource_key):
    row, selected, data, _org, _actor_value = _snapshot(operation_id, expected_actor_id)
    token = _load(resource_key)
    if (
        len(token) != 4
        or token[:3] != ["resource", str(row.guid), row.version]
        or type(token[3]) is not int
        or not 0 <= token[3] < len(data["items"])
    ):
        raise NvkeError("INVALID_CURSOR", "Use a resource from this NVKE inventory observation")
    return row, selected, _item(row, selected, data["items"][token[3]], token[3]), data, token[3]


def document(*, operation_id, expected_actor_id, resource_key):
    _row, _selected, _item_value, data, index = resource(
        operation_id=operation_id, expected_actor_id=expected_actor_id, resource_key=resource_key
    )
    return operations.canonical(data["items"][index], limit=64 * 1024 * 1024).decode("utf-8")
