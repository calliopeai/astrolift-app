"""Tests for ServiceBusDriver (#47 — queue/servicebus)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
)
from azure.core.exceptions import ResourceNotFoundError
from azure.managed.queue_servicebus import (
    ServiceBusConfig,
    ServiceBusDriver,
)

OWNER = "018f42f0-4420-7000-8000-000000000001"
SUBSCRIPTION = "018f42f0-4420-7000-8000-000000000002"

BINDING = "binding-guid"


@dataclass
class FakeQueue:
    name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    user_metadata: str = ""
    id: str = ""
    status: str = "Active"


@dataclass
class FakeQueues:
    queues: dict[str, FakeQueue] = field(default_factory=dict)

    def create_or_update(
        self,
        *,
        resource_group_name: str,
        namespace_name: str,
        queue_name: str,
        parameters: Any,
        **kwargs: Any,
    ) -> FakeQueue:
        q = FakeQueue(
            name=queue_name,
            parameters={
                key: getattr(parameters, key) for key in ("dead_lettering_on_message_expiration", "max_delivery_count")
            },
            user_metadata=str(parameters.user_metadata),
            id=f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{resource_group_name}/providers/Microsoft.ServiceBus/namespaces/{namespace_name}/queues/{queue_name}",
        )
        self.queues[queue_name] = q
        return q

    def get(
        self,
        *,
        resource_group_name: str,
        namespace_name: str,
        queue_name: str,
        **kwargs: Any,
    ) -> FakeQueue:
        if queue_name not in self.queues:
            raise ResourceNotFoundError(queue_name)
        return self.queues[queue_name]

    def delete(
        self,
        *,
        resource_group_name: str,
        namespace_name: str,
        queue_name: str,
        **kwargs: Any,
    ) -> None:
        if queue_name not in self.queues:
            raise ResourceNotFoundError(queue_name)
        del self.queues[queue_name]


@dataclass
class FakeSBClient:
    queues_obj: FakeQueues = field(default_factory=FakeQueues)

    @property
    def queues(self) -> FakeQueues:
        return self.queues_obj


@pytest.fixture
def fake_client() -> FakeSBClient:
    return FakeSBClient()


@pytest.fixture
def driver(fake_client: FakeSBClient) -> ServiceBusDriver:
    return ServiceBusDriver(
        config=ServiceBusConfig(
            subscription_id=SUBSCRIPTION,
            resource_group="rg",
            namespace_name="acme-prod-sb",
            client=fake_client,
        ),
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="org-1",
        organization_slug="acme",
        app_id="app-1",
        app_slug="api",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="cluster-1",
        service_handle_hint="",
        size="small",
        binding_id=BINDING,
        managed_service_id=OWNER,
    )
    base.update(overrides)
    return ProvisionSpec(**base)


def test_provision_creates_queue(
    driver: ServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    assert len(fake_client.queues_obj.queues) == 1


def test_provision_dead_lettering_enabled(
    driver: ServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    driver.provision(_spec())
    q = next(iter(fake_client.queues_obj.queues.values()))
    assert q.parameters["dead_lettering_on_message_expiration"] is True
    assert q.parameters["max_delivery_count"] == 10


def test_status_available(driver: ServiceBusDriver) -> None:
    res = driver.provision(_spec())
    status = driver.status(ServiceHandle(handle=res.handle, managed_service_id=OWNER))
    assert status.state == "available"


def test_status_deprovisioned(driver: ServiceBusDriver) -> None:
    status = driver.status(ServiceHandle(handle="queue/never", managed_service_id=OWNER))
    assert status.state == "deprovisioned"


def test_deprovision_deletes_queue(
    driver: ServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    res = driver.provision(_spec())
    driver.deprovision(
        DeprovisionSpec(handle=res.handle, managed_service_id=OWNER),
        delete_data=True,
    )
    assert fake_client.queues_obj.queues == {}


def test_deprovision_refuses_to_drop_messages_by_default(
    driver: ServiceBusDriver,
    fake_client: FakeSBClient,
) -> None:
    res = driver.provision(_spec())
    deprov = driver.deprovision(DeprovisionSpec(handle=res.handle, managed_service_id=OWNER))
    assert not deprov.ok
    assert deprov.retryable is False
    assert "delete_data=True" in deprov.message
    assert fake_client.queues_obj.queues


def test_deprovision_idempotent_when_already_gone(
    driver: ServiceBusDriver,
) -> None:
    res = driver.provision(_spec())
    driver.deprovision(
        DeprovisionSpec(handle=res.handle, managed_service_id=OWNER),
        delete_data=True,
    )
    deprov = driver.deprovision(DeprovisionSpec(handle=res.handle, managed_service_id=OWNER))
    assert deprov.ok


def test_binding_envs_and_iam_grants(driver: ServiceBusDriver) -> None:
    result = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=result.handle, managed_service_id=OWNER))
    assert set(binding.env_vars.keys()) == {
        "SERVICEBUS_NAMESPACE",
        "SERVICEBUS_QUEUE",
        "SERVICEBUS_ENDPOINT",
    }
    actions = {a for grant in binding.iam_grants for a in grant.actions}
    assert "Azure Service Bus Data Sender" in actions
    assert "Azure Service Bus Data Receiver" in actions


def test_snapshot_raises(driver: ServiceBusDriver) -> None:
    with pytest.raises(NotImplementedError):
        driver.snapshot(ServiceHandle(handle="queue/x", managed_service_id=OWNER))


def test_queue_name_canonicalization(driver: ServiceBusDriver) -> None:
    name = driver._queue_name(
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
