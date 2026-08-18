from __future__ import annotations

import copy
import json
import re
from typing import Any

import pytest
from astrolift_manifest.env_injection import envelope_keys_for

from _sdk import UnsupportedOperationError
from _sdk.availability import MATRIX
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from azure.managed.private_endpoint import AzurePrivateEndpointConfig, AzurePrivateEndpointDriver
from azure.plugin import PLUGIN

SUBSCRIPTION = "00000000-1111-2222-3333-444444444444"
RESOURCE_GROUP = "rg-platform"
SUBNET = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-network/providers/"
    "Microsoft.Network/virtualNetworks/platform/subnets/private-endpoints"
)
SERVICE = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/records"
ZONE = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-network/providers/"
    "Microsoft.Network/privateDnsZones/privatelink.blob.core.windows.net"
)


class ResourceNotFoundError(Exception):
    pass


class _Poller:
    def __init__(self, value: Any = None, error: Exception | None = None) -> None:
        self.value = value
        self.error = error

    def result(self) -> Any:
        if self.error:
            raise self.error
        return copy.deepcopy(self.value)


class _PrivateEndpoints:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.puts: list[tuple[str, str, dict[str, Any]]] = []
        self.deletes: list[tuple[str, str]] = []

    def get(self, resource_group: str, name: str) -> dict[str, Any]:
        del resource_group
        if name not in self.objects:
            raise ResourceNotFoundError(name)
        return copy.deepcopy(self.objects[name])

    def begin_create_or_update(self, resource_group: str, name: str, parameters: dict[str, Any]) -> _Poller:
        self.puts.append((resource_group, name, copy.deepcopy(parameters)))
        connection_key = (
            "manualPrivateLinkServiceConnections"
            if "manualPrivateLinkServiceConnections" in parameters["properties"]
            else "privateLinkServiceConnections"
        )
        rows = [
            {
                "name": row["name"],
                "properties": {
                    "privateLinkServiceId": row["properties"]["privateLinkServiceId"],
                    "groupIds": list(row["properties"]["groupIds"]),
                    "requestMessage": row["properties"]["requestMessage"],
                    "privateLinkServiceConnectionState": {
                        "status": "Pending" if connection_key.startswith("manual") else "Approved",
                    },
                },
            }
            for row in parameters["properties"][connection_key]
        ]
        resource = {
            "id": (
                f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{resource_group}/providers/"
                f"Microsoft.Network/privateEndpoints/{name}"
            ),
            "name": name,
            "location": parameters["location"],
            "tags": copy.deepcopy(parameters["tags"]),
            "properties": {
                "provisioningState": "Succeeded",
                "subnet": copy.deepcopy(parameters["properties"]["subnet"]),
                connection_key: rows,
                "customDnsConfigs": [
                    {
                        "fqdn": "records.blob.core.windows.net",
                        "ipAddresses": ["10.20.0.5"],
                    },
                ],
                "networkInterfaces": [
                    {
                        "id": (
                            f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{resource_group}/"
                            f"providers/Microsoft.Network/networkInterfaces/{name}-nic"
                        ),
                    },
                ],
            },
        }
        self.objects[name] = resource
        return _Poller(resource)

    def begin_delete(self, resource_group: str, name: str) -> _Poller:
        self.deletes.append((resource_group, name))
        if name not in self.objects:
            return _Poller(error=ResourceNotFoundError(name))
        self.objects.pop(name)
        return _Poller()


class _ZoneGroups:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.puts: list[tuple[str, str, str, dict[str, Any]]] = []
        self.deletes: list[tuple[str, str, str]] = []
        self.put_error: Exception | None = None

    def get(self, resource_group: str, endpoint: str, name: str) -> dict[str, Any]:
        del resource_group
        if (endpoint, name) not in self.objects:
            raise ResourceNotFoundError(name)
        return copy.deepcopy(self.objects[(endpoint, name)])

    def begin_create_or_update(
        self,
        resource_group: str,
        endpoint: str,
        name: str,
        parameters: dict[str, Any],
    ) -> _Poller:
        self.puts.append((resource_group, endpoint, name, copy.deepcopy(parameters)))
        if self.put_error:
            return _Poller(error=self.put_error)
        self.objects[(endpoint, name)] = copy.deepcopy(parameters)
        return _Poller(parameters)

    def begin_delete(self, resource_group: str, endpoint: str, name: str) -> _Poller:
        self.deletes.append((resource_group, endpoint, name))
        if (endpoint, name) not in self.objects:
            return _Poller(error=ResourceNotFoundError(name))
        self.objects.pop((endpoint, name))
        return _Poller()


class _Network:
    def __init__(self) -> None:
        self.private_endpoints = _PrivateEndpoints()
        self.private_dns_zone_groups = _ZoneGroups()


def _spec(**overrides: Any) -> ProvisionSpec:
    values = {
        "organization_id": "org-1",
        "organization_slug": "steadymd",
        "app_id": "app-1",
        "app_slug": "triage",
        "environment_id": "env-1",
        "environment_name": "prod",
        "tenant_cluster_id": "aks-prod",
        "service_handle_hint": "records",
        "size": "custom",
        "config": {
            "private_link_service_id": SERVICE,
            "group_ids": ["blob"],
            "private_dns_zone_ids": [ZONE],
        },
        "managed_service_id": "service-1",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _driver(client: _Network | None = None, **policy: Any) -> tuple[AzurePrivateEndpointDriver, _Network]:
    network = client or _Network()
    config = AzurePrivateEndpointConfig(
        subscription_id=SUBSCRIPTION,
        resource_group=RESOURCE_GROUP,
        location="eastus2",
        default_subnet_id=SUBNET,
        allowed_subnet_ids=(SUBNET,),
        allowed_service_id_prefixes=(f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-data",),
        allowed_private_dns_zone_id_prefixes=(
            f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-network/providers/Microsoft.Network/privateDnsZones",
        ),
        **policy,
    )
    return AzurePrivateEndpointDriver(config=config, client=network), network


def test_provision_is_idempotent_and_emits_safe_binding() -> None:
    driver, network = _driver()

    first = driver.provision(_spec())
    second = driver.provision(_spec())

    assert first.ok and first.ready and second.ok
    assert first.handle.startswith("private_endpoint/astrolift-pe-steadymd-triage-prod-records-")
    assert len(network.private_endpoints.puts) == 2
    request = network.private_endpoints.puts[0][2]
    assert request["properties"]["subnet"] == {"id": SUBNET}
    connection = request["properties"]["privateLinkServiceConnections"][0]
    assert connection["properties"]["groupIds"] == ["blob"]
    assert request["tags"]["astrolift-managed-by"] == "platform"
    assert request["tags"]["astrolift-managed-service-id"] == "service-1"
    zones = network.private_dns_zone_groups.puts[-1][3]["properties"]["privateDnsZoneConfigs"]
    assert zones == [{"name": "zone-1", "properties": {"privateDnsZoneId": ZONE}}]
    binding = driver.binding(ServiceHandle(first.handle))
    assert binding.env_vars["PRIVATE_ENDPOINT_IPS"].literal == '["10.20.0.5"]'
    assert binding.env_vars["PRIVATE_ENDPOINT_DNS"].literal == "records.blob.core.windows.net"
    assert binding.env_vars["PRIVATE_ENDPOINT_DNS_NAMES"].literal == '["records.blob.core.windows.net"]'
    assert binding.env_vars["PRIVATE_ENDPOINT_SERVICE_NAME"].literal == SERVICE
    assert not binding.iam_grants
    assert all("KEY" not in key and "TOKEN" not in key for key in binding.env_vars)


def test_manual_approval_is_explicit_and_status_stays_provisioning() -> None:
    driver, network = _driver(allow_manual_approval=True)
    cfg = dict(_spec().config)
    cfg["manual_approval"] = True

    result = driver.provision(_spec(config=cfg))

    assert result.ok and not result.ready
    assert "manualPrivateLinkServiceConnections" in network.private_endpoints.puts[-1][2]["properties"]
    assert driver.status(ServiceHandle(result.handle)).state == "provisioning"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({}, "private_link_service_id"),
        ({"private_link_service_id": "not-an-id", "group_ids": ["blob"]}, "complete Azure resource id"),
        (
            {
                "private_link_service_id": SERVICE.replace("rg-data", "rg-data-evil"),
                "group_ids": ["blob"],
            },
            "service resource-id allowlist",
        ),
        ({"private_link_service_id": SERVICE, "group_ids": []}, "group_ids"),
        ({"private_link_service_id": SERVICE, "group_ids": ["bad group"]}, "group_ids"),
        (
            {"private_link_service_id": SERVICE, "group_ids": ["blob"], "subnet_id": SUBNET + "-other"},
            "subnet_id is outside",
        ),
        (
            {"private_link_service_id": SERVICE, "group_ids": ["blob"], "manual_approval": True},
            "manual Private Link approval",
        ),
        (
            {
                "private_link_service_id": SERVICE,
                "group_ids": ["blob"],
                "private_dns_zone_ids": [ZONE.replace("rg-network", "rg-network-evil")],
            },
            "DNS zone id is outside",
        ),
        ({"private_link_service_id": SERVICE, "group_ids": ["blob"], "native": {}}, "unsupported fields"),
    ],
)
def test_policy_fails_closed(config: dict[str, Any], message: str) -> None:
    driver, network = _driver()

    result = driver.provision(_spec(config=config))

    assert result.ok is False
    assert message in result.message
    assert not network.private_endpoints.puts


def test_install_policy_requires_explicit_service_and_subnet_allowlists() -> None:
    driver = AzurePrivateEndpointDriver(
        config=AzurePrivateEndpointConfig(
            subscription_id=SUBSCRIPTION,
            resource_group=RESOURCE_GROUP,
            network_client=_Network(),
        ),
    )

    result = driver.provision(_spec())

    assert result.ok is False
    assert "explicit service resource-id allowlist" in result.message


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"allowed_service_id_prefixes": (f"/subscriptions/{SUBSCRIPTION}",)},
            "service resource-id allowlist",
        ),
        ({"allowed_subnet_ids": (SUBNET + "/nested",)}, "explicitly allowed subnet ids"),
        (
            {
                "allowed_private_dns_zone_id_prefixes": (
                    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-network/providers/"
                    "Microsoft.Network/privateDnsZones-evil",
                ),
            },
            "private DNS zone allowlist",
        ),
        ({"max_group_ids": 65}, "install limits"),
    ],
)
def test_invalid_install_policy_is_rejected(overrides: dict[str, Any], message: str) -> None:
    values: dict[str, Any] = {
        "subscription_id": SUBSCRIPTION,
        "resource_group": RESOURCE_GROUP,
        "default_subnet_id": SUBNET,
        "allowed_subnet_ids": (SUBNET,),
        "allowed_service_id_prefixes": (f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-data",),
        "allowed_private_dns_zone_id_prefixes": (
            f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-network/providers/Microsoft.Network/privateDnsZones",
        ),
        "network_client": _Network(),
    }
    values.update(overrides)
    driver = AzurePrivateEndpointDriver(config=AzurePrivateEndpointConfig(**values))

    result = driver.provision(_spec())

    assert result.ok is False
    assert message in result.message


def test_config_schema_reflects_install_policy() -> None:
    config = AzurePrivateEndpointConfig(
        subscription_id=SUBSCRIPTION,
        resource_group=RESOURCE_GROUP,
        allowed_subnet_ids=(SUBNET,),
        allowed_service_id_prefixes=(f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-data",),
        network_client=_Network(),
    )
    schema = AzurePrivateEndpointDriver(config=config).config_schema()
    properties = schema["properties"]

    assert "subnet_id" in schema["required"]
    assert properties["subnet_id"]["enum"] == [SUBNET]
    assert properties["manual_approval"]["const"] is False
    assert properties["private_dns_zone_ids"]["maxItems"] == 0
    allowed_pattern = properties["private_link_service_id"]["allOf"][0]["pattern"]
    assert re.fullmatch(allowed_pattern, SERVICE)
    assert not re.fullmatch(allowed_pattern, SERVICE.replace("rg-data", "rg-data-evil"))


def test_foreign_collision_and_immutable_changes_are_rejected_before_put() -> None:
    driver, network = _driver()
    result = driver.provision(_spec())
    name = result.handle.split("/", 1)[1]
    network.private_endpoints.objects[name]["tags"]["astrolift-managed-service-id"] = "other"
    count = len(network.private_endpoints.puts)

    collision = driver.provision(_spec())

    assert collision.ok is False and "belongs to managed service other, not service-1" in collision.message
    assert len(network.private_endpoints.puts) == count

    network.private_endpoints.objects[name]["tags"]["astrolift-managed-service-id"] = "service-1"
    changed = dict(_spec().config)
    changed["group_ids"] = ["dfs"]
    update = driver.update(UpdateSpec(result.handle, config=changed, managed_service_id="service-1"))
    assert update.ok is False and "immutable" in update.message
    assert len(network.private_endpoints.puts) == count


def test_update_reconciles_dns_and_deletion_protection() -> None:
    driver, network = _driver()
    result = driver.provision(_spec())
    updated_cfg = dict(_spec().config)
    updated_cfg["private_dns_zone_ids"] = []
    updated_cfg["deletion_protection"] = False

    updated = driver.update(UpdateSpec(result.handle, config=updated_cfg, managed_service_id="service-1"))

    assert updated.ok
    assert network.private_endpoints.puts[-1][2]["tags"]["astrolift-deletion-protection"] == "false"
    assert network.private_dns_zone_groups.deletes[-1][2] == "astrolift"


def test_dns_failure_returns_existing_handle_and_retry_repairs() -> None:
    driver, network = _driver()
    network.private_dns_zone_groups.put_error = RuntimeError("zone denied")

    failed = driver.provision(_spec())

    assert failed.ok is False
    assert failed.handle.startswith("private_endpoint/")
    assert network.private_endpoints.objects
    network.private_dns_zone_groups.put_error = None
    retried = driver.provision(_spec())
    assert retried.ok and retried.ready


def test_deletion_protection_ownership_and_idempotent_teardown() -> None:
    driver, network = _driver()
    result = driver.provision(_spec())

    protected = driver.deprovision(DeprovisionSpec(result.handle, managed_service_id="service-1"))
    assert protected.ok is False and protected.errors == ["deletion_protection_enabled"]

    name = result.handle.split("/", 1)[1]
    network.private_endpoints.objects[name]["tags"]["astrolift-managed-by"] = "foreign"
    foreign = driver.deprovision(DeprovisionSpec(result.handle, managed_service_id="service-1"), force_destroy=True)
    assert foreign.ok is False and "carries no Astrolift astrolift-managed-by=platform" in foreign.message

    network.private_endpoints.objects[name]["tags"]["astrolift-managed-by"] = "platform"
    removed = driver.deprovision(DeprovisionSpec(result.handle, managed_service_id="service-1"), force_destroy=True)
    repeated = driver.deprovision(DeprovisionSpec(result.handle, managed_service_id="service-1"), force_destroy=True)
    assert removed.ok and repeated.ok


def test_rejected_connection_status_and_snapshot_contract() -> None:
    driver, network = _driver()
    result = driver.provision(_spec())
    name = result.handle.split("/", 1)[1]
    state = network.private_endpoints.objects[name]["properties"]["privateLinkServiceConnections"][0]["properties"][
        "privateLinkServiceConnectionState"
    ]
    state["status"] = "Rejected"

    assert driver.status(ServiceHandle(result.handle)).state == "error"
    with pytest.raises(UnsupportedOperationError, match="without snapshots"):
        driver.snapshot(ServiceHandle(result.handle))
    with pytest.raises(UnsupportedOperationError, match="reconcile"):
        driver.restore(None, _spec())  # type: ignore[arg-type]


def test_binding_emits_the_canonical_private_endpoint_envelope() -> None:
    """The deploy render injects only ``envelope_keys_for('private_endpoint')``.

    A driver-local key name (the original ``PRIVATE_ENDPOINT_FQDNS`` with no
    ``PRIVATE_ENDPOINT_DNS``) never reaches a workload, so the binding silently
    no-ops -- the #1003 contract. Portable values must also match the AWS and
    GCP ``private_endpoint`` drivers, which emit IP and DNS lists as JSON.
    """
    driver, _ = _driver()
    result = driver.provision(_spec())

    binding = driver.binding(ServiceHandle(result.handle))

    assert set(envelope_keys_for("private_endpoint")) <= set(binding.env_vars)
    assert json.loads(binding.env_vars["PRIVATE_ENDPOINT_IPS"].literal) == ["10.20.0.5"]
    assert json.loads(binding.env_vars["PRIVATE_ENDPOINT_DNS_NAMES"].literal) == [
        "records.blob.core.windows.net",
    ]
    assert json.loads(binding.env_vars["PRIVATE_ENDPOINT_NETWORK_INTERFACE_IDS"].literal) == [
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}/providers/"
        f"Microsoft.Network/networkInterfaces/{result.handle.split('/', 1)[1]}-nic",
    ]
    assert binding.env_vars["PRIVATE_ENDPOINT_TYPE"].literal == "private_link"


def test_plugin_catalog_and_schema_expose_preview_driver() -> None:
    driver, _ = _driver()
    entry = next(
        row
        for row in MATRIX.managed_services
        if row.plugin_id == "azure" and row.kind == "private_endpoint" and row.variant == "private_link"
    )

    assert PLUGIN.managed_service_drivers[("private_endpoint", "private_link")] is AzurePrivateEndpointDriver
    assert entry.status == "preview"
    assert set(entry.binding_envs) == set(driver.binding_schema().env_vars)
    assert driver.config_schema()["additionalProperties"] is False
    assert driver.editable_fields() == ["deletion_protection", "private_dns_zone_ids"]
