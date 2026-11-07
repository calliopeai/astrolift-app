"""Bounded, non-retrying transport for every pinned NVKE HTTP operation."""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit

import httpx
from jsonschema import Draft4Validator, FormatChecker

from nvke.contract import Operation, Surface, controller_spec, operation, resource_schema

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_REQUEST_BYTES = 16 * 1024 * 1024
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")
_VERSION = re.compile(r'[^"\s,*\x00-\x1f\x7f]+\Z')


class NvkeError(Exception):
    """Safe for an operation record: upstream payloads and URLs never appear."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        ambiguous: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.ambiguous = ambiguous


@dataclass(frozen=True)
class Endpoint:
    surface: Surface
    url: str = field(repr=False)
    token: Callable[[], str] | None = field(default=None, repr=False)
    allow_http: bool = False

    def __post_init__(self) -> None:
        try:
            parsed = urlsplit(self.url)
            valid = (
                parsed.scheme in ({"https", "http"} if self.allow_http else {"https"})
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and not parsed.query
                and not parsed.fragment
                and parsed.port != 0
                and not any(c in self.url for c in ("\\", "%", "\r", "\n", "\t", " "))
                and not any(ord(c) < 33 or ord(c) == 127 for c in self.url)
                and all(segment not in {".", ".."} for segment in parsed.path.split("/"))
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("NVKE endpoint must be a valid HTTPS base URL without credentials, query or fragment")
        if self.surface not in {"controller", "tenant", "operator"}:
            raise ValueError("unknown NVKE endpoint surface")


@dataclass(frozen=True)
class NvkeResponse:
    status_code: int
    data: Any
    resource_version: str | None = None


def _schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_schema(v) for v in value]
    if not isinstance(value, dict):
        return value
    converted = {k: _schema(v) for k, v in value.items() if k != "nullable"}
    if value.get("nullable"):
        return {"anyOf": [converted, {"type": "null"}]}
    return converted


def _validate(schema: dict[str, Any], value: Any, *, components: dict[str, Any] | None = None) -> None:
    document = _schema(schema)
    if components is not None:
        document["components"] = _schema(components)
    validator = Draft4Validator(document, format_checker=FormatChecker())
    if next(validator.iter_errors(value), None) is not None:
        # ValidationError.message embeds the rejected value, which can contain
        # credentials. The caller can use the published schema for field help.
        raise NvkeError("INVALID_INPUT", "NVKE request does not conform to the pinned operation schema")


def _strict_object(schema: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(schema)
    if "properties" in result and "additionalProperties" not in result:
        result["additionalProperties"] = False
    for key in ("properties",):
        if key in result:
            result[key] = {name: _strict_object(value) for name, value in result[key].items()}
    if isinstance(result.get("items"), dict):
        result["items"] = _strict_object(result["items"])
    for key in ("allOf", "anyOf", "oneOf"):
        if key in result:
            result[key] = [_strict_object(value) for value in result[key]]
    return result


def _public_body(op: Operation, body: Any) -> None:
    spec = resource_schema(op.resource)["properties"]["spec"]
    _validate(
        {
            "type": "object",
            "required": ["metadata", "spec"],
            "additionalProperties": False,
            "properties": {
                "metadata": {
                    "type": "object",
                    "required": ["name"],
                    "additionalProperties": False,
                    "properties": {"name": {"type": "string", "pattern": _NAME.pattern, "maxLength": 253}},
                },
                "spec": _strict_object(spec),
            },
        },
        body,
    )


def _public_query(op: Operation, query: Mapping[str, Any]) -> None:
    properties: dict[str, Any] = {}
    if op.id.split(".")[-1].startswith("list"):
        properties = {
            "pageSize": {"type": "integer", "minimum": 1, "maximum": 500},
            "pageKey": {"type": "string"},
        }
    elif op.method == "DELETE":
        properties = {"propagationPolicy": {"enum": ["background", "foreground"]}}
    _validate({"type": "object", "properties": properties, "additionalProperties": False}, dict(query))


class NvkeClient:
    def __init__(
        self,
        endpoints: list[Endpoint],
        *,
        timeout: float = 30.0,
        verify: Any = True,
        transport: httpx.BaseTransport | None = None,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0 or max_response_bytes <= 0:
            raise ValueError("NVKE timeout and response limit must be positive and finite")
        if len({e.surface for e in endpoints}) != len(endpoints):
            raise ValueError("each NVKE surface may have only one endpoint")
        self._endpoints = {e.surface: e for e in endpoints}
        self._http = httpx.Client(
            timeout=timeout,
            verify=verify,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )
        self._max_response_bytes = max_response_bytes
        self._spec = controller_spec()

    def __enter__(self) -> NvkeClient:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def execute(
        self,
        operation_id: str,
        *,
        path: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        resource_version: str | None = None,
    ) -> NvkeResponse:
        op = operation(operation_id)
        endpoint = self._endpoints.get(op.surface)
        if endpoint is None:
            raise NvkeError("SURFACE_UNAVAILABLE", "The requested NVKE interface is not configured")
        path = dict(path or {})
        query = dict(query or {})
        names = set(re.findall(r"\{([^}]+)\}", op.path))
        if set(path) != names:
            raise NvkeError("INVALID_INPUT", "NVKE path parameters do not match the operation")
        for name, value in path.items():
            if not isinstance(value, str) or not value or any(c in value for c in ("/", "\\", "%", "?", "#")):
                raise NvkeError("INVALID_INPUT", "NVKE path parameter is invalid")
            if value in {".", ".."} or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise NvkeError("INVALID_INPUT", "NVKE path parameter is invalid")
            if name == "name" and _NAME.fullmatch(value) is None:
                raise NvkeError("INVALID_INPUT", "NVKE resource name is invalid")

        headers = {"Accept": "application/json"}
        if op.surface == "controller":
            self._validate_controller(op, path, query, body)
            if resource_version is not None:
                raise NvkeError("INVALID_INPUT", "The controller interface does not support resource versions")
        else:
            _public_query(op, query)
            if op.method in {"POST", "PUT"}:
                _public_body(op, body)
                if op.method == "PUT" and body is not None and body["metadata"]["name"] != path["name"]:
                    raise NvkeError("INVALID_INPUT", "NVKE body name must match the resource path")
            elif body is not None:
                raise NvkeError("INVALID_INPUT", "This NVKE operation accepts no request body")
            if op.method in {"PUT", "DELETE"}:
                if (
                    not isinstance(resource_version, str)
                    or not resource_version
                    or _VERSION.fullmatch(resource_version) is None
                ):
                    raise NvkeError(
                        "INVALID_INPUT", "Use the current opaque NVKE resource version for update or deletion"
                    )
                headers["If-Match"] = f'"{resource_version}"'
            elif resource_version is not None:
                raise NvkeError("INVALID_INPUT", "This NVKE operation accepts no resource version")
        if endpoint.token is not None:
            try:
                token = endpoint.token()
            except Exception:
                raise NvkeError("CREDENTIALS_UNAVAILABLE", "NVKE credentials could not be loaded") from None
            if not isinstance(token, str) or not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise NvkeError("CREDENTIALS_UNAVAILABLE", "NVKE bearer credential is invalid")
            headers["Authorization"] = f"Bearer {token}"
        try:
            encoded = json.dumps(body, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, UnicodeError):
            raise NvkeError("INVALID_INPUT", "NVKE request must contain only finite JSON values") from None
        if len(encoded) > MAX_REQUEST_BYTES:
            raise NvkeError("INVALID_INPUT", "NVKE request exceeds the size limit")
        url_path = op.path
        for name, value in path.items():
            url_path = url_path.replace("{" + name + "}", quote(value, safe=""))
        url = endpoint.url.rstrip("/") + url_path
        return self._send(op, url, query, body, headers)

    def _validate_controller(self, op: Operation, path: dict[str, str], query: dict[str, Any], body: Any) -> None:
        item = self._spec["paths"][op.path]
        declaration = item[op.method.lower()]
        parameters = [*item.get("parameters", []), *declaration.get("parameters", [])]
        schemas: dict[str, dict[str, Any]] = {"path": {}, "query": {}}
        required: dict[str, list[str]] = {"path": [], "query": []}
        for parameter in parameters:
            if "$ref" in parameter:
                parameter = self._spec["components"]["parameters"][parameter["$ref"].rsplit("/", 1)[-1]]
            location = parameter["in"]
            if location not in schemas:
                raise NvkeError("CONTRACT_UNSUPPORTED", "NVKE operation has an unsupported parameter location")
            schemas[location][parameter["name"]] = parameter["schema"]
            if parameter.get("required"):
                required[location].append(parameter["name"])
        for location, values in (("path", path), ("query", query)):
            _validate(
                {
                    "type": "object",
                    "properties": schemas[location],
                    "required": required[location],
                    "additionalProperties": False,
                },
                values,
                components=self._spec.get("components", {}),
            )
        request = declaration.get("requestBody")
        if request is None:
            if body is not None:
                raise NvkeError("INVALID_INPUT", "This NVKE operation accepts no request body")
        elif body is None and request.get("required"):
            raise NvkeError("INVALID_INPUT", "This NVKE operation requires a request body")
        elif body is not None:
            _validate(request["content"]["application/json"]["schema"], body, components=self._spec["components"])

    def _send(
        self,
        op: Operation,
        url: str,
        query: Mapping[str, Any],
        body: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> NvkeResponse:
        try:
            # Mutations are sent exactly once. A disconnect can happen after
            # upstream committed; the workflow must reconcile before retrying.
            with self._http.stream(op.method, url, params=query, json=body, headers=headers) as response:
                if not 200 <= response.status_code < 300:
                    status = response.status_code
                    code = {
                        400: "INVALID_INPUT",
                        401: "UNAUTHENTICATED",
                        403: "PERMISSION_DENIED",
                        404: "NOT_FOUND",
                        409: "CONFLICT",
                        412: "CONFLICT",
                        429: "RATE_LIMITED",
                    }.get(status, "UPSTREAM_ERROR")
                    raise NvkeError(
                        code,
                        f"NVKE operation failed (HTTP {status})",
                        status_code=status,
                        retryable=status in {408, 425, 429} or status >= 500,
                        ambiguous=op.mutates and (status in {408, 425} or status >= 500),
                    )
                chunks = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    if len(chunks) + len(chunk) > self._max_response_bytes:
                        raise NvkeError(
                            "RESPONSE_TOO_LARGE", "NVKE response exceeds the size limit", ambiguous=op.mutates
                        )
                    chunks.extend(chunk)
                if response.status_code == 204 or (not chunks and op.path in {"/healthz", "/readyz"}):
                    data = None
                else:
                    try:
                        data = json.loads(chunks, parse_constant=_reject_nonfinite)
                    except (ValueError, UnicodeDecodeError):
                        raise NvkeError(
                            "INVALID_RESPONSE", "NVKE returned an invalid JSON response", ambiguous=op.mutates
                        ) from None
                etag = response.headers.get("ETag")
                version = None
                if etag is not None:
                    if len(etag) < 3 or not etag.startswith('"') or not etag.endswith('"'):
                        raise NvkeError(
                            "INVALID_RESPONSE", "NVKE returned an invalid resource version", ambiguous=op.mutates
                        )
                    version = etag[1:-1]
                    if _VERSION.fullmatch(version) is None:
                        raise NvkeError(
                            "INVALID_RESPONSE", "NVKE returned an invalid resource version", ambiguous=op.mutates
                        )
                return NvkeResponse(response.status_code, data, version)
        except httpx.HTTPError:
            raise NvkeError(
                "TRANSPORT_ERROR",
                "NVKE connection failed or timed out",
                retryable=True,
                ambiguous=op.mutates,
            ) from None


def _reject_nonfinite(_value: str) -> Any:
    raise ValueError("non-finite JSON value")
