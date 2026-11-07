"""Typed, actor-bound metadata from saved cluster diagnostic observations."""

import hmac
import ipaddress
import re
from datetime import datetime
from uuid import UUID

from django.core import signing
from nvke.client import NvkeError
from nvke.operations import action

from astrolift_clusters import nvke_operations as operations
from astrolift_clusters import nvke_sites as sites
from astrolift_clusters.nvke_resource_operations import digest
from core.permissions import Permission

KINDS = {
    "getClusterControlPlane": "control_plane",
    "getClusterControlPlaneConfigHistory": "config_history",
    "getClusterSpireServer": "spire",
}
MAX_ROWS = 100_000
_SALT = "astrolift.nvke.diagnostics.v1"
_FIELDS = frozenset(
    {
        "release",
        "configProfile",
        "kubernetesVersion",
        "systemReservedMemory",
        "kubeReservedMemory",
        "teleportEnabled",
        "provisioningMonitorDisabled",
    }
)
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z", re.I)
_VERSION = re.compile(r"v?\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.-]+)?\Z")


def _object(value):
    return value if isinstance(value, dict) else {}


def _bool(value):
    return value if type(value) is bool else None


def _count(value):
    return str(value) if type(value) is int and value >= 0 else None


def _name(value):
    return value if isinstance(value, str) and _NAME.fullmatch(value) else None


def _uuid(value):
    try:
        identity = UUID(value) if isinstance(value, str) else None
        return str(identity) if identity and identity.int else None
    except ValueError:
        return None


def _date(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.isoformat() if parsed.tzinfo and parsed.utcoffset() is not None else None
    except ValueError:
        return None


def _address(value):
    if _name(value):
        return value
    if isinstance(value, str) and len(value) <= 253:
        try:
            ipaddress.ip_address(value)
            return value
        except ValueError:
            pass
    return None


def _port(value):
    return value if type(value) is int and 1 <= value <= 65535 else None


def _summary(kind, data):
    result = {
        "kind": kind,
        "configuration_id": None,
        "kubernetes_version": None,
        "requested_address": None,
        "requested_port": None,
        "counts": [],
        "flags": [],
        "conditions": [],
    }
    if kind == "control_plane":
        config, status = (_object(data.get(key)) for key in ("config", "status"))
        rollout = _object(status.get("rollout"))
        result["configuration_id"] = _uuid(config.get("configurationId"))
        version = status.get("kubernetesVersion")
        result["kubernetes_version"] = (
            version
            if isinstance(version, str) and len(version) <= 128 and _VERSION.fullmatch(version)
            else None
        )
        for source, fields in (
            (config, ("desiredCount", "maxSurge")),
            (status, ("nodeCount", "readyNodeCount", "deletingNodeCount", "deleteRequestedNodeCount")),
            (rollout, ("managedNodeCount", "upToDateNodeCount", "pendingNodeCount", "inProgressNodeCount")),
        ):
            result["counts"].extend({"name": field, "value": _count(source.get(field))} for field in fields)
        for source, fields in (
            (config, ("autoScaleDownDisabled", "nodeRebalanceDisabled", "provisioningMonitorDisabled")),
            (rollout, ("inProgress", "recyclingEnabled")),
        ):
            result["flags"].extend({"name": field, "value": _bool(source.get(field))} for field in fields)
        for field in ("controlPlaneUpgrading", "cpNodesRecycling"):
            value = _object(status.get(field)).get("status")
            result["conditions"].append(
                {
                    "name": field,
                    "value": value
                    if isinstance(value, str) and value in {"True", "False", "Unknown"}
                    else None,
                }
            )
    elif kind == "spire":
        requested = _object(data.get("requested"))
        result.update(
            requested_address=_address(requested.get("address")), requested_port=_port(requested.get("port"))
        )
    return result


def _item(kind, value, index):
    result = {
        "key": str(index),
        "native_id": None,
        "name": None,
        "created_at": None,
        "is_current": None,
        "reachable": None,
        "has_valid_svid": None,
        "server_address": None,
        "server_port": None,
        "svid_expires_at": None,
        "changed_fields": [],
        "changed_field_count": None,
        "fields_omitted": None,
    }
    if kind == "spire":
        result.update(
            native_id=_uuid(value.get("nodeID")),
            name=_name(value.get("name")),
            reachable=_bool(value.get("reachable")),
            has_valid_svid=_bool(value.get("hasValidSVID")),
            server_address=_address(value.get("serverAddress")),
            server_port=_port(value.get("serverPort")),
            svid_expires_at=_date(value.get("svidExpiresAt")),
        )
    elif kind == "config_history":
        fields = value.get("changedFields")
        if not isinstance(fields, list):
            raise NvkeError("SNAPSHOT_UNAVAILABLE", "The native configuration history is unavailable")
        safe = [
            field.get("field")
            for field in fields
            if isinstance(field, dict) and isinstance(field.get("field"), str) and field["field"] in _FIELDS
        ]
        result.update(
            native_id=_uuid(value.get("configurationID")),
            created_at=_date(value.get("createdAt")),
            is_current=_bool(value.get("isCurrent")),
            changed_fields=safe[:64],
            changed_field_count=str(len(fields)),
            fields_omitted=str(len(fields) - len(safe[:64])),
        )
    return result


def _search(item):
    return " ".join(
        str(value) if not isinstance(value, list) else " ".join(value)
        for key, value in item.items()
        if key != "key" and value is not None
    ).casefold()


def page(*, site_id, operation_id, expected_actor_id, expected_version, after=None, limit=50, search=""):
    org, actor = sites.authorized(Permission.NVKE_READ)
    if type(expected_actor_id) is not int or expected_actor_id != actor.pk:
        raise NvkeError("SOURCE_CHANGED", "The reviewed NVKE caller changed")
    row = operations.read(operation_id)
    sites.authorized(Permission(action(row.operation_key).permission))
    if str(row.site.guid) != site_id:
        raise NvkeError("NOT_FOUND", "NVKE operation not found")
    guard = row.authority.get("resource", {})
    if (
        type(expected_version) is not int
        or row.version != expected_version
        or row.status != "succeeded"
        or row.operation_key not in KINDS
        or guard.get("subresource") != row.operation_key
        or guard.get("collection") != "listClusters"
        or guard.get("mutates") is not False
    ):
        raise NvkeError("SNAPSHOT_UNAVAILABLE", "A successful bound cluster diagnostic read is required")
    if type(limit) is not int or not 1 <= limit <= 200 or not isinstance(search, str) or len(search) > 200:
        raise NvkeError(
            "INVALID_INPUT", "Use a diagnostic page size 1–200 and a search of at most 200 characters"
        )
    target = operations.decode_input(row).get("path", {}).get("clusterID")
    cluster_id = _uuid(target)
    if (
        cluster_id is None
        or cluster_id != target
        or guard.get("identity_action") != "getCluster"
        or not hmac.compare_digest(digest({"identity": cluster_id}), guard.get("identity_digest", ""))
    ):
        raise NvkeError("SNAPSHOT_UNAVAILABLE", "The diagnostic observation lost its fixed cluster identity")
    data = operations.decode_result(row).get("response", {}).get("data")
    if not isinstance(data, dict):
        raise NvkeError("SNAPSHOT_UNAVAILABLE", "The saved native diagnostic observation is unavailable")
    kind = KINDS[row.operation_key]
    if kind == "control_plane" and any(not isinstance(data.get(key), dict) for key in ("config", "status")):
        raise NvkeError("SNAPSHOT_UNAVAILABLE", "The native control-plane observation is unavailable")
    values = data.get("nodes") if kind == "spire" else data.get("changes") if kind == "config_history" else []
    if (
        not isinstance(values, list)
        or len(values) > MAX_ROWS
        or any(not isinstance(value, dict) for value in values)
    ):
        raise NvkeError("SNAPSHOT_UNAVAILABLE", "The complete native diagnostic observation is unavailable")
    if kind == "config_history" and any(not isinstance(value.get("changedFields"), list) for value in values):
        raise NvkeError("SNAPSHOT_UNAVAILABLE", "The native configuration history is unavailable")
    binding = [str(org.guid), actor.pk, site_id, str(row.guid), row.version, row.operation_key, search, limit]
    offset = 0
    if after is not None:
        try:
            token = (
                signing.Signer(salt=_SALT).unsign_object(after)
                if isinstance(after, str) and len(after) <= 4096
                else None
            )
        except (signing.BadSignature, ValueError, TypeError):
            token = None
        if (
            not isinstance(token, list)
            or len(token) != len(binding) + 1
            or token[:-1] != binding
            or type(token[-1]) is not int
            or token[-1] < 0
        ):
            raise NvkeError("INVALID_CURSOR", "Use a cursor from this diagnostic observation and search")
        offset = token[-1]
    needle = search.casefold()
    indices = [
        index
        for index, value in enumerate(values)
        if not needle or needle in _search(_item(kind, value, index))
    ]
    if offset > len(indices):
        raise NvkeError("INVALID_CURSOR", "The diagnostic cursor is outside this observation")
    selected = indices[offset : offset + limit]
    items = [_item(kind, values[index], index) for index in selected]
    next_offset = offset + len(items)
    cursor = (
        signing.Signer(salt=_SALT).sign_object([*binding, next_offset])
        if next_offset < len(indices)
        else None
    )
    return row, {**_summary(kind, data), "cluster_id": cluster_id}, items, cursor, len(indices)
