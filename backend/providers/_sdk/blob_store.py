"""BlobStoreDriver protocol — pipeline artifact blob storage (#90).

Distinct from ObjectStoreDriver (which provisions per-app buckets):
BlobStoreDriver is the raw key-value blob interface used by the pipeline
artifact store (astrolift_pipelines/artifact_store.py) and the task
metering system for large payload storage.

Implementations live in each cloud provider module:
  providers/aws/blob_store.py        — S3BlobStoreDriver
  providers/gcp/blob_store.py        — GCSBlobStoreDriver
  providers/azure/blob_store.py      — AzureBlobStoreDriver
  providers/_sdk/blob_store.py       — LocalFsBlobStoreDriver (dev)

The platform selects the driver based on the install's configured
blob backend (PIPELINE_BLOB_STORE_BACKEND setting).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class BlobStoreDriver(Protocol):
    """Protocol: raw blob key-value operations for pipeline artifacts.

    All drivers must satisfy this protocol. The @runtime_checkable
    decorator allows isinstance() checks at driver registration time.
    """

    def upload(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        """Upload bytes to the blob store at the given key."""
        ...

    def download(self, key: str) -> bytes:
        """Download bytes from the blob store. Raises FileNotFoundError if missing."""
        ...

    def presigned_url(self, key: str, *, expires_in: int = 3600) -> str:
        """Generate a presigned download URL valid for expires_in seconds."""
        ...

    def delete(self, key: str) -> None:
        """Delete an object. No-op if the key doesn't exist."""
        ...

    def list_keys(self, prefix: str) -> list[str]:
        """Return all keys under a prefix. Used for artifact expiry sweeps."""
        ...


class LocalFsBlobStoreDriver:
    """Local filesystem blob driver for dev and test installs.

    Not suitable for production — artifacts do not survive container restarts
    and are not accessible across nodes. Use an S3-compatible backend in production.

    Configured by setting PIPELINE_ARTIFACT_LOCAL_PATH in the environment.
    """

    def __init__(self, base_path: str) -> None:
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)

    def upload(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
        dest = self._base / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def download(self, key: str) -> bytes:
        dest = self._base / key
        if not dest.exists():
            raise FileNotFoundError(f"Blob not found: {key}")
        return dest.read_bytes()

    def presigned_url(self, key: str, *, expires_in: int = 3600) -> str:
        return f"file://{self._base / key}"

    def delete(self, key: str) -> None:
        dest = self._base / key
        if dest.exists():
            dest.unlink()

    def list_keys(self, prefix: str) -> list[str]:
        search_root = self._base / prefix
        if not search_root.exists():
            return []
        return [
            str(p.relative_to(self._base))
            for p in search_root.rglob("*")
            if p.is_file()
        ]


class S3BlobStoreDriver:
    """AWS S3 blob store driver for pipeline artifacts.

    Requires boto3 and AWS credentials (via IAM role, env vars, or profile).
    The bucket name and region are set via PIPELINE_S3_BUCKET and
    PIPELINE_S3_REGION settings.
    """

    def __init__(self, bucket: str, region: str) -> None:
        self._bucket = bucket
        self._region = region
        self._client = None  # lazy init

    def _get_client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("s3", region_name=self._region)
        return self._client

    def upload(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
        self._get_client().put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    def download(self, key: str) -> bytes:
        try:
            response = self._get_client().get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()
        except Exception as exc:
            raise FileNotFoundError(f"S3 object not found: {key}") from exc

    def presigned_url(self, key: str, *, expires_in: int = 3600) -> str:
        return self._get_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def delete(self, key: str) -> None:
        try:
            self._get_client().delete_object(Bucket=self._bucket, Key=key)
        except Exception:  # noqa: BLE001
            pass

    def list_keys(self, prefix: str) -> list[str]:
        paginator = self._get_client().get_paginator("list_objects_v2")
        keys = []
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents") or []:
                keys.append(obj["Key"])
        return keys


def get_blob_store_driver() -> BlobStoreDriver | None:
    """Return the configured blob store driver for the current install.

    Selection order:
    1. S3 — if PIPELINE_S3_BUCKET is set
    2. Local FS — if PIPELINE_ARTIFACT_LOCAL_PATH is set
    3. None — artifact store returns an error

    Cloud-specific drivers (GCS, Azure) follow the same pattern using
    their respective environment variables.
    """
    s3_bucket = os.environ.get("PIPELINE_S3_BUCKET")
    if s3_bucket:
        return S3BlobStoreDriver(
            bucket=s3_bucket,
            region=os.environ.get("PIPELINE_S3_REGION", "us-east-1"),
        )

    local_path = os.environ.get("PIPELINE_ARTIFACT_LOCAL_PATH")
    if local_path:
        return LocalFsBlobStoreDriver(local_path)

    return None
