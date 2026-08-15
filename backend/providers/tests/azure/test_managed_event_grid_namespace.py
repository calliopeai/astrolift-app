"""Lifecycle and adversarial tests for Event Grid Standard namespace topics."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, SnapshotHandle, UpdateSpec
from azure.managed.event_grid_namespace import (
    AzureEventGridNamespaceConfig,
    AzureEventGridNamespaceDriver,
    AzureEventGridNamespaceError,
)

SUBSCRIPTION_ID = "00000000-1111-2222-3333-444444444444"
RESOURCE_GROUP = "rg-platform"
VAULT_URL = "https://platform.vault.azure.net"
UAMI_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
    "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/event-grid"
)
EVENT_HUB_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
    "/providers/Microsoft.EventHub/namespaces/events/eventhubs/triage"
)
STORAGE_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
    "/providers/Microsoft.Storage/storageAccounts/triage"
)


class ResourceNotFoundError(Exception):
    pass


class ResourceExistsError(Exception):
    status_code = 409


class Poller:
    def __init__(self, value: Any = None) -> None:
        self.value = value

    def result(self) -> Any:
        return self.value


@dataclass
class FakeNamespaces:
    values: dict[str, SimpleNamespace] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)
    key1: str = "namespace-primary-key"

    def get(self, resource_group: str, name: str) -> SimpleNamespace:
        assert resource_group == RESOURCE_GROUP
        try:
            return self.values[name]
        except KeyError as exc:
            raise ResourceNotFoundError(name) from exc

    def begin_create_or_update(self, resource_group: str, name: str, parameters: Any) -> Poller:
        assert resource_group == RESOURCE_GROUP
        self.create_calls.append({"name": name, "parameters": parameters})
        value = SimpleNamespace(
            name=name,
            id=(
                f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
                f"/providers/Microsoft.EventGrid/namespaces/{name}"
            ),
            tags=dict(parameters.tags or {}),
            sku=parameters.sku,
            identity=parameters.identity,
            topics_configuration=SimpleNamespace(hostname=f"{name}.eastus2-1.eventgrid.azure.net"),
            is_zone_redundant=parameters.is_zone_redundant,
            public_network_access=parameters.public_network_access,
            inbound_ip_rules=list(parameters.inbound_ip_rules or []),
            minimum_tls_version_allowed=parameters.minimum_tls_version_allowed,
            provisioning_state="Succeeded",
        )
        self.values[name] = value
        return Poller(value)

    def begin_update(self, resource_group: str, name: str, parameters: Any) -> Poller:
        value = self.get(resource_group, name)
        self.update_calls.append({"name": name, "parameters": parameters})
        for field_name in ("sku", "identity", "public_network_access", "inbound_ip_rules"):
            changed = getattr(parameters, field_name, None)
            if changed is not None:
                setattr(value, field_name, changed)
        if parameters.tags is not None:
            value.tags = dict(parameters.tags)
        return Poller(value)

    def begin_delete(self, resource_group: str, name: str) -> Poller:
        self.get(resource_group, name)
        self.delete_calls.append(name)
        self.values.pop(name)
        return Poller()

    def list_shared_access_keys(self, resource_group: str, name: str) -> SimpleNamespace:
        self.get(resource_group, name)
        return SimpleNamespace(key1=self.key1, key2="secondary")


@dataclass
class FakeNamespaceTopics:
    namespaces: FakeNamespaces
    values: dict[tuple[str, str], SimpleNamespace] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[tuple[str, str]] = field(default_factory=list)

    def get(self, resource_group: str, namespace: str, topic: str) -> SimpleNamespace:
        assert resource_group == RESOURCE_GROUP
        try:
            return self.values[(namespace, topic)]
        except KeyError as exc:
            raise ResourceNotFoundError(topic) from exc

    def begin_create_or_update(
        self,
        resource_group: str,
        namespace: str,
        topic: str,
        parameters: Any,
    ) -> Poller:
        self.namespaces.get(resource_group, namespace)
        self.create_calls.append({"namespace": namespace, "topic": topic, "parameters": parameters})
        value = SimpleNamespace(
            name=topic,
            id=(
                f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
                f"/providers/Microsoft.EventGrid/namespaces/{namespace}/topics/{topic}"
            ),
            publisher_type=parameters.publisher_type,
            input_schema=parameters.input_schema,
            event_retention_in_days=parameters.event_retention_in_days,
            provisioning_state="Succeeded",
        )
        self.values[(namespace, topic)] = value
        return Poller(value)

    def begin_update(self, resource_group: str, namespace: str, topic: str, parameters: Any) -> Poller:
        value = self.get(resource_group, namespace, topic)
        self.update_calls.append({"namespace": namespace, "topic": topic, "parameters": parameters})
        value.event_retention_in_days = parameters.event_retention_in_days
        return Poller(value)

    def begin_delete(self, resource_group: str, namespace: str, topic: str) -> Poller:
        self.get(resource_group, namespace, topic)
        self.delete_calls.append((namespace, topic))
        self.values.pop((namespace, topic))
        return Poller()

    def list_by_namespace(self, resource_group: str, namespace: str) -> list[SimpleNamespace]:
        self.namespaces.get(resource_group, namespace)
        return [value for (parent, _), value in self.values.items() if parent == namespace]


@dataclass
class FakeNamespaceSubscriptions:
    topics: FakeNamespaceTopics
    values: dict[tuple[str, str, str], SimpleNamespace] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[tuple[str, str, str]] = field(default_factory=list)

    def get(self, resource_group: str, namespace: str, topic: str, name: str) -> SimpleNamespace:
        assert resource_group == RESOURCE_GROUP
        try:
            return self.values[(namespace, topic, name)]
        except KeyError as exc:
            raise ResourceNotFoundError(name) from exc

    def begin_create_or_update(
        self,
        resource_group: str,
        namespace: str,
        topic: str,
        name: str,
        parameters: Any,
    ) -> Poller:
        self.topics.get(resource_group, namespace, topic)
        self.create_calls.append(
            {"namespace": namespace, "topic": topic, "name": name, "parameters": parameters},
        )
        value = SimpleNamespace(
            name=name,
            delivery_configuration=parameters.delivery_configuration,
            filters_configuration=parameters.filters_configuration,
            event_delivery_schema=parameters.event_delivery_schema,
            provisioning_state="Succeeded",
        )
        self.values[(namespace, topic, name)] = value
        return Poller(value)

    def begin_delete(self, resource_group: str, namespace: str, topic: str, name: str) -> Poller:
        assert resource_group == RESOURCE_GROUP
        self.delete_calls.append((namespace, topic, name))
        self.values.pop((namespace, topic, name), None)
        return Poller()

    def list_by_namespace_topic(self, resource_group: str, namespace: str, topic: str) -> list[SimpleNamespace]:
        self.topics.get(resource_group, namespace, topic)
        return [
            value
            for (parent_namespace, parent_topic, _), value in self.values.items()
            if parent_namespace == namespace and parent_topic == topic
        ]


@dataclass
class FakeMgmt:
    namespaces: FakeNamespaces = field(default_factory=FakeNamespaces)
    namespace_topics: FakeNamespaceTopics = field(init=False)
    namespace_topic_event_subscriptions: FakeNamespaceSubscriptions = field(init=False)

    def __post_init__(self) -> None:
        self.namespace_topics = FakeNamespaceTopics(self.namespaces)
        self.namespace_topic_event_subscriptions = FakeNamespaceSubscriptions(self.namespace_topics)


@dataclass
class FakeManagementLocks:
    values: list[object] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

    def list_at_resource_level(self, **kwargs: Any) -> list[object]:
        assert kwargs["resource_provider_namespace"] == "Microsoft.EventGrid"
        assert kwargs["resource_type"] == "namespaces"
        return list(self.values)

    def delete_at_resource_level(self, **kwargs: Any) -> None:
        self.delete_calls.append(str(kwargs["lock_name"]))
        self.values = [value for value in self.values if getattr(value, "name", "") != kwargs["lock_name"]]


@dataclass
class FakeLocks:
    management_locks: FakeManagementLocks = field(default_factory=FakeManagementLocks)


@dataclass
class FakeSecrets:
    values: dict[str, str] = field(default_factory=dict)
    deleted: set[str] = field(default_factory=set)
    delete_calls: list[str] = field(default_factory=list)
    recover_calls: list[str] = field(default_factory=list)

    def set_secret(self, name: str, value: str) -> SimpleNamespace:
        if name in self.deleted:
            raise ResourceExistsError(name)
        self.values[name] = value
        return SimpleNamespace(name=name, value=value)

    def get_secret(self, name: str) -> SimpleNamespace:
        try:
            return SimpleNamespace(name=name, value=self.values[name])
        except KeyError as exc:
            raise ResourceNotFoundError(name) from exc

    def begin_delete_secret(self, name: str) -> Poller:
        self.delete_calls.append(name)
        if name not in self.values:
            raise ResourceNotFoundError(name)
        self.values.pop(name)
        self.deleted.add(name)
        return Poller()

    def begin_recover_deleted_secret(self, name: str) -> Poller:
        self.recover_calls.append(name)
        self.deleted.discard(name)
        return Poller()


def _spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-id",
        organization_slug="steadymd",
        app_id="app-id",
        app_slug="emr-triage",
        environment_id="env-id",
        environment_name="production",
        tenant_cluster_id="cluster-id",
        service_handle_hint="emr-events",
        size="small",
        isolation="dedicated",
        config=config,
        binding_id="binding-id",
        managed_service_id="service-id",
        tags={"cost-center": "engineering"},
    )


def _driver(*, with_secrets: bool = True) -> tuple[AzureEventGridNamespaceDriver, FakeMgmt, FakeLocks, FakeSecrets]:
    mgmt = FakeMgmt()
    locks = FakeLocks()
    secrets = FakeSecrets()
    return (
        AzureEventGridNamespaceDriver(
            config=AzureEventGridNamespaceConfig(
                subscription_id=SUBSCRIPTION_ID,
                resource_group=RESOURCE_GROUP,
                keyvault_url=VAULT_URL if with_secrets else "",
                location="eastus2",
                mgmt_client=mgmt,
                locks_client=locks,
                secret_client=secrets if with_secrets else None,
            ),
        ),
        mgmt,
        locks,
        secrets,
    )


def _provisioned(driver: AzureEventGridNamespaceDriver, **config: Any) -> str:
    result = driver.provision(_spec(**config))
    assert result.ok, result
    return result.handle


def _identity() -> dict[str, str]:
    return {"type": "UserAssigned", "user_assigned_identity_resource_id": UAMI_ID}


def _dead_letter() -> dict[str, Any]:
    return {
        "storage_account_resource_id": STORAGE_ID,
        "blob_container_name": "dead-letter",
        "identity": _identity(),
        "preauthorized": True,
    }


def test_provisions_standard_namespace_topic_pull_and_push_subscriptions() -> None:
    driver, mgmt, _, secrets = _driver()
    handle = _provisioned(
        driver,
        capacity=4,
        topic_retention_days=7,
        is_zone_redundant=True,
        inbound_ip_rules=["10.20.0.0/16"],
        user_assigned_identity_resource_ids=[UAMI_ID],
        subscriptions=[
            {
                "name": "workers",
                "delivery_mode": "pull",
                "receive_lock_duration_seconds": 300,
                "max_delivery_count": 12,
                "event_time_to_live_minutes": 10080,
                "included_event_types": ["com.steadymd.bug.created"],
                "advanced_filters": [{"operator": "string_contains", "key": "subject", "values": ["EMR"]}],
                "dead_letter": _dead_letter(),
            },
            {
                "name": "webhook",
                "delivery_mode": "push",
                "event_time_to_live_minutes": 1440,
                "destination": {
                    "type": "webhook",
                    "endpoint_url": "https://example.com/events",
                    "aad_tenant_id": "tenant-id",
                    "aad_application_id_or_uri": "api://receiver",
                    "max_events_per_batch": 50,
                    "preferred_batch_size_in_kilobytes": 512,
                    "delivery_attributes": [
                        {"type": "static", "name": "x-source", "value": "astrolift"},
                    ],
                },
            },
            {
                "name": "event-hub",
                "delivery_mode": "push",
                "destination": {"type": "event_hub", "resource_id": EVENT_HUB_ID},
                "delivery_identity": _identity(),
                "destination_preauthorized": True,
            },
        ],
    )
    _, namespace, topic = handle.split("/")
    created_namespace = mgmt.namespaces.values[namespace]
    assert created_namespace.sku.name == "Standard" and created_namespace.sku.capacity == 4
    assert created_namespace.is_zone_redundant is True
    assert created_namespace.identity.type == "UserAssigned"
    created_topic = mgmt.namespace_topics.values[(namespace, topic)]
    assert created_topic.publisher_type == "Custom"
    assert created_topic.input_schema == "CloudEventSchemaV1_0"
    assert created_topic.event_retention_in_days == 7

    calls = {call["name"]: call["parameters"] for call in mgmt.namespace_topic_event_subscriptions.create_calls}
    pull = next(value for name, value in calls.items() if "workers" in name)
    assert pull.delivery_configuration.delivery_mode == "Queue"
    assert pull.delivery_configuration.queue.receive_lock_duration_in_seconds == 300
    assert pull.delivery_configuration.queue.event_time_to_live.days == 7
    webhook = next(value for name, value in calls.items() if "webhook" in name)
    assert webhook.delivery_configuration.push.destination.endpoint_url == "https://example.com/events"
    assert webhook.delivery_configuration.push.destination.delivery_attribute_mappings[0].is_secret is False
    hub = next(value for name, value in calls.items() if "event-hub" in name)
    assert hub.delivery_configuration.push.destination is None
    assert hub.delivery_configuration.push.delivery_with_resource_identity.identity.type == "UserAssigned"

    assert "namespace-primary-key" in secrets.values.values()
    registry = next(json.loads(value) for value in secrets.values.values() if value.startswith("{"))
    assert sorted(registry.values()) == ["Push", "Push", "Queue"]


def test_push_only_does_not_copy_namespace_access_key() -> None:
    driver, _, _, secrets = _driver()
    _provisioned(
        driver,
        subscriptions=[
            {
                "name": "receiver",
                "delivery_mode": "push",
                "destination": {"type": "webhook", "endpoint_url": "https://example.com/events"},
            },
        ],
    )
    assert "namespace-primary-key" not in secrets.values.values()
    assert any(value.startswith("{") for value in secrets.values.values())


def test_partial_update_uses_existing_retention_and_uami_context() -> None:
    driver, mgmt, _, _ = _driver()
    handle = _provisioned(
        driver,
        topic_retention_days=7,
        user_assigned_identity_resource_ids=[UAMI_ID],
    )
    updated = driver.update(
        UpdateSpec(
            handle,
            config={
                "capacity": 3,
                "subscriptions": [
                    {
                        "name": "hub",
                        "delivery_mode": "push",
                        "event_time_to_live_minutes": 10080,
                        "destination": {"type": "event_hub", "resource_id": EVENT_HUB_ID},
                        "delivery_identity": _identity(),
                        "destination_preauthorized": True,
                    },
                ],
            },
        ),
    )
    assert updated.ok, updated
    namespace = handle.split("/")[1]
    assert mgmt.namespaces.values[namespace].sku.capacity == 3


def test_bindings_are_keyless_for_publish_and_secret_backed_for_pull() -> None:
    driver, _, _, _ = _driver()
    handle = _provisioned(
        driver,
        subscriptions=[{"name": "workers", "delivery_mode": "pull"}],
    )
    publish = driver.binding(ServiceHandle(handle))
    assert publish.env_vars["EVENT_BUS_ENDPOINT"].literal.endswith(":publish")
    assert publish.iam_grants[0].actions == ["EventGrid Data Sender"]
    assert "ACCESS_KEY" not in publish.env_vars

    pull = driver.binding(ServiceHandle(handle), {"access_mode": "pull", "subscription_name": "workers"})
    assert pull.env_vars["EVENT_GRID_NAMESPACE_RECEIVE_ENDPOINT"].literal.endswith(":receive")
    assert pull.env_vars["EVENT_GRID_NAMESPACE_ACCESS_KEY"].secret_ref
    assert pull.iam_grants == []

    combined = driver.binding(
        ServiceHandle(handle),
        {"access_mode": "publish_pull", "subscription_name": "workers"},
    )
    assert combined.iam_grants[0].actions == ["EventGrid Data Sender"]
    manage = driver.binding(ServiceHandle(handle), {"access_mode": "manage"})
    assert manage.iam_grants[0].actions == ["EventGrid Contributor"]

    with pytest.raises(AzureEventGridNamespaceError, match="requires subscription_name"):
        driver.binding(ServiceHandle(handle), {"access_mode": "pull"})
    with pytest.raises(AzureEventGridNamespaceError, match="not recorded as platform-owned"):
        driver.binding(ServiceHandle(handle), {"access_mode": "pull", "subscription_name": "missing"})


def test_pull_binding_requires_platform_ownership_registry_entry() -> None:
    driver, _, _, secrets = _driver()
    handle = _provisioned(driver, subscriptions=[{"name": "workers", "delivery_mode": "pull"}])
    registry_name = next(name for name, value in secrets.values.items() if value.startswith("{"))
    secrets.values[registry_name] = "{}"

    with pytest.raises(AzureEventGridNamespaceError, match="not recorded as platform-owned"):
        driver.binding(ServiceHandle(handle), {"access_mode": "pull", "subscription_name": "workers"})


def test_subscription_name_collision_is_not_adopted_or_overwritten() -> None:
    driver, mgmt, _, _ = _driver()
    handle = _provisioned(driver)
    _, namespace, topic = handle.split("/")
    external_name = driver._subscription_name(topic, "workers")
    external = SimpleNamespace(
        name=external_name,
        delivery_configuration=SimpleNamespace(delivery_mode="Queue"),
    )
    mgmt.namespace_topic_event_subscriptions.values[(namespace, topic, external_name)] = external

    result = driver.update(
        UpdateSpec(handle, config={"subscriptions": [{"name": "workers", "delivery_mode": "pull"}]}),
    )

    assert not result.ok and "not recorded as platform-owned" in result.message
    assert mgmt.namespace_topic_event_subscriptions.values[(namespace, topic, external_name)] is external
    assert not mgmt.namespace_topic_event_subscriptions.create_calls


def test_pruning_last_pull_subscription_deletes_and_later_recovers_access_key() -> None:
    driver, _, _, secrets = _driver()
    handle = _provisioned(driver, subscriptions=[{"name": "workers", "delivery_mode": "pull"}])
    access_key_name = next(name for name, value in secrets.values.items() if value == "namespace-primary-key")

    push_only = driver.update(
        UpdateSpec(
            handle,
            config={
                "subscriptions": [
                    {
                        "name": "receiver",
                        "delivery_mode": "push",
                        "destination": {"type": "webhook", "endpoint_url": "https://example.com/events"},
                    },
                ],
                "prune_subscriptions": True,
                "confirm_message_loss": True,
            },
        ),
    )
    assert push_only.ok, push_only
    assert access_key_name not in secrets.values and access_key_name in secrets.deleted

    pull_again = driver.update(
        UpdateSpec(handle, config={"subscriptions": [{"name": "workers-2", "delivery_mode": "pull"}]}),
    )
    assert pull_again.ok, pull_again
    assert secrets.values[access_key_name] == "namespace-primary-key"
    assert access_key_name in secrets.recover_calls


def test_key_vault_is_required_before_any_cloud_mutation() -> None:
    driver, mgmt, _, _ = _driver(with_secrets=False)
    result = driver.provision(_spec())
    assert not result.ok and result.errors == ["no_secret_backend"]
    assert not mgmt.namespaces.create_calls


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"namespace_name": "Microsoft-events"}, "reserved"),
        ({"capacity": 0}, "capacity"),
        ({"topic_retention_days": 8}, "retention"),
        ({"input_schema": "EventGridSchema"}, "CloudEventSchemaV1_0"),
        ({"minimum_tls_version_allowed": "1.1"}, "TLS 1.2"),
        ({"public_network_access": "Disabled"}, "Private Endpoint"),
        ({"inbound_ip_rules": ["not-an-ip"]}, "IP address"),
        ({"prune_subscriptions": True}, "confirm_message_loss"),
        (
            {"subscriptions": [{"name": "pull", "delivery_mode": "pull", "destination": {"type": "webhook"}}]},
            "do not accept a destination",
        ),
        (
            {
                "subscriptions": [
                    {
                        "name": "pull",
                        "delivery_mode": "pull",
                        "delivery_identity": _identity(),
                        "destination_preauthorized": True,
                    },
                ],
            },
            "do not use a delivery identity",
        ),
        ({"subscriptions": [{"name": "push", "delivery_mode": "push"}]}, "requires a destination"),
        (
            {
                "subscriptions": [
                    {
                        "name": "push",
                        "delivery_mode": "push",
                        "receive_lock_duration_seconds": 60,
                        "destination": {"type": "webhook", "endpoint_url": "https://example.com"},
                    },
                ],
            },
            "applies to pull",
        ),
        (
            {
                "subscriptions": [
                    {
                        "name": "push",
                        "delivery_mode": "push",
                        "destination": {
                            "type": "webhook",
                            "endpoint_url": "https://example.com?sig=secret",
                        },
                    },
                ],
            },
            "secret query",
        ),
        (
            {
                "subscriptions": [
                    {
                        "name": "push",
                        "delivery_mode": "push",
                        "destination": {
                            "type": "webhook",
                            "endpoint_url": "https://example.com",
                            "delivery_attributes": [
                                {"name": "authorization", "type": "static", "value": "secret"},
                            ],
                        },
                    },
                ],
            },
            "sensitive",
        ),
        (
            {"subscriptions": [{"name": "pull", "delivery_mode": "pull", "receive_lock_duration_seconds": 59}]},
            "receive lock",
        ),
        (
            {"subscriptions": [{"name": "pull", "delivery_mode": "pull", "event_time_to_live_minutes": 0}]},
            "event TTL",
        ),
        (
            {
                "user_assigned_identity_resource_ids": [UAMI_ID],
                "subscriptions": [
                    {
                        "name": "pull",
                        "delivery_mode": "pull",
                        "dead_letter": {**_dead_letter(), "blob_container_name": "Not_Valid"},
                    },
                ],
            },
            "blob_container_name",
        ),
    ],
)
def test_invalid_configs_fail_before_cloud_calls(config: dict[str, Any], message: str) -> None:
    driver, mgmt, _, _ = _driver()
    result = driver.provision(_spec(**config))
    assert not result.ok and message in result.message
    assert not mgmt.namespaces.create_calls


def test_prune_only_removes_names_recorded_in_ownership_registry() -> None:
    driver, mgmt, _, _ = _driver()
    handle = _provisioned(
        driver,
        subscriptions=[
            {"name": "keep", "delivery_mode": "pull"},
            {"name": "remove", "delivery_mode": "pull"},
        ],
    )
    _, namespace, topic = handle.split("/")
    external_name = "astrolift-external-looking"
    mgmt.namespace_topic_event_subscriptions.values[(namespace, topic, external_name)] = SimpleNamespace(
        name=external_name,
        delivery_configuration=SimpleNamespace(delivery_mode="Queue"),
    )
    result = driver.update(
        UpdateSpec(
            handle,
            config={
                "subscriptions": [{"name": "keep", "delivery_mode": "pull"}],
                "prune_subscriptions": True,
                "confirm_message_loss": True,
            },
        ),
    )
    assert result.ok, result
    deleted = {name for _, _, name in mgmt.namespace_topic_event_subscriptions.delete_calls}
    assert external_name not in deleted
    assert any("remove" in name for name in deleted)


def test_deprovision_guards_data_external_resources_locks_and_cleans_secrets() -> None:
    driver, mgmt, locks, secrets = _driver()
    handle = _provisioned(driver, subscriptions=[{"name": "managed", "delivery_mode": "pull"}])
    _, namespace, topic = handle.split("/")
    retained = driver.deprovision(DeprovisionSpec(handle))
    assert not retained.ok and retained.errors == ["delete_data_required"]

    external_name = "external"
    mgmt.namespace_topic_event_subscriptions.values[(namespace, topic, external_name)] = SimpleNamespace(
        name=external_name,
        delivery_configuration=SimpleNamespace(delivery_mode="Queue"),
    )
    external = driver.deprovision(DeprovisionSpec(handle), delete_data=True)
    assert not external.ok and external.errors == ["external_resources_present"]
    mgmt.namespace_topic_event_subscriptions.values.pop((namespace, topic, external_name))

    mgmt.namespace_topics.values[(namespace, "external-topic")] = SimpleNamespace(name="external-topic")
    external_topic = driver.deprovision(DeprovisionSpec(handle), delete_data=True)
    assert not external_topic.ok and external_topic.errors == ["external_resources_present"]
    mgmt.namespace_topics.values.pop((namespace, "external-topic"))

    locks.management_locks.values.append(SimpleNamespace(name="protect"))
    locked = driver.deprovision(DeprovisionSpec(handle), delete_data=True)
    assert not locked.ok and locked.errors == ["resource_lock_present"]
    assert locks.management_locks.delete_calls == []

    deleted = driver.deprovision(DeprovisionSpec(handle), delete_data=True, force_destroy=True)
    assert deleted.ok
    assert locks.management_locks.delete_calls == ["protect"]
    assert namespace in mgmt.namespaces.delete_calls
    assert not secrets.values
    again = driver.deprovision(DeprovisionSpec(handle), delete_data=True)
    assert again.ok and "already gone" in again.message


def test_forged_handle_cannot_update_delete_or_bind_external_namespace() -> None:
    driver, mgmt, _, _ = _driver()
    namespace = "external-namespace"
    topic = "external-topic"
    mgmt.namespaces.values[namespace] = SimpleNamespace(
        name=namespace,
        tags={"owner": "outside"},
        sku=SimpleNamespace(name="Standard", capacity=1),
        topics_configuration=SimpleNamespace(hostname="external.example.com"),
        provisioning_state="Succeeded",
    )
    mgmt.namespace_topics.values[(namespace, topic)] = SimpleNamespace(
        name=topic,
        publisher_type="Custom",
        input_schema="CloudEventSchemaV1_0",
        event_retention_in_days=1,
        provisioning_state="Succeeded",
    )
    handle = f"event_bus/{namespace}/{topic}"
    update = driver.update(UpdateSpec(handle, config={"capacity": 2}))
    assert not update.ok and "does not own" in update.message
    delete = driver.deprovision(DeprovisionSpec(handle), delete_data=True, force_destroy=True)
    assert not delete.ok and "does not own" in delete.message
    with pytest.raises(AzureEventGridNamespaceError, match="does not own"):
        driver.binding(ServiceHandle(handle))


def test_immutable_drift_and_explicit_identity_removal() -> None:
    driver, mgmt, _, _ = _driver()
    handle = _provisioned(driver, user_assigned_identity_resource_ids=[UAMI_ID])
    _, namespace, topic = handle.split("/")
    mgmt.namespace_topics.values[(namespace, topic)].input_schema = "EventGridSchema"
    drift = driver.provision(_spec())
    assert not drift.ok and "reprovision required" in drift.message
    mgmt.namespace_topics.values[(namespace, topic)].input_schema = "CloudEventSchemaV1_0"

    removed = driver.update(
        UpdateSpec(
            handle,
            config={"system_assigned_identity": False, "user_assigned_identity_resource_ids": []},
        ),
    )
    assert removed.ok, removed
    assert mgmt.namespaces.values[namespace].identity.type == "None"

    mgmt.namespaces.values[namespace].sku.name = "Basic"
    wrong_sku = driver.update(UpdateSpec(handle, config={"capacity": 2}))
    assert not wrong_sku.ok and "Standard is required" in wrong_sku.message


def test_status_missing_topic_updates_and_snapshot_contract() -> None:
    driver, mgmt, _, _ = _driver()
    missing = driver.status(ServiceHandle("event_bus/missing/topic"))
    assert missing.state == "deprovisioned"
    invalid = driver.update(UpdateSpec("event_bus/missing", config={}))
    assert not invalid.ok and invalid.errors == ["invalid_handle"]

    handle = _provisioned(driver)
    _, namespace, topic = handle.split("/")
    mgmt.namespace_topics.values[(namespace, topic)].provisioning_state = "Updating"
    status = driver.status(ServiceHandle(handle))
    assert status.state == "updating"
    mgmt.namespace_topics.values.pop((namespace, topic))
    missing_topic = driver.status(ServiceHandle(handle))
    assert missing_topic.state == "error"

    with pytest.raises(AzureEventGridNamespaceError, match="exact restorable"):
        driver.snapshot(ServiceHandle(handle))
    with pytest.raises(AzureEventGridNamespaceError, match="cannot be restored"):
        driver.restore(SnapshotHandle(handle, "snapshot", "now"), _spec())


def test_schema_constructor_names_registry_recovery_and_corruption_guards() -> None:
    driver, mgmt, _, secrets = _driver()
    schema = driver.config_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["capacity"]["maximum"] == 40
    assert "EVENT_GRID_NAMESPACE_RECEIVE_ENDPOINT" in driver.binding_schema().env_vars
    assert "subscriptions" in driver.editable_fields()

    with pytest.raises(ValueError, match="default_capacity"):
        AzureEventGridNamespaceConfig(SUBSCRIPTION_ID, RESOURCE_GROUP, default_capacity=41)
    with pytest.raises(ValueError, match="reserved"):
        AzureEventGridNamespaceConfig(SUBSCRIPTION_ID, RESOURCE_GROUP, namespace_name_prefix="eventgrid")

    first = driver.provision(_spec())
    second = driver.provision(_spec())
    assert first.ok and second.ok and first.handle == second.handle
    _, namespace, topic = first.handle.split("/")
    assert len(namespace) <= 50 and len(topic) <= 50

    registry_name = next(name for name, value in secrets.values.items() if value.startswith("{"))
    secrets.values.pop(registry_name)
    secrets.deleted.add(registry_name)
    repaired = driver.provision(_spec())
    assert repaired.ok, repaired
    assert registry_name in secrets.recover_calls

    secrets.values[registry_name] = "not-json"
    corrupt = driver.update(
        UpdateSpec(
            first.handle,
            config={"subscriptions": [{"name": "pull", "delivery_mode": "pull"}]},
        ),
    )
    assert not corrupt.ok and "registry" in corrupt.message
    assert namespace in mgmt.namespaces.values
