"""Real queue lifecycle rows reach the actual Google SDK through local gRPC."""

from __future__ import annotations

import dataclasses
from unittest.mock import patch

import grpc
import pytest
from _sdk.managed_service import ServiceHandle
from gcp.managed.queue_pubsub import PubSubConfig, PubSubDriver, PubSubQueueError
from google.pubsub_v1 import types as messages

from astrolift_workflows.activities.managed_service_lifecycle import (
    _deprovision_sync,
    _managed_binding_for,
    _provision_sync,
    _update_sync,
)
from astrolift_workflows.tests.test_pubsub_immutable_names_2032 import _new
from astrolift_workflows.tests.test_pubsub_immutable_names_2032 import cloud as _sdk_cloud

pytestmark = pytest.mark.django_db


@pytest.fixture
def cloud():
    for state in _sdk_cloud.__wrapped__():
        state.cfg = PubSubConfig(
            project_id="controlled-project",
            publisher_client=state.publisher,
            subscriber_client=state.subscriber,
        )
        state.deadlines = []
        original = state.api.dispatch

        def record(method, request, context, state=state, original=original):
            state.deadlines.append((method, context.time_remaining()))
            return original(method, request, context)

        state.api.dispatch = record
        with patch("astrolift_drivers.registry.plugins.get", return_value=PubSubDriver):
            yield state


def _queue(org_slug, app_slug="api"):
    svc = _new(org_slug, app_slug)
    svc.kind = "queue"
    svc.variant = "pubsub"
    svc.config = {}
    svc.save(update_fields=["kind", "variant", "config"])
    return svc


def _owned(cloud, slug):
    svc = _queue(slug)
    result = _provision_sync(svc.pk)
    assert result["ok"], result
    svc.backend_ref = result["handle"]
    svc.save(update_fields=["backend_ref"])
    cloud.api.calls.clear()
    return svc


def _writes(cloud):
    return [name for name, _ in cloud.api.calls if name.startswith(("Create", "Update", "Delete", "Seek"))]


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_two_orgs_reach_real_sdk_as_distinct_full_uuid_queue_targets_and_retry_without_data_loss(
    cloud, collision
):
    if collision == "joined":
        rows = [_queue("alpha-beta", "gamma"), _queue("alpha", "beta-gamma")]
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, topic_prefix="p" * 300)
        rows = [_queue("long-alpha"), _queue("long-beta")]
    old = [
        "-".join(
            (
                cloud.cfg.topic_prefix,
                svc.registered_app.organization.slug,
                svc.registered_app.slug,
                svc.app_environment.name,
                svc.name,
            )
        )[:255]
        for svc in rows
    ]
    assert old[0] == old[1]
    results = [_provision_sync(svc.pk) for svc in rows]
    assert all(result["ok"] for result in results)
    assert results[0]["handle"] != results[1]["handle"]
    assert len(cloud.api.topics) == len(cloud.api.subscriptions) == 2
    for svc, result in zip(rows, results, strict=True):
        topic_id = result["handle"].partition("/")[2]
        assert topic_id.endswith(str(svc.guid).replace("-", "")) and len(topic_id) <= 251
        path = cloud.publisher.topic_path("controlled-project", topic_id)
        child = cloud.subscriber.subscription_path("controlled-project", topic_id + "-sub")
        assert len(child.rsplit("/", 1)[1]) <= 255
        assert cloud.publisher.get_topic(request={"topic": path}, timeout=5).labels[
            "astrolift_io_managed_service_id"
        ] == str(svc.guid)
        assert cloud.subscriber.get_subscription(request={"subscription": child}, timeout=5).labels[
            "astrolift_io_managed_service_id"
        ] == str(svc.guid)
        cloud.publisher.publish(path, b"controlled retained queue payload").result(timeout=5)
        cloud.api.calls.clear()
        assert _provision_sync(svc.pk)["handle"] == result["handle"]
        assert _writes(cloud) == [] and cloud.api.payloads[path] == [b"controlled retained queue payload"]
    # Actual outgoing SDK deadlines are finite even on the recording server.
    assert all(0 < value < 5.5 for method, value in cloud.deadlines if method != "Publish")


def test_recorded_legacy_targets_and_unlabelled_default_child_keep_their_identity_and_data(cloud):
    svc = _queue("legacy-queue-owner")
    name = "p" * 250
    path = cloud.publisher.topic_path("controlled-project", name)
    child = cloud.subscriber.subscription_path("controlled-project", name + "-sub")
    cloud.publisher.create_topic(
        request={"name": path, "labels": {"astrolift_io_managed_service_id": str(svc.guid)}}, timeout=5
    )
    cloud.subscriber.create_subscription(request={"name": child, "topic": path}, timeout=5)
    cloud.publisher.publish(path, b"legacy queue payload").result(timeout=5)
    svc.backend_ref = "queue/" + name
    svc.name = "renamed-service"
    svc.save(update_fields=["backend_ref", "name"])
    svc.registered_app.slug = "renamed-app"
    svc.registered_app.save(update_fields=["slug"])
    cloud.cfg = dataclasses.replace(cloud.cfg, topic_prefix="changed-prefix")
    cloud.api.calls.clear()
    assert _provision_sync(svc.pk)["handle"] == svc.backend_ref
    assert _writes(cloud) == [] and cloud.api.payloads[path] == [b"legacy queue payload"]
    assert _managed_binding_for(svc).env_vars["PUBSUB_SUBSCRIPTION"].literal == child


@pytest.mark.parametrize("foreign", ["topic", "child", "child-topic"])
def test_actual_foreign_live_target_refuses_every_operational_path_before_effects(cloud, foreign):
    svc = _owned(cloud, "foreign-queue-target")
    if foreign == "topic":
        cloud.api.topics[next(iter(cloud.api.topics))].labels = {"astrolift_io_managed_service_id": "foreign"}
    elif foreign == "child":
        cloud.api.subscriptions[next(iter(cloud.api.subscriptions))].labels = {
            "astrolift_io_managed_service_id": "foreign"
        }
    else:
        cloud.api.subscriptions[next(iter(cloud.api.subscriptions))].topic = "projects/foreign/topics/foreign"
    assert not _provision_sync(svc.pk)["ok"]
    assert not _update_sync(svc.pk)["ok"]
    result = _deprovision_sync(svc.pk, True, True)
    assert not result["ok"] and result["errors"] == ["ownership_refused"]
    with pytest.raises(PubSubQueueError):
        _managed_binding_for(svc)
    assert (
        PubSubDriver(config=cloud.cfg)
        .status(ServiceHandle(svc.backend_ref, managed_service_id=str(svc.guid)))
        .state
        == "error"
    )
    assert _writes(cloud) == []
    assert len(cloud.api.topics) == len(cloud.api.subscriptions) == 1


@pytest.mark.parametrize(
    "stage", ["GetTopic", "GetSubscription", "ListTopicSubscriptions", "DeleteSubscription", "DeleteTopic"]
)
def test_sdk_denied_not_found_diagnostic_never_claims_queue_cleanup_or_force_success(cloud, stage):
    svc = _owned(cloud, "denied-queue-target")
    cloud.api.failures[stage] = (grpc.StatusCode.PERMISSION_DENIED, "permission denied: resource not found")
    result = _deprovision_sync(svc.pk, True, True)
    assert not result["ok"] and result["errors"] == ["ownership_unknown"]
    assert len(cloud.api.topics) == 1
    if stage.startswith(("Get", "List")):
        assert _writes(cloud) == [] and len(cloud.api.subscriptions) == 1
    elif stage == "DeleteSubscription":
        assert _writes(cloud) == ["DeleteSubscription"] and len(cloud.api.subscriptions) == 1
    else:
        assert _writes(cloud) == ["DeleteSubscription", "DeleteTopic"] and cloud.api.subscriptions == {}


def test_absent_parent_with_remaining_child_has_unknown_authority_and_never_deletes_or_adopts(cloud):
    svc = _owned(cloud, "missing-parent-queue")
    path = next(iter(cloud.api.topics))
    cloud.publisher.delete_topic(request={"topic": path}, timeout=5)
    next(iter(cloud.api.subscriptions.values())).topic = "_deleted-topic_"
    cloud.api.calls.clear()
    assert not _provision_sync(svc.pk)["ok"]
    result = _deprovision_sync(svc.pk, True, True)
    assert not result["ok"] and result["errors"] == ["ownership_unknown"]
    assert _writes(cloud) == [] and len(cloud.api.subscriptions) == 1


def test_retained_refusal_has_no_seek_no_delete_and_no_lost_controlled_payload(cloud):
    svc = _owned(cloud, "retained-queue")
    path = next(iter(cloud.api.topics))
    cloud.publisher.publish(path, b"retained queue payload").result(timeout=5)
    cloud.api.calls.clear()
    driver = PubSubDriver(config=cloud.cfg)
    from _sdk.managed_service import DeprovisionSpec

    for force in (False, True):
        result = driver.deprovision(
            DeprovisionSpec(svc.backend_ref, managed_service_id=str(svc.guid)), force_destroy=force
        )
        assert not result.ok and result.errors == ["retained_messages_require_delete_data"]
    with pytest.raises(Exception, match="snapshot|retained"):
        _deprovision_sync(svc.pk, False, True)
    assert _writes(cloud) == [] and cloud.api.payloads[path] == [b"retained queue payload"]
    assert len(cloud.api.topics) == len(cloud.api.subscriptions) == 1


def test_owned_positive_cleanup_and_concrete_missing_targets_converge(cloud):
    svc = _owned(cloud, "delete-owned-queue")
    assert _deprovision_sync(svc.pk, True, False)["ok"]
    assert cloud.api.topics == {} and cloud.api.subscriptions == {}
    cloud.api.calls.clear()
    assert _deprovision_sync(svc.pk, True, True)["ok"]
    assert _writes(cloud) == []


@pytest.mark.parametrize("inventory", ["overflow", "pages", "repeat", "partial-denial", "foreign-project"])
def test_complete_inventory_budget_or_scope_failure_refuses_before_destructive_effects(cloud, inventory):
    svc = _owned(cloud, "inventory-queue")
    original = cloud.api.dispatch

    def reply(method, request, context):
        if method != "ListTopicSubscriptions":
            return original(method, request, context)
        cloud.api.calls.append((method, request))
        if inventory == "overflow":
            return messages.ListTopicSubscriptionsResponse(
                subscriptions=[
                    f"projects/controlled-project/subscriptions/s-{index}" for index in range(1001)
                ]
            )
        if inventory == "foreign-project":
            return messages.ListTopicSubscriptionsResponse(
                subscriptions=["projects/foreign/subscriptions/foreign"]
            )
        if inventory == "partial-denial" and request.page_token:
            context.abort(grpc.StatusCode.PERMISSION_DENIED, "inventory unavailable: resource not found")
        token = "repeat" if inventory == "repeat" else str(int(request.page_token or "0") + 1)
        return messages.ListTopicSubscriptionsResponse(next_page_token=token)

    cloud.api.dispatch = reply
    result = _deprovision_sync(svc.pk, True, True)
    assert not result["ok"] and result["errors"] == [
        "ownership_refused" if inventory == "foreign-project" else "ownership_unknown"
    ]
    assert _writes(cloud) == [] and len(cloud.api.topics) == len(cloud.api.subscriptions) == 1
    assert sum(method == "ListTopicSubscriptions" for method, _ in cloud.api.calls) <= 10


@pytest.mark.parametrize("extra_owner", ["owned", "foreign", "unlabelled"])
def test_outside_default_children_need_force_and_their_own_owner_proof(cloud, extra_owner):
    svc = _owned(cloud, "outside-queue-child")
    topic = next(iter(cloud.api.topics))
    extra = cloud.subscriber.subscription_path("controlled-project", "outside-child")
    labels = (
        {}
        if extra_owner == "unlabelled"
        else {"astrolift_io_managed_service_id": str(svc.guid) if extra_owner == "owned" else "foreign"}
    )
    cloud.subscriber.create_subscription(request={"name": extra, "topic": topic, "labels": labels}, timeout=5)
    cloud.api.calls.clear()
    assert not _deprovision_sync(svc.pk, True, False)["ok"]
    assert _writes(cloud) == []
    result = _deprovision_sync(svc.pk, True, True)
    if extra_owner == "owned":
        assert result["ok"] and cloud.api.topics == {} and cloud.api.subscriptions == {}
    else:
        assert not result["ok"] and result["errors"] == ["ownership_refused"]
        assert _writes(cloud) == [] and len(cloud.api.subscriptions) == 2


def test_central_exclusive_legacy_record_does_not_become_forged_config_authority(cloud):
    svc = _queue("legacy-exclusive-queue")
    topic = cloud.publisher.topic_path("controlled-project", "legacy-queue")
    cloud.publisher.create_topic(request={"name": topic}, timeout=5)
    child = cloud.subscriber.subscription_path("controlled-project", "legacy-queue-sub")
    cloud.subscriber.create_subscription(request={"name": child, "topic": topic}, timeout=5)
    svc.backend_ref = "queue/legacy-queue"
    svc.save(update_fields=["backend_ref"])
    cloud.api.calls.clear()
    assert _provision_sync(svc.pk)["ok"] and _writes(cloud) == []
    contender = _queue("legacy-contender-queue")
    contender.backend_ref = svc.backend_ref
    contender.config = {"recorded_handle_exclusive": True, "managed_service_id": str(svc.guid)}
    contender.save(update_fields=["backend_ref", "config"])
    cloud.api.calls.clear()
    assert not _provision_sync(contender.pk)["ok"]
    assert not _provision_sync(svc.pk)["ok"]
    assert _writes(cloud) == []


@pytest.mark.parametrize("target", ["topic", "child"])
def test_sdk_response_from_another_physical_target_cannot_reuse_owner_labels_as_authority(cloud, target):
    svc = _owned(cloud, "replaced-response-queue")
    if target == "topic":
        cloud.api.topics[next(iter(cloud.api.topics))].name = "projects/foreign/topics/foreign"
    else:
        cloud.api.subscriptions[
            next(iter(cloud.api.subscriptions))
        ].name = "projects/foreign/subscriptions/foreign"
    result = _deprovision_sync(svc.pk, True, True)
    assert not result["ok"] and result["errors"] == ["ownership_unknown"]
    assert not _provision_sync(svc.pk)["ok"]
    assert _writes(cloud) == []
    with pytest.raises(PubSubQueueError):
        _managed_binding_for(svc)


@pytest.mark.parametrize("force", [False, True])
def test_real_failed_precondition_is_not_a_force_delete_bypass(cloud, force):
    svc = _owned(cloud, "precondition-queue")
    cloud.api.failures["DeleteSubscription"] = (
        grpc.StatusCode.FAILED_PRECONDITION,
        "delete did not complete",
    )
    result = _deprovision_sync(svc.pk, True, force)
    assert not result["ok"] and result["errors"] == ["ownership_unknown"]
    assert _writes(cloud) == ["DeleteSubscription"]
    assert len(cloud.api.topics) == len(cloud.api.subscriptions) == 1
