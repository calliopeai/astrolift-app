"""Native requests bound to a reviewed resource observation."""

from __future__ import annotations

import copy
import hashlib
import hmac
import re
from dataclasses import dataclass
from uuid import UUID

from django.conf import settings
from nvke.client import NvkeError
from nvke.contract import controller_spec
from nvke.inventory import Collection, collections, identity, resource_actions
from nvke.operations import action, validate_request

from astrolift_clusters import nvke_inventory as inventory
from astrolift_clusters import nvke_operations as operations
from astrolift_clusters import nvke_sites as sites
from astrolift_clusters.models import NvkeConnection, NvkeOperation
from core.permissions import Permission


def digest(value):
    return hmac.new(
        str(settings.SECRET_KEY).encode(), operations.canonical(value, limit=64 * 1024 * 1024), hashlib.sha256
    ).hexdigest()


@dataclass(frozen=True)
class ObservedResource:
    operation: NvkeOperation
    collection: Collection
    document: dict
    version: str | None
    identity: dict
    subresource: str | None = None


def read_action(collection):
    original = action(collection.key)
    path = re.compile(re.escape(original.http.path) + r"/\{[^}]+\}\Z") if original.http else None
    return next(
        (
            candidate
            for candidate in resource_actions(collection)
            if candidate.verb == "get"
            or (candidate.http and candidate.http.method == "GET" and path.fullmatch(candidate.http.path))
        ),
        None,
    )


def is_subresource(selected, collection):
    read = read_action(collection)
    return bool(
        collection.surface == "controller"
        and selected.http
        and read
        and selected.http.path != read.http.path
        and selected.key != collection.key
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
            collection = next(
                (
                    value
                    for value in collections()
                    if source_action.verb == "GET"
                    and source_action in resource_actions(value)
                    and is_subresource(source_action, value)
                ),
                None,
            )
            guard = source.authority.get("resource", {})
            if (
                collection is None
                or guard.get("subresource") != source_action.key
                or guard.get("collection") != collection.key
                or guard.get("mutates") is not False
                or guard.get("identity_action") != read_action(collection).key
            ):
                raise NvkeError("INVALID_INPUT", "Choose a successful bound resource read")
            original = operations.decode_input(source)
            fields = re.findall(r"\{([^}]+)\}", source_action.http.path)
            path = original.get("path")
            if len(fields) != 1 or not isinstance(path, dict) or set(path) != set(fields):
                raise NvkeError("SOURCE_CHANGED", "The native resource read lost its fixed target")
            native_identity = _identity(collection, {"id": path[fields[0]]})
            if not hmac.compare_digest(digest(native_identity), guard.get("identity_digest", "")):
                raise NvkeError("SOURCE_CHANGED", "The native resource read binding changed")
            raw = operations.decode_result(source).get("response", {}).get("data")
            if not isinstance(raw, dict):
                raise NvkeError("SOURCE_CHANGED", "The native resource observation is unavailable")
            return ObservedResource(source, collection, raw, None, native_identity, source_action.key)
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
    return ObservedResource(source, collection, raw, version, _identity(collection, raw))


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
    value = raw.get("requested", {}) if selected.key == "updateClusterSpireServer" else raw
    return _project(schema, value, spec["components"]["schemas"])


def editor(*, site_id, expected_actor_id, operation_key, reference):
    selected = action(operation_key)
    org, actor = sites.authorized(Permission(selected.permission))
    if type(expected_actor_id) is not int or actor.pk != expected_actor_id:
        raise NvkeError("SOURCE_CHANGED", "The reviewed NVKE caller changed")
    site = sites.site_row(site_id, org)
    observed = _source(site, actor, reference)
    source, collection, raw = observed.operation, observed.collection, observed.document
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
        if selected.key != "updateClusterSpireServer" or parameters.get("body"):
            validate_request(selected.key, parameters, site.organization_namespace)
    except NvkeError as exc:
        if exc.code != "PREPARATION_REQUIRED":
            raise
        preparation = (
            "getClusterSpireServer"
            if selected.key == "updateClusterSpireServer"
            else read_action(collection).key
        )
        return (
            source,
            collection,
            selected,
            None,
            preparation,
            "observed_only" if collection.surface == "controller" else "native_version",
        )
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
    observed = _source(site, actor, reference)
    source, collection, raw, version = (
        observed.operation,
        observed.collection,
        observed.document,
        observed.version,
    )
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
    if observed.subresource and (
        not is_subresource(selected, collection)
        or selected.http.path != action(observed.subresource).http.path
    ):
        raise NvkeError("INVALID_INPUT", "Choose an action on the reviewed native subresource")
    if selected.key == "updateClusterSpireServer" and observed.subresource != "getClusterSpireServer":
        raise NvkeError("PREPARATION_REQUIRED", "Read the SPIRE server before editing its configuration")
    if not isinstance(parameters, dict):
        raise NvkeError("INVALID_INPUT", "Native resource parameters must be a JSON object")
    result = copy.deepcopy(parameters)
    native_identity = observed.identity
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
        **(
            {"subresource": selected.key, "identity_action": read.key}
            if is_subresource(selected, collection)
            else {}
        ),
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


def check_subresource_configuration(guard, data):
    if not isinstance(data, dict) or not hmac.compare_digest(
        digest(controller_configuration(action(guard["comparison_action"]), data)),
        guard.get("baseline_digest", ""),
    ):
        raise NvkeError("CONFLICT", "The native subresource configuration changed since review")
