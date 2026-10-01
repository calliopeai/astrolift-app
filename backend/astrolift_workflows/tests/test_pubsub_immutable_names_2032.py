"""Persisted lifecycle identity reaches the real Pub/Sub SDK over local gRPC.

The server records bounded protobuf RPCs; it does not certify GCP persistence,
IAM or delivery semantics. No cloud endpoint or credentials are used.
"""

from __future__ import annotations

import dataclasses
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import grpc
import pytest
from gcp.managed.topic_pubsub import PubSubTopicConfig, PubSubTopicDriver
from google.auth.credentials import AnonymousCredentials
from google.cloud import pubsub_v1
from google.protobuf import empty_pb2
from google.pubsub_v1 import types as messages
from google.pubsub_v1.services.publisher.transports.grpc import PublisherGrpcTransport
from google.pubsub_v1.services.subscriber.transports.grpc import SubscriberGrpcTransport

from astrolift_workflows.activities.managed_service_lifecycle import (
    _provision_sync,
    _update_sync,
)
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service

pytestmark = pytest.mark.django_db


class RecordingPubSub:
    def __init__(self):
        self.topics = {}
        self.subscriptions = {}
        self.calls = []
        self.payloads = {}
        self.lookup_failure = None
        self.failures = {}
        self.partial_inventory_failure = False
        self.missing_on_get = False
        self.missing_on_delete = False
        self.inventory_reads = 0

    def dispatch(self, method, request, context):
        self.calls.append((method, request))
        if method in self.failures:
            context.abort(*self.failures[method])
        if method == "GetTopic":
            if self.lookup_failure:
                context.abort(*self.lookup_failure)
            if request.topic not in self.topics:
                context.abort(grpc.StatusCode.NOT_FOUND, "topic missing")
            return self.topics[request.topic]
        if method == "CreateTopic":
            if request.name in self.topics:
                context.abort(grpc.StatusCode.ALREADY_EXISTS, "topic exists")
            self.topics[request.name] = messages.Topic(request)
            return self.topics[request.name]
        if method == "UpdateTopic":
            current = self.topics[request.topic.name]
            for field in request.update_mask.paths:
                setattr(current, field, getattr(request.topic, field))
            return current
        if method == "ListTopicSubscriptions":
            self.inventory_reads += 1
            if self.partial_inventory_failure:
                if request.page_token:
                    context.abort(
                        grpc.StatusCode.PERMISSION_DENIED, "inventory unavailable: resource not found"
                    )
                return messages.ListTopicSubscriptionsResponse(
                    subscriptions=list(self.subscriptions)[:1], next_page_token="actual-next-page"
                )
            return messages.ListTopicSubscriptionsResponse(
                subscriptions=[name for name, row in self.subscriptions.items() if row.topic == request.topic]
            )
        if method == "GetSubscription":
            if self.missing_on_get:
                self.subscriptions.pop(request.subscription, None)
            if request.subscription not in self.subscriptions:
                context.abort(grpc.StatusCode.NOT_FOUND, "subscription missing")
            return self.subscriptions[request.subscription]
        if method == "CreateSubscription":
            if request.name in self.subscriptions:
                context.abort(grpc.StatusCode.ALREADY_EXISTS, "subscription exists")
            self.subscriptions[request.name] = messages.Subscription(request)
            return self.subscriptions[request.name]
        if method == "UpdateSubscription":
            current = self.subscriptions[request.subscription.name]
            for field in request.update_mask.paths:
                setattr(current, field, getattr(request.subscription, field))
            return current
        if method == "DeleteSubscription":
            if self.missing_on_delete:
                self.subscriptions.pop(request.subscription, None)
            if request.subscription not in self.subscriptions:
                context.abort(grpc.StatusCode.NOT_FOUND, "subscription already gone")
            del self.subscriptions[request.subscription]
            return empty_pb2.Empty()
        if method == "DeleteTopic":
            if request.topic not in self.topics:
                context.abort(grpc.StatusCode.NOT_FOUND, "topic already gone")
            del self.topics[request.topic]
            return empty_pb2.Empty()
        if method == "Publish":
            assert request.topic in self.topics
            self.payloads.setdefault(request.topic, []).extend(
                bytes(message.data) for message in request.messages
            )
            return messages.PublishResponse(
                message_ids=[str(index) for index in range(len(request.messages))]
            )
        raise AssertionError(method)


@pytest.fixture
def cloud():
    api = RecordingPubSub()
    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    publisher_methods = {
        "GetTopic": (messages.GetTopicRequest, messages.Topic),
        "CreateTopic": (messages.Topic, messages.Topic),
        "UpdateTopic": (messages.UpdateTopicRequest, messages.Topic),
        "ListTopicSubscriptions": (
            messages.ListTopicSubscriptionsRequest,
            messages.ListTopicSubscriptionsResponse,
        ),
        "Publish": (messages.PublishRequest, messages.PublishResponse),
        "DeleteTopic": (messages.DeleteTopicRequest, empty_pb2.Empty),
    }
    subscriber_methods = {
        "GetSubscription": (messages.GetSubscriptionRequest, messages.Subscription),
        "CreateSubscription": (messages.Subscription, messages.Subscription),
        "UpdateSubscription": (messages.UpdateSubscriptionRequest, messages.Subscription),
        "DeleteSubscription": (messages.DeleteSubscriptionRequest, empty_pb2.Empty),
    }
    for service, methods in (("Publisher", publisher_methods), ("Subscriber", subscriber_methods)):
        handlers = {}
        for method, (request_type, response_type) in methods.items():
            handlers[method] = grpc.unary_unary_rpc_method_handler(
                lambda request, context, name=method: api.dispatch(name, request, context),
                request_deserializer=request_type.deserialize,
                response_serializer=(
                    response_type.serialize
                    if hasattr(response_type, "serialize")
                    else response_type.SerializeToString
                ),
            )
        server.add_generic_rpc_handlers(
            (grpc.method_handlers_generic_handler(f"google.pubsub.v1.{service}", handlers),)
        )
    port = server.add_insecure_port("127.0.0.1:0")
    assert port
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    grpc.channel_ready_future(channel).result(timeout=5)
    publisher = pubsub_v1.PublisherClient(
        transport=PublisherGrpcTransport(channel=channel, credentials=AnonymousCredentials())
    )
    subscriber = pubsub_v1.SubscriberClient(
        transport=SubscriberGrpcTransport(channel=channel, credentials=AnonymousCredentials())
    )
    state = SimpleNamespace(
        api=api,
        publisher=publisher,
        subscriber=subscriber,
        cfg=PubSubTopicConfig(
            project_id="controlled-project", publisher_client=publisher, subscriber_client=subscriber
        ),
    )
    try:
        with (
            patch("astrolift_drivers.registry.plugins.get", return_value=PubSubTopicDriver),
            patch("core.cluster_observability.managed_config_for", side_effect=lambda *_, **__: state.cfg),
        ):
            yield state
    finally:
        publisher.stop()
        channel.close()
        server.stop(0).wait(timeout=5)


def _new(org_slug, app_slug="api"):
    svc = _service(org_slug=org_slug, variant="pubsub_topic", backend_ref="")
    svc.kind = "topic"
    svc.config = {"subscriptions": [{"name": "s" * 199 + ending} for ending in ("x", "y")]}
    svc.save(update_fields=["kind", "config"])
    svc.registered_app.slug = app_slug
    svc.registered_app.save(update_fields=["slug"])
    assert UUID(str(svc.guid)).int
    return svc


def _writes(cloud):
    return [name for name, _ in cloud.api.calls if name.startswith(("Create", "Update", "Delete"))]


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_two_org_collision_has_distinct_full_uuid_topics_and_children_through_real_sdk(cloud, collision):
    if collision == "joined":
        rows = [_new("alpha-beta", "gamma"), _new("alpha", "beta-gamma")]
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, topic_prefix="p" * 300)
        rows = [_new("truncated-alpha"), _new("truncated-beta")]
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
    assert len(cloud.api.topics) == 2 and len(cloud.api.subscriptions) == 4
    for svc, result in zip(rows, results, strict=True):
        topic_id = result["handle"].partition("/")[2]
        assert len(topic_id) <= 54 and topic_id.endswith(str(svc.guid).replace("-", ""))
        path = cloud.publisher.topic_path("controlled-project", topic_id)
        assert cloud.publisher.get_topic(request={"topic": path}, timeout=5).labels[
            "astrolift_io_managed_service_id"
        ] == str(svc.guid)
        children = [row for row in cloud.api.subscriptions.values() if row.topic == path]
        assert len(children) == 2
        assert {row.name.rsplit("/", 1)[1][-200:] for row in children} == {
            "s" * 199 + ending for ending in ("x", "y")
        }
        assert all(topic_id in row.name and len(row.name.rsplit("/", 1)[1]) <= 255 for row in children)
        cloud.publisher.publish(path, b"preserved controlled payload").result(timeout=5)
        creates_before = sum(name.startswith("Create") for name, _ in cloud.api.calls)
        assert _provision_sync(svc.pk)["handle"] == result["handle"]
        assert sum(name.startswith("Create") for name, _ in cloud.api.calls) == creates_before
        assert cloud.api.payloads[path] == [b"preserved controlled payload"]
    assert "CreateTopic" in _writes(cloud) and "CreateSubscription" in _writes(cloud)


def test_recorded_long_topic_and_child_keep_exact_legacy_identity_after_reprovision(cloud):
    svc = _new("legacy-pubsub-2032")
    topic_id = "p" * 250
    path = cloud.publisher.topic_path("controlled-project", topic_id)
    child_path = cloud.subscriber.subscription_path("controlled-project", topic_id + "-work")
    cloud.publisher.create_topic(
        request={"name": path, "labels": {"astrolift_io_managed_service_id": str(svc.guid)}}, timeout=5
    )
    cloud.subscriber.create_subscription(
        request={
            "name": child_path,
            "topic": path,
            "labels": {"astrolift_io_managed_service_id": str(svc.guid)},
        },
        timeout=5,
    )
    cloud.publisher.publish(path, b"legacy controlled payload").result(timeout=5)
    svc.backend_ref = f"topic/{topic_id}"
    svc.config = {"subscriptions": [{"name": "worker"}]}
    svc.name = "renamed-service"
    svc.save(update_fields=["backend_ref", "config", "name"])
    svc.registered_app.slug = "renamed-app"
    svc.registered_app.save(update_fields=["slug"])
    svc.app_environment.name = "renamed-environment"
    svc.app_environment.save(update_fields=["name"])
    cloud.cfg = dataclasses.replace(cloud.cfg, topic_prefix="changed-prefix")
    cloud.api.calls.clear()
    assert _provision_sync(svc.pk)["handle"] == svc.backend_ref
    assert list(cloud.api.topics) == [path] and list(cloud.api.subscriptions) == [child_path]
    assert cloud.api.payloads[path] == [b"legacy controlled payload"]
    assert not any(name.startswith("Create") for name, _ in cloud.api.calls)
    svc.config = {"subscriptions": [{"name": "worker-a"}, {"name": "worker-b"}]}
    svc.save(update_fields=["config"])
    cloud.api.calls.clear()
    result = _provision_sync(svc.pk)
    assert not result["ok"] and "physical name" in result["message"]
    assert _writes(cloud) == []
    cloud.api.calls.clear()
    update = _update_sync(svc.pk)
    assert not update["ok"] and "physical name" in update["message"]
    assert _writes(cloud) == []


def test_foreign_recorded_target_and_caller_claims_cannot_mutate_live_topic_or_children(cloud):
    owner = _new("pubsub-owner-2032")
    owned = _provision_sync(owner.pk)
    assert owned["ok"]
    contender = _new("pubsub-foreign-2032")
    contender.backend_ref = owned["handle"]
    contender.config = {
        "managed_service_id": str(owner.guid),
        "recorded_handle_exclusive": True,
        "subscriptions": [{"name": "workers"}],
    }
    contender.save(update_fields=["backend_ref", "config"])
    cloud.api.calls.clear()
    result = _provision_sync(contender.pk)
    assert not result["ok"] and "another managed service" in result["message"]
    assert _writes(cloud) == []
    assert len(cloud.api.topics) == 1 and len(cloud.api.subscriptions) == 2
    assert next(iter(cloud.api.topics.values())).labels["astrolift_io_managed_service_id"] == str(owner.guid)


def test_actual_sdk_permission_denial_with_not_found_text_never_allows_creation(cloud):
    svc = _new("denied-pubsub-2032")
    cloud.api.lookup_failure = (grpc.StatusCode.PERMISSION_DENIED, "permission denied: resource not found")
    result = _provision_sync(svc.pk)
    assert not result["ok"]
    assert _writes(cloud) == [] and cloud.api.topics == {} and cloud.api.subscriptions == {}
    assert [name for name, _ in cloud.api.calls] == ["GetTopic"]


def test_actual_foreign_subscription_refuses_parent_reconciliation_before_effects(cloud):
    svc = _new("child-owner-pubsub-2032")
    owned = _provision_sync(svc.pk)
    assert owned["ok"]
    svc.backend_ref = owned["handle"]
    svc.save(update_fields=["backend_ref"])
    child = next(iter(cloud.api.subscriptions.values()))
    cloud.subscriber.update_subscription(
        request={
            "subscription": {
                "name": child.name,
                "labels": {"astrolift_io_managed_service_id": "foreign-owner"},
            },
            "update_mask": {"paths": ["labels"]},
        },
        timeout=5,
    )
    cloud.api.calls.clear()
    assert not _provision_sync(svc.pk)["ok"]
    assert _writes(cloud) == []
    cloud.api.calls.clear()
    assert not _update_sync(svc.pk)["ok"]
    assert _writes(cloud) == []
    assert child.labels["astrolift_io_managed_service_id"] == "foreign-owner"
