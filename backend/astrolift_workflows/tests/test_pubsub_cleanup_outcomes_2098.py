"""Actual SDK outcomes cannot make denied Pub/Sub cleanup look successful."""

from __future__ import annotations

import grpc
import pytest

from astrolift_workflows.activities.managed_service_lifecycle import _deprovision_sync, _provision_sync
from astrolift_workflows.tests.test_pubsub_immutable_names_2032 import _new
from astrolift_workflows.tests.test_pubsub_immutable_names_2032 import cloud as _sdk_cloud

pytestmark = pytest.mark.django_db


@pytest.fixture
def cloud():
    yield from _sdk_cloud.__wrapped__()


def _owned(cloud, slug):
    svc = _new(slug)
    result = _provision_sync(svc.pk)
    assert result["ok"]
    svc.backend_ref = result["handle"]
    svc.save(update_fields=["backend_ref"])
    cloud.api.calls.clear()
    return svc


def _deletes(cloud):
    return [name for name, _ in cloud.api.calls if name.startswith("Delete")]


@pytest.mark.parametrize(
    "stage", ["GetTopic", "ListTopicSubscriptions", "GetSubscription", "DeleteSubscription", "DeleteTopic"]
)
def test_real_sdk_denial_at_each_cleanup_stage_never_reports_cleanup_success(cloud, stage):
    svc = _owned(cloud, "denied-cleanup-pubsub")
    cloud.api.failures[stage] = (grpc.StatusCode.PERMISSION_DENIED, "permission denied: resource not found")
    result = _deprovision_sync(svc.pk, True, True)
    assert not result["ok"] and result["errors"] == ["ownership_unknown"]
    assert "already deprovisioned" not in result["message"]
    assert len(cloud.api.topics) == 1
    if stage in {"GetTopic", "ListTopicSubscriptions", "GetSubscription"}:
        assert _deletes(cloud) == [] and len(cloud.api.subscriptions) == 2
    elif stage == "DeleteSubscription":
        assert _deletes(cloud) == ["DeleteSubscription"] and len(cloud.api.subscriptions) == 2
    else:
        assert _deletes(cloud).count("DeleteSubscription") == 2
        assert _deletes(cloud).count("DeleteTopic") == 1 and cloud.api.subscriptions == {}
    svc.refresh_from_db()
    assert svc.backend_ref and svc.deleted_at is None and svc.provider_cleanup_receipt is None


def test_real_sdk_second_inventory_page_denial_cannot_delete_from_partial_inventory(cloud):
    svc = _owned(cloud, "partial-inventory-pubsub")
    cloud.api.partial_inventory_failure = True
    result = _deprovision_sync(svc.pk, True, True)
    assert not result["ok"] and result["errors"] == ["ownership_unknown"]
    assert cloud.api.inventory_reads == 2
    assert _deletes(cloud) == []
    assert len(cloud.api.topics) == 1 and len(cloud.api.subscriptions) == 2


@pytest.mark.parametrize("foreign", ["topic", "child"])
def test_current_foreign_owner_refuses_cleanup_even_when_force_flags_are_true(cloud, foreign):
    svc = _owned(cloud, "foreign-owner-cleanup-pubsub")
    if foreign == "topic":
        name = next(iter(cloud.api.topics))
        cloud.publisher.update_topic(
            request={
                "topic": {"name": name, "labels": {"astrolift_io_managed_service_id": "foreign"}},
                "update_mask": {"paths": ["labels"]},
            },
            timeout=5,
        )
    else:
        name = next(iter(cloud.api.subscriptions))
        cloud.subscriber.update_subscription(
            request={
                "subscription": {"name": name, "labels": {"astrolift_io_managed_service_id": "foreign"}},
                "update_mask": {"paths": ["labels"]},
            },
            timeout=5,
        )
    cloud.api.calls.clear()
    result = _deprovision_sync(svc.pk, True, True)
    assert not result["ok"] and result["errors"] == ["ownership_refused"]
    assert not result["retryable"] and _deletes(cloud) == []
    assert len(cloud.api.topics) == 1 and len(cloud.api.subscriptions) == 2


@pytest.mark.parametrize("refusal", ["handle", "config", "outside-declaration"])
def test_caller_controlled_not_found_text_in_refusal_cannot_record_cleanup(cloud, refusal):
    svc = _owned(cloud, "invalid-cleanup-pubsub")
    if refusal == "handle":
        svc.backend_ref = "queue/NoSuchBucket"
    elif refusal == "config":
        svc.config = {"labels": {"astrolift-notfound-owner": "caller"}}
    else:
        name = cloud.subscriber.subscription_path("controlled-project", "notfound-child")
        topic = next(iter(cloud.api.topics))
        cloud.subscriber.create_subscription(request={"name": name, "topic": topic}, timeout=5)
    svc.save(update_fields=["backend_ref", "config"])
    cloud.api.calls.clear()
    result = _deprovision_sync(svc.pk, True, False)
    assert not result["ok"] and "ownership_unknown" in result["errors"]
    assert "already deprovisioned" not in result["message"] and _deletes(cloud) == []
    assert len(cloud.api.topics) == 1


@pytest.mark.parametrize("missing", ["parent", "child-get", "child-delete", "none"])
def test_real_typed_missing_converges_and_owned_cleanup_really_deletes_target(cloud, missing):
    svc = _owned(cloud, "real-missing-pubsub")
    if missing == "parent":
        cloud.publisher.delete_topic(request={"topic": next(iter(cloud.api.topics))}, timeout=5)
        cloud.api.calls.clear()
    elif missing == "child-get":
        cloud.api.missing_on_get = True
    elif missing == "child-delete":
        cloud.api.missing_on_delete = True
    result = _deprovision_sync(svc.pk, True, True)
    assert result["ok"] and cloud.api.topics == {}
    if missing == "parent":
        # Typed parent absence proves only the parent. Its former children are
        # not inferred absent and no child delete is attempted.
        assert _deletes(cloud) == [] and len(cloud.api.subscriptions) == 2
    else:
        assert cloud.api.subscriptions == {}
        assert _deletes(cloud).count("DeleteTopic") == 1
        if missing == "child-get":
            assert _deletes(cloud).count("DeleteSubscription") == 0
    cloud.api.calls.clear()
    assert _deprovision_sync(svc.pk, True, True)["ok"]
    assert _deletes(cloud) == []
