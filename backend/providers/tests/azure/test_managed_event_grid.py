"""Lifecycle and adversarial tests for Azure Event Grid custom topics."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, SnapshotHandle, UpdateSpec
from azure.managed.event_grid import (
    AzureEventGridConfig,
    AzureEventGridDriver,
    AzureEventGridError,
)

SUBSCRIPTION_ID = "00000000-1111-2222-3333-444444444444"
RESOURCE_GROUP = "rg-platform"
FUNCTION_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
    "/providers/Microsoft.Web/sites/triage/functions/on-event"
)
EVENT_HUB_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
    "/providers/Microsoft.EventHub/namespaces/events/eventhubs/triage"
)
STORAGE_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
    "/providers/Microsoft.Storage/storageAccounts/triage"
)
UAMI_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
    "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/event-grid"
)


class ResourceNotFoundError(Exception):
    pass


class Poller:
    def __init__(self, value: Any = None) -> None:
        self.value = value

    def result(self) -> Any:
        return self.value


@dataclass
class FakeTopics:
    values: dict[str, SimpleNamespace] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

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
                f"/providers/Microsoft.EventGrid/topics/{name}"
            ),
            endpoint=f"https://{name}.eastus2-1.eventgrid.azure.net/api/events",
            provisioning_state="Succeeded",
            tags=dict(parameters.tags or {}),
            identity=parameters.identity,
            input_schema=parameters.input_schema,
            minimum_tls_version_allowed=parameters.minimum_tls_version_allowed,
            public_network_access=parameters.public_network_access,
            inbound_ip_rules=list(parameters.inbound_ip_rules or []),
            disable_local_auth=parameters.disable_local_auth,
            data_residency_boundary=parameters.data_residency_boundary,
        )
        self.values[name] = value
        return Poller(value)

    def begin_update(self, resource_group: str, name: str, parameters: Any) -> Poller:
        value = self.get(resource_group, name)
        self.update_calls.append({"name": name, "parameters": parameters})
        for field_name in (
            "identity",
            "minimum_tls_version_allowed",
            "public_network_access",
            "inbound_ip_rules",
            "disable_local_auth",
            "data_residency_boundary",
        ):
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


@dataclass
class FakeSubscriptions:
    values: dict[tuple[str, str], SimpleNamespace] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[tuple[str, str]] = field(default_factory=list)

    def begin_create_or_update(
        self,
        resource_group: str,
        topic_name: str,
        name: str,
        parameters: Any,
    ) -> Poller:
        assert resource_group == RESOURCE_GROUP
        self.create_calls.append(
            {
                "topic_name": topic_name,
                "name": name,
                "parameters": parameters,
            },
        )
        value = SimpleNamespace(
            name=name,
            provisioning_state="Succeeded",
            labels=list(parameters.labels or []),
            parameters=parameters,
        )
        self.values[(topic_name, name)] = value
        return Poller(value)

    def list(self, resource_group: str, topic_name: str) -> list[SimpleNamespace]:
        assert resource_group == RESOURCE_GROUP
        return [value for (topic, _), value in self.values.items() if topic == topic_name]

    def begin_delete(self, resource_group: str, topic_name: str, name: str) -> Poller:
        assert resource_group == RESOURCE_GROUP
        self.delete_calls.append((topic_name, name))
        self.values.pop((topic_name, name), None)
        return Poller()


@dataclass
class FakeMgmt:
    topics: FakeTopics = field(default_factory=FakeTopics)
    topic_event_subscriptions: FakeSubscriptions = field(default_factory=FakeSubscriptions)


@dataclass
class FakeManagementLocks:
    values: list[object] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

    def list_at_resource_level(self, **kwargs: Any) -> list[object]:
        assert kwargs["resource_provider_namespace"] == "Microsoft.EventGrid"
        assert kwargs["resource_type"] == "topics"
        return list(self.values)

    def delete_at_resource_level(self, **kwargs: Any) -> None:
        self.delete_calls.append(str(kwargs["lock_name"]))
        self.values = [value for value in self.values if getattr(value, "name", "") != kwargs["lock_name"]]


@dataclass
class FakeLocks:
    management_locks: FakeManagementLocks = field(default_factory=FakeManagementLocks)


def _spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-id",
        organization_slug="steadymd",
        app_id="app-id",
        app_slug="emr-triage",
        environment_id="env-id",
        environment_name="prod",
        tenant_cluster_id="cluster-id",
        service_handle_hint="triage-events",
        size="small",
        config=config,
        tags={"owner": "platform"},
        isolation="dedicated",
        binding_id="binding-id",
        managed_service_id="service-id",
    )


def _driver() -> tuple[AzureEventGridDriver, FakeMgmt, FakeLocks]:
    mgmt = FakeMgmt()
    locks = FakeLocks()
    driver = AzureEventGridDriver(
        config=AzureEventGridConfig(
            subscription_id=SUBSCRIPTION_ID,
            resource_group=RESOURCE_GROUP,
            location="eastus2",
            topic_name_prefix="astrolift-eg",
            mgmt_client=mgmt,
            locks_client=locks,
        ),
    )
    return driver, mgmt, locks


def _provisioned(driver: AzureEventGridDriver, **config: Any) -> str:
    result = driver.provision(_spec(**config))
    assert result.ok, result
    return result.handle


def test_provision_binding_and_idempotent_reconcile() -> None:
    driver, mgmt, _ = _driver()
    handle = _provisioned(
        driver,
        input_schema="CloudEventSchemaV1_0",
        inbound_ip_rules=["10.0.0.0/24"],
        data_residency_boundary="WithinRegion",
    )

    assert handle.startswith("event_bus/astrolift-eg-triage-events-")
    create = mgmt.topics.create_calls[0]
    payload = create["parameters"]
    assert payload.disable_local_auth is True
    assert payload.minimum_tls_version_allowed == "1.2"
    assert payload.inbound_ip_rules[0].ip_mask == "10.0.0.0/24"
    assert payload.tags["astrolift.io/binding"] == "binding-id"
    assert payload.tags["astrolift.io/extra/owner"] == "platform"

    binding = driver.binding(ServiceHandle(handle))
    assert binding.env_vars["EVENT_BUS_ENDPOINT"].literal.startswith("https://")
    assert binding.env_vars["EVENT_GRID_INPUT_SCHEMA"].literal == "CloudEventSchemaV1_0"
    assert binding.iam_grants[0].actions == ["EventGrid Data Sender"]
    assert not any("KEY" in key or "TOKEN" in key for key in binding.env_vars)

    second = driver.provision(_spec(minimum_tls_version_allowed="1.2"))
    assert second.ok and second.handle == handle
    assert len(mgmt.topics.create_calls) == 1
    assert len(mgmt.topics.update_calls) == 1


def test_binding_manage_mode_adds_only_subscription_contributor() -> None:
    driver, _, _ = _driver()
    handle = _provisioned(driver)
    binding = driver.binding(ServiceHandle(handle), {"access_mode": "manage"})
    assert binding.iam_grants[0].actions == [
        "EventGrid Data Sender",
        "EventGrid EventSubscription Contributor",
    ]


@pytest.mark.parametrize(
    ("kind", "destination", "model_name"),
    [
        (
            "webhook",
            {
                "type": "webhook",
                "endpoint_url": "https://example.com/events",
                "max_events_per_batch": 20,
                "preferred_batch_size_in_kilobytes": 128,
            },
            "WebHookEventSubscriptionDestination",
        ),
        (
            "azure_function",
            {"type": "azure_function", "resource_id": FUNCTION_ID},
            "AzureFunctionEventSubscriptionDestination",
        ),
        ("event_hub", {"type": "event_hub", "resource_id": EVENT_HUB_ID}, "EventHubEventSubscriptionDestination"),
        (
            "service_bus_queue",
            {"type": "service_bus_queue", "resource_id": EVENT_HUB_ID},
            "ServiceBusQueueEventSubscriptionDestination",
        ),
        (
            "service_bus_topic",
            {"type": "service_bus_topic", "resource_id": EVENT_HUB_ID},
            "ServiceBusTopicEventSubscriptionDestination",
        ),
        (
            "storage_queue",
            {"type": "storage_queue", "resource_id": STORAGE_ID, "queue_name": "events"},
            "StorageQueueEventSubscriptionDestination",
        ),
        (
            "hybrid_connection",
            {"type": "hybrid_connection", "resource_id": EVENT_HUB_ID},
            "HybridConnectionEventSubscriptionDestination",
        ),
        (
            "namespace_topic",
            {"type": "namespace_topic", "resource_id": EVENT_HUB_ID},
            "NamespaceTopicEventSubscriptionDestination",
        ),
        (
            "monitor_alert",
            {"type": "monitor_alert", "severity": "Sev2", "action_groups": [FUNCTION_ID]},
            "MonitorAlertEventSubscriptionDestination",
        ),
    ],
)
def test_all_supported_push_destinations(kind: str, destination: dict[str, Any], model_name: str) -> None:
    driver, mgmt, _ = _driver()
    result = driver.provision(
        _spec(
            subscriptions=[{"name": kind.replace("_", "-"), "destination": destination}],
        ),
    )
    assert result.ok, result
    parameters = mgmt.topic_event_subscriptions.create_calls[0]["parameters"]
    assert type(parameters.destination).__name__ == model_name


def test_filters_retry_dead_letter_attributes_and_identity_are_rendered() -> None:
    driver, mgmt, _ = _driver()
    handle = _provisioned(
        driver,
        system_assigned_identity=True,
        topic_user_assigned_identity_resource_ids=[UAMI_ID],
        subscriptions=[
            {
                "name": "triage",
                "destination": {
                    "type": "webhook",
                    "endpoint_url": "https://example.com/events",
                    "aad_tenant_id": "tenant",
                    "aad_application_id_or_uri": "api://triage",
                    "delivery_attributes": [
                        {"name": "x-source", "type": "static", "value": "astrolift"},
                        {"name": "x-subject", "type": "dynamic", "source_field": "subject"},
                    ],
                },
                "included_event_types": ["Triage.Created"],
                "subject_begins_with": "/emr/",
                "subject_ends_with": "/created",
                "is_subject_case_sensitive": True,
                "advanced_filtering_on_arrays": True,
                "advanced_filters": [
                    {"operator": "string_in", "key": "data.priority", "values": ["high"]},
                    {"operator": "number_greater_than", "key": "data.score", "value": 0.8},
                    {"operator": "bool_equals", "key": "data.actionable", "value": True},
                    {"operator": "is_not_null", "key": "data.owner"},
                ],
                "retry": {"max_delivery_attempts": 8, "event_time_to_live_in_minutes": 120},
                "delivery_identity": {
                    "type": "UserAssigned",
                    "user_assigned_identity_resource_id": UAMI_ID,
                },
                "destination_preauthorized": True,
                "dead_letter": {
                    "storage_account_resource_id": STORAGE_ID,
                    "blob_container_name": "dead-letter",
                    "identity": {
                        "type": "UserAssigned",
                        "user_assigned_identity_resource_id": UAMI_ID,
                    },
                    "preauthorized": True,
                },
            },
        ],
    )
    assert handle.startswith("event_bus/")
    topic_identity = mgmt.topics.create_calls[0]["parameters"].identity
    assert topic_identity.type == "SystemAssigned, UserAssigned"
    assert UAMI_ID in topic_identity.user_assigned_identities

    parameters = mgmt.topic_event_subscriptions.create_calls[0]["parameters"]
    assert parameters.destination is None
    assert (
        type(parameters.delivery_with_resource_identity.destination).__name__ == "WebHookEventSubscriptionDestination"
    )
    assert parameters.delivery_with_resource_identity.identity.type == "UserAssigned"
    assert parameters.delivery_with_resource_identity.identity.user_assigned_identity == UAMI_ID
    assert parameters.filter.included_event_types == ["Triage.Created"]
    assert len(parameters.filter.advanced_filters) == 4
    assert parameters.retry_policy.max_delivery_attempts == 8
    assert parameters.dead_letter_destination is None
    assert parameters.dead_letter_with_resource_identity.identity.user_assigned_identity == UAMI_ID


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"unknown": True}, "unsupported Event Grid config"),
        ({"topic_name": "bad_name"}, "interior hyphens"),
        ({"input_schema": "CustomEventSchema"}, "custom mapping"),
        ({"public_network_access": "Disabled"}, "Private Endpoint"),
        ({"inbound_ip_rules": ["not-an-ip"]}, "not an IP"),
        ({"prune_subscriptions": True}, "confirm_message_loss"),
        ({"access_mode": "admin"}, "publish or manage"),
        (
            {
                "subscriptions": [
                    {"name": "events", "destination": {"type": "webhook", "endpoint_url": "http://example.com"}}
                ]
            },
            "absolute HTTPS",
        ),
        (
            {
                "subscriptions": [
                    {
                        "name": "events",
                        "destination": {
                            "type": "webhook",
                            "endpoint_url": "https://example.com/events?sig=secret",
                        },
                    },
                ],
            },
            "secret query",
        ),
        (
            {
                "subscriptions": [
                    {"name": "events", "destination": {"type": "storage_queue", "resource_id": STORAGE_ID}}
                ]
            },
            "queue_name",
        ),
        (
            {"subscriptions": [{"name": "events", "destination": {"type": "unknown"}}]},
            "unsupported destination",
        ),
        (
            {
                "subscriptions": [
                    {
                        "name": "events",
                        "destination": {"type": "webhook", "endpoint_url": "https://example.com"},
                        "retry": {"max_delivery_attempts": 31},
                    },
                ],
            },
            "between 1 and 30",
        ),
        (
            {
                "subscriptions": [
                    {
                        "name": "events",
                        "destination": {"type": "webhook", "endpoint_url": "https://example.com"},
                        "advanced_filters": [{"operator": "string_in", "key": "data.x", "values": []}],
                    },
                ],
            },
            "non-empty values",
        ),
        (
            {
                "subscriptions": [
                    {
                        "name": "events",
                        "destination": {
                            "type": "webhook",
                            "endpoint_url": "https://example.com",
                            "delivery_attributes": [
                                {"name": "x-custom-secret", "type": "static", "value": "secret", "is_secret": True},
                            ],
                        },
                    },
                ],
            },
            "must not be stored",
        ),
        (
            {
                "subscriptions": [
                    {
                        "name": "events",
                        "destination": {
                            "type": "webhook",
                            "endpoint_url": "https://example.com",
                            "delivery_attributes": [
                                {"name": "authorization", "type": "static", "value": "not-marked-secret"},
                            ],
                        },
                    },
                ],
            },
            "external secret-aware integration",
        ),
        (
            {
                "subscriptions": [
                    {
                        "name": "events",
                        "destination": {
                            "type": "namespace_topic",
                            "resource_id": EVENT_HUB_ID,
                            "delivery_attributes": [],
                        },
                    },
                ],
            },
            "unsupported namespace_topic destination fields",
        ),
        (
            {
                "system_assigned_identity": True,
                "subscriptions": [
                    {
                        "name": "events",
                        "destination": {"type": "event_hub", "resource_id": EVENT_HUB_ID},
                        "delivery_identity": {"type": "SystemAssigned"},
                    },
                ],
            },
            "preauthorized",
        ),
        (
            {
                "subscriptions": [
                    {
                        "name": "events",
                        "destination": {"type": "event_hub", "resource_id": EVENT_HUB_ID},
                        "delivery_identity": {"type": "SystemAssigned"},
                        "destination_preauthorized": True,
                    },
                ],
            },
            "managed destination-role lifecycle",
        ),
    ],
)
def test_invalid_configs_fail_before_cloud_calls(config: dict[str, Any], message: str) -> None:
    driver, mgmt, _ = _driver()
    result = driver.provision(_spec(**config))
    assert not result.ok
    assert message in result.message
    assert not mgmt.topics.create_calls


def test_prune_is_explicit_and_only_removes_managed_subscriptions() -> None:
    driver, mgmt, _ = _driver()
    handle = _provisioned(
        driver,
        subscriptions=[
            {"name": "keep", "destination": {"type": "webhook", "endpoint_url": "https://example.com/keep"}},
            {"name": "remove", "destination": {"type": "webhook", "endpoint_url": "https://example.com/remove"}},
        ],
    )
    topic_name = handle.split("/", 1)[1]
    external_name = "astrolift-external-looking"
    mgmt.topic_event_subscriptions.values[(topic_name, external_name)] = SimpleNamespace(
        name=external_name,
        labels=[],
    )

    result = driver.update(
        UpdateSpec(
            handle,
            config={
                "subscriptions": [
                    {"name": "keep", "destination": {"type": "webhook", "endpoint_url": "https://example.com/keep"}},
                ],
                "prune_subscriptions": True,
                "confirm_message_loss": True,
            },
        ),
    )
    assert result.ok, result
    deleted = {name for _, name in mgmt.topic_event_subscriptions.delete_calls}
    assert external_name not in deleted
    assert any(name.startswith("astrolift-remove-") for name in deleted)


def test_deprovision_four_corner_guards_locks_and_external_subscriptions() -> None:
    driver, mgmt, locks = _driver()
    handle = _provisioned(
        driver,
        subscriptions=[
            {"name": "managed", "destination": {"type": "webhook", "endpoint_url": "https://example.com"}},
        ],
    )
    topic_name = handle.split("/", 1)[1]

    retained = driver.deprovision(DeprovisionSpec(handle))
    assert not retained.ok and retained.errors == ["delete_data_required"]

    locks.management_locks.values.append(SimpleNamespace(name="protect"))
    locked = driver.deprovision(DeprovisionSpec(handle), delete_data=True)
    assert not locked.ok and locked.errors == ["resource_lock_present"]
    forced_with_lock = driver.deprovision(
        DeprovisionSpec(handle),
        delete_data=False,
        force_destroy=True,
    )
    assert not forced_with_lock.ok and forced_with_lock.errors == ["delete_data_required"]
    assert locks.management_locks.delete_calls == []

    external_name = "astrolift-external-looking"
    mgmt.topic_event_subscriptions.values[(topic_name, external_name)] = SimpleNamespace(
        name=external_name,
        labels=[],
    )
    external = driver.deprovision(DeprovisionSpec(handle), delete_data=True)
    assert not external.ok and external.errors == ["external_subscriptions_present"]

    deleted = driver.deprovision(DeprovisionSpec(handle), delete_data=True, force_destroy=True)
    assert deleted.ok
    assert locks.management_locks.delete_calls == ["protect"]
    assert topic_name in mgmt.topics.delete_calls
    assert (topic_name, external_name) in mgmt.topic_event_subscriptions.delete_calls
    again = driver.deprovision(DeprovisionSpec(handle), delete_data=True)
    assert again.ok and "already gone" in again.message


def test_collision_and_binding_ownership_are_rejected() -> None:
    driver, mgmt, _ = _driver()
    spec = _spec(topic_name="shared-topic")
    mgmt.topics.values["shared-topic"] = SimpleNamespace(
        name="shared-topic",
        tags={"owner": "someone-else"},
        provisioning_state="Succeeded",
        endpoint="https://example.com",
    )
    result = driver.provision(spec)
    assert not result.ok and "does not own" in result.message

    mgmt.topics.values["shared-topic"].tags = {
        "astrolift.io/managed-by": "platform",
        "astrolift.io/binding": "other-binding",
    }
    result = driver.provision(spec)
    assert not result.ok and "different managed-service binding" in result.message


def test_forged_handle_cannot_update_delete_or_bind_an_external_topic() -> None:
    driver, mgmt, _ = _driver()
    mgmt.topics.values["external-topic"] = SimpleNamespace(
        name="external-topic",
        tags={"owner": "outside"},
        input_schema="CloudEventSchemaV1_0",
        endpoint="https://external.example.com",
        provisioning_state="Succeeded",
    )
    handle = "event_bus/external-topic"
    update = driver.update(UpdateSpec(handle, config={"minimum_tls_version_allowed": "1.2"}))
    assert not update.ok and "does not own" in update.message
    delete = driver.deprovision(DeprovisionSpec(handle), delete_data=True, force_destroy=True)
    assert not delete.ok and "does not own" in delete.message
    with pytest.raises(AzureEventGridError, match="does not own"):
        driver.binding(ServiceHandle(handle))


def test_immutable_schema_drift_and_explicit_identity_removal() -> None:
    driver, mgmt, _ = _driver()
    handle = _provisioned(driver, topic_user_assigned_identity_resource_ids=[UAMI_ID])
    topic_name = handle.split("/", 1)[1]
    partial = driver.update(UpdateSpec(handle, config={"minimum_tls_version_allowed": "1.2"}))
    assert partial.ok, partial
    changed = driver.update(UpdateSpec(handle, config={"input_schema": "EventGridSchema"}))
    assert not changed.ok and "reprovision required" in changed.message

    removed = driver.update(
        UpdateSpec(
            handle,
            config={
                "system_assigned_identity": False,
                "topic_user_assigned_identity_resource_ids": [],
            },
        ),
    )
    assert removed.ok, removed
    assert mgmt.topics.values[topic_name].identity.type == "None"


def test_full_reconcile_removes_omitted_identity_and_reserves_ownership_label() -> None:
    driver, mgmt, _ = _driver()
    handle = _provisioned(
        driver,
        topic_user_assigned_identity_resource_ids=[UAMI_ID],
        subscriptions=[
            {"name": "managed", "destination": {"type": "webhook", "endpoint_url": "https://example.com"}},
        ],
    )
    topic_name = handle.split("/", 1)[1]

    reconciled = driver.provision(_spec())
    assert reconciled.ok, reconciled
    assert mgmt.topics.values[topic_name].identity.type == "None"
    subscription = next(iter(mgmt.topic_event_subscriptions.values.values()))
    assert "astrolift-managed" in subscription.labels

    reserved = driver.provision(
        _spec(
            subscriptions=[
                {
                    "name": "owned",
                    "destination": {"type": "webhook", "endpoint_url": "https://example.com"},
                    "labels": ["astrolift-managed"],
                },
            ],
        ),
    )
    assert not reserved.ok and "reserved" in reserved.message


def test_status_update_missing_invalid_handle_and_snapshot_contract() -> None:
    driver, mgmt, _ = _driver()
    missing = driver.update(UpdateSpec("event_bus/missing-topic", config={}))
    assert not missing.ok and missing.errors == ["not_found"]
    invalid = driver.update(UpdateSpec("topic/nope", config={}))
    assert not invalid.ok and invalid.errors == ["invalid_handle"]

    handle = _provisioned(driver)
    topic_name = handle.split("/", 1)[1]
    mgmt.topics.values[topic_name].provisioning_state = "Updating"
    status = driver.status(ServiceHandle(handle))
    assert status.state == "updating"

    with pytest.raises(AzureEventGridError, match="no snapshot"):
        driver.snapshot(ServiceHandle(handle))
    with pytest.raises(AzureEventGridError, match="cannot be restored"):
        driver.restore(
            SnapshotHandle(handle, "snapshot", "now"),
            _spec(),
        )


def test_schemas_and_config_constructor_guards() -> None:
    driver, _, _ = _driver()
    schema = driver.config_schema()
    assert schema["additionalProperties"] is False
    assert "subscriptions" in schema["properties"]
    assert "EVENT_BUS_ENDPOINT" in driver.binding_schema().env_vars
    assert "subscriptions" in driver.editable_fields()

    with pytest.raises(ValueError, match="input schema"):
        AzureEventGridConfig(SUBSCRIPTION_ID, RESOURCE_GROUP, default_input_schema="CustomEventSchema")
    with pytest.raises(ValueError, match="public-network"):
        AzureEventGridConfig(SUBSCRIPTION_ID, RESOURCE_GROUP, public_network_access_default="Private")


def test_generated_names_are_stable_distinct_and_within_azure_limits() -> None:
    driver, _, _ = _driver()
    first = driver.provision(_spec())
    second = driver.provision(_spec())
    assert first.handle == second.handle
    assert len(first.handle.split("/", 1)[1]) <= 50

    changed = ProvisionSpec(**{**_spec().__dict__, "environment_id": "other-env", "environment_name": "stage"})
    third_driver, _, _ = _driver()
    third = third_driver.provision(changed)
    assert third.handle != first.handle
