"""Build private per-run OCI layers before AWS's attested encrypted-image path."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import re
import tarfile
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from pathlib import Path

BUNDLE_PATH = "opt/astrolift-confidential/run.json"
BOOTSTRAP_PATH = "opt/astrolift-confidential/bootstrap.py"
BOOTSTRAP = b"""import json, os, sys
try:
    with open("/opt/astrolift-confidential/run.json") as stream:
        bundle = json.load(stream)
    if bundle["task_id"] != os.environ["ASTROLIFT_CONFIDENTIAL_TASK_ID"]:
        raise ValueError("task identity mismatch")
    os.environ.update(bundle["environment"])
    os.execvpe(bundle["argv"][0], bundle["argv"], os.environ)
except Exception:
    sys.exit(125)
"""


class ConfidentialImageError(ValueError):
    pass


def task_environment(pod_spec: dict[str, Any], secret_manifest: dict[str, Any] | None) -> dict[str, str]:
    """Resolve only the dispatcher's own literal/per-run secret env, before it reaches Kubernetes."""
    containers = pod_spec.get("containers")
    if not isinstance(containers, list) or len(containers) != 1 or pod_spec.get("initContainers"):
        raise ConfidentialImageError(
            "confidential task packaging requires exactly one container without init containers"
        )
    container = containers[0]
    if container.get("envFrom") or pod_spec.get("volumes") or container.get("volumeMounts"):
        raise ConfidentialImageError("confidential task packaging does not support host-delivered envFrom or storage")
    secret_name = (secret_manifest or {}).get("metadata", {}).get("name")
    values = (secret_manifest or {}).get("stringData") or {}
    result = {}
    for item in container.get("env", []):
        if not isinstance(item, dict):
            raise ConfidentialImageError("confidential task environment entries must be objects")
        name = item.get("name")
        if name in result or not isinstance(name, str):
            raise ConfidentialImageError("confidential task environment must have unique named entries")
        if set(item) == {"name", "value"} and isinstance(item["value"], str):
            result[name] = item["value"]
        elif set(item) == {"name", "valueFrom"}:
            source = item["valueFrom"]
            ref = source.get("secretKeyRef", {}) if isinstance(source, dict) else {}
            if (
                not isinstance(source, dict)
                or set(source) != {"secretKeyRef"}
                or not isinstance(ref, dict)
                or ref.get("name") != secret_name
                or ref.get("optional")
                or ref.get("key") not in values
                or not isinstance(values[ref["key"]], str)
            ):
                raise ConfidentialImageError("confidential task environment references an unresolved external source")
            result[name] = values[ref["key"]]
        else:
            raise ConfidentialImageError("confidential task environment contains an unsupported entry")
    return result


def _blob(layout: Path, descriptor: dict[str, Any]) -> bytes:
    digest = descriptor.get("digest", "")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ConfidentialImageError("OCI descriptor must contain an immutable SHA256 digest")
    data = (layout / "blobs" / "sha256" / digest.split(":")[1]).read_bytes()
    if hashlib.sha256(data).hexdigest() != digest.split(":")[1] or descriptor.get("size") != len(data):
        raise ConfidentialImageError("OCI blob integrity check failed")
    return data


def _write_blob(layout: Path, data: bytes, media_type: str) -> dict[str, Any]:
    digest = hashlib.sha256(data).hexdigest()
    (layout / "blobs" / "sha256" / digest).write_bytes(data)
    return {"mediaType": media_type, "digest": f"sha256:{digest}", "size": len(data)}


def _json(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def add_run_layer(
    layout: Path,
    *,
    task_id: str,
    environment: dict[str, str],
    command: list[str] | None = None,
    args: list[str] | None = None,
    uid: int = 42042,
    gid: int = 42042,
) -> dict[str, str]:
    """Mutate only a private, single-platform OCI export; never push this plaintext layout."""
    if not re.fullmatch(r"[0-9a-f]{32}", task_id):
        raise ConfidentialImageError("confidential images require the complete task UUID hex")
    if type(uid) is not int or type(gid) is not int or uid < 0 or gid < 0:
        raise ConfidentialImageError("bundle ownership must match numeric final Pod uid/gid")
    if not isinstance(environment, dict) or any(
        not isinstance(key, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
        or not isinstance(value, str)
        or "\x00" in value
        for key, value in environment.items()
    ):
        raise ConfidentialImageError("confidential environment must contain valid string variables")
    for name in ("ASTROLIFT_CONTROLLER_URL", "AGENT_CALLBACK_URL", "ASTROLIFT_MESSAGES_URL", "ASTROLIFT_RUN_MCP_URL"):
        if name in environment:
            url = urlparse(environment[name])
            if url.scheme != "https" or not url.hostname or url.username or url.password:
                raise ConfidentialImageError("confidential callbacks require HTTPS without URL credentials")
    index = json.loads((layout / "index.json").read_bytes())
    descriptors = index.get("manifests", [])
    if len(descriptors) != 1:
        raise ConfidentialImageError("export exactly one image platform before confidential packaging")
    manifest = json.loads(_blob(layout, descriptors[0]))
    if manifest.get("mediaType") != "application/vnd.oci.image.manifest.v1+json":
        raise ConfidentialImageError("confidential packaging requires an OCI image manifest")
    config = json.loads(_blob(layout, manifest["config"]))
    image_config = config.setdefault("config", {})
    contract = (image_config.get("Labels") or {}).get("ai.astrolift.agent-image.contract", "")
    if not isinstance(contract, str) or not contract.isdecimal() or int(contract) < 7:
        raise ConfidentialImageError(
            "confidential bootstrap requires an audited Astrolift Python agent image contract >=7"
        )
    defaults = {}
    for item in image_config.get("Env") or []:
        if not isinstance(item, str) or "=" not in item:
            raise ConfidentialImageError("base image environment must contain NAME=value strings")
        key, value = item.split("=", 1)
        defaults[key] = value
    defaults.update(environment)
    entrypoint = command if command else image_config.get("Entrypoint") or []
    arguments = args if args else image_config.get("Cmd") or []
    if not isinstance(entrypoint, list) or not isinstance(arguments, list):
        raise ConfidentialImageError("OCI image command and arguments must be arrays")
    argv = entrypoint + arguments
    if not argv or any(not isinstance(value, str) or "\x00" in value for value in argv):
        raise ConfidentialImageError("confidential image must resolve an explicit executable argv")
    if image_config.get("Volumes"):
        raise ConfidentialImageError("base image declared volumes require explicit confidential storage support")
    for descriptor in manifest.get("layers", []):
        _blob(layout, descriptor)
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in ("opt", "opt/astrolift-confidential"):
            info = tarfile.TarInfo(name)
            info.type, info.mode = tarfile.DIRTYPE, 0o755
            archive.addfile(info)
        for name, data, mode in (
            (BUNDLE_PATH, _json({"task_id": task_id, "environment": defaults, "argv": argv}), 0o400),
            (BOOTSTRAP_PATH, BOOTSTRAP, 0o500),
        ):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.uid, info.gid = len(data), mode, uid, gid
            archive.addfile(info, io.BytesIO(data))
    plaintext = layer.getvalue()
    config["rootfs"]["diff_ids"].append("sha256:" + hashlib.sha256(plaintext).hexdigest())
    manifest["layers"].append(
        _write_blob(layout, gzip.compress(plaintext, mtime=0), "application/vnd.oci.image.layer.v1.tar+gzip")
    )
    # Image config is registry-visible even after layer encryption. Secrets belong only in the encrypted layer.
    image_config["Entrypoint"] = ["python3", "/" + BOOTSTRAP_PATH]
    image_config["Cmd"] = []
    image_config["User"] = f"{uid}:{gid}"
    image_config["Env"] = ["PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/home/agent"]
    image_config.pop("Labels", None)
    config["history"] = []
    manifest["config"] = _write_blob(layout, _json(config), "application/vnd.oci.image.config.v1+json")
    descriptor = _write_blob(layout, _json(manifest), "application/vnd.oci.image.manifest.v1+json")
    descriptor["annotations"] = {**descriptors[0].get("annotations", {}), "org.opencontainers.image.ref.name": "run"}
    if "platform" in descriptors[0]:
        descriptor["platform"] = descriptors[0]["platform"]
    index["manifests"] = [descriptor]
    (layout / "index.json").write_bytes(_json(index))
    return {"plaintext_manifest_digest": descriptor["digest"], "task_id": task_id}


def verify_encrypted_manifest(manifest: dict[str, Any]) -> None:
    layers = manifest.get("layers")
    if not isinstance(layers, list) or not layers:
        raise ConfidentialImageError("encrypted run image must have layers")
    for layer in layers:
        if (
            not isinstance(layer, dict)
            or layer.get("mediaType")
            not in {
                "application/vnd.oci.image.layer.v1.tar+encrypted",
                "application/vnd.oci.image.layer.v1.tar+gzip+encrypted",
                "application/vnd.oci.image.layer.v1.tar+zstd+encrypted",
            }
            or not isinstance(
                (layer.get("annotations") or {}).get("org.opencontainers.image.enc.keys.provider.awskms"), str
            )
            or not (layer.get("annotations") or {}).get("org.opencontainers.image.enc.keys.provider.awskms")
        ):
            raise ConfidentialImageError("every run image layer must be encrypted with the AWS KMS provider")
        # ocicrypt wraps each provider's opaque packet in standard base64, comma-separated.
        # Validate that framing without guessing the AWS provider's private packet schema.
        annotation = layer["annotations"]["org.opencontainers.image.enc.keys.provider.awskms"]
        try:
            if any(not base64.b64decode(packet, validate=True) for packet in annotation.split(",")):
                raise ValueError("empty provider packet")
        except ValueError as exc:
            raise ConfidentialImageError("encrypted layer has malformed AWS KMS provider packets") from exc
