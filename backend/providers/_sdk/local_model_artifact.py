"""Immutable, independently owned model files in the install's versioned S3 store."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

MAX_FILE_BYTES = 5_000_000_000
MAX_TOTAL_BYTES = 200 * 1024**3
MAX_FILES = 256
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")
_METADATA = frozenset(
    {
        "config.json",
        "generation_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "tokenizer.model",
        "vocab.json",
        "merges.txt",
        "chat_template.jinja",
    }
)


class ArtifactStoreUnavailable(RuntimeError):
    """A safe refusal, never a provider response or signed URL."""


@dataclass(frozen=True)
class ModelFile:
    name: str
    size_bytes: int
    sha256: str


def model_manifest(files: list[ModelFile]) -> tuple[list[dict[str, Any]], str]:
    if not 1 <= len(files) <= MAX_FILES:
        raise ValueError("A local model requires 1 to 256 files.")
    names = set()
    records = []
    for file in files:
        if (
            not isinstance(file.name, str)
            or not _NAME.fullmatch(file.name)
            or ".." in file.name
            or (file.name not in _METADATA and not file.name.endswith((".safetensors", ".safetensors.index.json")))
        ):
            raise ValueError("Only flat safetensors model, tokenizer and configuration files are supported.")
        if file.name in names:
            raise ValueError("Model file names must be unique.")
        if (
            not isinstance(file.size_bytes, int)
            or isinstance(file.size_bytes, bool)
            or not 0 < file.size_bytes <= MAX_FILE_BYTES
        ):
            raise ValueError("Each file must be nonempty and at most 5 GB; multipart imports are unsupported.")
        if file.name.endswith(".json") and file.size_bytes > 64 * 1024**2:
            raise ValueError("Model metadata must be at most 64 MiB.")
        if not isinstance(file.sha256, str) or not _SHA.fullmatch(file.sha256):
            raise ValueError("Each model file requires its SHA-256 checksum.")
        names.add(file.name)
        records.append({"name": file.name, "size_bytes": file.size_bytes, "sha256": file.sha256})
    if "config.json" not in names or not names.intersection({"tokenizer.json", "tokenizer.model"}):
        raise ValueError("A model requires config.json and tokenizer.json or tokenizer.model.")
    if not any(name.endswith(".safetensors") for name in names):
        raise ValueError("A model requires safetensors weights; pickle weights and arbitrary code are unsupported.")
    if sum(file.size_bytes for file in files) > MAX_TOTAL_BYTES:
        raise ValueError("The local model exceeds the 200 GiB total limit.")
    records.sort(key=lambda file: file["name"])
    digest = hashlib.sha256(json.dumps(records, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    return records, digest


def model_object_key(org_id: str, artifact_id: str, file_id: str) -> str:
    ids = [str(UUID(value)) for value in (org_id, artifact_id, file_id)]
    return "astrolift/model-artifacts/" + "/".join(ids)


def local_source_identity(cfg: dict[str, Any]) -> tuple[str, int, str]:
    """Public immutable identity only; local sources never carry HF credentials/revisions."""
    try:
        artifact_id = str(UUID(cfg["model_artifact_id"]))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ValueError("Local model artifact identity is invalid.") from None
    version, digest = cfg.get("model_artifact_version"), cfg.get("model_artifact_manifest_sha256")
    if (
        cfg.get("model_source") != "local_artifact"
        or cfg.get("model") != "local-" + artifact_id
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
        or not isinstance(digest, str)
        or not _SHA.fullmatch(digest)
        or "model_revision" in cfg
        or "hf_token_secret_ref" in cfg
    ):
        raise ValueError("Local model requires an immutable artifact source without Hugging Face credentials.")
    return artifact_id, version, digest


class VersionedModelStore:
    """No arbitrary bucket/key input from a request; the backend owns both."""

    def __init__(self, *, client: Any, bucket: str, kms_key_id: str = "", checkpoint=lambda: None):
        self.client, self.bucket, self.kms_key_id, self.checkpoint = client, bucket, kms_key_id, checkpoint
        self.deadline = time.monotonic() + 120

    def _call(self, method, **kwargs):
        if time.monotonic() >= self.deadline:
            raise ArtifactStoreUnavailable(
                "Local model verification exceeded its bounded request window; retry safely."
            )
        self.checkpoint()
        try:
            return getattr(self.client, method)(Bucket=self.bucket, **kwargs)
        except Exception:
            self.checkpoint()
            raise ArtifactStoreUnavailable("Local model storage could not verify the request.") from None

    def require_versioning(self):
        if self._call("get_bucket_versioning").get("Status") != "Enabled":
            raise ArtifactStoreUnavailable("Local model imports require a configured versioned install bucket.")
        public = self._call("get_public_access_block").get("PublicAccessBlockConfiguration", {})
        if any(
            public.get(key) is not True
            for key in (
                "BlockPublicAcls",
                "IgnorePublicAcls",
                "BlockPublicPolicy",
                "RestrictPublicBuckets",
            )
        ):
            raise ArtifactStoreUnavailable("Local model imports require all install bucket public-access blocks.")

    def upload(self, key: str, file: dict[str, Any]) -> tuple[str, dict[str, str]]:
        self.checkpoint()
        checksum = base64.b64encode(bytes.fromhex(file["sha256"])).decode()
        params = {
            "Bucket": self.bucket,
            "Key": key,
            "ContentType": "application/octet-stream",
            "ContentLength": file["size_bytes"],
            "ChecksumSHA256": checksum,
            "ServerSideEncryption": "aws:kms" if self.kms_key_id else "AES256",
        }
        headers = {
            "Content-Type": "application/octet-stream",
            "x-amz-checksum-sha256": checksum,
            "x-amz-server-side-encryption": params["ServerSideEncryption"],
        }
        if self.kms_key_id:
            params["SSEKMSKeyId"] = self.kms_key_id
            headers["x-amz-server-side-encryption-aws-kms-key-id"] = self.kms_key_id
        try:
            url = self.client.generate_presigned_url("put_object", Params=params, ExpiresIn=900)
        except Exception:
            self.checkpoint()
            raise ArtifactStoreUnavailable("Local model upload authorization is unavailable.") from None
        return url, headers

    def verified_version(self, key: str, file: dict[str, Any], *, version_id: str | None = None) -> str:
        response = self._call(
            "head_object", Key=key, ChecksumMode="ENABLED", **({"VersionId": version_id} if version_id else {})
        )
        version = response.get("VersionId")
        if (
            not isinstance(version, str)
            or not 1 <= len(version) <= 1024
            or version == "null"
            or (version_id is not None and version != version_id)
            or response.get("ContentLength") != file["size_bytes"]
            or response.get("ChecksumSHA256") != base64.b64encode(bytes.fromhex(file["sha256"])).decode()
            or response.get("ChecksumType", "FULL_OBJECT") != "FULL_OBJECT"
            or response.get("DeleteMarker") is True
        ):
            raise ArtifactStoreUnavailable("A model file is missing or its immutable size/checksum differs.")
        return version

    def download(self, key: str, file: dict[str, Any], version_id: str) -> str:
        self.verified_version(key, file, version_id=version_id)
        self.checkpoint()
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key, "VersionId": version_id},
                ExpiresIn=3600,
            )
        except Exception:
            self.checkpoint()
            raise ArtifactStoreUnavailable("Local model delivery authorization is unavailable.") from None
