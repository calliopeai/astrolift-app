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

**Per-org resolution, keyed on the org's default cluster's plugin.** That
was the decision on #1610: artifacts and snapshots land in the same cloud as
the workloads that produce them, which is the behaviour a BYOC operator
expects. An org with no default cluster, a cloud with no builder, or a
cluster that has not been told which bucket to use all fall through to the
install bucket, so nothing that worked before resolves anywhere new.

Construction lives here rather than behind `plugins.get(slug, "blob_store")`
because the four drivers do not share a config protocol -- S3 takes
bucket/region/kms, GCS takes a bucket, ABS takes an account URL and a
container. A registry lookup would return a class the caller still could not
build, which is the shape of defect this module was written to remove.

**MinIO is deliberately absent.** `MinioBlobStoreDriver` needs an access key
and a secret key, and `provider_config` is a plaintext JSON column whose own
guard (`_sdk.binding_policy.classify_key`, via
`_reject_inline_secrets`) refuses credential-bearing keys outright. So an
on-prem object store cannot be configured this way at all; it needs the
secret-reference indirection that `_sdk.cloud_credentials` documents as
absent for the same reason. Tracked separately rather than half-supported.

The install S3 path honours `AWS_S3_ENDPOINT_URL`, so MinIO, SeaweedFS, Ceph
RGW and GCS's S3-compatible XML API remain reachable that way -- as the
install's own store, not per-org.
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


# ---- per-org resolution (#1610) --------------------------------------


#: `provider_config` keys a cluster uses to say where its blobs go. Absent
#: means "use the install bucket" rather than "fail": an operator who has not
#: configured per-org storage has not made a mistake.
BUCKET_KEY = "blob_bucket"
PREFIX_KEY = "blob_prefix"


def _aws_blob_driver(cluster, pc: dict) -> Any | None:
    bucket = str(pc.get(BUCKET_KEY, "") or "").strip()
    if not bucket:
        return None
    import boto3

    from providers._sdk.blob_store import S3BlobStoreDriver

    region = str(pc.get("region", "") or getattr(cluster, "region", "") or "us-east-1")
    endpoint = str(pc.get("blob_endpoint_url", "") or "").strip()
    kwargs: dict[str, str] = {"region_name": region}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return S3BlobStoreDriver(
        bucket=bucket,
        region=region,
        prefix=str(pc.get(PREFIX_KEY, "") or ""),
        kms_key_id=(pc.get("blob_kms_key_id") or None),
        s3_client=boto3.client("s3", **kwargs),
    )


def _gcp_blob_driver(cluster, pc: dict) -> Any | None:
    bucket = str(pc.get(BUCKET_KEY, "") or "").strip()
    if not bucket:
        return None
    from providers._sdk.blob_store import GCSBlobStoreDriver

    return GCSBlobStoreDriver(bucket=bucket, prefix=str(pc.get(PREFIX_KEY, "") or ""))


def _azure_blob_driver(cluster, pc: dict) -> Any | None:
    """Azure needs two values, and half of them is not a configuration.

    A container with no account URL cannot be addressed, so this returns None
    rather than constructing a driver that fails on first use -- the install
    bucket is a working answer and a broken driver is not.
    """
    container = str(pc.get("blob_container", "") or "").strip()
    account_url = str(pc.get("blob_account_url", "") or "").strip()
    if not (container and account_url):
        return None
    from providers._sdk.blob_store import ABSBlobStoreDriver

    return ABSBlobStoreDriver(
        account_url=account_url,
        container=container,
        prefix=str(pc.get(PREFIX_KEY, "") or ""),
    )


#: Keyed on `ProviderPlugin.slug`. A cloud absent from this map falls through
#: to the install bucket; see the module docstring on MinIO.
_BLOB_BUILDERS = {
    "aws": _aws_blob_driver,
    "gcp": _gcp_blob_driver,
    "azure": _azure_blob_driver,
}


def driver_for_org(org, *, purpose: str) -> Any | None:
    """The blob store for ``org``, or the install's, or None.

    Resolution order, per the #1610 decision:

    1. The org's ``default_tenant_cluster``'s plugin, when that cluster names
       a bucket in its ``provider_config``. Blobs then live in the same cloud
       as the workloads that produce them.
    2. The install's own S3 bucket.
    3. None, which callers treat as "not configured" rather than an error.

    Every failure to resolve at step 1 is a fall-through, never a raise: no
    default cluster, no builder for the cloud, no bucket configured, or a
    driver whose dependencies are not installed. An org that has not
    configured per-org storage has not made a mistake, and turning that into
    an exception would break every install that works today.
    """
    cluster = getattr(org, "default_tenant_cluster", None)
    if cluster is not None:
        slug = str(getattr(getattr(cluster, "provider_plugin", None), "slug", "") or "")
        builder = _BLOB_BUILDERS.get(slug)
        if builder is not None:
            pc = getattr(cluster, "provider_config", None) or {}
            try:
                driver = builder(cluster, pc)
            except Exception:  # noqa: BLE001
                # A missing cloud SDK or a malformed value must not take the
                # install bucket down with it. Logged, not swallowed silently.
                logger.exception(
                    "%s: per-org blob driver for cluster %s failed to build; using the install bucket",
                    purpose,
                    getattr(cluster, "slug", cluster),
                )
                driver = None
            if driver is not None:
                logger.debug(
                    "%s: using per-org %s blob store for %s", purpose, slug, getattr(org, "slug", org)
                )
                return driver

    return install_s3_driver(purpose=purpose)
