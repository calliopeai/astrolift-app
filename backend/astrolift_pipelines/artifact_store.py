"""Artifact store for pipeline run artifacts (#71).

Wraps Astrolift's tenant blob store backend (S3/GCS/AzBlob via the
provider SDK) to provide lifecycle management for pipeline artifacts:
upload, download, presigned URL generation, and expiry-based cleanup.

Blob key scheme (as defined in the DSL spec #65):
    pipelines/{org_id}/{pipeline_id}/{run_number}/{job_id}/{artifact_name}

Artifacts are scoped to a run. Cross-run references are not supported at v1.
Retention default: 30 days. Configurable per-org via ``PIPELINE_ARTIFACT_TTL_DAYS``.

This module is called by the K8s Job spawner (M2b) at job completion
and by the GraphQL presigned-URL resolver.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_TTL_DAYS = 30


def _blob_key(org_id: str, pipeline_id: str, run_number: int, job_id: str, artifact_name: str) -> str:
    """Return the canonical blob store key for a pipeline artifact."""
    return f"pipelines/{org_id}/{pipeline_id}/{run_number}/{job_id}/{artifact_name}"


def _get_artifact_ttl_days(org) -> int:
    """Return the artifact retention TTL in days for an org."""
    extra = getattr(org, "extra_data", None) or {}
    return int(extra.get("pipeline_artifact_ttl_days", _DEFAULT_TTL_DAYS))


def _get_blob_driver(org):
    """Return a blob store driver for an org, or None if not configured.

    The blob store driver is looked up from the org's ObservabilityProfile
    or install-level configuration. This mirrors how the observability
    surface resolves log/metric backends.
    """
    try:
        from astrolift_observability.services import get_blob_driver_for_org
        return get_blob_driver_for_org(org)
    except (ImportError, Exception):
        pass

    # Dev fallback — check environment variable for a simple local path
    local_path = os.environ.get("PIPELINE_ARTIFACT_LOCAL_PATH")
    if local_path:
        return _LocalBlobDriver(local_path)

    return None


class _LocalBlobDriver:
    """Simple local filesystem blob driver for dev/test installs.

    Not suitable for production — use a real cloud blob store.
    """

    def __init__(self, base_path: str) -> None:
        self._base = Path(base_path)

    def upload(self, key: str, data: bytes) -> None:
        dest = self._base / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def download(self, key: str) -> bytes:
        dest = self._base / key
        if not dest.exists():
            raise FileNotFoundError(f"Artifact not found: {key}")
        return dest.read_bytes()

    def presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return f"file://{self._base / key}"

    def delete(self, key: str) -> None:
        dest = self._base / key
        if dest.exists():
            dest.unlink()


class ArtifactStoreError(Exception):
    """Raised when an artifact operation fails."""


def upload_artifact(
    run,
    job_run,
    *,
    artifact_name: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> "Artifact":
    """Upload artifact bytes and create an ``Artifact`` record.

    Args:
        run: PipelineRun instance
        job_run: JobRun instance
        artifact_name: Logical name declared in ``[jobs.outputs]``
        data: Raw bytes to upload
        content_type: MIME type (default: application/octet-stream)

    Returns:
        The created Artifact model instance.

    Raises:
        ArtifactStoreError: when the blob store is not configured or upload fails.
    """
    from astrolift_pipelines.models import Artifact

    org = run.pipeline.organization
    driver = _get_blob_driver(org)
    if driver is None:
        raise ArtifactStoreError(
            "No blob store configured for this install. "
            "Configure a blob store backend to enable pipeline artifacts."
        )

    key = _blob_key(
        org_id=str(org.id),
        pipeline_id=str(run.pipeline.guid),
        run_number=run.run_number,
        job_id=job_run.job.job_id,
        artifact_name=artifact_name,
    )

    try:
        driver.upload(key, data)
    except Exception as exc:
        raise ArtifactStoreError(f"Upload failed for {key!r}: {exc}") from exc

    artifact = Artifact.objects.create(
        pipeline_run=run,
        job_run=job_run,
        name=artifact_name,
        blob_key=key,
        size_bytes=len(data),
        content_type=content_type,
    )
    logger.info("pipelines.artifact_store: uploaded %s (%d bytes)", key, len(data))
    return artifact


def download_artifact(artifact) -> bytes:
    """Download artifact bytes for a given Artifact model instance.

    Raises:
        ArtifactStoreError: when the blob store is not configured or download fails.
    """
    org = artifact.pipeline_run.pipeline.organization
    driver = _get_blob_driver(org)
    if driver is None:
        raise ArtifactStoreError("No blob store configured for this install.")

    try:
        return driver.download(artifact.blob_key)
    except Exception as exc:
        raise ArtifactStoreError(f"Download failed for {artifact.blob_key!r}: {exc}") from exc


def presigned_download_url(artifact, expires_in: int = 3600) -> str:
    """Generate a short-lived presigned download URL for an artifact.

    ``expires_in`` is the URL validity period in seconds (default: 1 hour).
    For local filesystem drivers this returns a ``file://`` URL usable
    only on the server.

    Raises:
        ArtifactStoreError: when the blob store is not configured.
    """
    org = artifact.pipeline_run.pipeline.organization
    driver = _get_blob_driver(org)
    if driver is None:
        raise ArtifactStoreError("No blob store configured for this install.")

    try:
        return driver.presigned_url(artifact.blob_key, expires_in=expires_in)
    except Exception as exc:
        raise ArtifactStoreError(
            f"Presigned URL generation failed for {artifact.blob_key!r}: {exc}"
        ) from exc


def expire_artifacts(pipeline_run) -> int:
    """Soft-delete all artifacts for a pipeline run past their TTL.

    Returns the number of artifacts soft-deleted.
    """
    from django.utils import timezone
    from datetime import timedelta
    from astrolift_pipelines.models import Artifact

    org = pipeline_run.pipeline.organization
    ttl_days = _get_artifact_ttl_days(org)
    cutoff = pipeline_run.finished_at or pipeline_run.created_at
    if cutoff is None:
        return 0

    expiry = cutoff + timedelta(days=ttl_days)
    now = timezone.now()

    if now < expiry:
        return 0  # not yet expired

    expired = Artifact.objects.filter(
        pipeline_run=pipeline_run,
        deleted_at__isnull=True,
    )
    count = expired.count()
    expired.update(deleted_at=now)
    logger.info(
        "pipelines.artifact_store: expired %d artifacts for run %s", count, pipeline_run.guid
    )
    return count
