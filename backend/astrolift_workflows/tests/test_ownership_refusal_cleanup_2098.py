"""Explicit owner outcomes cannot become success through resource-name substrings."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from _sdk.managed_service import DeprovisionResult

from astrolift_workflows.activities.managed_service_lifecycle import _deprovision_sync, _provision_sync
from astrolift_workflows.tests.test_aws_live_ownership_2098 import world  # noqa: F401 - persisted/SDK fixture
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("world", ["s3"], indirect=True)
def test_actual_s3_foreign_ownership_is_not_gone_when_physical_name_contains_not_found_marker(
    world,  # noqa: F811 - imported pytest fixture
):
    svc = _service(org_slug="nosuchbucket-owner-2098", plugin_slug="aws", variant="s3", backend_ref="")
    svc.kind = "object_store"
    svc.save(update_fields=["kind"])
    provisioned = _provision_sync(svc.pk)
    assert provisioned["ok"] and "nosuchbucket" in provisioned["handle"]
    svc.backend_ref = provisioned["handle"]
    svc.save(update_fields=["backend_ref"])
    bucket = provisioned["handle"].split("/", 1)[1]
    tags = [
        {"Key": "astrolift.io/managed-by", "Value": "platform"},
        {"Key": "astrolift.io/managed_service_id", "Value": "22222222-2222-4222-8222-222222222222"},
    ]
    world.api.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": tags})
    world.calls.clear()
    result = _deprovision_sync(svc.pk, True, True)
    assert not result["ok"] and result["errors"] == ["ownership_refused"]
    assert "nosuchbucket" in result["message"].lower()
    assert set(world.calls) <= {"HeadBucket", "GetBucketTagging"}
    assert world.api.get_bucket_tagging(Bucket=bucket)["TagSet"] == tags
    svc.refresh_from_db()
    assert svc.deleted_at is None and svc.provider_cleanup_receipt is None


@pytest.mark.parametrize("world", ["s3"], indirect=True)
@pytest.mark.parametrize("code", ["ownership_refused", "ownership_unknown"])
def test_production_dispatch_preserves_explicit_failed_result_despite_not_found_diagnostic(
    world,  # noqa: F811 - imported pytest fixture
    code,
):
    from aws.managed.object_store_s3 import S3Driver

    # Exercise the actual production dispatch over a persisted source row and
    # SDK outcome boundary, without issuing any provider mutation.
    refusal = DeprovisionResult(
        False,
        world.svc.backend_ref,
        "NoSuchBucket resource diagnostic is not ownership proof",
        [code],
        retryable=False,
    )
    with patch.object(S3Driver, "deprovision", return_value=refusal) as dispatch:
        result = _deprovision_sync(world.svc.pk, True, True)
    assert not result["ok"] and result["errors"] == [code]
    assert dispatch.call_args.args[0].managed_service_id == str(world.svc.guid)
    assert world.calls == []
    world.svc.refresh_from_db()
    assert world.svc.deleted_at is None and world.svc.provider_cleanup_receipt is None


@pytest.mark.parametrize("world", ["s3"], indirect=True)
def test_actual_missing_s3_resource_still_converges_without_a_mutating_call(
    world,  # noqa: F811 - imported pytest fixture
):
    world.api.delete_bucket(Bucket=world.name)
    world.calls.clear()
    result = _deprovision_sync(world.svc.pk, True, True)
    assert result["ok"] and "already gone" in result["message"]
    assert world.calls == ["HeadBucket"]
