"""Per-task VNC snapshot store — blob driver resolution + presigned URLs.

The ``-vnc`` agent images run a pod-side uploader that PUTs a JPEG of the
live framebuffer every N seconds to ``$ASTROLIFT_SNAPSHOT_URL`` (a no-op if
that env var is unset). At spawn time the dispatcher mints a presigned PUT
URL for the task's snapshot key and injects it into the pod (see
``astrolift_dispatch.snapshot_injector``). A later gallery surface reads the
latest frame back via a presigned GET.

This module reuses the *same* :class:`BlobStoreDriver` the pipeline artifact
system uses (resolved from the install's provider plugin registry, or the
local-fs fallback in dev/CI) — snapshots are just a distinct key prefix in
the same blob store, not a new bucket.

Key layout (one well-known key per task; the uploader overwrites it):
    snapshots/{task_guid}/latest.jpg

Driver resolution order (mirrors ``astrolift_pipelines.artifact_store``):
1. Provider plugin registry ``blob_store`` driver — production path.
2. ``ASTROLIFT_SNAPSHOT_LOCAL_PATH`` (falls back to
   ``PIPELINE_ARTIFACT_LOCAL_PATH``) — LocalFsBlobStoreDriver for dev/CI.
3. Raises ``BlobStoreNotConfiguredError``.
"""

from __future__ import annotations

import logging
import os

from providers._sdk.blob_store import (
    BlobStoreDriver,
    BlobStoreNotConfiguredError,
    LocalFsBlobStoreDriver,
)

logger = logging.getLogger("astrolift.agents.snapshot_store")

# Snapshot PUT/GET URLs are short-lived; the uploader re-mints on each spawn
# and the gallery re-mints per view. 15 minutes matches the artifact TTL.
SNAPSHOT_URL_TTL_SECONDS = 900

# The uploader writes JPEG frames.
SNAPSHOT_CONTENT_TYPE = "image/jpeg"


def snapshot_blob_key(task_guid: str) -> str:
    """Return the canonical blob key for a task's latest VNC snapshot.

    Layout: ``snapshots/{task_guid}/latest.jpg`` — a single well-known key
    the pod-side uploader overwrites each interval, so the gallery always
    reads the most recent frame from the same key.
    """
    return f"snapshots/{task_guid}/latest.jpg"


def _get_blob_driver(org: object) -> BlobStoreDriver:
    """Return the BlobStoreDriver for ``org``'s install, or raise.

    Same resolution strategy as the pipeline artifact store: try the
    provider plugin registry first, then the local-fs dev fallback.

    ``org`` is the Django Organization instance; it is not imported here so
    the resolution stays usable from the providers-isolated layers.
    """
    # Step 1 — the install's platform-owned S3 bucket. This is the production
    # path, and it is new (#1610): what stood here was a lookup of
    # `astrolift_drivers.registry.get_driver_for_org`, which does not exist,
    # so snapshots fell straight through to a dev-only local path and then
    # raised. There was no way to store a snapshot on a production install.
    from core.blob_store_resolution import install_s3_driver

    driver = install_s3_driver(purpose="snapshot store")
    if driver is not None:
        return driver

    # Step 2 — local filesystem fallback. Snapshots share the artifact local
    # path by default so they work wherever artifacts do, but can be split
    # onto their own path via ASTROLIFT_SNAPSHOT_LOCAL_PATH.
    local_path = os.environ.get("ASTROLIFT_SNAPSHOT_LOCAL_PATH") or os.environ.get(
        "PIPELINE_ARTIFACT_LOCAL_PATH", ""
    )
    if local_path:
        logger.debug("snapshot store using LocalFsBlobStoreDriver at %s", local_path)
        return LocalFsBlobStoreDriver(base_path=local_path)

    # Step 3 — nothing configured.
    raise BlobStoreNotConfiguredError(
        "No blob store is configured for VNC snapshots. Set "
        "AWS_STORAGE_BUCKET_NAME for a normal install, or "
        "ASTROLIFT_SNAPSHOT_LOCAL_PATH (or PIPELINE_ARTIFACT_LOCAL_PATH) for "
        "local dev."
    )


def presigned_snapshot_upload_url(
    *,
    org: object,
    task_guid: str,
    expires_in: int = SNAPSHOT_URL_TTL_SECONDS,
) -> str:
    """Mint a presigned PUT URL for ``task_guid``'s snapshot key.

    Injected into the agent pod as ``ASTROLIFT_SNAPSHOT_URL`` so the pod-side
    uploader can PUT JPEG frames to it. Raises ``BlobStoreNotConfiguredError``
    when the install has no blob store.
    """
    driver = _get_blob_driver(org)
    key = snapshot_blob_key(task_guid)
    return driver.presigned_upload_url(
        key,
        expires_in=expires_in,
        content_type=SNAPSHOT_CONTENT_TYPE,
    )


def presigned_snapshot_download_url(
    *,
    org: object,
    task_guid: str,
    expires_in: int = SNAPSHOT_URL_TTL_SECONDS,
) -> str:
    """Mint a presigned GET URL for ``task_guid``'s latest snapshot.

    Used by the gallery read surface. Raises ``BlobStoreNotFoundError`` when
    no frame has been uploaded yet, ``BlobStoreNotConfiguredError`` when the
    install has no blob store.
    """
    driver = _get_blob_driver(org)
    key = snapshot_blob_key(task_guid)
    return driver.presigned_url(key, expires_in=expires_in)
