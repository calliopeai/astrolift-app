"""Source-pinned hosted operator commands; inputs name artifacts, never host paths."""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

REVISION = "92c39b8ad563ebe5ef705f781b3038b901aba10f"
MAX_REQUEST_BYTES = 1048576
MAX_CHUNK_BYTES = 262144
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
ARCH = {"type": "choice", "choices": ["linux/amd64", "linux/arm64"]}
GLOBAL = {"json-logs": "boolean", "verbose": "boolean"}


class ContractError(ValueError):
    pass


def command(
    native: list[str],
    options: dict[str, Any] | None = None,
    *,
    required: tuple[str, ...] = (),
    positionals: tuple[str, ...] = (),
    helper: str | None = None,
) -> dict[str, Any]:
    return {
        "native": native,
        "options": {**(GLOBAL if helper is None else {}), **(options or {})},
        "required": list(required),
        "positionals": list(positionals),
        "helper": helper,
    }


CATALOG: dict[str, dict[str, Any]] = {
    "version": command(["version"]),
    "generate.sitespec": command(
        ["generate", "sitespec"],
        {"nodes": "file", "set": "strings"},
        required=("nodes",),
        positionals=("string",),
    ),
    "generate.oidc-kubeconfig": command(
        ["generate", "oidc-kubeconfig"],
        {
            "kubeconfig": "file",
            "oidc-issuer-url": "string",
            "oidc-client-id": "string",
            "oidc-extra-scope": "strings",
            "headless": "boolean",
            "output-file": "output",
        },
        required=("kubeconfig", "oidc-issuer-url", "oidc-client-id"),
    ),
    "inspect.sitespec": command(["inspect", "sitespec"], positionals=("file",)),
    "inspect.stack-config": command(["inspect", "stack-config"], positionals=("file",)),
    "inspect.values": command(["inspect", "values"], positionals=("file",)),
    "get.bootstrap-manifest": command(["get", "bootstrap-manifest"]),
    "inspect.nvkr-release": command(
        ["inspect", "nvkr-release"],
        {
            "artifacts": "boolean",
            "meta": "boolean",
            "kind": "string",
            "select": "string",
            "arch": ARCH,
        },
        positionals=("file",),
    ),
    "validate.sitespec": command(
        ["validate", "sitespec"],
        {
            "sitespec": "file",
            "bootstrap-only": "boolean",
            "disable-mtls": "boolean",
            "allow-control-plane-without-vip": "boolean",
        },
        required=("sitespec",),
    ),
    "bootstrap.site": command(
        ["bootstrap", "site"],
        {
            "sitespec": "file",
            "arch": ARCH,
            "release": "file",
            "diagnostics-dir": "output-directory",
            "encryption-key-out": "output",
            "kubeconfig-out": "output",
            "nvke-controller-image": "string",
            "provisioned-provider-image": "string",
            "otlp-endpoint": "string",
            "timeout": "string",
            "dry-run": "boolean",
            "disable-mtls": "boolean",
            "allow-control-plane-without-vip": "boolean",
            "keep-bootstrap-cluster": "boolean",
            "otlp-insecure": "boolean",
        },
        required=("sitespec",),
    ),
    "render.deploy-path": command(
        ["render", "deploy-path"],
        {
            "deploy-path": "directory",
            "sitespec": "file",
            "stack-config": "file",
            "release": "file",
            "nvkr-run-dir": "directory",
            "out": "output-directory",
            "arch": ARCH,
            "credentials": {"type": "choice", "choices": ["rendered", "external"]},
            "ephemeral": "boolean",
            "plain-http-registries": "string",
        },
        required=("deploy-path", "out"),
    ),
    "management.install": command(
        ["management", "install"],
        {
            "deploy-path": "directory",
            "sitespec": "file",
            "stack-config": "file",
            "release": "file",
            "nvkr-run-dir": "directory",
            "config": "file",
            "arch": ARCH,
            "kubeconfig": "file",
            "timeout": "string",
            "values-for": "item-files",
            "dry-run": "boolean",
        },
        required=("kubeconfig",),
    ),
    "management.check": command(
        ["management", "check"],
        {
            "sitespec": "file",
            "stack-config": "file",
            "kubeconfig": "file",
            "release": "file",
            "nvkr-run-dir": "directory",
            "diagnostics": "boolean",
        },
        required=("kubeconfig",),
    ),
    "management.configure-access": command(
        ["management", "configure-access"],
        {"kubeconfig": "file"},
        required=("kubeconfig",),
    ),
}
for _key, _helper in (
    ("render.nktp-backend", "nktp_backend.py"),
    ("render.management-apis", "management_api.py"),
    ("render.management-controller", "management_controller.py"),
):
    CATALOG[_key] = command(
        [],
        {"profile": "file", "output": "output"},
        required=("profile", "output"),
        helper=_helper,
    )


def identifier(value: object) -> str:
    try:
        if (
            not isinstance(value, str)
            or str(UUID(value)) != value
            or not UUID(value).int
        ):
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise ContractError("Use a canonical nonzero UUID") from None
    return value


def artifact_name(value: object) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value) or value in {".", ".."}:
        raise ContractError("Use a simple artifact name")
    return value


def canonical(value: object) -> bytes:
    try:
        result = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError, UnicodeError):
        raise ContractError("Use finite JSON operator input") from None
    if len(result) > MAX_REQUEST_BYTES:
        raise ContractError("Operator input exceeds the size limit")
    return result


def _text(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 8192
        or any(ord(c) < 32 for c in value)
    ):
        raise ContractError(
            "Use a bounded nonempty option value without control characters"
        )
    return value


def reference(value: object, kind: str, files: dict[str, str]) -> str:
    value = _text(value)
    if value.startswith("input:") and kind in {"file", "file-or-directory"}:
        name = artifact_name(value[6:])
        if name not in files:
            raise ContractError("Input artifact was not supplied")
    elif value.startswith("output:") and kind in {"output", "output-directory"}:
        artifact_name(value[7:])
        if value[7:] in {"stdout", "stderr", "runner.stdout", "runner.stderr"}:
            raise ContractError(
                "Choose an output name distinct from retained diagnostics"
            )
    elif value.startswith("job:") and kind in {
        "file",
        "directory",
        "file-or-directory",
    }:
        parts = value.split(":")
        if len(parts) != 3:
            raise ContractError("Choose a retained job artifact")
        identifier(parts[1])
        segments = parts[2].split("/")
        if not segments or len(segments) > 16:
            raise ContractError("Choose a retained job artifact")
        for segment in segments:
            artifact_name(segment)
    elif (
        value in {"source:deploy/nico", "source:controller/deploy/capd-e2e"}
        and kind == "directory"
    ):
        pass
    else:
        raise ContractError(
            "File options require an input, output, retained job or pinned source artifact reference"
        )
    return value


def validate(request: object, site_id: str) -> dict[str, Any]:
    canonical(request)
    if not isinstance(request, dict) or set(request) != {
        "format",
        "revision",
        "siteId",
        "command",
        "positionals",
        "options",
        "files",
    }:
        raise ContractError("Use the complete hosted operator request envelope")
    if (
        request["format"] != 1
        or type(request["format"]) is not int
        or request["revision"] != REVISION
        or identifier(request["siteId"]) != identifier(site_id)
    ):
        raise ContractError("Operator site or native revision does not match")
    key = request["command"]
    if not isinstance(key, str) or key not in CATALOG:
        raise ContractError("Choose a supported pinned operator command")
    definition = CATALOG[key]
    files = request["files"]
    if not isinstance(files, dict) or len(files) > 32:
        raise ContractError("Supply at most 32 private input files")
    for name, content in files.items():
        artifact_name(name)
        if not isinstance(content, str) or len(content.encode()) > 524288:
            raise ContractError("Supply bounded UTF-8 input file contents")
    args, options = request["positionals"], request["options"]
    if not isinstance(args, list) or len(args) != len(definition["positionals"]):
        raise ContractError("Supply this command's declared positional arguments")
    if (
        not isinstance(options, dict)
        or options.keys() - definition["options"].keys()
        or not set(definition["required"]) <= options.keys()
    ):
        raise ContractError(
            "Supply only this command's declared options and required inputs"
        )

    def check(kind: Any, value: Any) -> None:
        if isinstance(kind, dict):
            if value not in kind["choices"]:
                raise ContractError("Choose a declared option value")
        elif kind in {"file", "directory", "output", "output-directory"}:
            reference(value, kind, files)
        elif kind == "boolean":
            if type(value) is not bool:
                raise ContractError("Boolean options must be JSON booleans")
        elif kind in {"strings", "item-files"}:
            if not isinstance(value, list) or not value or len(value) > 64:
                raise ContractError("Supply a bounded nonempty option list")
            for item in value:
                _text(item)
                if kind == "item-files":
                    name, equals, path = item.partition("=")
                    if not equals:
                        raise ContractError(
                            "Values overrides require item=artifact-reference"
                        )
                    artifact_name(name)
                    reference(path, "file", files)
        else:
            _text(value)

    for kind, value in zip(definition["positionals"], args, strict=True):
        check(kind, value)
        if kind == "string" and value.startswith("-"):
            raise ContractError("Positional names cannot be options")
    for name, value in options.items():
        check(definition["options"][name], value)
    return request


def validate_admission(request: object, site_id: str) -> dict[str, Any]:
    """Apply native preflight rules to new work while retaining readable history."""
    request = validate(request, site_id)
    if (
        request["command"] == "bootstrap.site"
        and "arch" in request["options"]
        and "release" not in request["options"]
    ):
        raise ContractError("Bootstrap architecture requires a release artifact")
    return request


def effectful(request: dict[str, Any]) -> bool:
    return (
        request["command"]
        in {"bootstrap.site", "management.install", "management.configure-access"}
        and request["options"].get("dry-run") is not True
    )
