"""Native requests bound to a reviewed resource observation."""

from __future__ import annotations

import copy
import hashlib
import hmac
import re
from uuid import UUID

from django.conf import settings
from nvke.client import NvkeError
from nvke.contract import controller_spec
from nvke.inventory import collections, identity, resource_actions
from nvke.operations import action, validate_request

from astrolift_clusters import nvke_inventory as inventory
from astrolift_clusters import nvke_operations as operations
from astrolift_clusters import nvke_sites as sites
from astrolift_clusters.models import NvkeConnection
from core.permissions import Permission


def digest(value):
    return hmac.new(
        str(settings.SECRET_KEY).encode(), operations.canonical(value, limit=64 * 1024 * 1024), hashlib.sha256
    ).hexdigest()


def read_action(collection):
    original = action(collection.key)
    return next(
        (
            candidate
            for candidate in resource_actions(collection)
            if candidate.verb == "get"
            or (
                candidate.http
                and candidate.http.method == "GET"
                and candidate.http.path != original.http.path
            )
        ),
        None,
    )


def _source(site, actor, reference):
    if not isinstance(reference, dict) or set(reference) != {"operation_id", "version", "resource_key"}:
        raise NvkeError("INVALID_INPUT", "Choose a reviewed NVKE resource observation")
    if type(reference["version"]) is not int or reference["version"] < 1:
        raise NvkeError("INVALID_INPUT", "Use the reviewed NVKE observation version")
    source = operations.read(reference["operation_id"])
    source_action = action(source.operation_key)
    sites.authorized(Permission(source_action.permission))
    if source.site_id != site.pk or source.version != reference["version"] or source.status != "succeeded":
        raise NvkeError("SOURCE_CHANGED", "The reviewed NVKE resource observation changed")
    if source.authority.get("inventory") is True:
        source, collection, _item, data, index = inventory.resource(
            operation_id=str(source.guid), expected_actor_id=actor.pk, resource_key=reference["resource_key"]
        )
        raw = data["items"][index]
        version = (
            raw.get("metadata", {}).get("resourceVersion") if collection.surface == "datastore" else None
        )
    else:
        if reference["resource_key"] is not None:
            raise NvkeError("INVALID_INPUT", "Use the resource read observation without an inventory row key")
        collection = next((value for value in collections() if read_action(value) == source_action), None)
        if collection is None:
            raise NvkeError("INVALID_INPUT", "Choose a successful single-resource native read")
        response = operations.decode_result(source).get("response", {})
        raw, version = response.get("data"), response.get("resource_version")
    if not isinstance(raw, dict):
        raise NvkeError("SOURCE_CHANGED", "The native resource observation is unavailable")
    if source.authority.get("inventory") is not True:
        original = operations.decode_input(source)
        target = original.get("path") if source_action.http else {"name": original.get("name")}
        if not isinstance(target, dict) or list(target.values()) != [identity(collection, raw)]:
            raise NvkeError(
                "SOURCE_CHANGED", "The native read did not confirm its requested resource identity"
            )
    return source, collection, raw, version


def _project(schema, value, schemas):
    if "$ref" in schema:
        return _project(schemas[schema["$ref"].split("/")[-1]], value, schemas)
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [_project(schema["items"], item, schemas) for item in value]
    if isinstance(value, dict) and "properties" in schema:
        return {
            key: _project(definition, value[key], schemas)
            for key, definition in schema["properties"].items()
            if key in value and value[key] is not None and not definition.get("readOnly")
        }
    return copy.deepcopy(value)


def controller_configuration(selected, raw):
    if selected.verb != "PATCH":
        return {}
    spec = controller_spec()
    schema = spec["paths"][selected.http.path]["patch"]["requestBody"]["content"]["application/json"][
        "schema"
    ]
    return _project(schema, raw, spec["components"]["schemas"])


def editor(*, site_id, expected_actor_id, operation_key, reference):
    selected = action(operation_key)
    org, actor = sites.authorized(Permission(selected.permission))
    if type(expected_actor_id) is not int or actor.pk != expected_actor_id:
        raise NvkeError("SOURCE_CHANGED", "The reviewed NVKE caller changed")
    site = sites.site_row(site_id, org)
    source, collection, raw, version = _source(site, actor, reference)
    connection = NvkeConnection.objects.filter(site=site, surface=selected.surface, is_enabled=True).first()
    if connection is None:
        raise NvkeError("SURFACE_UNAVAILABLE", "The requested NVKE interface is not configured")
    parameters = {}
    if selected.verb == "replace":
        parameters["document"] = {
            "metadata": {
                key: value
                for key, value in raw.get("metadata", {}).items()
                if key in {"name", "namespace", "labels", "annotations"}
            },
            "spec": copy.deepcopy(raw.get("spec", {})),
        }
    elif selected.verb == "deprecate":
        parameters["deprecated"] = not raw.get("spec", {}).get("lifecycle", {}).get("deprecated", False)
    elif selected.http and selected.http.method == "PUT":
        parameters["body"] = {
            "metadata": {"name": identity(collection, raw)},
            "spec": copy.deepcopy(raw.get("spec", {})),
        }
    elif selected.http and selected.http.method == "PATCH":
        parameters["body"] = controller_configuration(selected, raw)
    try:
        parameters, guard = bind(
            site=site,
            actor=actor,
            selected=selected,
            parameters=parameters,
            reference=reference,
            connection=connection,
        )
        validate_request(selected.key, parameters, site.organization_namespace)
    except NvkeError as exc:
        if exc.code != "PREPARATION_REQUIRED":
            raise
        return source, collection, selected, None, read_action(collection).key, "native_version"
    return (
        source,
        collection,
        selected,
        parameters,
        None,
        "native_version" if guard["conditional"] else "observed_only",
    )


def _identity(collection, raw):
    value = {"identity": identity(collection, raw)}
    if collection.surface == "controller":
        try:
            native = UUID(value["identity"])
            if not native.int or str(native) != value["identity"]:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise NvkeError("SOURCE_CHANGED", "The native resource lacks a stable controller UUID") from None
    else:
        metadata = raw.get("metadata")
        if not isinstance(metadata, dict) or not isinstance(metadata.get("uid"), str) or not metadata["uid"]:
            raise NvkeError("SOURCE_CHANGED", "The native resource lacks an observed UID")
        value["uid"] = metadata["uid"]
    return value


def _fixed(parameters, field, expected):
    if field in parameters and parameters[field] != expected:
        raise NvkeError("INVALID_INPUT", "Native target and preconditions are fixed by the reviewed resource")
    parameters[field] = expected


def bind(*, site, actor, selected, parameters, reference, connection):
    source, collection, raw, version = _source(site, actor, reference)
    if (
        source.authority.get("connection_id") != str(connection.guid)
        or source.authority.get("connection_digest") != operations.connection_digest(connection)
        or source.authority.get("revision") != site.upstream_revision
        or source.authority.get("organization_namespace") != site.organization_namespace
    ):
        raise NvkeError(
            "SOURCE_CHANGED", "The reviewed NVKE resource belongs to a different interface authority"
        )
    read = read_action(collection)
    if (
        read is None
        or selected not in resource_actions(collection)
        or selected.key == collection.key
        or selected.verb in {"POST", "create"}
    ):
        raise NvkeError("INVALID_INPUT", "Choose an action on the reviewed NVKE resource")
    if not isinstance(parameters, dict):
        raise NvkeError("INVALID_INPUT", "Native resource parameters must be a JSON object")
    result = copy.deepcopy(parameters)
    native_identity = _identity(collection, raw)
    if selected.http:
        fields = re.findall(r"\{([^}]+)\}", selected.http.path)
        if len(fields) != 1:
            raise NvkeError("INVALID_INPUT", "Choose a single-resource native action")
        _fixed(result, "path", {fields[0]: native_identity["identity"]})
    else:
        _fixed(result, "name", native_identity["identity"])
    if selected.mutates and collection.surface != "controller":
        if not isinstance(version, str) or not version:
            raise NvkeError(
                "PREPARATION_REQUIRED", "Read this resource to obtain its native version before editing"
            )
        _fixed(result, "resource_version", version)
        if collection.surface == "datastore":
            _fixed(result, "uid", native_identity["uid"])
    body_key = "document" if collection.surface == "datastore" else "body"
    body = result.get(body_key)
    if body is not None and collection.surface != "controller":
        if not isinstance(body, dict):
            raise NvkeError("INVALID_INPUT", "Use the native writable resource document")
        metadata = body.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            raise NvkeError("INVALID_INPUT", "Use the native writable resource metadata")
        _fixed(metadata, "name", native_identity["identity"])
    guard = {
        "source": reference,
        "collection": collection.key,
        "identity_digest": digest(native_identity),
        "baseline_digest": digest(controller_configuration(selected, raw))
        if collection.surface == "controller"
        else digest(raw),
        "comparison_action": selected.key,
        "mutates": selected.mutates,
        "conditional": collection.surface != "controller",
    }
    return result, guard


def check_observation(guard, data, *, mutates):
    collection = next((value for value in collections() if value.key == guard.get("collection")), None)
    if collection is None or not isinstance(data, dict):
        raise NvkeError("SOURCE_CHANGED", "The reviewed NVKE resource is unavailable")
    if not hmac.compare_digest(digest(_identity(collection, data)), guard.get("identity_digest", "")):
        raise NvkeError("CONFLICT", "The reviewed NVKE resource identity changed")
    if (
        mutates
        and not guard.get("conditional")
        and not hmac.compare_digest(
            digest(controller_configuration(action(guard["comparison_action"]), data)),
            guard.get("baseline_digest", ""),
        )
    ):
        raise NvkeError("CONFLICT", "The native resource changed since the reviewed observation")
