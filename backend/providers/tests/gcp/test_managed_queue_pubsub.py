"""Tests for PubSubDriver (#41 — queue/pubsub)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
)
from gcp.managed.queue_pubsub import PubSubConfig, PubSubDriver


class _NotFound(Exception):
    pass


class _AlreadyExists(Exception):
    pass


@dataclass
class FakePublisher:
    project_id: str
    topics: set[str] = field(default_factory=set)

    def topic_path(self, project: str, topic: str) -> str:
        return f"projects/{project}/topics/{topic}"

    def create_topic(self, *, request: dict[str, Any]) -> Any:
        topic_id = request["name"].rsplit("/", 1)[-1]
        if topic_id in self.topics:
            raise _AlreadyExists(topic_id)
        self.topics.add(topic_id)
        return None

    def delete_topic(self, *, request: dict[str, Any]) -> None:
        topic_id = request["topic"].rsplit("/", 1)[-1]
        if topic_id not in self.topics:
            raise _NotFound(topic_id)
        self.topics.discard(topic_id)

    def get_topic(self, *, request: dict[str, Any]) -> Any:
        topic_id = request["topic"].rsplit("/", 1)[-1]
        if topic_id not in self.topics:
            raise _NotFound(topic_id)
        return object()


@dataclass
class FakeSubscriber:
    subscriptions: set[str] = field(default_factory=set)
    seeks: list[dict[str, Any]] = field(default_factory=list)
    delete_raises: dict[str, Exception] = field(default_factory=dict)

    def subscription_path(self, project: str, sub: str) -> str:
        return f"projects/{project}/subscriptions/{sub}"

    def create_subscription(self, *, request: dict[str, Any]) -> Any:
        sub_id = request["name"].rsplit("/", 1)[-1]
        if sub_id in self.subscriptions:
            raise _AlreadyExists(sub_id)
        self.subscriptions.add(sub_id)
        return None

    def delete_subscription(self, *, request: dict[str, Any]) -> None:
        sub_id = request["subscription"].rsplit("/", 1)[-1]
        if sub_id in self.delete_raises:
            raise self.delete_raises[sub_id]
        if sub_id not in self.subscriptions:
            raise _NotFound(sub_id)
        self.subscriptions.discard(sub_id)

    def seek(self, *, request: dict[str, Any]) -> None:
        sub_id = request["subscription"].rsplit("/", 1)[-1]
        if sub_id not in self.subscriptions:
            raise _NotFound(sub_id)
        self.seeks.append(request)


@pytest.fixture
def fake_pub() -> FakePublisher:
    _NotFound.__name__ = "NotFound"
    _AlreadyExists.__name__ = "AlreadyExists"
    return FakePublisher(project_id="acme")


@pytest.fixture
def fake_sub() -> FakeSubscriber:
    return FakeSubscriber()


@pytest.fixture
def driver(
    fake_pub: FakePublisher, fake_sub: FakeSubscriber,
) -> PubSubDriver:
    return PubSubDriver(
        config=PubSubConfig(
            project_id="acme",
            publisher_client=fake_pub,
            subscriber_client=fake_sub,
        ),
    )


def _spec(
    *,
    organization_slug: str = "acme",
    app_slug: str = "api",
    environment_name: str = "prod",
    service_handle_hint: str = "",
) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-1",
        organization_slug=organization_slug,
        app_id="app-1",
        app_slug=app_slug,
        environment_id="env-1",
        environment_name=environment_name,
        tenant_cluster_id="cluster-1",
        service_handle_hint=service_handle_hint,
        size="small",
    )


def test_provision_creates_topic_and_subscription(
    driver: PubSubDriver, fake_pub: FakePublisher, fake_sub: FakeSubscriber,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    assert len(fake_pub.topics) == 1
    assert len(fake_sub.subscriptions) == 1


def test_provision_idempotent(driver: PubSubDriver) -> None:
    driver.provision(_spec())
    # Second call hits AlreadyExists path; treated as success
    result = driver.provision(_spec())
    assert result.ok


def test_deprovision_deletes_both(
    driver: PubSubDriver, fake_pub: FakePublisher, fake_sub: FakeSubscriber,
) -> None:
    res = driver.provision(_spec())
    deprov = driver.deprovision(
        DeprovisionSpec(handle=res.handle), delete_data=True,
    )
    assert deprov.ok
    assert fake_pub.topics == set()
    assert fake_sub.subscriptions == set()


def test_deprovision_idempotent_when_already_gone(
    driver: PubSubDriver,
) -> None:
    res = driver.provision(_spec())
    driver.deprovision(DeprovisionSpec(handle=res.handle))
    # Second time should not raise
    deprov = driver.deprovision(DeprovisionSpec(handle=res.handle))
    assert deprov.ok


def test_status_available(driver: PubSubDriver) -> None:
    res = driver.provision(_spec())
    status = driver.status(ServiceHandle(handle=res.handle))
    assert status.state == "available"


def test_status_deprovisioned_for_missing(driver: PubSubDriver) -> None:
    status = driver.status(ServiceHandle(handle="queue/never"))
    assert status.state == "deprovisioned"


def test_binding_envs_and_iam_grants(driver: PubSubDriver) -> None:
    binding = driver.binding(
        ServiceHandle(handle="queue/astrolift-acme-api-prod"),
    )
    assert set(binding.env_vars.keys()) == {
        "PUBSUB_TOPIC", "PUBSUB_SUBSCRIPTION", "GCP_PROJECT_ID",
    }
    grant_actions = {
        action for grant in binding.iam_grants for action in grant.actions
    }
    assert "roles/pubsub.publisher" in grant_actions
    assert "roles/pubsub.subscriber" in grant_actions


def test_snapshot_raises_for_ephemeral_messages(
    driver: PubSubDriver,
) -> None:
    with pytest.raises(NotImplementedError):
        driver.snapshot(ServiceHandle(handle="queue/x"))


def test_topic_id_canonicalization(driver: PubSubDriver) -> None:
    topic = driver._topic_id(
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
            service_handle_hint="events",
        ),
    )
    assert "--" not in topic
    # Pub/Sub allows alphanumeric + - + _
    for c in topic:
        assert c.isalnum() or c in "-_"


# ---- four-corner deprovision matrix --------------------------------


def test_deprovision_default_drains_subscription(
    driver: PubSubDriver,
    fake_pub: FakePublisher, fake_sub: FakeSubscriber,
) -> None:
    """delete_data=False (default) seeks the subscription forward
    to drain in-flight messages before deleting."""
    res = driver.provision(_spec())
    deprov = driver.deprovision(DeprovisionSpec(handle=res.handle))
    assert deprov.ok
    assert len(fake_sub.seeks) == 1
    # Both topic + subscription deleted regardless of delete_data
    assert fake_pub.topics == set()
    assert fake_sub.subscriptions == set()
    assert "drained=yes" in deprov.message


def test_deprovision_delete_data_skips_drain(
    driver: PubSubDriver,
    fake_pub: FakePublisher, fake_sub: FakeSubscriber,
) -> None:
    res = driver.provision(_spec())
    deprov = driver.deprovision(
        DeprovisionSpec(handle=res.handle), delete_data=True,
    )
    assert deprov.ok
    assert fake_sub.seeks == []
    assert "drained=no" in deprov.message


def test_deprovision_refuses_active_subscribers(
    driver: PubSubDriver, fake_sub: FakeSubscriber,
) -> None:
    """FAILED_PRECONDITION on delete_subscription (active pull
    consumers) errors out cleanly when force_destroy=False."""
    res = driver.provision(_spec())
    sub_id = next(iter(fake_sub.subscriptions))
    fake_sub.delete_raises[sub_id] = RuntimeError(
        "FAILED_PRECONDITION: active subscribers attached",
    )

    deprov = driver.deprovision(
        DeprovisionSpec(handle=res.handle), delete_data=True,
    )
    assert not deprov.ok
    assert "force_destroy=True" in deprov.message


def test_deprovision_force_destroy_bypasses_active_subscribers(
    driver: PubSubDriver,
    fake_pub: FakePublisher, fake_sub: FakeSubscriber,
) -> None:
    res = driver.provision(_spec())
    sub_id = next(iter(fake_sub.subscriptions))
    fake_sub.delete_raises[sub_id] = RuntimeError(
        "FAILED_PRECONDITION: active subscribers attached",
    )

    deprov = driver.deprovision(
        DeprovisionSpec(handle=res.handle),
        delete_data=True, force_destroy=True,
    )
    assert deprov.ok
    # Topic still gets deleted even when subscription delete is
    # bypassed
    assert fake_pub.topics == set()
    assert "force_destroy" in deprov.message


def test_deprovision_already_gone_is_idempotent(
    driver: PubSubDriver,
) -> None:
    deprov = driver.deprovision(
        DeprovisionSpec(handle="queue/never"),
    )
    assert deprov.ok
    assert "already gone" in deprov.message
