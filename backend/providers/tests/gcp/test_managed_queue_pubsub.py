"""Tests for PubSubDriver (#41 — queue/pubsub)."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
)
from gcp.managed.queue_pubsub import PubSubConfig, PubSubDriver

SERVICE_ID = "11111111-1111-4111-8111-111111111111"


class _NotFound(Exception):
    pass


class _AlreadyExists(Exception):
    pass


@dataclass
class FakePublisher:
    project_id: str
    topics: set[str] = field(default_factory=set)
    topic_labels: dict[str, dict[str, str]] = field(default_factory=dict)
    subscriber: Any = None

    def topic_path(self, project: str, topic: str) -> str:
        return f"projects/{project}/topics/{topic}"

    def create_topic(self, *, request: dict[str, Any], **_kwargs: Any) -> Any:
        topic_id = request["name"].rsplit("/", 1)[-1]
        if topic_id in self.topics:
            raise _AlreadyExists(topic_id)
        self.topics.add(topic_id)
        self.topic_labels[topic_id] = dict(request.get("labels") or {})
        return None

    def list_topic_subscriptions(self, *, request: dict[str, Any], **_kwargs: Any):
        return SimpleNamespace(
            subscriptions=[
                row["name"]
                for name, row in self.subscriber.documents.items()
                if name in self.subscriber.subscriptions and row["topic"] == request["topic"]
            ],
            next_page_token="",
        )

    def delete_topic(self, *, request: dict[str, Any], **_kwargs: Any) -> None:
        topic_id = request["topic"].rsplit("/", 1)[-1]
        if topic_id not in self.topics:
            raise _NotFound(topic_id)
        self.topics.discard(topic_id)

    def get_topic(self, *, request: dict[str, Any], **_kwargs: Any) -> Any:
        topic_id = request["topic"].rsplit("/", 1)[-1]
        if topic_id not in self.topics:
            raise _NotFound(topic_id)
        return {"name": request["topic"], "labels": self.topic_labels.get(topic_id, {})}


@dataclass
class FakeSubscriber:
    subscriptions: set[str] = field(default_factory=set)
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    seeks: list[dict[str, Any]] = field(default_factory=list)
    delete_raises: dict[str, Exception] = field(default_factory=dict)

    def subscription_path(self, project: str, sub: str) -> str:
        return f"projects/{project}/subscriptions/{sub}"

    def create_subscription(self, *, request: dict[str, Any], **_kwargs: Any) -> Any:
        sub_id = request["name"].rsplit("/", 1)[-1]
        if sub_id in self.subscriptions:
            raise _AlreadyExists(sub_id)
        self.subscriptions.add(sub_id)
        self.documents[sub_id] = dict(request)
        return None

    def get_subscription(self, *, request: dict[str, Any], **_kwargs: Any):
        sub_id = request["subscription"].rsplit("/", 1)[-1]
        if sub_id not in self.subscriptions:
            raise _NotFound(sub_id)
        return self.documents[sub_id]

    def delete_subscription(self, *, request: dict[str, Any], **_kwargs: Any) -> None:
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
    fake_pub: FakePublisher,
    fake_sub: FakeSubscriber,
) -> PubSubDriver:
    fake_pub.subscriber = fake_sub
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
        managed_service_id=SERVICE_ID,
    )


def test_provision_creates_topic_and_subscription(
    driver: PubSubDriver,
    fake_pub: FakePublisher,
    fake_sub: FakeSubscriber,
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
    driver: PubSubDriver,
    fake_pub: FakePublisher,
    fake_sub: FakeSubscriber,
) -> None:
    res = driver.provision(_spec())
    deprov = driver.deprovision(
        DeprovisionSpec(handle=res.handle, managed_service_id=SERVICE_ID),
        delete_data=True,
    )
    assert deprov.ok
    assert fake_pub.topics == set()
    assert fake_sub.subscriptions == set()


def test_deprovision_idempotent_when_already_gone(
    driver: PubSubDriver,
) -> None:
    res = driver.provision(_spec())
    driver.deprovision(DeprovisionSpec(handle=res.handle, managed_service_id=SERVICE_ID), delete_data=True)
    # Both recorded resources were actually removed.
    deprov = driver.deprovision(DeprovisionSpec(handle=res.handle, managed_service_id=SERVICE_ID))
    assert deprov.ok


def test_status_available(driver: PubSubDriver) -> None:
    res = driver.provision(_spec())
    status = driver.status(ServiceHandle(handle=res.handle, managed_service_id=SERVICE_ID))
    assert status.state == "available"


def test_status_deprovisioned_for_missing(driver: PubSubDriver) -> None:
    status = driver.status(ServiceHandle(handle="queue/never", managed_service_id=SERVICE_ID))
    assert status.state == "deprovisioned"


def test_binding_envs_and_iam_grants(driver: PubSubDriver) -> None:
    result = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(result.handle, managed_service_id=SERVICE_ID))
    assert set(binding.env_vars.keys()) == {
        "PUBSUB_TOPIC",
        "PUBSUB_SUBSCRIPTION",
        "GCP_PROJECT_ID",
    }
    grant_actions = {action for grant in binding.iam_grants for action in grant.actions}
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


def test_deprovision_default_preserves_retained_data_without_seeking(
    driver: PubSubDriver,
    fake_pub: FakePublisher,
    fake_sub: FakeSubscriber,
) -> None:
    """Without explicit delete_data, neither acknowledgement nor deletion occurs."""
    res = driver.provision(_spec())
    deprov = driver.deprovision(DeprovisionSpec(handle=res.handle, managed_service_id=SERVICE_ID))
    assert not deprov.ok and deprov.errors == ["retained_messages_require_delete_data"]
    assert fake_sub.seeks == [] and len(fake_pub.topics) == len(fake_sub.subscriptions) == 1


def test_deprovision_delete_data_skips_drain(
    driver: PubSubDriver,
    fake_pub: FakePublisher,
    fake_sub: FakeSubscriber,
) -> None:
    res = driver.provision(_spec())
    deprov = driver.deprovision(
        DeprovisionSpec(handle=res.handle, managed_service_id=SERVICE_ID),
        delete_data=True,
    )
    assert deprov.ok
    assert fake_sub.seeks == []
    assert "deleted" in deprov.message


def test_deprovision_refuses_active_subscribers(
    driver: PubSubDriver,
    fake_sub: FakeSubscriber,
) -> None:
    """FAILED_PRECONDITION on delete_subscription (active pull
    consumers) errors out cleanly when force_destroy=False."""
    res = driver.provision(_spec())
    sub_id = next(iter(fake_sub.subscriptions))
    fake_sub.delete_raises[sub_id] = RuntimeError(
        "FAILED_PRECONDITION: active subscribers attached",
    )

    deprov = driver.deprovision(
        DeprovisionSpec(handle=res.handle, managed_service_id=SERVICE_ID),
        delete_data=True,
    )
    assert not deprov.ok
    assert deprov.errors == ["ownership_unknown"]


def test_deprovision_force_destroy_cannot_bypass_failed_subscription_deletion(
    driver: PubSubDriver,
    fake_pub: FakePublisher,
    fake_sub: FakeSubscriber,
) -> None:
    res = driver.provision(_spec())
    sub_id = next(iter(fake_sub.subscriptions))
    fake_sub.delete_raises[sub_id] = RuntimeError(
        "FAILED_PRECONDITION: active subscribers attached",
    )

    deprov = driver.deprovision(
        DeprovisionSpec(handle=res.handle, managed_service_id=SERVICE_ID),
        delete_data=True,
        force_destroy=True,
    )
    assert not deprov.ok and deprov.errors == ["ownership_unknown"]
    assert len(fake_pub.topics) == len(fake_sub.subscriptions) == 1


def test_deprovision_already_gone_is_idempotent(
    driver: PubSubDriver,
) -> None:
    deprov = driver.deprovision(
        DeprovisionSpec(handle="queue/never", managed_service_id=SERVICE_ID),
    )
    assert deprov.ok
    assert "already gone" in deprov.message


def test_provision_does_not_adopt_another_services_topic(driver) -> None:
    """A recorded foreign handle never supplies ownership authority."""
    import dataclasses

    first = driver.provision(_spec())
    second = driver.provision(
        dataclasses.replace(
            _spec(), managed_service_id="22222222-2222-4222-8222-222222222222", recorded_handle=first.handle
        )
    )

    assert first.ok, first.message
    assert not second.ok and second.errors == ["ownership_refused"]


def test_unlabeled_existing_topic_needs_actual_exclusive_record_proof(driver, fake_pub) -> None:
    topic_id = driver._topic_id(spec=_spec())
    fake_pub.topics.add(topic_id)  # created before topics were labeled

    assert not driver.provision(_spec()).ok
    from dataclasses import replace

    assert driver.provision(replace(_spec(), recorded_handle=f"queue/{topic_id}", recorded_handle_exclusive=True)).ok


@pytest.mark.parametrize("identity", ["", "svc-a", "0" * 32, "00000000-0000-0000-0000-000000000000", None])
@pytest.mark.parametrize("recorded", [False, True])
def test_unknown_service_identity_never_reaches_provider_read_or_create(
    driver, fake_pub, fake_sub, identity, recorded, monkeypatch
):
    from dataclasses import replace

    reads = []
    monkeypatch.setattr(fake_pub, "get_topic", lambda **kwargs: reads.append(kwargs))
    result = driver.provision(
        replace(_spec(), managed_service_id=identity, recorded_handle="queue/recorded-queue" if recorded else "")
    )
    assert not result.ok and not reads and not fake_pub.topics and not fake_sub.subscriptions


def test_expired_operation_budget_refuses_before_provider_read_or_creation(driver, fake_pub, fake_sub, monkeypatch):
    moments = iter([0, 21])
    monkeypatch.setattr("gcp.managed.queue_pubsub.monotonic", lambda: next(moments))
    result = driver.provision(_spec())
    assert not result.ok and result.errors == ["ownership_unknown"]
    assert not fake_pub.topics and not fake_sub.subscriptions


def test_sdk_reads_have_explicit_finite_deadline_and_no_implicit_retry(driver, fake_pub, monkeypatch):
    actual_get = fake_pub.get_topic
    kwargs_seen = []

    def get(**kwargs):
        kwargs_seen.append(kwargs)
        return actual_get(**kwargs)

    monkeypatch.setattr(fake_pub, "get_topic", get)
    assert driver.provision(_spec()).ok
    assert kwargs_seen and all(0 < kwargs["timeout"] <= 5 and kwargs["retry"] is None for kwargs in kwargs_seen)


@pytest.mark.parametrize("name", ["p" * 252, "goog-queue", "queue/foreign", "bad!name", "x"])
def test_invalid_recorded_target_is_refused_instead_of_truncated_or_renamed(driver, fake_pub, fake_sub, name):
    from dataclasses import replace

    result = driver.provision(replace(_spec(), recorded_handle="queue/" + name))
    assert not result.ok and result.errors == ["invalid_resource_identity"]
    assert not fake_pub.topics and not fake_sub.subscriptions


@pytest.mark.parametrize("project", ["", None, " projects/foreign ", "projects/foreign"])
def test_unknown_or_noncanonical_project_configuration_cannot_imply_absence(
    driver, fake_pub, fake_sub, project, monkeypatch
):
    from dataclasses import replace

    driver._config = replace(driver._config, project_id=project)
    reads = []
    monkeypatch.setattr(fake_pub, "get_topic", lambda **kwargs: reads.append(kwargs))
    result = driver.provision(_spec())
    assert not result.ok and result.errors == ["ownership_unknown"]
    assert not reads and not fake_pub.topics and not fake_sub.subscriptions
