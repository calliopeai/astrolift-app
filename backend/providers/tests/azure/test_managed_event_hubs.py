from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, SnapshotHandle, UpdateSpec
from _sdk.managed_service_tags import canonical_key
from azure.managed.event_hubs import (
    AzureEventHubsConfig,
    AzureEventHubsDriver,
    AzureEventHubsError,
)


class ResourceNotFoundError(Exception):
    status_code = 404


@dataclass
class FakePoller:
    value: Any = None
    error: Exception | None = None
    waited: bool = False

    def result(self) -> Any:
        self.waited = True
        if self.error:
            raise self.error
        return self.value


@dataclass
class FakeNamespaces:
    values: dict[str, Any] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)
    network_calls: list[dict[str, Any]] = field(default_factory=list)
    get_error: Exception | None = None
    delete_error: Exception | None = None

    def get(self, *, resource_group_name: str, namespace_name: str) -> Any:
        if self.get_error:
            raise self.get_error
        if namespace_name not in self.values:
            raise ResourceNotFoundError(namespace_name)
        return self.values[namespace_name]

    def begin_create_or_update(
        self,
        *,
        resource_group_name: str,
        namespace_name: str,
        parameters: Any,
    ) -> FakePoller:
        self.create_calls.append({"namespace_name": namespace_name, "parameters": parameters})
        payload = parameters.as_dict()
        properties = payload.get("properties", {})
        namespace = SimpleNamespace(
            name=namespace_name,
            tags=dict(payload.get("tags", {})),
            sku=SimpleNamespace(**payload["sku"]),
            properties=SimpleNamespace(
                status="Active",
                kafka_enabled=properties.get("kafkaEnabled", False),
                zone_redundant=properties.get("zoneRedundant", False),
                disable_local_auth=properties.get("disableLocalAuth", False),
            ),
        )
        self.values[namespace_name] = namespace
        return FakePoller(namespace)

    def update(
        self,
        *,
        resource_group_name: str,
        namespace_name: str,
        parameters: Any,
    ) -> Any:
        self.update_calls.append({"namespace_name": namespace_name, "parameters": parameters})
        namespace = self.values[namespace_name]
        payload = parameters.as_dict()
        if "tags" in payload:
            namespace.tags = dict(payload["tags"])
        if "sku" in payload:
            namespace.sku = SimpleNamespace(**payload["sku"])
        return namespace

    def create_or_update_network_rule_set(
        self,
        *,
        resource_group_name: str,
        namespace_name: str,
        parameters: Any,
    ) -> Any:
        self.network_calls.append({"namespace_name": namespace_name, "parameters": parameters})
        return parameters

    def begin_delete(self, *, resource_group_name: str, namespace_name: str) -> FakePoller:
        self.delete_calls.append(namespace_name)
        if self.delete_error:
            return FakePoller(error=self.delete_error)
        self.values.pop(namespace_name, None)
        return FakePoller()


@dataclass
class FakeEventHubs:
    values: dict[tuple[str, str], Any] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    get_error: Exception | None = None

    def get(
        self,
        *,
        resource_group_name: str,
        namespace_name: str,
        event_hub_name: str,
    ) -> Any:
        if self.get_error:
            raise self.get_error
        key = (namespace_name, event_hub_name)
        if key not in self.values:
            raise ResourceNotFoundError(str(key))
        return self.values[key]

    def create_or_update(
        self,
        *,
        resource_group_name: str,
        namespace_name: str,
        event_hub_name: str,
        parameters: Any,
    ) -> Any:
        self.create_calls.append(
            {
                "namespace_name": namespace_name,
                "event_hub_name": event_hub_name,
                "parameters": parameters,
            },
        )
        payload = parameters.as_dict()["properties"]
        retention = payload.get("retentionDescription", {})
        capture = payload.get("captureDescription")
        value = SimpleNamespace(
            name=event_hub_name,
            properties=SimpleNamespace(
                partition_count=payload.get("partitionCount", 2),
                status=payload.get("status", "Active"),
                retention_description=SimpleNamespace(
                    cleanup_policy=retention.get("cleanupPolicy", "Delete"),
                    retention_time_in_hours=retention.get("retentionTimeInHours", 24),
                    min_compaction_lag_time_in_minutes=retention.get("minCompactionLagTimeInMinutes"),
                    tombstone_retention_time_in_hours=retention.get("tombstoneRetentionTimeInHours"),
                ),
                capture_description=capture,
                user_metadata=payload.get("userMetadata", ""),
            ),
        )
        self.values[(namespace_name, event_hub_name)] = value
        return value


@dataclass
class FakeConsumerGroups:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create_or_update(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return kwargs["parameters"]


@dataclass
class FakeMgmt:
    namespaces: FakeNamespaces = field(default_factory=FakeNamespaces)
    event_hubs: FakeEventHubs = field(default_factory=FakeEventHubs)
    consumer_groups: FakeConsumerGroups = field(default_factory=FakeConsumerGroups)


@dataclass
class FakeLock:
    name: str


@dataclass
class FakeManagementLocks:
    locks: list[FakeLock] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    list_error: Exception | None = None

    def list_at_resource_level(self, **kwargs: Any) -> list[FakeLock]:
        if self.list_error:
            raise self.list_error
        return list(self.locks)

    def delete_at_resource_level(self, *, lock_name: str, **kwargs: Any) -> None:
        self.deleted.append(lock_name)
        self.locks = [item for item in self.locks if item.name != lock_name]


@dataclass
class FakeLocks:
    management_locks: FakeManagementLocks = field(default_factory=FakeManagementLocks)


def _spec(**overrides: Any) -> ProvisionSpec:
    values = {
        "organization_id": "org-id",
        "organization_slug": "acme",
        "app_id": "app-id",
        "app_slug": "api",
        "environment_id": "env-id",
        "environment_name": "prod",
        "tenant_cluster_id": "azure-prod",
        "service_handle_hint": "events",
        "size": "small",
        "binding_id": "binding-id",
        "managed_service_id": "service-id",
        "config": {},
    }
    values.update(overrides)
    return ProvisionSpec(**values)


EVENT_HUBS_UAMI_ID = (
    "/subscriptions/00000000-1111-2222-3333-444444444444/resourceGroups/rg-events/"
    "providers/Microsoft.ManagedIdentity/userAssignedIdentities/event-hubs"
)
FOREIGN_UAMI_ID = (
    "/subscriptions/00000000-1111-2222-3333-444444444444/resourceGroups/rg-other-tenant/"
    "providers/Microsoft.ManagedIdentity/userAssignedIdentities/their-identity"
)


def _driver(
    variant: str,
    *,
    mgmt: FakeMgmt | None = None,
    locks: FakeLocks | None = None,
    **config: Any,
) -> tuple[AzureEventHubsDriver, FakeMgmt, FakeLocks]:
    mgmt = mgmt or FakeMgmt()
    locks = locks or FakeLocks()
    driver = AzureEventHubsDriver(
        config=AzureEventHubsConfig(
            subscription_id="00000000-1111-2222-3333-444444444444",
            resource_group="rg-events",
            variant=variant,
            location="eastus2",
            mgmt_client=mgmt,
            locks_client=locks,
            **{"allowed_identity_resource_ids": (EVENT_HUBS_UAMI_ID,), **config},
        ),
    )
    return driver, mgmt, locks


@pytest.mark.parametrize(
    ("variant", "kind", "binding_keys"),
    [
        ("event_hubs", "stream", {"STREAM_NAME", "STREAM_ENDPOINT", "EVENTHUB_CONSUMER_GROUP"}),
        (
            "event_hubs_kafka",
            "event_stream",
            {"EVENT_STREAM_BROKERS", "EVENT_STREAM_TLS", "EVENT_STREAM_AUTH_MECHANISM"},
        ),
    ],
)
def test_provision_and_binding_match_portable_profile(
    variant: str,
    kind: str,
    binding_keys: set[str],
) -> None:
    driver, mgmt, _ = _driver(variant)
    result = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(result.handle))

    assert result.ok and result.ready and result.handle.startswith(f"{kind}/")
    assert binding_keys <= set(binding.env_vars)
    assert set(driver.binding_schema().env_vars) == set(binding.env_vars)
    assert all(value.secret_ref is None for value in binding.env_vars.values())
    assert {action for grant in binding.iam_grants for action in grant.actions} == {
        "Azure Event Hubs Data Sender",
        "Azure Event Hubs Data Receiver",
    }
    payload = mgmt.namespaces.create_calls[0]["parameters"].as_dict()
    assert payload["properties"]["kafkaEnabled"] is (variant == "event_hubs_kafka")
    assert payload["properties"]["disableLocalAuth"] is True
    assert payload["tags"][canonical_key("azure")] == "service-id"


def test_reconcile_is_idempotent_partial_and_ownership_safe() -> None:
    driver, mgmt, _ = _driver("event_hubs")
    first = driver.provision(_spec())
    second = driver.provision(_spec())
    assert first.ok and second.ok and first.handle == second.handle
    assert len(mgmt.namespaces.create_calls) == 1
    assert len(mgmt.event_hubs.create_calls) == 1
    update = mgmt.namespaces.update_calls[-1]["parameters"].as_dict()
    assert update["tags"][canonical_key("azure")] == "service-id"
    assert update.get("properties", {}) == {}

    namespace_name = first.handle.split("/")[1]
    mgmt.namespaces.values[namespace_name].tags[canonical_key("azure")] = "other"
    before = len(mgmt.namespaces.update_calls)
    rejected = driver.provision(_spec())
    assert not rejected.ok and "belongs to managed service other, not service-id" in rejected.message
    assert len(mgmt.namespaces.update_calls) == before


_CAPTURE_STORAGE = (
    "/subscriptions/00000000-1111-2222-3333-444444444444/resourceGroups/rg-events/"
    "providers/Microsoft.Storage/storageAccounts/capture"
)


@pytest.mark.parametrize(
    "config",
    [
        pytest.param(
            {
                "sku": "Standard",
                "capture_enabled": True,
                "capture_storage_account_resource_id": _CAPTURE_STORAGE,
                "capture_blob_container": "events",
                "capture_identity_type": "UserAssigned",
                "capture_user_assigned_identity_resource_id": FOREIGN_UAMI_ID,
            },
            id="capture",
        ),
        # The Capture identity is attached to the namespace whenever it is
        # set, even with Capture off and no identity type chosen.
        pytest.param(
            {"capture_enabled": False, "capture_user_assigned_identity_resource_id": FOREIGN_UAMI_ID},
            id="capture-off",
        ),
        pytest.param(
            {
                "sku": "Premium",
                "customer_managed_key_name": "event-hubs",
                "customer_managed_key_vault_uri": "https://keys.vault.azure.net/",
                "customer_managed_identity_resource_id": FOREIGN_UAMI_ID,
            },
            id="customer-managed-key",
        ),
    ],
)
def test_a_namespace_cannot_attach_an_unlisted_identity(config: dict[str, Any]) -> None:
    driver, mgmt, _ = _driver("event_hubs")

    result = driver.provision(_spec(config=config))

    assert not result.ok
    assert "eventhubs_allowed_identity_resource_ids" in result.message
    assert mgmt.namespaces.create_calls == []


def test_no_allowlist_refuses_every_config_supplied_identity() -> None:
    driver, mgmt, _ = _driver("event_hubs", allowed_identity_resource_ids=())

    result = driver.provision(
        _spec(config={"capture_enabled": False, "capture_user_assigned_identity_resource_id": EVENT_HUBS_UAMI_ID}),
    )

    assert not result.ok and "eventhubs_allowed_identity_resource_ids" in result.message
    assert mgmt.namespaces.create_calls == []


def test_provision_exposes_scaling_retention_compaction_capture_network_and_cmk() -> None:
    driver, mgmt, _ = _driver("event_hubs")
    identity = (
        "/subscriptions/00000000-1111-2222-3333-444444444444/resourceGroups/rg-events/"
        "providers/Microsoft.ManagedIdentity/userAssignedIdentities/event-hubs"
    )
    storage = (
        "/subscriptions/00000000-1111-2222-3333-444444444444/resourceGroups/rg-events/"
        "providers/Microsoft.Storage/storageAccounts/capture"
    )
    subnet = (
        "/subscriptions/00000000-1111-2222-3333-444444444444/resourceGroups/rg-network/"
        "providers/Microsoft.Network/virtualNetworks/data/subnets/events"
    )
    result = driver.provision(
        _spec(
            config={
                "sku": "Premium",
                "capacity": 2,
                "zone_redundant": True,
                "minimum_tls_version": "1.3",
                "disable_local_auth": True,
                "network_default_action": "Deny",
                "trusted_service_access_enabled": True,
                "ip_rules": ["203.0.113.0/24"],
                "virtual_network_rule_ids": [subnet],
                "partition_count": 40,
                "cleanup_policy": "DeleteOrCompact",
                "retention_time_in_hours": 720,
                "min_compaction_lag_time_in_minutes": 5,
                "tombstone_retention_time_in_hours": 24,
                "consumer_groups": ["analytics", "billing"],
                "capture_enabled": True,
                "capture_storage_account_resource_id": storage,
                "capture_blob_container": "events",
                "capture_identity_type": "UserAssigned",
                "capture_user_assigned_identity_resource_id": identity,
                "customer_managed_key_name": "event-hubs",
                "customer_managed_key_vault_uri": "https://keys.vault.azure.net/",
                "customer_managed_key_version": "v1",
                "customer_managed_identity_resource_id": identity,
                "require_infrastructure_encryption": True,
            },
        ),
    )
    assert result.ok
    namespace = mgmt.namespaces.create_calls[0]["parameters"].as_dict()
    assert namespace["sku"] == {"name": "Premium", "tier": "Premium", "capacity": 2}
    assert namespace["identity"]["type"] == "UserAssigned"
    assert namespace["properties"]["encryption"]["requireInfrastructureEncryption"] is True
    network = mgmt.namespaces.network_calls[0]["parameters"].as_dict()["properties"]
    assert network["defaultAction"] == "Deny"
    assert network["ipRules"] == [{"ipMask": "203.0.113.0/24", "action": "Allow"}]
    hub = mgmt.event_hubs.create_calls[0]["parameters"].as_dict()["properties"]
    assert hub["partitionCount"] == 40
    assert hub["retentionDescription"]["cleanupPolicy"] == "DeleteOrCompact"
    assert hub["captureDescription"]["destination"]["properties"]["blobContainer"] == "events"
    groups = {call["consumer_group_name"] for call in mgmt.consumer_groups.calls}
    assert groups == {"astrolift", "analytics", "billing"}


@pytest.mark.parametrize(
    ("variant", "config", "message"),
    [
        ("event_hubs", {"sku": "Unknown"}, "unsupported"),
        ("event_hubs_kafka", {"sku": "Basic"}, "requires Standard"),
        ("event_hubs", {"auto_inflate_enabled": True, "sku": "Premium"}, "only on Standard"),
        ("event_hubs", {"maximum_throughput_units": 10}, "requires auto_inflate"),
        ("event_hubs", {"public_network_access": "Disabled"}, "Private Endpoint"),
        ("event_hubs", {"network_default_action": "Deny"}, "requires an IP"),
        ("event_hubs", {"ip_rules": ["bad-ip"]}, "not an IP"),
        ("event_hubs", {"virtual_network_rule_ids": ["bad-id"]}, "ARM resource"),
        ("event_hubs", {"partition_count": 40}, "between 1 and 32"),
        ("event_hubs", {"sku": "Basic", "cleanup_policy": "Compact"}, "requires Standard"),
        ("event_hubs", {"retention_time_in_hours": 200}, "retention"),
        ("event_hubs", {"cleanup_policy": "Delete", "retention_time_in_hours": -1}, "infinite"),
        ("event_hubs", {"consumer_groups": ["same", "same"]}, "unique"),
        ("event_hubs_kafka", {"consumer_groups": ["client-owned"]}, "client-managed"),
        ("event_hubs", {"sku": "Basic", "consumer_groups": ["extra"]}, "built-in $Default"),
        ("event_hubs", {"capture_enabled": True}, "full storage account"),
        ("event_hubs", {"capture_interval_seconds": 60}, "full storage account"),
        (
            "event_hubs",
            {
                "capture_enabled": True,
                "capture_storage_account_resource_id": "/subscriptions/x",
                "capture_blob_container": "events",
            },
            "pre-authorized UserAssigned",
        ),
        (
            "event_hubs",
            {"capture_enabled": True, "capture_storage_account_resource_id": "bad", "capture_blob_container": "x"},
            "ARM resource",
        ),
        ("event_hubs", {"customer_managed_key_name": "key"}, "requires key name"),
        (
            "event_hubs",
            {
                "customer_managed_key_name": "key",
                "customer_managed_key_vault_uri": "https://vault/",
                "customer_managed_identity_resource_id": "/subscriptions/x",
            },
            "requires Premium",
        ),
        ("event_hubs", {"unknown": True}, "unsupported Event Hubs config"),
    ],
)
def test_invalid_controls_fail_before_cloud_mutation(
    variant: str,
    config: dict[str, Any],
    message: str,
) -> None:
    driver, mgmt, _ = _driver(variant)
    result = driver.provision(_spec(config=config))
    assert not result.ok and message in result.message
    assert not mgmt.namespaces.create_calls


def test_update_is_partial_and_standard_partition_change_fails_closed() -> None:
    driver, mgmt, _ = _driver("event_hubs")
    provisioned = driver.provision(_spec())
    partition = driver.update(
        UpdateSpec(provisioned.handle, config={"partition_count": 3}, managed_service_id="service-id")
    )
    assert not partition.ok and "Premium" in partition.message

    updated = driver.update(
        UpdateSpec(
            provisioned.handle,
            config={
                "capacity": 2,
                "network_default_action": "Deny",
                "ip_rules": ["203.0.113.1"],
                "retention_time_in_hours": 48,
                "consumer_groups": ["extra"],
            },
            managed_service_id="service-id",
        ),
    )
    assert updated.ok
    namespace_update = mgmt.namespaces.update_calls[-1]["parameters"].as_dict()
    assert namespace_update["sku"]["capacity"] == 2
    assert "kafkaEnabled" not in namespace_update.get("properties", {})
    hub_update = mgmt.event_hubs.create_calls[-1]["parameters"].as_dict()["properties"]
    assert hub_update["retentionDescription"]["retentionTimeInHours"] == 48
    assert any(call["consumer_group_name"] == "extra" for call in mgmt.consumer_groups.calls)


def test_size_update_scales_namespace_without_repartitioning_stream() -> None:
    driver, mgmt, _ = _driver("event_hubs")
    provisioned = driver.provision(_spec())
    hub_calls = len(mgmt.event_hubs.create_calls)

    updated = driver.update(UpdateSpec(provisioned.handle, size="large", managed_service_id="service-id"))

    assert updated.ok
    namespace_update = mgmt.namespaces.update_calls[-1]["parameters"].as_dict()
    assert namespace_update["sku"] == {"name": "Standard", "tier": "Standard", "capacity": 4}
    assert len(mgmt.event_hubs.create_calls) == hub_calls


def test_capacity_update_preserves_existing_premium_tier() -> None:
    driver, mgmt, _ = _driver("event_hubs")
    provisioned = driver.provision(_spec(config={"sku": "Premium", "capacity": 2}))

    updated = driver.update(UpdateSpec(provisioned.handle, config={"capacity": 3}, managed_service_id="service-id"))

    assert updated.ok
    payload = mgmt.namespaces.update_calls[-1]["parameters"].as_dict()
    assert payload["sku"] == {"name": "Premium", "tier": "Premium", "capacity": 3}

    rejected = driver.update(UpdateSpec(provisioned.handle, config={"capacity": 20}, managed_service_id="service-id"))
    assert not rejected.ok and "between 1 and 16" in rejected.message


def test_basic_uses_builtin_consumer_group_without_creating_another() -> None:
    driver, mgmt, _ = _driver("event_hubs")
    provisioned = driver.provision(_spec(config={"sku": "Basic"}))
    binding = driver.binding(ServiceHandle(provisioned.handle))

    assert provisioned.ok
    assert not mgmt.consumer_groups.calls
    assert binding.env_vars["EVENTHUB_CONSUMER_GROUP"].literal == "$Default"


def test_kafka_reconcile_refuses_namespace_without_kafka_capability() -> None:
    driver, mgmt, _ = _driver("event_hubs_kafka")
    provisioned = driver.provision(_spec())
    namespace_name = provisioned.handle.split("/")[1]
    mgmt.namespaces.values[namespace_name].properties.kafka_enabled = False

    result = driver.provision(_spec())

    assert not result.ok and "does not expose Kafka" in result.message


def test_deprovision_requires_explicit_data_loss_and_respects_locks() -> None:
    driver, mgmt, locks = _driver("event_hubs")
    provisioned = driver.provision(_spec())
    refused = driver.deprovision(DeprovisionSpec(provisioned.handle, managed_service_id="service-id"))
    assert not refused.ok and refused.retryable is False
    assert not mgmt.namespaces.delete_calls

    locks.management_locks.locks = [FakeLock("protect-stream")]
    locked = driver.deprovision(DeprovisionSpec(provisioned.handle, managed_service_id="service-id"), delete_data=True)
    assert not locked.ok and "protect-stream" in locked.message
    deleted = driver.deprovision(
        DeprovisionSpec(provisioned.handle, managed_service_id="service-id"),
        delete_data=True,
        force_destroy=True,
    )
    assert deleted.ok
    assert locks.management_locks.deleted == ["protect-stream"]
    assert mgmt.namespaces.delete_calls
    assert driver.deprovision(DeprovisionSpec(provisioned.handle, managed_service_id="service-id"), delete_data=True).ok


def test_status_distinguishes_namespace_hub_and_provider_state() -> None:
    driver, mgmt, _ = _driver("event_hubs")
    assert driver.status(ServiceHandle("stream/missing/hub")).state == "deprovisioned"
    provisioned = driver.provision(_spec())
    namespace_name, event_hub_name = provisioned.handle.split("/")[1:]
    mgmt.event_hubs.values.pop((namespace_name, event_hub_name))
    assert driver.status(ServiceHandle(provisioned.handle)).state == "error"
    driver.provision(_spec())
    mgmt.event_hubs.values[(namespace_name, event_hub_name)].properties.status = "Deleting"
    assert driver.status(ServiceHandle(provisioned.handle)).state == "deprovisioning"


def test_non_404_provider_error_is_not_misreported_as_missing() -> None:
    driver, mgmt, _ = _driver("event_hubs")
    mgmt.namespaces.get_error = RuntimeError("control plane unavailable")
    with pytest.raises(RuntimeError, match="control plane unavailable"):
        driver.status(ServiceHandle("stream/ns/hub"))


@pytest.mark.parametrize("variant", ["event_hubs", "event_hubs_kafka"])
def test_schema_is_closed_and_snapshot_contract_is_honest(variant: str) -> None:
    driver, _, _ = _driver(variant)
    schema = driver.config_schema()
    assert schema["additionalProperties"] is False
    assert {
        "sku",
        "partition_count",
        "cleanup_policy",
        "capture_enabled",
        "virtual_network_rule_ids",
        "customer_managed_key_name",
    } <= set(schema["properties"])
    with pytest.raises(AzureEventHubsError, match="no exact stream snapshot"):
        driver.snapshot(ServiceHandle(f"{driver._profile.kind}/ns/hub"))
    with pytest.raises(AzureEventHubsError, match="cannot restore"):
        driver.restore(SnapshotHandle("x", "y", "2026-08-14T00:00:00Z"), _spec())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("variant", "unknown"),
        ("default_sku", "unknown"),
        ("default_capacity", 0),
        ("public_network_access_default", "Public"),
    ],
)
def test_install_config_rejects_invalid_defaults(field: str, value: Any) -> None:
    values = {
        "subscription_id": "sub",
        "resource_group": "rg",
        "variant": "event_hubs",
    }
    values[field] = value
    with pytest.raises(ValueError):
        AzureEventHubsConfig(**values)


def test_names_are_bounded_collision_resistant_and_handles_are_strict() -> None:
    driver, _, _ = _driver("event_hubs", namespace_name_prefix="X" * 100)
    first = driver.provision(_spec(managed_service_id="one"))
    second = driver.provision(_spec(managed_service_id="two"))
    first_namespace = first.handle.split("/")[1]
    second_namespace = second.handle.split("/")[1]
    assert len(first_namespace) <= 50 and first_namespace != second_namespace
    with pytest.raises(AzureEventHubsError, match="invalid event_hubs handle"):
        driver.binding(ServiceHandle("event_stream/wrong/hub"))
