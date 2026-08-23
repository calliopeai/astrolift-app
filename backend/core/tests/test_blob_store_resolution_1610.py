"""Both blob stores resolve on a normal install (#1610).

The dead `get_driver_for_org` step cost the two stores differently, and that
asymmetry is the thing worth holding: artifacts had an S3 fallback behind it
and worked; snapshots did not, so agent VNC snapshots could not be stored on
any production install. Nothing compared the two, so one could lose its
production path while the other looked fine.

These drive the real `_get_blob_driver` in both modules against the same
settings a normal install has.
"""

from __future__ import annotations

import pytest

from astrolift_agents.snapshot_store import _get_blob_driver as snapshot_driver
from astrolift_pipelines.artifact_store import _get_blob_driver as artifact_driver

pytestmark = pytest.mark.django_db


@pytest.fixture
def install_bucket(settings, monkeypatch):
    settings.AWS_STORAGE_BUCKET_NAME = "astrolift-platform-media"
    settings.AWS_S3_REGION_NAME = "us-west-2"
    monkeypatch.delenv("PIPELINE_ARTIFACT_LOCAL_PATH", raising=False)
    monkeypatch.delenv("ASTROLIFT_SNAPSHOT_LOCAL_PATH", raising=False)


def test_snapshots_resolve_on_a_normal_install(install_bucket):
    """The regression. This raised BlobStoreNotConfiguredError before #1610,
    on every install that was not a developer laptop.

    The fixture clears both local-path env vars, which is what a production
    install looks like -- only dev and CI set them, and they still take
    precedence when they are set.
    """
    driver = snapshot_driver(object())

    assert driver._bucket == "astrolift-platform-media"


def test_artifacts_resolve_on_a_normal_install(install_bucket):
    driver = artifact_driver(object())

    assert driver._bucket == "astrolift-platform-media"


def test_both_stores_resolve_to_the_same_place(install_bucket):
    """They share one resolver now. Two copies of a resolution chain is how
    they drifted into different fallbacks while looking identical."""
    assert type(snapshot_driver(object())) is type(artifact_driver(object()))


def test_a_custom_endpoint_is_honoured(settings, monkeypatch):
    """What makes the S3 path reach MinIO, SeaweedFS, Ceph and GCS's XML API
    rather than only AWS. Claimed in the module docstring, so held here."""
    from core.blob_store_resolution import install_s3_driver

    settings.AWS_STORAGE_BUCKET_NAME = "bucket"
    settings.AWS_S3_ENDPOINT_URL = "https://minio.internal:9000"

    driver = install_s3_driver(purpose="test")

    assert driver is not None
    assert "minio.internal" in driver._s3.meta.endpoint_url


def test_no_bucket_configured_returns_none_rather_than_a_broken_driver(settings):
    """The caller's next fallback must get a chance. Returning a driver
    pointed at an empty bucket would fail later, at upload time."""
    from core.blob_store_resolution import install_s3_driver

    settings.AWS_STORAGE_BUCKET_NAME = ""

    assert install_s3_driver(purpose="test") is None


def test_snapshots_still_raise_when_nothing_at_all_is_configured(settings, monkeypatch):
    from providers._sdk.blob_store import BlobStoreNotConfiguredError

    settings.AWS_STORAGE_BUCKET_NAME = ""
    monkeypatch.delenv("PIPELINE_ARTIFACT_LOCAL_PATH", raising=False)
    monkeypatch.delenv("ASTROLIFT_SNAPSHOT_LOCAL_PATH", raising=False)

    with pytest.raises(BlobStoreNotConfiguredError) as exc:
        snapshot_driver(object())

    # The old message told operators to configure a blob_store driver in the
    # provider plugin registry, which nothing can do.
    assert "AWS_STORAGE_BUCKET_NAME" in str(exc.value)
    assert "provider plugin registry" not in str(exc.value)


def test_an_explicit_local_path_still_wins_for_snapshots(settings, monkeypatch, tmp_path):
    """Ordering, held explicitly.

    #1610 added the S3 step *below* the local one rather than above it. The
    other way round would have silently moved a developer's snapshots into
    the install bucket the first time both were configured, which is a
    behaviour change disguised as a bug fix.
    """
    from providers._sdk.blob_store import LocalFsBlobStoreDriver

    settings.AWS_STORAGE_BUCKET_NAME = "astrolift-platform-media"
    monkeypatch.setenv("ASTROLIFT_SNAPSHOT_LOCAL_PATH", str(tmp_path))

    assert isinstance(snapshot_driver(object()), LocalFsBlobStoreDriver)
