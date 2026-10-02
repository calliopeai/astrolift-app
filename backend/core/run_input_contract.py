"""Canonical JSON Schema contracts for reviewed run inputs."""

from __future__ import annotations

import copy
import hashlib
import json

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError


class InputContractError(ValueError):
    pass


def no_input_schema() -> dict:
    return {"type": "object", "properties": {}, "additionalProperties": False}


def canonical_bytes(value) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise InputContractError("Inputs and schemas must contain finite JSON values") from exc


def digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _walk_schema(value, *, depth=0):
    if depth > 32:
        raise InputContractError("Input schema nesting exceeds the supported limit")
    if not isinstance(value, dict):
        return
    sensitive = value.get("writeOnly") or value.get("x-astrolift-sensitive")
    if sensitive and value.get("type") not in (None, "string"):
        raise InputContractError("Sensitive inputs must use string secret references")
    if sensitive and "default" in value:
        raise InputContractError("Sensitive inputs cannot declare defaults")
    if "$dynamicRef" in value:
        raise InputContractError("Dynamic input references are not supported")
    reference = value.get("$ref")
    if reference is not None and (
        not isinstance(reference, str) or (reference != "#" and not reference.startswith("#/"))
    ):
        raise InputContractError("Input schemas may use local JSON pointers only")
    if any(key in value for key in ("pattern", "patternProperties")):
        raise InputContractError("Regular expression input constraints are not supported")
    if "$schema" in value and value["$schema"] != "https://json-schema.org/draft/2020-12/schema":
        raise InputContractError("Input contracts use JSON Schema draft 2020-12")
    if "format" in value and value["format"] not in FormatChecker.checkers:
        raise InputContractError("The input format is not supported")
    for key in ("properties", "$defs", "dependentSchemas"):
        children = value.get(key, {})
        if isinstance(children, dict):
            for child in children.values():
                _walk_schema(child, depth=depth + 1)
    for key in (
        "items",
        "additionalProperties",
        "contains",
        "propertyNames",
        "unevaluatedProperties",
        "unevaluatedItems",
        "not",
        "if",
        "then",
        "else",
    ):
        if key in value:
            _walk_schema(value[key], depth=depth + 1)
    for key in ("prefixItems", "allOf", "anyOf", "oneOf"):
        for child in value.get(key, []):
            _walk_schema(child, depth=depth + 1)


def validate_schema(schema) -> dict:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise InputContractError("The run input schema must declare an object")
    if len(canonical_bytes(schema)) > 65536:
        raise InputContractError("The run input schema exceeds 64 KiB")
    _walk_schema(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise InputContractError("The run input schema is invalid") from exc
    return copy.deepcopy(schema)


def _defaults(schema, value):
    if not isinstance(schema, dict):
        return
    if isinstance(value, dict):
        for name, field in schema.get("properties", {}).items():
            if name not in value and isinstance(field, dict) and "default" in field:
                value[name] = copy.deepcopy(field["default"])
            if name in value:
                _defaults(field, value[name])
    elif isinstance(value, list):
        for item in value:
            _defaults(schema.get("items", {}), item)


def _sensitive_refs(schema, value, *, root=None, depth=0):
    if not isinstance(schema, dict):
        return
    if depth > 32:
        raise InputContractError("Sensitive input schema nesting exceeds the supported limit")
    root = schema if root is None else root
    if "$ref" in schema:
        resolved = root
        try:
            reference = schema["$ref"]
            if reference != "#" and not reference.startswith("#/"):
                raise InputContractError("Input references must use local JSON pointers")
            for token in [] if reference == "#" else reference[2:].split("/"):
                resolved = resolved[token.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError) as exc:
            raise InputContractError("Sensitive input references cannot be resolved") from exc
        _sensitive_refs(resolved, value, root=root, depth=depth + 1)
    validator = Draft202012Validator(root, format_checker=FormatChecker())

    def matches(branch, candidate=value):
        return validator.evolve(schema=branch).is_valid(candidate)

    for keyword in ("allOf", "anyOf", "oneOf"):
        for branch in schema.get(keyword, []):
            if keyword == "allOf" or matches(branch):
                _sensitive_refs(branch, value, root=root, depth=depth + 1)
    if "if" in schema:
        keyword = "then" if matches(schema["if"]) else "else"
        if keyword in schema:
            _sensitive_refs(schema[keyword], value, root=root, depth=depth + 1)
    if schema.get("writeOnly") or schema.get("x-astrolift-sensitive"):
        from urllib.parse import urlsplit

        reference = urlsplit(value) if isinstance(value, str) else None
        if (
            reference is None
            or reference.scheme != "secret"
            or not reference.netloc
            or reference.username
            or reference.password
            or reference.query
            or reference.fragment
        ):
            raise InputContractError("Sensitive inputs accept secret references, never literal values")
    if isinstance(value, dict):
        for name, branch in schema.get("dependentSchemas", {}).items():
            if name in value:
                _sensitive_refs(branch, value, root=root, depth=depth + 1)
        for name, item in value.items():
            _sensitive_refs(schema.get("propertyNames", {}), name, root=root, depth=depth + 1)
            _sensitive_refs(
                schema.get("properties", {}).get(name, schema.get("additionalProperties", {})),
                item,
                root=root,
                depth=depth + 1,
            )
            # Evaluated-property annotations can span references/composition.
            # Apply this security annotation conservatively to every value.
            _sensitive_refs(schema.get("unevaluatedProperties", {}), item, root=root, depth=depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            prefix = schema.get("prefixItems", [])
            child = prefix[index] if index < len(prefix) else schema.get("items", {})
            _sensitive_refs(child, item, root=root, depth=depth + 1)
            _sensitive_refs(schema.get("unevaluatedItems", {}), item, root=root, depth=depth + 1)
            if "contains" in schema and matches(schema["contains"], item):
                _sensitive_refs(schema["contains"], item, root=root, depth=depth + 1)


def validate_inputs(schema, inputs) -> dict:
    schema = validate_schema(schema)
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, dict) or len(canonical_bytes(inputs)) > 65536:
        raise InputContractError("Run inputs must be an object of at most 64 KiB")
    values = copy.deepcopy(inputs)
    _defaults(schema, values)
    try:
        error = next(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(values), None)
    except Exception as exc:
        raise InputContractError("The input schema cannot be resolved safely") from exc
    if error is not None:
        # jsonschema's message includes submitted values; expose only its keyword.
        raise InputContractError(f"Run inputs violate the {error.validator} constraint")
    _sensitive_refs(schema, values)
    return values


def input_contract(schema) -> dict:
    try:
        schema = validate_schema(schema)
    except InputContractError as exc:
        return {
            "schema": None,
            "digest": digest(schema),
            "supported": False,
            "error": str(exc),
            "fields": [],
            "accepts_inputs": True,
            "supports_simple_form": False,
        }
    required = schema.get("required", [])
    fields = []
    for name, field in schema.get("properties", {}).items():
        field = field if isinstance(field, dict) else {}
        kind = field.get("type", "complex")
        fields.append(
            {
                "name": name,
                "kind": kind if isinstance(kind, str) else "complex",
                "required": name in required,
                "has_default": "default" in field,
                "default": None
                if field.get("writeOnly") or field.get("x-astrolift-sensitive")
                else field.get("default"),
                "sensitive": bool(field.get("writeOnly") or field.get("x-astrolift-sensitive")),
                "enum_values": field.get("enum"),
                "constraints": {
                    key: value
                    for key, value in field.items()
                    if key
                    in {
                        "minimum",
                        "maximum",
                        "exclusiveMinimum",
                        "exclusiveMaximum",
                        "multipleOf",
                        "minLength",
                        "maxLength",
                        "format",
                        "minItems",
                        "maxItems",
                        "uniqueItems",
                    }
                },
                "simple": isinstance(kind, str)
                and kind in {"string", "boolean", "integer", "number"}
                and not any(key in field for key in ("$ref", "oneOf", "anyOf", "allOf", "if")),
            }
        )
    complex_root = any(key in schema for key in ("$ref", "allOf", "anyOf", "oneOf", "if", "dependentSchemas"))
    return {
        "schema": schema,
        "digest": digest(schema),
        "supported": True,
        "error": "",
        "fields": fields,
        "accepts_inputs": bool(fields) or schema.get("additionalProperties") is not False or complex_root,
        "supports_simple_form": not complex_root
        and schema.get("additionalProperties") is False
        and all(field["simple"] for field in fields),
    }
