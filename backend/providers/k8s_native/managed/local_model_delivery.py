"""Private immutable-file hydration; no user paths, archive extraction or shell argv."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import UUID

from _sdk.local_model_artifact import ModelFile, local_source_identity, model_manifest


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_content(directory, manifest):
    config = json.loads((directory / "config.json").read_text())
    if not isinstance(config, dict) or not isinstance(config.get("model_type"), str) or config.get("auto_map"):
        raise ValueError("Local model configuration requiring arbitrary remote code is unsupported.")
    for file in manifest:
        if file["name"].endswith(".safetensors.index.json"):
            index = json.loads((directory / file["name"]).read_text())
            weights = index.get("weight_map") if isinstance(index, dict) else None
            if (
                not isinstance(weights, dict)
                or not weights
                or any(
                    not isinstance(name, str)
                    or not name.endswith(".safetensors")
                    or name not in {f["name"] for f in manifest}
                    for name in weights.values()
                )
            ):
                raise ValueError("Local model weight index references missing or unsupported files.")


def hydrate(plan: dict, destination: Path, *, allow_local_http=False):
    """Verify all files before atomic directory publication; existing files are rehashed."""
    manifest, digest = model_manifest(
        [ModelFile(**{key: file[key] for key in ("name", "sha256", "size_bytes")}) for file in plan["files"]]
    )
    if digest != plan["manifest_sha256"]:
        raise ValueError("Model delivery manifest differs from its admitted digest.")
    destination = destination / digest
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise ValueError("Model destination is not an owned directory.")
        valid = {path.name for path in destination.iterdir()} == {file["name"] for file in manifest}
        for file in manifest:
            path = destination / file["name"]
            if path.is_symlink() or not path.is_file() or path.stat().st_size != file["size_bytes"]:
                valid = False
                break
            with path.open("rb") as body:
                if hashlib.file_digest(body, "sha256").hexdigest() != file["sha256"]:
                    valid = False
                    break
        if valid:
            _validate_content(destination, manifest)
            return destination
        # Do not overwrite or adopt a pre-existing conflicting content directory.
        raise ValueError("Existing model bytes differ from the immutable manifest.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.parent / (".staging-" + str(UUID(bytes=os.urandom(16))))
    stage.mkdir(mode=0o700)
    opener = urllib.request.build_opener(_NoRedirect())
    deadline = time.monotonic() + 3600
    try:
        for file in plan["files"]:
            parsed = urllib.parse.urlsplit(file["url"])
            if (
                parsed.username
                or parsed.password
                or parsed.fragment
                or not parsed.hostname
                or (
                    parsed.scheme != "https"
                    and not (
                        allow_local_http and parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")
                    )
                )
            ):
                raise ValueError("Model delivery requires a trusted HTTPS object URL.")
            path = stage / file["name"]
            checksum, total = hashlib.sha256(), 0
            with opener.open(file["url"], timeout=20) as response, path.open("xb") as output:
                if response.status != 200:
                    raise ValueError("Model delivery did not return the exact object.")
                while block := response.read(min(1024 * 1024, file["size_bytes"] - total + 1)):
                    if time.monotonic() >= deadline:
                        raise ValueError("Local model delivery exceeded its bounded download window.")
                    total += len(block)
                    if total > file["size_bytes"]:
                        raise ValueError("Model file exceeds its admitted size.")
                    checksum.update(block)
                    output.write(block)
            if total != file["size_bytes"] or checksum.hexdigest() != file["sha256"]:
                raise ValueError("Model file differs from its admitted size/checksum.")
        _validate_content(stage, manifest)
        stage.rename(destination)
        return destination
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def delivery_resources(*, namespace, name, service_id, image, pvc_name, manifest_sha256, private_plan):
    """Only a server-selected certified runtime may execute the downloader."""
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", image or ""):
        raise ValueError("Local model hydration requires a certified digest-pinned runtime image.")
    if private_plan["manifest_sha256"] != manifest_sha256:
        raise ValueError("Model delivery plan differs from the immutable source.")
    UUID(str(service_id))
    secret_name = name + "-model-delivery"
    return {
        "secret": {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "Opaque",
            "metadata": {
                "namespace": namespace,
                "name": secret_name,
                "labels": {
                    "app.kubernetes.io/managed-by": "astrolift",
                    "astrolift.io/managed-service-id": str(service_id),
                },
            },
            "stringData": {"delivery.json": json.dumps(private_plan, separators=(",", ":"))},
        },
        "init_container": {
            "name": "hydrate-local-model",
            "image": image,
            "command": ["python3", "/astrolift-runtime/local_model_delivery.py"],
            "args": ["/model-delivery/delivery.json", "/models"],
            "volumeMounts": [
                {"name": "cache", "mountPath": "/models"},
                {"name": "runtime", "mountPath": "/astrolift-runtime", "readOnly": True},
                {"name": "model-delivery", "mountPath": "/model-delivery", "readOnly": True},
            ],
            "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"memory": "256Mi"}},
        },
        "volume": {"name": "model-delivery", "secret": {"secretName": secret_name}},
        "model_mount": {"name": "cache", "mountPath": "/models", "readOnly": True},
        "model_path": "/models/" + manifest_sha256,
        "pvc_name": pvc_name,
    }


def validate_delivery(plan, cfg, placement):
    artifact_id, version, digest = local_source_identity(cfg)
    expected = {
        "organization_id": placement.organization_id,
        "cluster_id": placement.cluster_id,
        "managed_service_id": placement.managed_service_id,
        "artifact_id": artifact_id,
        "artifact_version": version,
        "manifest_sha256": digest,
    }
    if not isinstance(plan, dict) or any(plan.get(key) != value for key, value in expected.items()):
        raise ValueError("Private local delivery does not match the current owner and source.")
    try:
        _, actual = model_manifest(
            [ModelFile(**{key: file[key] for key in ("name", "sha256", "size_bytes")}) for file in plan["files"]]
        )
    except (KeyError, TypeError, AttributeError):
        raise ValueError("Private local delivery manifest is invalid.") from None
    if actual != digest:
        raise ValueError("Private local delivery manifest differs from the admitted source.")


def runtime_sources():
    from _sdk import local_model_artifact

    return {
        "local_model_delivery.py": Path(__file__)
        .read_text()
        .replace(
            "from _sdk.local_model_artifact import ModelFile, local_source_identity, model_manifest",
            "from astrolift_local_model_artifact import ModelFile, local_source_identity, model_manifest",
        ),
        "astrolift_local_model_artifact.py": Path(local_model_artifact.__file__).read_text(),
    }


if __name__ == "__main__":
    import sys

    try:
        if len(sys.argv) != 3:
            raise ValueError("Model delivery arguments are invalid.")
        hydrate(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]))
    except Exception:
        print("Local model hydration failed; immutable source delivery remains unverified.", file=sys.stderr)
        raise SystemExit(1) from None
