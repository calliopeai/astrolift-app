from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from google.cloud import pubsub_v1
from google.protobuf import field_mask_pb2

from _sdk import UnsupportedOperationError
from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from gcp.managed.topic_pubsub import (
    PubSubTopicConfig,
    PubSubTopicDriver,
    PubSubTopicError,
    _parse_handle,
    _resource_id,
)


class NotFound(Exception):
    pass


class AlreadyExists(Exception):
    pass


@dataclass
class CloudState:
    topics: dict[str, dict[str, Any]] = field(default_factory=dict)
    subscriptions: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class FakePublisher:
    state: CloudState
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def topic_path(project: str, topic: str) -> str:
        return f"projects/{project}/topics/{topic}"

    def create_topic(self, *, request: dict[str, Any]) -> dict[str, Any]:
        self.create_calls.append(request)
        name = request["name"]
        if name in self.state.topics:
            raise AlreadyExists(name)
        self.state.topics[name] = dict(request)
        return self.state.topics[name]

    def get_topic(self, *, request: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.state.topics[request["topic"]]
        except KeyError as exc:
            raise NotFound(request["topic"]) from exc

    def update_topic(self, *, request: dict[str, Any]) -> dict[str, Any]:
        self.update_calls.append(request)
        topic = request["topic"]
        current = self.state.topics[topic["name"]]
        for path in request["update_mask"]["paths"]:
            current[path] = topic.get(path)
        return current

    def list_topic_subscriptions(self, *, request: dict[str, Any]) -> list[str]:
        return [
            name for name, subscription in self.state.subscriptions.items() if subscription["topic"] == request["topic"]
        ]

    def delete_topic(self, *, request: dict[str, Any]) -> None:
        self.delete_calls.append(request)
        if request["topic"] not in self.state.topics:
            raise NotFound(request["topic"])
        del self.state.topics[request["topic"]]


@dataclass
class FakeSubscriber:
    state: CloudState
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def subscription_path(project: str, subscription: str) -> str:
        return f"projects/{project}/subscriptions/{subscription}"

    def create_subscription(self, *, request: dict[str, Any]) -> dict[str, Any]:
        self.create_calls.append(request)
        if request["name"] in self.state.subscriptions:
            raise AlreadyExists(request["name"])
        self.state.subscriptions[request["name"]] = dict(request)
        return self.state.subscriptions[request["name"]]

    def get_subscription(self, *, request: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.state.subscriptions[request["subscription"]]
        except KeyError as exc:
            raise NotFound(request["subscription"]) from exc

    def update_subscription(self, *, request: dict[str, Any]) -> dict[str, Any]:
        self.update_calls.append(request)
        update = request["subscription"]
        current = self.state.subscriptions[update["name"]]
        for path in request["update_mask"]["paths"]:
            current[path] = update.get(path)
        return current

    def delete_subscription(self, *, request: dict[str, Any]) -> None:
        self.delete_calls.append(request)
        if request["subscription"] not in self.state.subscriptions:
            raise NotFound(request["subscription"])
        del self.state.subscriptions[request["subscription"]]


_ALLOWED_ACCOUNT = "push@acme-prod.iam.gserviceaccount.com"
_FOREIGN_ACCOUNT = "platform-admin@acme-prod.iam.gserviceaccount.com"


@dataclass
class Harness:
    state: CloudState
    publisher: FakePublisher
    subscriber: FakeSubscriber
    driver: PubSubTopicDriver


@pytest.fixture
def harness() -> Harness:
    state = CloudState()
    publisher = FakePublisher(state)
    subscriber = FakeSubscriber(state)
    return Harness(
        state,
        publisher,
        subscriber,
        PubSubTopicDriver(
            config=PubSubTopicConfig(
                project_id="acme-prod",
                topic_prefix="astrolift",
                publisher_client=publisher,
                subscriber_client=subscriber,
                allowed_service_accounts=(_ALLOWED_ACCOUNT,),
            ),
        ),
    )


def _spec(config: dict[str, Any] | None = None) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-1",
        organization_slug="acme",
        app_id="app-1",
        app_slug="api",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="gke-prod",
        service_handle_hint="events",
        size="small",
        config=config or {},
        tags={"Cost Center": "Engineering"},
        binding_id="binding-1",
        managed_service_id="service-1",
    )


def _provision(harness: Harness, config: dict[str, Any] | None = None):
    result = harness.driver.provision(_spec(config))
    assert result.ok, result
    return result


def test_provision_creates_full_topic_and_subscription_shapes(harness: Harness) -> None:
    result = _provision(
        harness,
        {
            "kms_key_name": "projects/acme-prod/locations/us/keyRings/app/cryptoKeys/events",
            "message_retention_duration": "86400s",
            "message_storage_policy": {
                "allowed_persistence_regions": ["us-central1", "us-east1"],
                "enforce_in_transit": True,
            },
            "schema_settings": {
                "schema": "projects/acme-prod/schemas/event-v1",
                "encoding": "JSON",
            },
            "ingestion_data_source_settings": {
                "cloud_storage": {"bucket": "events-ingest"},
            },
            "message_transforms": [
                {
                    "javascript_udf": {
                        "function_name": "normalize",
                        "code": "function normalize(message, metadata) { return message; }",
                    },
                },
            ],
            "labels": {"Data Class": "internal"},
            "subscriptions": [
                {
                    "name": "workers",
                    "ack_deadline_seconds": 60,
                    "message_retention_duration": "604800s",
                    "retain_acked_messages": True,
                    "filter": 'attributes.kind="ticket"',
                    "enable_message_ordering": True,
                    "labels": {"consumer": "workers"},
                    "dead_letter_policy": {
                        "dead_letter_topic": "projects/acme-prod/topics/dead-letter",
                        "max_delivery_attempts": 10,
                    },
                    "retry_policy": {
                        "minimum_backoff": "10s",
                        "maximum_backoff": "600s",
                    },
                    "push_config": {
                        "push_endpoint": "https://example.test/events",
                        "oidc_token": {"service_account_email": "push@acme-prod.iam.gserviceaccount.com"},
                    },
                },
            ],
        },
    )
    assert result.handle == "topic/astrolift-acme-api-prod-events"
    assert not result.ready
    topic = next(iter(harness.state.topics.values()))
    assert topic["kms_key_name"].endswith("/events")
    assert topic["message_storage_policy"]["enforce_in_transit"] is True
    assert topic["schema_settings"]["encoding"] == "JSON"
    assert topic["ingestion_data_source_settings"]["cloud_storage"]["bucket"] == "events-ingest"
    assert topic["message_transforms"][0]["javascript_udf"]["function_name"] == "normalize"
    assert topic["labels"]["astrolift-binding"] == "binding-1"
    assert topic["labels"]["data-class"] == "internal"
    subscription = next(iter(harness.state.subscriptions.values()))
    assert subscription["ack_deadline_seconds"] == 60
    assert subscription["labels"] == {"consumer": "workers"}
    assert subscription["push_config"]["push_endpoint"].startswith("https://")


def test_provision_is_idempotent_and_reconciles_existing_topic(harness: Harness) -> None:
    first = _provision(harness, {"message_retention_duration": "86400s"})
    second = harness.driver.provision(
        _spec({"message_retention_duration": "172800s"}),
    )
    assert second.ok
    assert second.handle == first.handle
    assert len(harness.state.topics) == 1
    topic = next(iter(harness.state.topics.values()))
    assert topic["message_retention_duration"] == "172800s"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"access_mode": "write"}, "access_mode"),
        ({"message_retention_duration": "5m"}, "Google Duration"),
        ({"message_retention_duration": "60s"}, "between"),
        ({"subscriptions": {}}, "must be an array"),
        ({"subscriptions": [{}]}, ".name is required"),
        (
            {"subscriptions": [{"name": "worker", "ack_deadline_seconds": 5}]},
            "ack_deadline_seconds",
        ),
        (
            {
                "subscriptions": [
                    {"name": "worker", "push_config": {"x": 1}, "bigquery_config": {"x": 2}},
                ],
            },
            "only one delivery",
        ),
        (
            {
                "subscriptions": [
                    {
                        "name": "worker",
                        "dead_letter_policy": {
                            "dead_letter_topic": "projects/p/topics/dlq",
                            "max_delivery_attempts": 3,
                        },
                    },
                ],
            },
            "must be 5-100",
        ),
        (
            {
                "subscriptions": [
                    {
                        "name": "push-once",
                        "enable_exactly_once_delivery": True,
                        "push_config": {"push_endpoint": "https://example.test/events"},
                    },
                ],
            },
            "only for pull delivery",
        ),
        (
            {
                "subscriptions": [
                    {
                        "name": "warehouse",
                        "bigquery_config": {
                            "table": "acme.events.raw",
                            "use_topic_schema": True,
                            "use_table_schema": True,
                        },
                    },
                ],
            },
            "both topic and table schemas",
        ),
        (
            {
                "ingestion_data_source_settings": {
                    "aws_kinesis": {"stream_arn": "a"},
                    "cloud_storage": {"bucket": "b"},
                },
            },
            "only one source",
        ),
        (
            {
                "subscriptions": [
                    {"name": "worker", "expiration_policy": {"ttl": "3600s"}},
                ],
            },
            "expiration_policy.ttl",
        ),
    ],
)
def test_provision_rejects_invalid_config(
    harness: Harness,
    config: dict[str, Any],
    message: str,
) -> None:
    result = harness.driver.provision(_spec(config))
    assert not result.ok
    assert message in result.message
    assert harness.state.topics == {}


def test_update_reconciles_topic_and_mutable_subscription(harness: Harness) -> None:
    provisioned = _provision(
        harness,
        {"subscriptions": [{"name": "workers", "ack_deadline_seconds": 30}]},
    )
    result = harness.driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={
                "kms_key_name": "projects/acme/locations/us/keyRings/r/cryptoKeys/events-v2",
                "message_retention_duration": "172800s",
                "message_transforms": [
                    {
                        "ai_inference": {
                            "endpoint": "projects/acme/locations/us/endpoints/classifier",
                        },
                    },
                ],
                "subscriptions": [
                    {
                        "name": "workers",
                        "ack_deadline_seconds": 90,
                        "enable_exactly_once_delivery": True,
                        "labels": {"tier": "critical"},
                    },
                ],
            },
        ),
    )
    assert result.ok
    assert next(iter(harness.state.topics.values()))["message_retention_duration"] == "172800s"
    assert next(iter(harness.state.topics.values()))["kms_key_name"].endswith("events-v2")
    assert next(iter(harness.state.subscriptions.values()))["ack_deadline_seconds"] == 90
    assert harness.subscriber.update_calls[-1]["update_mask"]["paths"] == [
        "ack_deadline_seconds",
        "enable_exactly_once_delivery",
        "labels",
    ]


def test_update_rejects_immutable_subscription_change(harness: Harness) -> None:
    provisioned = _provision(
        harness,
        {"subscriptions": [{"name": "workers", "filter": 'attributes.kind="a"'}]},
    )
    result = harness.driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"subscriptions": [{"name": "workers", "filter": 'attributes.kind="b"'}]},
        ),
    )
    assert not result.ok
    assert "immutable" in result.message


def test_update_rejects_message_ordering_change(harness: Harness) -> None:
    provisioned = _provision(
        harness,
        {"subscriptions": [{"name": "workers", "enable_message_ordering": True}]},
    )
    result = harness.driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"subscriptions": [{"name": "workers", "enable_message_ordering": False}]},
        ),
    )
    assert not result.ok
    assert "enable_message_ordering" in result.message


def test_update_can_add_bigquery_and_cloud_storage_subscriptions(harness: Harness) -> None:
    provisioned = _provision(harness)
    result = harness.driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={
                "subscriptions": [
                    {
                        "name": "warehouse",
                        "bigquery_config": {"table": "acme-prod.events.raw", "write_metadata": True},
                    },
                    {
                        "name": "archive",
                        "cloud_storage_config": {"bucket": "events-archive", "filename_prefix": "events/"},
                    },
                    {
                        "name": "hot-index",
                        "bigtable_config": {
                            "table": "projects/acme-prod/instances/events/tables/hot",
                            "app_profile_id": "single-cluster",
                        },
                    },
                ],
            },
        ),
    )
    assert result.ok
    deliveries = list(harness.state.subscriptions.values())
    assert any("bigquery_config" in item for item in deliveries)
    assert any("cloud_storage_config" in item for item in deliveries)
    assert any("bigtable_config" in item for item in deliveries)


def _subscription(**fields: Any) -> dict[str, Any]:
    return {"name": "exports", **fields}


def _ai_transform(account: str) -> dict[str, Any]:
    return {
        "ai_inference": {
            "endpoint": "projects/acme-prod/locations/us-central1/endpoints/1",
            "service_account_email": account,
        },
    }


@pytest.mark.parametrize(
    "config",
    [
        pytest.param(
            {
                "subscriptions": [
                    _subscription(
                        push_config={
                            "push_endpoint": "https://example.test/events",
                            "oidc_token": {"service_account_email": _FOREIGN_ACCOUNT},
                        },
                    ),
                ],
            },
            id="push-oidc",
        ),
        pytest.param(
            {
                "subscriptions": [
                    _subscription(
                        bigquery_config={"table": "acme-prod.events.raw", "service_account_email": _FOREIGN_ACCOUNT},
                    ),
                ],
            },
            id="bigquery",
        ),
        pytest.param(
            {
                "subscriptions": [
                    _subscription(
                        bigtable_config={
                            "table": "projects/acme-prod/instances/events/tables/hot",
                            "service_account_email": _FOREIGN_ACCOUNT,
                        },
                    ),
                ],
            },
            id="bigtable",
        ),
        pytest.param(
            {
                "subscriptions": [
                    _subscription(
                        cloud_storage_config={"bucket": "events-archive", "service_account_email": _FOREIGN_ACCOUNT},
                    ),
                ],
            },
            id="cloud-storage",
        ),
        pytest.param({"message_transforms": [_ai_transform(_FOREIGN_ACCOUNT)]}, id="topic-ai-transform"),
        pytest.param(
            {"subscriptions": [_subscription(message_transforms=[_ai_transform(_FOREIGN_ACCOUNT)])]},
            id="subscription-ai-transform",
        ),
        pytest.param(
            {
                "ingestion_data_source_settings": {
                    "aws_kinesis": {
                        "stream_arn": "arn:aws:kinesis:us-east-1:111122223333:stream/events",
                        "consumer_arn": "arn:aws:kinesis:us-east-1:111122223333:stream/events/consumer/c:1",
                        "aws_role_arn": "arn:aws:iam::111122223333:role/pubsub-ingest",
                        "gcp_service_account": _FOREIGN_ACCOUNT,
                    },
                },
            },
            id="ingestion",
        ),
        pytest.param(
            {
                "subscriptions": [
                    _subscription(
                        push_config={
                            "push_endpoint": "https://example.test/events",
                            "oidc_token": {"serviceAccountEmail": _FOREIGN_ACCOUNT},
                        },
                    ),
                ],
            },
            id="json-spelling",
        ),
    ],
)
def test_pubsub_cannot_act_as_an_unlisted_account(harness: Harness, config: dict[str, Any]) -> None:
    denied = harness.driver.provision(_spec(config))

    assert not denied.ok
    assert "pubsub_allowed_service_accounts" in denied.message
    assert harness.state.topics == {}
    assert harness.state.subscriptions == {}


def test_a_listed_account_may_back_push_and_export_subscriptions(harness: Harness) -> None:
    _provision(
        harness,
        {
            "subscriptions": [
                _subscription(
                    bigquery_config={"table": "acme-prod.events.raw", "service_account_email": _ALLOWED_ACCOUNT},
                ),
            ],
        },
    )

    subscription = next(iter(harness.state.subscriptions.values()))
    assert subscription["bigquery_config"]["service_account_email"] == _ALLOWED_ACCOUNT


def test_update_cannot_move_a_subscription_to_an_unlisted_account(harness: Harness) -> None:
    provisioned = _provision(harness)

    denied = harness.driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={
                "subscriptions": [
                    _subscription(
                        cloud_storage_config={"bucket": "events-archive", "service_account_email": _FOREIGN_ACCOUNT},
                    ),
                ],
            },
        ),
    )

    assert not denied.ok and "pubsub_allowed_service_accounts" in denied.message
    assert harness.state.subscriptions == {}


def test_a_label_named_like_an_identity_field_is_not_an_identity(harness: Harness) -> None:
    _provision(harness, {"labels": {"service_account_email": "anything"}})

    topic = next(iter(harness.state.topics.values()))
    assert topic["labels"]["service_account_email"] == "anything"


def test_prune_removes_undeclared_topic_subscriptions(harness: Harness) -> None:
    provisioned = _provision(
        harness,
        {"subscriptions": [{"name": "keep"}, {"name": "remove"}]},
    )
    result = harness.driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"subscriptions": [{"name": "keep"}], "prune_subscriptions": True},
        ),
    )
    assert result.ok
    assert len(harness.state.subscriptions) == 1
    assert next(iter(harness.state.subscriptions)).endswith("-keep")


def test_prune_leaves_subscriptions_not_owned_by_this_driver(harness: Harness) -> None:
    provisioned = _provision(harness, {"subscriptions": [{"name": "owned"}]})
    topic_path = next(iter(harness.state.topics))
    foreign = "projects/other-project/subscriptions/external-consumer"
    harness.state.subscriptions[foreign] = {"name": foreign, "topic": topic_path}
    result = harness.driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"subscriptions": [], "prune_subscriptions": True},
        ),
    )
    assert result.ok
    assert list(harness.state.subscriptions) == [foreign]


def test_binding_exposes_portable_values_and_publisher_role(harness: Harness) -> None:
    provisioned = _provision(harness)
    binding = harness.driver.binding(ServiceHandle(provisioned.handle))
    assert binding.env_vars["TOPIC_NAME"].literal == "astrolift-acme-api-prod-events"
    assert binding.env_vars["TOPIC_REGION"].literal == "global"
    assert json.loads(binding.env_vars["PUBSUB_SUBSCRIPTIONS"].literal or "null") == []
    assert [grant.actions for grant in binding.iam_grants] == [["roles/pubsub.publisher"]]


def test_binding_subscribe_and_manage_modes(harness: Harness) -> None:
    provisioned = _provision(
        harness,
        {"subscriptions": [{"name": "workers"}]},
    )
    subscribe = harness.driver.binding(
        ServiceHandle(provisioned.handle),
        config={"access_mode": "subscribe", "subscriptions": [{"name": "workers"}]},
    )
    assert subscribe.env_vars["PUBSUB_SUBSCRIPTION"].literal.endswith("-workers")
    assert [grant.actions for grant in subscribe.iam_grants] == [["roles/pubsub.subscriber"]]
    manage = harness.driver.binding(
        ServiceHandle(provisioned.handle),
        config={"access_mode": "manage"},
    )
    assert [grant.actions for grant in manage.iam_grants] == [
        ["roles/pubsub.publisher"],
        ["roles/pubsub.editor"],
    ]


def test_subscribe_binding_requires_a_declared_subscription(harness: Harness) -> None:
    provisioned = _provision(harness)
    with pytest.raises(PubSubTopicError, match="requires at least one"):
        harness.driver.binding(
            ServiceHandle(provisioned.handle),
            config={"access_mode": "subscribe"},
        )


def test_deprovision_requires_explicit_data_acknowledgement(harness: Harness) -> None:
    provisioned = _provision(
        harness,
        {"subscriptions": [{"name": "workers"}]},
    )
    result = harness.driver.deprovision(
        DeprovisionSpec(provisioned.handle, config={"subscriptions": [{"name": "workers"}]}),
    )
    assert not result.ok
    assert not result.retryable
    assert result.errors == ["retained_messages_require_delete_data"]
    assert harness.state.topics


def test_deprovision_requires_force_for_foreign_subscription(harness: Harness) -> None:
    provisioned = _provision(harness)
    topic_path = next(iter(harness.state.topics))
    foreign = "projects/acme-prod/subscriptions/foreign"
    harness.state.subscriptions[foreign] = {"name": foreign, "topic": topic_path}
    result = harness.driver.deprovision(
        DeprovisionSpec(provisioned.handle),
        delete_data=True,
    )
    assert not result.ok
    assert result.errors == ["foreign_subscriptions_require_force_destroy"]
    forced = harness.driver.deprovision(
        DeprovisionSpec(provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert forced.ok
    assert harness.state.topics == {}
    assert harness.state.subscriptions == {}


def test_deprovision_deletes_declared_subscriptions_and_is_idempotent(harness: Harness) -> None:
    config = {"subscriptions": [{"name": "workers"}]}
    provisioned = _provision(harness, config)
    first = harness.driver.deprovision(
        DeprovisionSpec(provisioned.handle, config=config),
        delete_data=True,
    )
    second = harness.driver.deprovision(
        DeprovisionSpec(provisioned.handle, config=config),
        delete_data=True,
    )
    assert first.ok and second.ok
    assert "already gone" in second.message


def test_deprovision_continues_after_a_subscription_delete_race(harness: Harness) -> None:
    config = {"subscriptions": [{"name": "first"}, {"name": "second"}]}
    provisioned = _provision(harness, config)
    original_delete = harness.subscriber.delete_subscription
    calls = 0

    def racing_delete(*, request: dict[str, Any]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            harness.state.subscriptions.pop(request["subscription"])
            raise NotFound(request["subscription"])
        original_delete(request=request)

    harness.subscriber.delete_subscription = racing_delete  # type: ignore[method-assign]
    result = harness.driver.deprovision(
        DeprovisionSpec(provisioned.handle, config=config),
        delete_data=True,
    )
    assert result.ok
    assert calls == 2
    assert len(harness.publisher.delete_calls) == 1


def test_status_reports_encryption_and_subscription_count(harness: Harness) -> None:
    provisioned = _provision(
        harness,
        {
            "kms_key_name": "projects/acme/locations/us/keyRings/r/cryptoKeys/k",
            "subscriptions": [{"name": "a"}, {"name": "b"}],
        },
    )
    status = harness.driver.status(ServiceHandle(provisioned.handle))
    assert status.state == "available"
    assert "2 managed / 2 attached subscriptions" in status.message
    assert "CMEK" in status.message


def test_status_surfaces_export_subscription_resource_errors(harness: Harness) -> None:
    provisioned = _provision(
        harness,
        {
            "subscriptions": [
                {"name": "warehouse", "bigquery_config": {"table": "acme.events.raw"}},
            ],
        },
    )
    subscription = next(iter(harness.state.subscriptions.values()))
    subscription["state"] = "RESOURCE_ERROR"
    subscription["bigquery_config"]["state"] = "PERMISSION_DENIED"
    status = harness.driver.status(ServiceHandle(provisioned.handle))
    assert status.state == "error"
    assert "bigquery_config: PERMISSION_DENIED" in status.message


def test_status_and_update_report_missing_topic(harness: Harness) -> None:
    status = harness.driver.status(ServiceHandle("topic/missing"))
    update = harness.driver.update(UpdateSpec("topic/missing", config={"labels": {"x": "y"}}))
    assert status.state == "deprovisioned"
    assert not update.ok
    assert update.errors == ["not_found"]


def test_snapshot_and_restore_fail_explicitly(harness: Harness) -> None:
    with pytest.raises(UnsupportedOperationError, match="cannot represent"):
        harness.driver.snapshot(ServiceHandle("topic/events"))
    with pytest.raises(UnsupportedOperationError, match="not portable"):
        harness.driver.restore(None, _spec())  # type: ignore[arg-type]


def test_config_and_binding_schema_cover_public_contract(harness: Harness) -> None:
    schema = harness.driver.config_schema()
    properties = schema["properties"]
    assert {
        "access_mode",
        "kms_key_name",
        "message_retention_duration",
        "message_storage_policy",
        "schema_settings",
        "ingestion_data_source_settings",
        "message_transforms",
        "subscriptions",
        "prune_subscriptions",
    }.issubset(properties)
    assert properties["subscriptions"]["items"]["properties"]["enable_exactly_once_delivery"]
    assert properties["subscriptions"]["items"]["properties"]["bigtable_config"]
    assert properties["ingestion_data_source_settings"]["properties"]["confluent_cloud"]
    binding = harness.driver.binding_schema().env_vars
    assert {"TOPIC_ARN_OR_ID", "PUBSUB_TOPIC", "PUBSUB_SUBSCRIPTIONS"}.issubset(binding)


def test_resource_ids_and_handles_are_safe() -> None:
    assert _resource_id("goog.events", max_length=255).startswith("astrolift-")
    assert _resource_id("123 events", max_length=255).startswith("a-")
    assert _parse_handle("topic/events") == "events"
    with pytest.raises(ValueError):
        _parse_handle("queue/events")


def test_provider_native_documents_and_update_masks_match_current_sdk(harness: Harness) -> None:
    topic_path = harness.publisher.topic_path("acme-prod", "events")
    topic = harness.driver._topic_document(
        topic_path=topic_path,
        cfg={
            "kms_key_name": "projects/acme-prod/locations/us/keyRings/r/cryptoKeys/k",
            "message_transforms": [
                {
                    "javascript_udf": {
                        "function_name": "normalize",
                        "code": "function normalize(message, metadata) { return message; }",
                    },
                },
            ],
            "ingestion_data_source_settings": {
                "cloud_storage": {"bucket": "events", "text_format": {"delimiter": "\\n"}},
            },
        },
        labels={},
    )
    pubsub_v1.types.UpdateTopicRequest(
        topic=pubsub_v1.types.Topic(topic),
        update_mask=field_mask_pb2.FieldMask(
            paths=["kms_key_name", "message_transforms", "ingestion_data_source_settings"],
        ),
    )
    subscription = harness.driver._subscription_document(
        "projects/acme-prod/subscriptions/events-worker",
        topic_path,
        {
            "name": "worker",
            "enable_exactly_once_delivery": True,
            "bigtable_config": {
                "table": "projects/acme-prod/instances/events/tables/hot",
                "app_profile_id": "single-cluster",
            },
        },
    )
    pubsub_v1.types.UpdateSubscriptionRequest(
        subscription=pubsub_v1.types.Subscription(subscription),
        update_mask=field_mask_pb2.FieldMask(
            paths=["enable_exactly_once_delivery", "bigtable_config"],
        ),
    )


def test_provision_does_not_adopt_another_services_resource(harness) -> None:
    """Names are slug-joined, so another service can map to this one's name (#1961)."""
    import dataclasses

    first = harness.driver.provision(dataclasses.replace(_spec(), managed_service_id="svc-a"))
    second = harness.driver.provision(dataclasses.replace(_spec(), managed_service_id="svc-b"))

    assert first.ok, first.message
    assert not second.ok and "refusing to adopt" in second.message
