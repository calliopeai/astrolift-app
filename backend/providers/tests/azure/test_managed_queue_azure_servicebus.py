"""Tests for AzureServiceBusDriver (#364 -- queue/azure_servicebus).

Mirrors the GCP Pub/Sub driver test surface: provision (topic + default
subscription, idempotent, tagged), four-corner deprovision matrix
(delete_data x force_destroy, plus the topic-status lock axis), status,
binding shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from azure.managed.queue_servicebus import (
    KIND,
    AzureServiceBusConfig,
    AzureServiceBusDriver,
    AzureServiceBusError,
)

# ---- fakes ------------------------------------------------------


class _NotFound(Exception):
    pass


_NotFound.__name__ = "ResourceNotFoundError"


@dataclass
class FakeTopic:
    name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    status: str = "Active"


@dataclass
class FakeSubscription:
    name: str
    topic_name: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeTopics:
    topics: dict[str, FakeTopic] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

    def create_or_update(
        self,
        *,
        resource_group_name: str,
        namespace_name: str,
        topic_name: str,
        parameters: dict[str, Any],
    ) -> FakeTopic:
        self.create_calls.append(
            {"name": topic_name, "parameters": parameters},
        )
        existing = self.topics.get(topic_name)
        if existing is not None:
            existing.parameters.update(parameters)
            return existing
        topic = FakeTopic(name=topic_name, parameters=dict(parameters))
        self.topics[topic_name] = topic
        return topic

    def get(
        self,
        *,
        resource_group_name: str,
        namespace_name: str,
        topic_name: str,
    ) -> FakeTopic:
        if topic_name not in self.topics:
            raise _NotFound(topic_name)
        return self.topics[topic_name]

    def delete(
        self,
        *,
        resource_group_name: str,
        namespace_name: str,
        topic_name: str,
    ) -> None:
        self.delete_calls.append(topic_name)
        if topic_name not in self.topics:
            raise _NotFound(topic_name)
        del self.topics[topic_name]


@dataclass
class FakeSubscriptions:
    subscriptions: dict[tuple[str, str], FakeSubscription] = field(
        default_factory=dict,
    )
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[tuple[str, str]] = field(default_factory=list)

    def create_or_update(
        self,
        *,
        resource_group_name: str,
        namespace_name: str,
        topic_name: str,
        subscription_name: str,
        parameters: dict[str, Any],
    ) -> FakeSubscription:
        self.create_calls.append(
            {
                "topic": topic_name,
                "subscription": subscription_name,
                "parameters": parameters,
            },
        )
        key = (topic_name, subscription_name)
        existing = self.subscriptions.get(key)
        if existing is not None:
            existing.parameters.update(parameters)
            return existing
        sub = FakeSubscription(
            name=subscription_name,
            topic_name=topic_name,
            parameters=dict(parameters),
        )
        self.subscriptions[key] = sub
        return sub

    def delete(
        self,
        *,
        resource_group_name: str,
        namespace_name: str,
        topic_name: str,
        subscription_name: str,
    ) -> None:
        self.delete_calls.append((topic_name, subscription_name))
        key = (topic_name, subscription_name)
        if key not in self.subscriptions:
            raise _NotFound(subscription_name)
        del self.subscriptions[key]


@dataclass
class FakeSBClient:
    topics_obj: FakeTopics = field(default_factory=FakeTopics)
    subs_obj: FakeSubscriptions = field(default_factory=FakeSubscriptions)

    @property
    def topics(self) -> FakeTopics:
        return self.topics_obj

    @property
    def subscriptions(self) -> FakeSubscriptions:
        return self.subs_obj


# ---- fixtures ---------------------------------------------------


@pytest.fixture
def fake_client() -> FakeSBClient:
    return FakeSBClient()


@pytest.fixture
def driver(fake_client: FakeSBClient) -> AzureServiceBusDriver:
    return AzureServiceBusDriver(
        config=AzureServiceBusConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            namespace_name="acme-prod-sb",
            client=fake_client,
        ),
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="azure-prod",
        service_handle_hint="events",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_topic_and_default_subscription(
    driver: AzureServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    assert result.handle.startswith(f"{KIND}/")
    topic_name = result.handle.split("/", 1)[1]
    assert topic_name in fake_client.topics_obj.topics
    sub_name = (topic_name, f"{topic_name[:42]}-default")
    assert sub_name in fake_client.subs_obj.subscriptions


def test_provision_default_subscription_name_pattern(
    driver: AzureServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    """The default subscription name follows ``<topic>-default``,
    truncated so it stays within Azure's 50-char subscription limit."""
    result = driver.provision(_spec(service_handle_hint="x"))
    topic_name = result.handle.split("/", 1)[1]
    sub_keys = list(fake_client.subs_obj.subscriptions.keys())
    assert len(sub_keys) == 1
    sub_topic, sub_name = sub_keys[0]
    assert sub_topic == topic_name
    assert sub_name.endswith("-default")
    assert len(sub_name) <= 50


def test_provision_topic_carries_user_metadata(
    driver: AzureServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    driver.provision(_spec())
    create = fake_client.topics_obj.create_calls[0]
    metadata = create["parameters"]["userMetadata"]
    assert "astrolift.io/managed-by=platform" in metadata
    assert "astrolift.io/app=api" in metadata


def test_provision_default_message_ttl_passed_through(
    driver: AzureServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    driver.provision(_spec())
    create = fake_client.topics_obj.create_calls[0]
    assert create["parameters"]["default_message_time_to_live"] == "P14D"


def test_provision_dead_lettering_enabled_on_subscription(
    driver: AzureServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    driver.provision(_spec())
    create = fake_client.subs_obj.create_calls[0]
    assert create["parameters"]["dead_lettering_on_message_expiration"] is True


def test_provision_honours_spec_config_override(
    driver: AzureServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    driver.provision(
        _spec(
            config={
                "max_size_in_megabytes": 5120,
                "enable_partitioning": True,
                "default_message_ttl": "P7D",
                "max_delivery_count": 5,
            },
        ),
    )
    topic_params = fake_client.topics_obj.create_calls[0]["parameters"]
    sub_params = fake_client.subs_obj.create_calls[0]["parameters"]
    assert topic_params["max_size_in_megabytes"] == 5120
    assert topic_params["enable_partitioning"] is True
    assert topic_params["default_message_time_to_live"] == "P7D"
    assert sub_params["max_delivery_count"] == 5


def test_provision_idempotent(
    driver: AzureServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    # Both calls invoke create_or_update (Azure SB upsert semantic)
    # but the topic count should still be 1.
    assert len(fake_client.topics_obj.topics) == 1


# ---- update -----------------------------------------------------


def test_update_passes_size_changes_through(
    driver: AzureServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    res = driver.provision(_spec())
    before = len(fake_client.topics_obj.create_calls)
    update = driver.update(
        UpdateSpec(
            handle=res.handle,
            config={"max_size_in_megabytes": 2048},
        ),
    )
    assert update.ok
    after = len(fake_client.topics_obj.create_calls)
    assert after == before + 1
    last = fake_client.topics_obj.create_calls[-1]
    assert last["parameters"]["max_size_in_megabytes"] == 2048


def test_update_noop_when_nothing_to_change(
    driver: AzureServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    res = driver.provision(_spec())
    before = len(fake_client.topics_obj.create_calls)
    result = driver.update(UpdateSpec(handle=res.handle))
    assert result.ok
    assert "no-op" in result.message
    assert len(fake_client.topics_obj.create_calls) == before


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_refuses_to_drop_messages(
    driver: AzureServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    res = driver.provision(_spec())
    topic_name = res.handle.split("/", 1)[1]
    result = driver.deprovision(DeprovisionSpec(handle=res.handle))
    assert not result.ok
    assert result.retryable is False
    assert "delete_data=True" in result.message
    assert topic_name not in fake_client.topics_obj.delete_calls
    assert topic_name in fake_client.topics_obj.topics


def test_deprovision_delete_data_only_skips_drain(
    driver: AzureServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    res = driver.provision(_spec())
    drain_calls_before = len(
        [c for c in fake_client.subs_obj.create_calls if c["parameters"].get("status") == "ReceiveDisabled"],
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=res.handle),
        delete_data=True,
    )
    assert result.ok
    assert "messages purged" in result.message
    drain_calls_after = len(
        [c for c in fake_client.subs_obj.create_calls if c["parameters"].get("status") == "ReceiveDisabled"],
    )
    assert drain_calls_after == drain_calls_before


def test_deprovision_default_respects_topic_lock(
    driver: AzureServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    res = driver.provision(_spec())
    topic_name = res.handle.split("/", 1)[1]
    fake_client.topics_obj.topics[topic_name].status = "Disabled"
    result = driver.deprovision(
        DeprovisionSpec(handle=res.handle),
        delete_data=True,
    )
    assert not result.ok
    assert "locked" in result.message
    assert "topic_locked" in result.errors
    # Topic still present.
    assert topic_name in fake_client.topics_obj.topics


def test_deprovision_force_destroy_bypasses_topic_lock(
    driver: AzureServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    res = driver.provision(_spec())
    topic_name = res.handle.split("/", 1)[1]
    fake_client.topics_obj.topics[topic_name].status = "Disabled"
    result = driver.deprovision(
        DeprovisionSpec(handle=res.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "force_destroy=True" in result.message
    assert topic_name not in fake_client.topics_obj.topics


def test_deprovision_atomic_both_flags(
    driver: AzureServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    res = driver.provision(_spec())
    topic_name = res.handle.split("/", 1)[1]
    result = driver.deprovision(
        DeprovisionSpec(handle=res.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "messages purged" in result.message
    assert "force_destroy=True" in result.message
    assert topic_name not in fake_client.topics_obj.topics


def test_deprovision_idempotent_when_already_gone(
    driver: AzureServiceBusDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="queue/never-existed"),
    )
    assert result.ok
    assert "already gone" in result.message


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: AzureServiceBusDriver,
) -> None:
    state = driver.status(ServiceHandle(handle="queue/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_active_to_available(
    driver: AzureServiceBusDriver,
) -> None:
    res = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=res.handle))
    assert state.state == "available"


def test_status_maps_deleting_to_deprovisioning(
    driver: AzureServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    res = driver.provision(_spec())
    topic_name = res.handle.split("/", 1)[1]
    fake_client.topics_obj.topics[topic_name].status = "Deleting"
    state = driver.status(ServiceHandle(handle=res.handle))
    assert state.state == "deprovisioning"


# ---- binding ----------------------------------------------------


def test_binding_returns_envelope(
    driver: AzureServiceBusDriver,
) -> None:
    res = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=res.handle))
    env = binding.env_vars
    assert set(env.keys()) == {
        "SERVICEBUS_NAMESPACE",
        "SERVICEBUS_TOPIC",
        "SERVICEBUS_SUBSCRIPTION",
        "SERVICEBUS_ENDPOINT",
    }
    assert env["SERVICEBUS_NAMESPACE"].literal == "acme-prod-sb"
    assert env["SERVICEBUS_SUBSCRIPTION"].literal.endswith("-default")
    assert env["SERVICEBUS_ENDPOINT"].literal.startswith("sb://")


def test_binding_iam_grants_split_sender_and_receiver(
    driver: AzureServiceBusDriver,
) -> None:
    res = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=res.handle))
    actions = {a for g in binding.iam_grants for a in g.actions}
    assert "Azure Service Bus Data Sender" in actions
    assert "Azure Service Bus Data Receiver" in actions
    # Sender scopes the topic; Receiver scopes the subscription.
    sender = [g for g in binding.iam_grants if "Sender" in g.actions[0]]
    receiver = [g for g in binding.iam_grants if "Receiver" in g.actions[0]]
    assert sender and receiver
    assert "/topics/" in sender[0].resource
    assert "/subscriptions/" in receiver[0].resource


# ---- snapshot + restore -----------------------------------------


def test_snapshot_raises(driver: AzureServiceBusDriver) -> None:
    with pytest.raises(AzureServiceBusError):
        driver.snapshot(ServiceHandle(handle="queue/x"))


def test_restore_raises(driver: AzureServiceBusDriver) -> None:
    from _sdk.managed_service import SnapshotHandle

    snap = SnapshotHandle(
        handle="queue/x",
        snapshot_id="snap-1",
        created_at="2026-05-15T00:00:00+00:00",
    )
    with pytest.raises(AzureServiceBusError):
        driver.restore(snap, _spec())


# ---- naming + helpers -------------------------------------------


def test_topic_name_canonicalization(
    driver: AzureServiceBusDriver,
) -> None:
    name = driver._topic_name(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
            service_handle_hint="events",
        ),
    )
    assert "--" not in name
    for c in name:
        assert c.isalnum() or c in "-_./"
    assert len(name) <= 260


def test_default_sub_name_truncates_long_topic(
    driver: AzureServiceBusDriver,
) -> None:
    long_topic = "x" * 100
    sub = driver._default_sub_name(  # type: ignore[attr-defined]
        topic_name=long_topic,
    )
    assert sub.endswith("-default")
    assert len(sub) <= 50


def test_handle_round_trip(driver: AzureServiceBusDriver) -> None:
    handle = driver._handle_for(topic_name="my-topic")  # type: ignore[attr-defined]
    assert handle.startswith(f"{KIND}/")
    assert (
        driver._topic_name_from_handle(handle) == "my-topic"  # type: ignore[attr-defined]
    )


def test_handle_rejects_malformed(driver: AzureServiceBusDriver) -> None:
    with pytest.raises(AzureServiceBusError):
        driver._topic_name_from_handle("no-slash")  # type: ignore[attr-defined]
    with pytest.raises(AzureServiceBusError):
        driver._topic_name_from_handle("queue/")  # type: ignore[attr-defined]


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(driver: AzureServiceBusDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in (
        "max_size_in_megabytes",
        "enable_partitioning",
        "default_message_ttl",
        "lock_duration",
        "max_delivery_count",
    ):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: AzureServiceBusDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "SERVICEBUS_NAMESPACE",
        "SERVICEBUS_TOPIC",
        "SERVICEBUS_SUBSCRIPTION",
        "SERVICEBUS_ENDPOINT",
    ):
        assert key in schema.env_vars


def test_topic_registration_uses_portable_handle_and_binding(fake_client: FakeSBClient) -> None:
    topic_driver = AzureServiceBusDriver(
        config=AzureServiceBusConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            namespace_name="acme-prod-sb",
            handle_kind="topic",
            location="eastus2",
            client=fake_client,
        ),
    )

    result = topic_driver.provision(_spec())
    binding = topic_driver.binding(ServiceHandle(result.handle))

    assert result.ok and result.handle.startswith("topic/")
    assert binding.env_vars["TOPIC_NAME"].literal == result.handle.split("/", 1)[1]
    assert binding.env_vars["TOPIC_REGION"].literal == "eastus2"
    assert binding.env_vars["TOPIC_ARN_OR_ID"].literal.endswith(
        f"/topics/{binding.env_vars['TOPIC_NAME'].literal}",
    )
    assert set(topic_driver.binding_schema().env_vars) == set(binding.env_vars)


def test_handle_rejects_wrong_portable_kind(fake_client: FakeSBClient) -> None:
    topic_driver = AzureServiceBusDriver(
        config=AzureServiceBusConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            namespace_name="acme-prod-sb",
            handle_kind="topic",
            client=fake_client,
        ),
    )
    with pytest.raises(AzureServiceBusError, match="must use kind"):
        topic_driver.status(ServiceHandle("queue/not-a-topic-handle"))


def test_config_rejects_unknown_handle_kind(fake_client: FakeSBClient) -> None:
    with pytest.raises(ValueError, match="handle_kind"):
        AzureServiceBusConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            namespace_name="acme-prod-sb",
            handle_kind="event_bus",
            client=fake_client,
        )
