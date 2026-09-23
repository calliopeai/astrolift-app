"""Docker connection references contain locations, never credential material."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

_CONNECTION_ENV = ("DOCKER_CONTEXT", "DOCKER_HOST", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")


def _invoke(command: list[str], *, env: dict | None = None, timeout: int = 5):
    try:
        return subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # A spawn argv can carry the temporary task token in an environment flag.
        raise RuntimeError("Docker command timed out") from None


def _context_endpoint(config_dir: str, context: str, env: dict) -> dict:
    result = _invoke(
        [
            "docker",
            "--config",
            config_dir,
            "context",
            "inspect",
            "--format",
            "{{json .Endpoints.docker}}",
            "--",
            context,
        ],
        env=env,
    )
    if result.returncode:
        raise RuntimeError("Cannot read the saved Docker context")
    try:
        endpoint = json.loads(result.stdout)
        if not isinstance(endpoint, dict) or not isinstance(endpoint.get("Host"), str):
            raise ValueError
        if not isinstance(endpoint.get("SkipTLSVerify", False), bool):
            raise ValueError
        return {"endpoint": endpoint["Host"], "skip_tls_verify": endpoint.get("SkipTLSVerify", False)}
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Docker returned an invalid context endpoint") from exc


def validate_connection(connection: dict) -> None:
    if (
        not isinstance(connection, dict)
        or type(connection.get("version")) is not int
        or connection["version"] != 1
    ):
        raise RuntimeError("Task has no valid Docker connection reference")
    common = {"version", "mode", "config_dir", "endpoint"}
    mode = connection.get("mode")
    fields = {"context", "skip_tls_verify"} if mode == "context" else {"tls", "tls_verify", "cert_path"}
    if mode not in {"context", "host"} or set(connection) != common | fields:
        raise RuntimeError("Task has an invalid Docker connection reference")
    for field in ["config_dir", "endpoint", *(["context"] if mode == "context" else ["cert_path"])]:
        value = connection[field]
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 4096
            or any(c in value for c in "\x00\r\n")
        ):
            raise RuntimeError("Task has an invalid Docker connection location")
    if not Path(connection["config_dir"]).is_absolute():
        raise RuntimeError("Docker configuration reference must be absolute")
    try:
        parsed = urlsplit(connection["endpoint"])
        if (
            parsed.scheme not in {"unix", "npipe", "tcp", "ssh"}
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        if parsed.username and parsed.scheme != "ssh":
            raise ValueError
    except ValueError as exc:
        raise RuntimeError("Docker endpoint must not contain credentials or query parameters") from exc
    if mode == "context":
        if connection["context"].startswith("-") or not isinstance(connection["skip_tls_verify"], bool):
            raise RuntimeError("Task has an invalid Docker context reference")
    elif (
        not isinstance(connection["tls"], bool)
        or not isinstance(connection["tls_verify"], bool)
        or (connection["tls_verify"] and not connection["tls"])
        or not Path(connection["cert_path"]).is_absolute()
    ):
        raise RuntimeError("Task has invalid Docker TLS references")


def snapshot_docker_connection() -> dict:
    environment = dict(os.environ)
    config_dir = str(Path(environment.get("DOCKER_CONFIG") or Path.home() / ".docker").absolute())
    shown = _invoke(["docker", "--config", config_dir, "context", "show"], env=environment)
    if shown.returncode or not shown.stdout.strip():
        raise RuntimeError("Cannot resolve the Docker context for this task")
    context = shown.stdout.strip()
    endpoint = _context_endpoint(config_dir, context, environment)
    connection = {"version": 1, "config_dir": config_dir, "endpoint": endpoint["endpoint"]}
    if context == "default":
        connection.update(
            mode="host",
            tls=bool(environment.get("DOCKER_TLS") or environment.get("DOCKER_TLS_VERIFY")),
            tls_verify=bool(environment.get("DOCKER_TLS_VERIFY")),
            cert_path=str(Path(environment.get("DOCKER_CERT_PATH") or config_dir).absolute()),
        )
    else:
        connection.update(mode="context", context=context, skip_tls_verify=endpoint["skip_tls_verify"])
    validate_connection(connection)
    return connection


def run_docker(connection: dict, args: list[str], *, timeout: int = 5):
    validate_connection(connection)
    environment = {key: value for key, value in os.environ.items() if key not in _CONNECTION_ENV}
    prefix = ["docker", "--config", connection["config_dir"]]
    if connection["mode"] == "context":
        endpoint = _context_endpoint(connection["config_dir"], connection["context"], environment)
        if endpoint != {key: connection[key] for key in ("endpoint", "skip_tls_verify")}:
            raise RuntimeError("The saved Docker context endpoint or TLS policy changed")
        prefix += ["--context", connection["context"]]
    else:
        prefix += ["--host", connection["endpoint"]]
        # Even --tlsverify=false enables TLS in Docker's CLI.
        if connection["tls"]:
            prefix += ["--tls", f"--tlsverify={str(connection['tls_verify']).lower()}"]
        environment["DOCKER_CERT_PATH"] = connection["cert_path"]
    return _invoke(prefix + args, env=environment, timeout=timeout)
