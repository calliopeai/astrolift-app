from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from _sdk import UnsupportedOperationError
from _sdk.availability import MATRIX
from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from azure.managed.api_management import (
    AzureAPIMConfig,
    AzureAPIMDriver,
    AzureAPIMError,
    AzureAPIMNotFound,
    AzureAPIMRestClient,
)
from azure.plugin import PLUGIN

SUBSCRIPTION_ID = "00000000-1111-2222-3333-444444444444"
SERVICE_PATH = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/rg-platform/"
    "providers/Microsoft.ApiManagement/service/astrolift-acme-api-prod-gateway"
)

SPEC = ProvisionSpec(
    organization_id="org-id",
    organization_slug="acme",
    app_id="app-id",
    app_slug="api",
    environment_id="env-id",
    environment_name="prod",
    tenant_cluster_id="azure-prod",
    service_handle_hint="gateway",
    size="small",
    binding_id="binding-id",
    managed_service_id="managed-id",
)


class FakeAPIMClient:
    def __init__(self) -> None:
        self.resources: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[Any, ...]] = []

    def get(self, path: str) -> dict[str, Any]:
        self.calls.append(("get", path))
        if path not in self.resources:
            raise AzureAPIMNotFound(path)
        return deepcopy(self.resources[path])

    def put(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("put", path, deepcopy(body)))
        resource = deepcopy(body)
        resource["id"] = path
        resource["name"] = path.rsplit("/", 1)[-1]
        if "/providers/Microsoft.ApiManagement/service/" in path and path.count("/") == 8:
            properties = dict(resource.get("properties") or {})
            properties["provisioningState"] = "Succeeded"
            properties["gatewayUrl"] = f"https://{resource['name']}.azure-api.net"
            resource["properties"] = properties
        self.resources[path] = resource
        return deepcopy(resource)

    def delete(self, path: str) -> None:
        self.calls.append(("delete", path))
        if path not in self.resources:
            raise AzureAPIMNotFound(path)
        for key in list(self.resources):
            if key == path or key.startswith(f"{path}/"):
                self.resources.pop(key)

    def list(self, path: str) -> list[dict[str, Any]]:
        self.calls.append(("list", path))
        prefix = f"{path}/"
        return [
            deepcopy(value)
            for key, value in self.resources.items()
            if key.startswith(prefix) and "/" not in key.removeprefix(prefix)
        ]


class FakeResponse:
    def __init__(self, body: dict[str, Any], *, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self._body = json.dumps(body).encode()
        self.status = status
        self.headers = headers or {}

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class FakeOpener:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[Any] = []

    def open(self, request: Any, *, timeout: float) -> FakeResponse:
        del timeout
        self.requests.append(request)
        return self.responses.pop(0)


class FakeCredential:
    def get_token(self, scope: str) -> Any:
        assert scope == "https://management.azure.com/.default"
        return type("Token", (), {"token": "test-token"})()


@pytest.fixture
def client() -> FakeAPIMClient:
    return FakeAPIMClient()


@pytest.fixture
def config(client: FakeAPIMClient) -> AzureAPIMConfig:
    return AzureAPIMConfig(
        subscription_id=SUBSCRIPTION_ID,
        resource_group="rg-platform",
        location="eastus2",
        publisher_email="platform@example.com",
        allowed_skus=("Consumption", "Developer", "StandardV2", "Premium"),
        max_capacity=4,
        allowed_policy_kinds=("backend", "cors", "managed_identity"),
        allowed_backend_identity_resources=("api://billing-backend",),
        allowed_user_assigned_identity_ids=(
            "/subscriptions/00000000-1111-2222-3333-444444444444/resourceGroups/rg-platform/"
            "providers/Microsoft.ManagedIdentity/userAssignedIdentities/apim",
        ),
        allowed_subnet_ids=(
            "/subscriptions/00000000-1111-2222-3333-444444444444/resourceGroups/rg-network/"
            "providers/Microsoft.Network/virtualNetworks/platform/subnets/apim",
        ),
        allowed_custom_domain_suffixes=(".example.com",),
        allowed_key_vault_secret_prefixes=("https://platform.vault.azure.net/secrets/apim-",),
        allow_internal_network=True,
        allow_custom_domains=True,
        allow_subscriptions=True,
        allow_child_pruning=True,
        allow_adoption=True,
        client=client,
    )


@pytest.fixture
def driver(config: AzureAPIMConfig) -> AzureAPIMDriver:
    return AzureAPIMDriver(config=config)


def _declaration(**overrides: Any) -> dict[str, Any]:
    config = {
        "sku": "Developer",
        "capacity": 1,
        "backends": [
            {
                "id": "astrolift-billing",
                "url": "https://billing.azurecontainerapps.io/api",
                "identity_resource": "api://billing-backend",
                "identity_client_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            },
        ],
        "apis": [
            {
                "id": "astrolift-public",
                "display_name": "Public API",
                "path": "v1",
                "backend_id": "astrolift-billing",
                "routes": [
                    {
                        "id": "astrolift-list-invoices",
                        "method": "GET",
                        "url_template": "/invoices/{id}",
                    },
                ],
                "cors": {
                    "origins": ["https://app.example.com"],
                    "methods": ["GET"],
                    "headers": ["Authorization"],
                    "expose_headers": ["X-Request-Id"],
                },
            },
        ],
        "subscriptions": [
            {
                "id": "astrolift-portal",
                "display_name": "Operator portal",
                "api_id": "astrolift-public",
            },
        ],
        "custom_domains": [
            {
                "hostname": "api.example.com",
                "key_vault_secret_id": "https://platform.vault.azure.net/secrets/apim-api",
                "default_ssl_binding": True,
            },
        ],
    }
    config.update(overrides)
    return config


def test_provision_reconciles_typed_service_children_and_generated_policy(
    driver: AzureAPIMDriver,
    client: FakeAPIMClient,
) -> None:
    result = driver.provision(replace(SPEC, config=_declaration()))
    assert result.ok and result.ready
    assert result.handle == "api_gateway/astrolift-acme-api-prod-gateway"
    service = client.resources[SERVICE_PATH]
    assert service["sku"] == {"name": "Developer", "capacity": 1}
    assert service["identity"] == {"type": "SystemAssigned"}
    assert service["properties"]["publicNetworkAccess"] == "Enabled"
    assert service["properties"]["hostnameConfigurations"] == [
        {
            "type": "Proxy",
            "hostName": "api.example.com",
            "keyVaultId": "https://platform.vault.azure.net/secrets/apim-api",
            "negotiateClientCertificate": False,
            "defaultSslBinding": True,
        },
    ]
    assert service["tags"]["astrolift-managed-service-id"] == "managed-id"
    policy_path = f"{SERVICE_PATH}/apis/astrolift-public/policies/policy"
    policy = client.resources[policy_path]["properties"]["value"]
    assert '<set-backend-service backend-id="astrolift-billing"' in policy
    assert '<authentication-managed-identity resource="api://billing-backend"' in policy
    assert "&lt;" not in policy
    assert "subscription_key" not in str(client.calls).lower()
    subscription = client.resources[f"{SERVICE_PATH}/subscriptions/astrolift-portal"]
    assert set(subscription["properties"]) == {"allowTracing", "displayName", "scope", "state"}


def test_provision_is_idempotent(driver: AzureAPIMDriver, client: FakeAPIMClient) -> None:
    spec = replace(SPEC, config=_declaration())
    assert driver.provision(spec).ok
    resources = deepcopy(client.resources)
    assert driver.provision(spec).ok
    assert client.resources == resources


def test_internal_network_and_user_identity_are_explicitly_allowlisted(
    driver: AzureAPIMDriver,
    client: FakeAPIMClient,
    config: AzureAPIMConfig,
) -> None:
    identity_id = config.allowed_user_assigned_identity_ids[0]
    subnet_id = config.allowed_subnet_ids[0]
    declaration = _declaration(
        sku="Premium",
        capacity=2,
        network_mode="internal",
        subnet_id=subnet_id,
        identity={"type": "user_assigned", "user_assigned_identity_id": identity_id},
    )
    result = driver.provision(replace(SPEC, config=declaration))
    assert result.ok
    service = client.resources[SERVICE_PATH]
    assert service["properties"]["publicNetworkAccess"] == "Disabled"
    assert service["properties"]["virtualNetworkConfiguration"]["subnetResourceId"] == subnet_id
    assert service["identity"] == {
        "type": "UserAssigned",
        "userAssignedIdentities": {identity_id: {}},
    }


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"sku": "Consumption", "capacity": 1}, "capacity=0"),
        ({"sku": "Premium", "capacity": 5}, "between 1 and 4"),
        ({"sku": "BasicV2", "capacity": 1}, "allowed_skus"),
        ({"network_mode": "internal", "sku": "StandardV2"}, "Developer or Premium"),
        ({"subnet_id": "/not/allowed", "network_mode": "internal"}, "subnet_id"),
        ({"backends": [{"id": "customer", "url": "https://billing.azurewebsites.net"}]}, "prefixed"),
        (
            {"backends": [{"id": "astrolift-b", "url": "https://169.254.169.254/metadata"}]},
            "allowlist",
        ),
        (
            {"backends": [{"id": "astrolift-b", "url": "https://user:pass@billing.azurewebsites.net"}]},
            "credential-free",
        ),
        ({"subscriptions": [], "policy_xml": "<policies />"}, "raw policies"),
        ({"identity": ["system_assigned"]}, "identity must be an object"),
        (
            {
                "backends": [
                    {
                        "id": "astrolift-b",
                        "url": "https://billing.azurewebsites.net",
                        "authorization": "Basic secret",
                    },
                ],
            },
            "unsupported fields",
        ),
        (
            {
                "apis": [
                    {
                        "id": "astrolift-missing-name",
                        "display_name": "",
                        "path": "v1",
                    },
                ],
                "subscriptions": [],
            },
            "display_name",
        ),
        (
            {
                "custom_domains": [
                    {
                        "hostname": "evil.test",
                        "key_vault_secret_id": "https://platform.vault.azure.net/secrets/apim-api",
                    },
                ],
            },
            "hostname",
        ),
        (
            {
                "custom_domains": [
                    {
                        "hostname": "api.example.com",
                        "key_vault_secret_id": ("https://platform.vault.azure.net/secrets/apim-api?api-version=7.4"),
                    },
                ],
            },
            "Key Vault",
        ),
    ],
)
def test_manifest_validation_fails_closed(
    driver: AzureAPIMDriver,
    change: dict[str, Any],
    message: str,
) -> None:
    declaration = _declaration()
    declaration.update(change)
    result = driver.provision(replace(SPEC, config=declaration))
    assert not result.ok and message in result.message


def test_policy_and_feature_gates_default_closed(client: FakeAPIMClient) -> None:
    locked = AzureAPIMDriver(
        config=AzureAPIMConfig(
            subscription_id=SUBSCRIPTION_ID,
            resource_group="rg-platform",
            publisher_email="platform@example.com",
            client=client,
        ),
    )
    result = locked.provision(replace(SPEC, config=_declaration()))
    assert not result.ok
    assert any(word in result.message for word in ("managed-identity", "subscription", "custom domains"))
    assert client.resources == {}


def test_update_reconciles_and_prunes_only_owned_prefix_children(
    driver: AzureAPIMDriver,
    client: FakeAPIMClient,
) -> None:
    result = driver.provision(replace(SPEC, config=_declaration()))
    external = f"{SERVICE_PATH}/backends/customer-backend"
    stale = f"{SERVICE_PATH}/backends/astrolift-stale"
    client.resources[external] = {"name": "customer-backend", "properties": {}}
    client.resources[stale] = {"name": "astrolift-stale", "properties": {}}
    update = driver.update(
        UpdateSpec(
            result.handle,
            config={
                "sku": "Premium",
                "capacity": 2,
                "backends": _declaration()["backends"],
                "apis": _declaration()["apis"],
                "subscriptions": _declaration()["subscriptions"],
                "prune_children": True,
            },
        ),
    )
    assert update.ok
    assert client.resources[SERVICE_PATH]["sku"] == {"name": "Premium", "capacity": 2}
    assert stale not in client.resources
    assert external in client.resources


def test_collision_requires_adoption_and_marks_service(
    driver: AzureAPIMDriver,
    client: FakeAPIMClient,
) -> None:
    client.resources[SERVICE_PATH] = {
        "name": SERVICE_PATH.rsplit("/", 1)[-1],
        "location": "eastus2",
        "sku": {"name": "Developer", "capacity": 1},
        "properties": {"provisioningState": "Succeeded"},
        "tags": {"owner": "customer"},
    }
    denied = driver.provision(replace(SPEC, config=_declaration()))
    assert not denied.ok and "adopt_existing" in denied.message
    adopted = driver.provision(replace(SPEC, config=_declaration(adopt_existing=True)))
    assert adopted.ok
    assert client.resources[SERVICE_PATH]["tags"]["astrolift-adopted"] == "true"


def test_foreign_owned_collision_is_never_reassigned(
    driver: AzureAPIMDriver,
    client: FakeAPIMClient,
) -> None:
    client.resources[SERVICE_PATH] = {
        "properties": {"provisioningState": "Succeeded"},
        "tags": {"astrolift-managed-service-id": "someone-else"},
    }
    result = driver.provision(replace(SPEC, config=_declaration(adopt_existing=True)))
    assert not result.ok and "another managed-service" in result.message


def test_teardown_is_protected_and_adoption_aware(
    driver: AzureAPIMDriver,
    client: FakeAPIMClient,
) -> None:
    result = driver.provision(replace(SPEC, config=_declaration()))
    protected = driver.deprovision(DeprovisionSpec(result.handle, _declaration()))
    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]
    no_force = driver.deprovision(
        DeprovisionSpec(result.handle, _declaration(deletion_protection=False)),
    )
    assert not no_force.ok and no_force.errors == ["force_destroy_required"]
    deleted = driver.deprovision(
        DeprovisionSpec(result.handle, _declaration(deletion_protection=False)),
        force_destroy=True,
    )
    assert deleted.ok and SERVICE_PATH not in client.resources


def test_adopted_teardown_requires_delete_adopted(
    driver: AzureAPIMDriver,
    client: FakeAPIMClient,
) -> None:
    client.resources[SERVICE_PATH] = {
        "name": SERVICE_PATH.rsplit("/", 1)[-1],
        "properties": {"provisioningState": "Succeeded"},
        "tags": {},
    }
    result = driver.provision(replace(SPEC, config=_declaration(adopt_existing=True)))
    blocked = driver.deprovision(
        DeprovisionSpec(result.handle, _declaration(deletion_protection=False)),
        force_destroy=True,
    )
    assert not blocked.ok and blocked.errors == ["delete_adopted_required"]
    deleted = driver.deprovision(
        DeprovisionSpec(
            result.handle,
            _declaration(deletion_protection=False, delete_adopted=True),
        ),
        force_destroy=True,
    )
    assert deleted.ok


def test_status_and_binding_never_expose_admin_or_subscription_keys(
    driver: AzureAPIMDriver,
    client: FakeAPIMClient,
) -> None:
    result = driver.provision(replace(SPEC, config=_declaration()))
    assert driver.status(ServiceHandle(result.handle)).state == "available"
    binding = driver.binding(ServiceHandle(result.handle))
    assert binding.env_vars["API_GATEWAY_URL"].literal == ("https://astrolift-acme-api-prod-gateway.azure-api.net")
    assert all(value.secret_ref is None for value in binding.env_vars.values())
    assert not any("key" in name.lower() for name in binding.env_vars)
    client.resources[SERVICE_PATH]["properties"]["provisioningState"] = "Failed"
    assert driver.status(ServiceHandle(result.handle)).state == "error"
    client.delete(SERVICE_PATH)
    assert driver.status(ServiceHandle(result.handle)).state == "deprovisioned"


def test_snapshot_and_restore_are_truthfully_unsupported(driver: AzureAPIMDriver) -> None:
    with pytest.raises(UnsupportedOperationError):
        driver.snapshot(ServiceHandle("api_gateway/example"))
    with pytest.raises(UnsupportedOperationError):
        driver.restore(
            type("Snapshot", (), {"snapshot_id": "x"})(),
            replace(SPEC, config=_declaration()),
        )


def test_install_validation_rejects_untrusted_management_endpoint(client: FakeAPIMClient) -> None:
    with pytest.raises(AzureAPIMError, match="subscription_id"):
        AzureAPIMDriver(
            config=AzureAPIMConfig(
                subscription_id="not-a-uuid",
                resource_group="rg-platform",
                publisher_email="platform@example.com",
                client=client,
            ),
        )


def test_rest_client_pins_arm_scope_and_rejects_untrusted_continuations() -> None:
    client = AzureAPIMRestClient(
        subscription_id=SUBSCRIPTION_ID,
        credential=FakeCredential(),
    )
    opener = FakeOpener([FakeResponse({"name": "service"})])
    client._opener = opener  # type: ignore[attr-defined]
    assert client.get(SERVICE_PATH) == {"name": "service"}
    request = opener.requests[0]
    assert request.full_url.endswith("api-version=2024-05-01")
    assert request.get_header("Authorization") == "Bearer test-token"

    malicious = FakeOpener(
        [
            FakeResponse(
                {
                    "value": [],
                    "nextLink": f"https://evil.example.test/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/rg",
                },
            ),
        ],
    )
    client._opener = malicious  # type: ignore[attr-defined]
    with pytest.raises(AzureAPIMError, match="escaped"):
        client.list(f"{SERVICE_PATH}/apis")
    with pytest.raises(AzureAPIMError, match="host suffix"):
        AzureAPIMDriver(
            config=AzureAPIMConfig(
                subscription_id=SUBSCRIPTION_ID,
                resource_group="rg-platform",
                publisher_email="platform@example.com",
                allowed_backend_host_suffixes=("azurewebsites.net",),
                client=client,
            ),
        )
    with pytest.raises(AzureAPIMError, match="api_endpoint"):
        AzureAPIMRestClient(
            subscription_id=SUBSCRIPTION_ID,
            credential=object(),
            api_endpoint="https://evil.example.com",
        )


def test_schema_catalog_and_plugin_registration_are_current(driver: AzureAPIMDriver) -> None:
    schema = driver.config_schema()
    assert schema["additionalProperties"] is False
    assert "policy_xml" not in schema["properties"]
    assert set(schema["required"]) == {"sku", "capacity"}
    assert PLUGIN.managed_service_drivers[("api_gateway", "api_management")] is AzureAPIMDriver
    entry = next(
        item
        for item in MATRIX.managed_services
        if item.plugin_id == "azure" and item.kind == "api_gateway" and item.variant == "api_management"
    )
    assert entry.status == "preview"
    assert "API_GATEWAY_URL" in entry.binding_envs
