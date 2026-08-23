"""One resolution path for the install's blob store (#1610).

`astrolift_pipelines.artifact_store` and `astrolift_agents.snapshot_store`
each resolved a `BlobStoreDriver` for an organization, and each opened with:

    from astrolift_drivers.registry import get_driver_for_org
    driver = get_driver_for_org(org, role="blob_store")

`get_driver_for_org` is defined nowhere in the repo. Both sites sat inside
`except ImportError: pass` whose comment read "astrolift_drivers not yet
wired", which is false twice over -- the module is wired and imported
unconditionally elsewhere; it is the *name* that was never written. That
comment is why this survived: it describes a temporary state that would
resolve itself, so nobody went looking.

The consequences differed, and only one of them was harmless:

* Artifacts fell through to the install's S3 bucket, so they worked. What
  was lost was only the per-org override nobody could register anyway.
* **Snapshots had no such fallback.** Step 2 was a local-filesystem path
  driven by an env var set only in dev, and step 3 raised. So agent VNC
  snapshots could not work on any production install, and the error told
  the operator to "configure a blob_store driver in the install's provider
  plugin registry" -- a dead end, since nothing can register that role and
  the function that would read it does not exist.

So the resolution lives here once and both call it. That the two had drifted
into different fallback chains while sharing a copied first step is the
argument for a single home: nothing compared them, so one could lose its
production path without the other showing any sign.

**No plugin registry step.** There is no `blob_store` role in any plugin
manifest, so a lookup would be a branch that cannot be taken, which is what
was here before. `GCSBlobStoreDriver`, `ABSBlobStoreDriver` and
`MinioBlobStoreDriver` exist in `_sdk/blob_store.py` and are never
constructed outside tests. Restoring genuine multicloud resolution means
registering those drivers and deciding what keys the choice -- the org's
default cluster's plugin, or explicit install settings. That is tracked in
#1610 and wants a design decision, not another unreachable branch.

What the S3 path does reach today: `AWS_S3_ENDPOINT_URL` is honoured, so
MinIO, SeaweedFS, Ceph RGW and GCS's S3-compatible XML API all work against
it. Azure Blob has no S3-compatible surface and is genuinely unreachable.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def install_s3_driver(*, purpose: str) -> Any | None:
    """The install's platform-owned S3 bucket, or None when unset.

    `purpose` names the caller in the debug log only; it is what tells you
    which of the two stores resolved when both are in play.

    Imports are lazy so the providers tree stays importable without Django
    and without boto3, which is the property the original docstrings were
    protecting.
    """
    try:
        from django.conf import settings
    except ImportError:
        return None

    if not getattr(settings, "configured", False):
        return None

    bucket = str(getattr(settings, "AWS_STORAGE_BUCKET_NAME", "") or "").strip()
    if not bucket:
        return None

    import boto3

    # `providers._sdk`, not `_sdk`. The providers tree is importable under
    # both names -- it is on sys.path directly *and* as a subpackage -- and
    # the two produce distinct class objects for the same file. Django-side
    # code uses `providers._sdk` throughout; importing the short form here
    # would make this driver's exception types unequal to the ones its
    # callers catch, and an `except` that silently misses is worse than one
    # that is missing.
    from providers._sdk.blob_store import S3BlobStoreDriver

    region = str(
        getattr(settings, "AWS_S3_REGION_NAME", "")
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    endpoint_url = str(getattr(settings, "AWS_S3_ENDPOINT_URL", "") or "").strip()
    client_kwargs: dict[str, str] = {"region_name": region}
    if endpoint_url:
        # Set only when configured. An empty endpoint_url is not the same as
        # an absent one -- botocore treats "" as a URL and fails to parse it.
        client_kwargs["endpoint_url"] = endpoint_url

    logger.debug("%s: using install S3 blob store bucket %s", purpose, bucket)
    return S3BlobStoreDriver(
        bucket=bucket,
        region=region,
        s3_client=boto3.client("s3", **client_kwargs),
    )
