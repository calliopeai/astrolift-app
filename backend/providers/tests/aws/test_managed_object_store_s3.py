"""Tests for S3 ObjectStore managed-service driver (#35)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import boto3
import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from aws.managed._base import ManagedServiceError, parse_handle
from aws.managed.object_store_s3 import KIND, S3Config, S3Driver

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture
def s3_client() -> Generator:
    from moto import mock_aws

    with mock_aws():
        yield boto3.client("s3", region_name="us-east-1")


@pytest.fixture
def driver(s3_client) -> S3Driver:
    return S3Driver(
        config=S3Config(region="us-east-1"),
        client=s3_client,
    )


def _spec(**overrides) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="aws-prod",
        service_handle_hint="uploads",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_bucket(driver: S3Driver, s3_client) -> None:
    result = driver.provision(_spec())
    assert result.ok is True
    kind, bucket_name = parse_handle(result.handle)
    assert kind == KIND
    assert "acme" in bucket_name
    assert "api" in bucket_name
    # Bucket actually exists
    response = s3_client.list_buckets()
    bucket_names = [b["Name"] for b in response["Buckets"]]
    assert bucket_name in bucket_names


def test_provision_idempotent(driver: S3Driver) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle


def test_provision_applies_versioning(
    driver: S3Driver,
    s3_client,
) -> None:
    result = driver.provision(_spec())
    _, bucket_name = parse_handle(result.handle)
    versioning = s3_client.get_bucket_versioning(Bucket=bucket_name)
    assert versioning.get("Status") == "Enabled"


def test_provision_applies_public_access_block(
    driver: S3Driver,
    s3_client,
) -> None:
    result = driver.provision(_spec())
    _, bucket_name = parse_handle(result.handle)
    pab = s3_client.get_public_access_block(Bucket=bucket_name)
    cfg = pab["PublicAccessBlockConfiguration"]
    assert cfg["BlockPublicAcls"] is True
    assert cfg["BlockPublicPolicy"] is True


def test_provision_tags_bucket(driver: S3Driver, s3_client) -> None:
    """Universal tag schema (memory: astrolift.io/* namespace)."""
    result = driver.provision(_spec())
    _, bucket_name = parse_handle(result.handle)
    tagging = s3_client.get_bucket_tagging(Bucket=bucket_name)
    tag_dict = {t["Key"]: t["Value"] for t in tagging["TagSet"]}
    assert tag_dict["astrolift.io/managed-by"] == "platform"
    assert tag_dict["astrolift.io/organization"] == "acme"
    assert tag_dict["astrolift.io/app"] == "api"


def test_bucket_name_collapses_invalid_chars(driver: S3Driver) -> None:
    result = driver.provision(
        _spec(
            organization_slug="ACME_Org",
            app_slug="my.app",
        )
    )
    _, bucket_name = parse_handle(result.handle)
    # No uppercase, no underscores, no dots
    assert bucket_name == bucket_name.lower()
    assert "_" not in bucket_name
    assert ".." not in bucket_name


def test_bucket_name_at_most_63_chars(driver: S3Driver) -> None:
    result = driver.provision(
        _spec(
            organization_slug="x" * 30,
            app_slug="y" * 30,
            environment_name="z" * 30,
        )
    )
    _, bucket_name = parse_handle(result.handle)
    assert len(bucket_name) <= 63


# ---- status ----------------------------------------------------


def test_status_available_after_provision(driver: S3Driver) -> None:
    result = driver.provision(_spec())
    status = driver.status(ServiceHandle(handle=result.handle))
    assert status.state == "available"


def test_status_deprovisioned_when_missing(driver: S3Driver) -> None:
    fake_handle = "object_store/never-existed-bucket-xyz"
    status = driver.status(ServiceHandle(handle=fake_handle))
    assert status.state == "deprovisioned"


# ---- binding ---------------------------------------------------


def test_binding_emits_env_vars(driver: S3Driver) -> None:
    # Canonical object_store envelope (#1003) + the S3_BUCKET_ARN extra.
    result = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=result.handle))
    assert "BUCKET_NAME" in binding.env_vars
    assert "BUCKET_REGION" in binding.env_vars
    assert "S3_BUCKET_ARN" in binding.env_vars
    bucket_arn = binding.env_vars["S3_BUCKET_ARN"].literal
    assert bucket_arn.startswith("arn:aws:s3:::")


def test_binding_emits_iam_grants(driver: S3Driver) -> None:
    """Bucket-level grants vs object-level grants split."""
    result = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=result.handle))
    assert len(binding.iam_grants) == 2
    bucket_grant = next(g for g in binding.iam_grants if not g.resource.endswith("/*"))
    object_grant = next(g for g in binding.iam_grants if g.resource.endswith("/*"))
    # Bucket-level: read-only
    assert "s3:ListBucket" in bucket_grant.actions
    assert "s3:DeleteObject" not in bucket_grant.actions
    # Object-level: full CRUD
    assert "s3:GetObject" in object_grant.actions
    assert "s3:PutObject" in object_grant.actions


# ---- update ---------------------------------------------------


def test_update_returns_no_op(driver: S3Driver) -> None:
    """S3 buckets have no updatable size attributes; spec
    invariant: idempotent update."""
    result = driver.provision(_spec())
    update = driver.update(UpdateSpec(handle=result.handle, size="medium"))
    assert update.ok is True


# ---- deprovision ----------------------------------------------


def test_deprovision_keep_data(driver: S3Driver, s3_client) -> None:
    """delete_data=False: bucket stays, just unbind."""
    result = driver.provision(_spec())
    _, bucket_name = parse_handle(result.handle)
    deprov = driver.deprovision(
        DeprovisionSpec(handle=result.handle),
        delete_data=False,
    )
    assert deprov.ok is True
    # Bucket still exists
    response = s3_client.list_buckets()
    assert bucket_name in [b["Name"] for b in response["Buckets"]]


def test_deprovision_delete_data(driver: S3Driver, s3_client) -> None:
    result = driver.provision(_spec())
    _, bucket_name = parse_handle(result.handle)
    # Put an object so we exercise the empty-bucket path
    s3_client.put_object(Bucket=bucket_name, Key="test.txt", Body=b"hi")
    deprov = driver.deprovision(
        DeprovisionSpec(handle=result.handle),
        delete_data=True,
    )
    assert deprov.ok is True
    # Bucket gone
    response = s3_client.list_buckets()
    assert bucket_name not in [b["Name"] for b in response["Buckets"]]


def test_deprovision_already_gone_idempotent(driver: S3Driver) -> None:
    """Deprovisioning a bucket that's already deleted is an idempotent
    success — the empty step hits NoSuchBucket and treats it as already-gone
    so teardown converges instead of stranding the row (#1034)."""
    fake_handle = "object_store/never-existed-bucket-xyz"
    deprov = driver.deprovision(
        DeprovisionSpec(handle=fake_handle),
        delete_data=True,
    )
    assert deprov.ok is True
    assert not deprov.errors


def test_deprovision_delete_data_idempotent_on_second_run(
    driver: S3Driver,
    s3_client,
) -> None:
    """Re-running delete_data deprovision after the bucket is already gone
    must still report ok=True (re-trigger / partial-teardown convergence)."""
    result = driver.provision(_spec())
    s3_client.put_object(
        Bucket=parse_handle(result.handle)[1],
        Key="x",
        Body=b"y",
    )
    first = driver.deprovision(
        DeprovisionSpec(handle=result.handle),
        delete_data=True,
    )
    assert first.ok is True
    second = driver.deprovision(
        DeprovisionSpec(handle=result.handle),
        delete_data=True,
    )
    assert second.ok is True
    assert not second.errors


# ---- snapshot --------------------------------------------------


def test_snapshot_returns_marker(driver: S3Driver) -> None:
    result = driver.provision(_spec())
    snapshot = driver.snapshot(ServiceHandle(handle=result.handle))
    assert snapshot.snapshot_id.startswith("v-")


def test_snapshot_requires_versioning(s3_client) -> None:
    driver = S3Driver(
        config=S3Config(region="us-east-1", versioning_enabled=False),
        client=s3_client,
    )
    result = driver.provision(_spec())
    with pytest.raises(ManagedServiceError, match="versioning"):
        driver.snapshot(ServiceHandle(handle=result.handle))


# ---- schemas ---------------------------------------------------


def test_binding_schema_documents_env_vars(driver: S3Driver) -> None:
    schema = driver.binding_schema()
    assert "S3_BUCKET_NAME" in schema.env_vars
    assert "S3_BUCKET_ARN" in schema.env_vars
