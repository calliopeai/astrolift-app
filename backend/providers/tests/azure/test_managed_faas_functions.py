from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.availability import MATRIX
from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    SnapshotHandle,
    UpdateSpec,
)
from azure.managed.faas_functions import (
    ArmHttpResponse,
    AzureFunctionsConfig,
    AzureFunctionsDriver,
    AzureFunctionsError,
    AzureFunctionsNotFound,
    AzureFunctionsRestClient,
)
from azure.plugin import PLUGIN

SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"
RESOURCE_GROUP = "rg-functions"
PLAN_ID = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}/providers/Microsoft.Web/serverfarms/flex-plan"
PREMIUM_PLAN_ID = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}/providers/Microsoft.Web/serverfarms/premium-plan"
)
IDENTITY_ID = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}"
    "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/function-uami"
)
STORAGE_ID = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}"
    "/providers/Microsoft.Storage/storageAccounts/functionstorage"
)
REGISTRY_ID = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}"
    "/providers/Microsoft.ContainerRegistry/registries/functionregistry"
)
SUBNET_ID = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}"
    "/providers/Microsoft.Network/virtualNetworks/platform/subnets/functions"
)
CLIENT_ID = "22222222-2222-4222-8222-222222222222"
PRINCIPAL_ID = "33333333-3333-4333-8333-333333333333"
DIGEST = "a" * 64
NEXT_DIGEST = "b" * 64


@dataclass
class FakeArmClient:
    resources: dict[str, dict[str, Any]] = field(default_factory=dict)
    get_calls: list[tuple[str, str]] = field(default_factory=list)
    put_calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    patch_calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    delete_calls: list[tuple[str, str]] = field(default_factory=list)
    fail_delete_site: bool = False

    def get(self, resource_id: str, api_version: str) -> dict[str, Any]:
        self.get_calls.append((resource_id, api_version))
        if resource_id not in self.resources:
            raise AzureFunctionsNotFound(resource_id)
        return self.resources[resource_id]

    def put(self, resource_id: str, api_version: str, body: dict[str, Any]) -> dict[str, Any]:
        self.put_calls.append((resource_id, api_version, body))
        if resource_id.endswith("/extensions/onedeploy"):
            return {"status": "Succeeded"}
        stored = _copy(body)
        stored["id"] = resource_id
        if "/Microsoft.Web/sites/" in resource_id:
            properties = stored.setdefault("properties", {})
            properties.setdefault("provisioningState", "Succeeded")
            properties.setdefault("state", "Running")
            properties.setdefault("enabled", True)
            properties.setdefault("defaultHostName", f"{resource_id.rsplit('/', 1)[-1]}.azurewebsites.net")
        self.resources[resource_id] = stored
        return stored

    def patch(self, resource_id: str, api_version: str, body: dict[str, Any]) -> dict[str, Any]:
        self.patch_calls.append((resource_id, api_version, body))
        if resource_id not in self.resources:
            raise AzureFunctionsNotFound(resource_id)
        _deep_update(self.resources[resource_id], _copy(body))
        return self.resources[resource_id]

    def delete(self, resource_id: str, api_version: str) -> None:
        self.delete_calls.append((resource_id, api_version))
        if self.fail_delete_site and "/Microsoft.Web/sites/" in resource_id:
            raise RuntimeError("site locked")
        if resource_id not in self.resources:
            raise AzureFunctionsNotFound(resource_id)
        del self.resources[resource_id]


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _deep_update(target: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _client() -> FakeArmClient:
    return FakeArmClient(
        resources={
            PLAN_ID: {
                "id": PLAN_ID,
                "location": "eastus",
                "sku": {"name": "FC1", "tier": "FlexConsumption"},
                "properties": {"reserved": True},
            },
            PREMIUM_PLAN_ID: {
                "id": PREMIUM_PLAN_ID,
                "location": "eastus",
                "sku": {"name": "EP1", "tier": "ElasticPremium"},
                "properties": {"reserved": True},
            },
            IDENTITY_ID: {
                "id": IDENTITY_ID,
                "location": "eastus",
                "properties": {"clientId": CLIENT_ID, "principalId": PRINCIPAL_ID},
            },
            STORAGE_ID: {"id": STORAGE_ID, "location": "eastus"},
            REGISTRY_ID: {"id": REGISTRY_ID, "location": "eastus"},
            SUBNET_ID: {"id": SUBNET_ID, "location": "eastus"},
        },
    )


def _driver(client: FakeArmClient | None = None, **overrides: Any) -> AzureFunctionsDriver:
    values: dict[str, Any] = {
        "subscription_id": SUBSCRIPTION,
        "resource_group": RESOURCE_GROUP,
        "location": "eastus",
        "default_plan_resource_id": PLAN_ID,
        "default_identity_resource_id": IDENTITY_ID,
        "allowed_plan_resource_ids": (PLAN_ID, PREMIUM_PLAN_ID),
        "allowed_identity_resource_ids": (IDENTITY_ID,),
        "allowed_storage_resource_ids": (STORAGE_ID,),
        "allowed_registry_resource_ids": (REGISTRY_ID,),
        "allowed_subnet_resource_ids": (SUBNET_ID,),
        "client": client or _client(),
    }
    values.update(overrides)
    return AzureFunctionsDriver(config=AzureFunctionsConfig(**values))


def _zip_config(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "deployment_mode": "flex_zip",
        "host_storage_resource_id": STORAGE_ID,
        "deployment_container": "function-packages",
        "package_uri": (
            "https://functionstorage.blob.core.windows.net/function-packages/"
            "releases/abc/released-package.zip?versionid=2026-08-15T00%3A00%3A00Z"
        ),
        "package_sha256": DIGEST,
        "runtime_name": "python",
        "runtime_version": "3.12",
        "instance_memory_mb": 2048,
        "maximum_instance_count": 20,
        "environment": {"APP_MODE": "triage"},
    }
    cfg.update(overrides)
    return cfg


def _container_config(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "deployment_mode": "container",
        "plan_resource_id": PREMIUM_PLAN_ID,
        "host_storage_resource_id": STORAGE_ID,
        "registry_resource_id": REGISTRY_ID,
        "image_uri": f"functionregistry.azurecr.io/team/function@sha256:{DIGEST}",
        "runtime_name": "python",
    }
    cfg.update(overrides)
    return cfg


def _spec(config: dict[str, Any] | None = None, **overrides: Any) -> ProvisionSpec:
    values: dict[str, Any] = {
        "organization_id": "org-id",
        "organization_slug": "steadymd",
        "app_id": "app-id",
        "app_slug": "emr-triage",
        "environment_id": "env-id",
        "environment_name": "prd",
        "tenant_cluster_id": "cluster-id",
        "service_handle_hint": "intake",
        "size": "small",
        "config": config or _zip_config(),
        "binding_id": "binding-id",
        "managed_service_id": "managed-id",
    }
    values.update(overrides)
    return ProvisionSpec(**values)


def _site(client: FakeArmClient) -> dict[str, Any]:
    return next(value for key, value in client.resources.items() if "/Microsoft.Web/sites/" in key)


def _site_id(client: FakeArmClient) -> str:
    return next(key for key in client.resources if "/Microsoft.Web/sites/" in key)


def _role_ids(client: FakeArmClient) -> list[str]:
    return [key for key in client.resources if "/Microsoft.Authorization/roleAssignments/" in key]


def test_flex_zip_provision_uses_uami_and_one_deploy() -> None:
    client = _client()
    result = _driver(client).provision(_spec())
    assert result.ok
    assert result.handle.startswith("faas/rg-functions/astrolift-")
    site = _site(client)
    assert site["kind"] == "functionapp,linux"
    assert site["identity"]["userAssignedIdentities"] == {IDENTITY_ID: {}}
    props = site["properties"]
    assert props["httpsOnly"] is True
    assert props["publicNetworkAccess"] == "Disabled"
    storage = props["functionAppConfig"]["deployment"]["storage"]
    assert storage["authentication"] == {
        "type": "UserAssignedIdentity",
        "userAssignedIdentityResourceId": IDENTITY_ID,
    }
    settings = {item["name"]: item["value"] for item in props["siteConfig"]["appSettings"]}
    assert settings["AzureWebJobsStorage__credential"] == "managedidentity"
    assert settings["AzureWebJobsStorage__clientId"] == CLIENT_ID
    assert "AzureWebJobsStorage" not in settings
    deploys = [call for call in client.put_calls if call[0].endswith("/extensions/onedeploy")]
    assert deploys[0][2]["properties"]["packageUri"].endswith("versionid=2026-08-15T00%3A00%3A00Z")
    assert site["tags"]["astrolift-package-sha256"] == DIGEST
    assert len(_role_ids(client)) == 3


def test_provision_is_idempotent_and_does_not_redeploy_same_digest() -> None:
    client = _client()
    driver = _driver(client)
    first = driver.provision(_spec())
    second = driver.provision(_spec())
    assert first.ok and second.ok and first.handle == second.handle
    assert len([call for call in client.put_calls if call[0].endswith("/extensions/onedeploy")]) == 1
    assert len(_role_ids(client)) == 3


def test_container_provision_uses_digest_and_acr_identity() -> None:
    client = _client()
    result = _driver(client).provision(_spec(_container_config()))
    assert result.ok
    site = _site(client)
    config = site["properties"]["siteConfig"]
    assert config["linuxFxVersion"] == f"DOCKER|functionregistry.azurecr.io/team/function@sha256:{DIGEST}"
    assert config["acrUseManagedIdentityCreds"] is True
    assert config["acrUserManagedIdentityID"] == CLIENT_ID
    settings = {item["name"]: item["value"] for item in config["appSettings"]}
    assert settings["DOCKER_REGISTRY_SERVER_URL"] == "https://functionregistry.azurecr.io"
    assert settings["WEBSITES_ENABLE_APP_SERVICE_STORAGE"] == "false"
    assert site["tags"]["astrolift-image-sha256"] == DIGEST
    assert len(_role_ids(client)) == 4
    assert not [call for call in client.put_calls if call[0].endswith("/extensions/onedeploy")]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"package_uri": "https://evil.example/released-package.zip"}, "allowlisted storage host"),
        (
            {
                "package_uri": (
                    "https://functionstorage.blob.core.windows.net/function-packages/"
                    "released-package.zip?sv=1&sig=secret"
                ),
            },
            "exactly one",
        ),
        (
            {"package_uri": "https://functionstorage.blob.core.windows.net/other/released-package.zip"},
            "configured deployment container",
        ),
        (
            {
                "package_uri": (
                    "https://functionstorage.blob.core.windows.net/function-packages/"
                    "released-package.zip?versionid=one&versionid=two"
                ),
            },
            "exactly one",
        ),
        ({"environment": {"TOKEN": "AccountKey=secret"}}, "credential-bearing"),
        ({"environment": {"AzureWebJobsStorage": "secret"}}, "platform setting"),
        ({"maximum_instance_count": 101}, "between 1 and 100"),
    ],
)
def test_flex_validation_fails_closed(change: dict[str, Any], message: str) -> None:
    result = _driver().provision(_spec(_zip_config(**change)))
    assert not result.ok
    assert message in result.message


def test_allowlists_are_exact() -> None:
    other = STORAGE_ID.replace("functionstorage", "otherstorage")
    result = _driver().provision(_spec(_zip_config(host_storage_resource_id=other)))
    assert not result.ok
    assert "not allowed" in result.message


def test_public_network_requires_install_policy() -> None:
    denied = _driver().provision(_spec(_zip_config(public_network_access=True)))
    assert not denied.ok
    allowed_client = _client()
    allowed = _driver(allowed_client, allow_public_network=True).provision(
        _spec(_zip_config(public_network_access=True)),
    )
    assert allowed.ok
    assert _site(allowed_client)["properties"]["publicNetworkAccess"] == "Enabled"
    assert _site(allowed_client)["properties"]["hostNamesDisabled"] is False


def test_flex_requires_fc1_and_container_rejects_fc1() -> None:
    flex_on_premium = _driver().provision(_spec(_zip_config(plan_resource_id=PREMIUM_PLAN_ID)))
    container_on_flex = _driver().provision(_spec(_container_config(plan_resource_id=PLAN_ID)))
    assert not flex_on_premium.ok and "FC1" in flex_on_premium.message
    assert not container_on_flex.ok and "Premium or Dedicated" in container_on_flex.message


def test_dependency_reads_use_resource_specific_api_versions() -> None:
    client = _client()
    result = _driver(client).provision(_spec(_container_config()))
    assert result.ok
    calls = dict(client.get_calls)
    assert calls[PREMIUM_PLAN_ID] == "2024-11-01"
    assert calls[IDENTITY_ID] == "2023-01-31"
    assert calls[STORAGE_ID] == "2023-05-01"
    assert calls[REGISTRY_ID] == "2023-07-01"


def test_container_requires_linux_premium_or_dedicated_plan() -> None:
    client = _client()
    client.resources[PREMIUM_PLAN_ID]["properties"]["reserved"] = False
    not_linux = _driver(client).provision(_spec(_container_config()))
    assert not not_linux.ok and "Linux hosting plan" in not_linux.message

    client = _client()
    client.resources[PREMIUM_PLAN_ID]["sku"] = {"name": "Y1", "tier": "Dynamic"}
    not_supported = _driver(client).provision(_spec(_container_config()))
    assert not not_supported.ok and "Premium or Dedicated" in not_supported.message


def test_invalid_install_policy_fails_closed() -> None:
    result = _driver(storage_blob_endpoint_suffix="https://blob.example/").provision(_spec())
    assert not result.ok
    assert "DNS suffix" in result.message


def test_container_rejects_tagged_or_other_registry_images() -> None:
    tagged = _driver().provision(
        _spec(_container_config(image_uri="functionregistry.azurecr.io/team/function:latest")),
    )
    other = _driver().provision(
        _spec(_container_config(image_uri=f"other.azurecr.io/team/function@sha256:{DIGEST}")),
    )
    assert not tagged.ok and "pinned" in tagged.message
    assert not other.ok and "allowlisted registry host" in other.message


def test_foreign_resource_and_owner_collision_are_not_adopted() -> None:
    client = _client()
    driver = _driver(client)
    first = driver.provision(_spec())
    assert first.ok
    site = _site(client)
    site["tags"]["astrolift-managed-by"] = "terraform"
    foreign = driver.provision(_spec())
    assert not foreign.ok and "not Astrolift-owned" in foreign.message
    site["tags"]["astrolift-managed-by"] = "platform"
    wrong_owner = driver.provision(_spec(managed_service_id="other-managed-id"))
    assert not wrong_owner.ok and "another managed service" in wrong_owner.message


def test_role_assignment_collision_is_rejected() -> None:
    client = _client()
    driver = _driver(client)
    assert driver.provision(_spec()).ok
    assignment = client.resources[_role_ids(client)[0]]
    assignment["properties"]["principalId"] = "44444444-4444-4444-8444-444444444444"
    result = driver.provision(_spec())
    assert not result.ok
    assert "foreign grant" in result.message


def test_package_update_requires_uri_and_digest_together() -> None:
    client = _client()
    driver = _driver(client)
    provisioned = driver.provision(_spec())
    result = driver.update(UpdateSpec(handle=provisioned.handle, config={"package_uri": "https://x"}))
    assert not result.ok
    assert "updated together" in result.message


def test_package_update_deploys_then_stamps_digest() -> None:
    client = _client()
    driver = _driver(client)
    provisioned = driver.provision(_spec())
    uri = (
        "https://functionstorage.blob.core.windows.net/function-packages/"
        "releases/next/released-package.zip?versionid=next"
    )
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"package_uri": uri, "package_sha256": NEXT_DIGEST},
        ),
    )
    assert result.ok
    deploys = [call for call in client.put_calls if call[0].endswith("/extensions/onedeploy")]
    assert len(deploys) == 2
    assert deploys[-1][2]["properties"]["packageUri"] == uri
    assert _site(client)["tags"]["astrolift-package-sha256"] == NEXT_DIGEST


def test_update_resolves_dependencies_allowlisted_in_another_resource_group() -> None:
    """Operator dependencies are allowlisted as full ARM IDs and may live in any
    resource group. Live Function App state only carries the storage account and
    registry *names*, so update must recover their IDs from the allowlist instead
    of assuming the cluster's own resource group."""
    shared = (
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-shared-platform"
        "/providers/Microsoft.Storage/storageAccounts/functionstorage"
    )
    shared_registry = (
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-shared-platform"
        "/providers/Microsoft.ContainerRegistry/registries/functionregistry"
    )
    client = _client()
    client.resources[shared] = {"id": shared, "location": "eastus"}
    client.resources[shared_registry] = {"id": shared_registry, "location": "eastus"}

    driver = _driver(client, allowed_storage_resource_ids=(shared,))
    provisioned = driver.provision(_spec(_zip_config(host_storage_resource_id=shared)))
    assert provisioned.ok
    updated = driver.update(UpdateSpec(handle=provisioned.handle, config={"environment": {"APP_MODE": "batch"}}))
    assert updated.ok, updated.message

    container_client = _client()
    container_client.resources[shared] = {"id": shared, "location": "eastus"}
    container_client.resources[shared_registry] = {"id": shared_registry, "location": "eastus"}
    container_driver = _driver(
        container_client,
        allowed_storage_resource_ids=(shared,),
        allowed_registry_resource_ids=(shared_registry,),
    )
    container = container_driver.provision(
        _spec(_container_config(host_storage_resource_id=shared, registry_resource_id=shared_registry)),
    )
    assert container.ok
    container_updated = container_driver.update(
        UpdateSpec(handle=container.handle, config={"environment": {"APP_MODE": "batch"}}),
    )
    assert container_updated.ok, container_updated.message


def test_update_rejects_unknown_dependency_name_with_an_honest_identifier() -> None:
    """An account name that matches nothing in the allowlist must still fail the
    policy check rather than silently adopting an allowlisted neighbour."""
    client = _client()
    driver = _driver(client)
    provisioned = driver.provision(_spec())
    site = _site(client)
    settings = site["properties"]["siteConfig"]["appSettings"]
    for item in settings:
        if item["name"] == "AzureWebJobsStorage__accountName":
            item["value"] = "rogueaccount"
    result = driver.update(UpdateSpec(handle=provisioned.handle, config={"environment": {"APP_MODE": "batch"}}))
    assert not result.ok
    assert "not allowed" in result.message


def test_update_rejects_function_rename() -> None:
    client = _client()
    driver = _driver(client)
    provisioned = driver.provision(_spec())
    result = driver.update(UpdateSpec(provisioned.handle, config={"function_name": "different"}))
    assert not result.ok
    assert "immutable" in result.message


def test_status_and_binding_are_credential_free() -> None:
    client = _client()
    driver = _driver(client)
    provisioned = driver.provision(_spec())
    status = driver.status(ServiceHandle(provisioned.handle))
    assert status.state == "available"
    binding = driver.binding(ServiceHandle(provisioned.handle))
    assert binding.env_vars["FUNCTION_PROVIDER"].literal == "azure_functions"
    assert binding.env_vars["FUNCTION_URL"].literal is not None
    assert binding.env_vars["AZURE_FUNCTION_IDENTITY_RESOURCE_ID"].literal == IDENTITY_ID
    assert not binding.iam_grants
    assert all(value.secret_ref is None for value in binding.env_vars.values())


def test_status_missing_and_failed() -> None:
    client = _client()
    driver = _driver(client)
    assert driver.status(ServiceHandle("faas/rg-functions/missing")).state == "deprovisioned"
    provisioned = driver.provision(_spec())
    _site(client)["properties"]["provisioningState"] = "Failed"
    assert driver.status(ServiceHandle(provisioned.handle)).state == "error"


def test_deprovision_protection_and_force_cleanup() -> None:
    client = _client()
    cfg = _zip_config()
    driver = _driver(client)
    provisioned = driver.provision(_spec(cfg))
    protected = driver.deprovision(DeprovisionSpec(provisioned.handle, cfg))
    assert not protected.ok and not protected.retryable
    forced = driver.deprovision(DeprovisionSpec(provisioned.handle, cfg), force_destroy=True)
    assert forced.ok
    assert not any("/Microsoft.Web/sites/" in key for key in client.resources)
    assert not _role_ids(client)
    for dependency in (PLAN_ID, IDENTITY_ID, STORAGE_ID):
        assert dependency in client.resources


def test_deprovision_never_removes_roles_before_site_delete_succeeds() -> None:
    client = _client()
    cfg = _zip_config(deletion_protection=False)
    driver = _driver(client)
    provisioned = driver.provision(_spec(cfg))
    client.fail_delete_site = True
    result = driver.deprovision(DeprovisionSpec(provisioned.handle, cfg))
    assert not result.ok and "site locked" in result.message
    assert len(_role_ids(client)) == 3


def test_deprovision_missing_site_cleans_roles_from_stored_config() -> None:
    client = _client()
    cfg = _zip_config(deletion_protection=False)
    driver = _driver(client)
    provisioned = driver.provision(_spec(cfg))
    del client.resources[_site_id(client)]
    result = driver.deprovision(DeprovisionSpec(provisioned.handle, cfg))
    assert result.ok
    assert not _role_ids(client)


def test_missing_site_without_stored_config_fails_closed_on_role_cleanup() -> None:
    result = _driver().deprovision(DeprovisionSpec("faas/rg-functions/missing"), force_destroy=True)
    assert not result.ok
    assert "stored config" in result.message


def test_snapshot_and_restore_are_honestly_unsupported() -> None:
    driver = _driver()
    with pytest.raises(AzureFunctionsError, match="no honest service snapshot"):
        driver.snapshot(ServiceHandle("faas/rg-functions/function"))
    restored = driver.restore(
        SnapshotHandle("faas/x/y", "snapshot", "2026-01-01"),
        _spec(),
    )
    assert not restored.ok and restored.errors == ["not_supported"]


def test_schema_plugin_and_catalog_registration() -> None:
    driver = _driver()
    schema = driver.config_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["image_uri"]["pattern"] == (
        r"^(?P<host>[a-z0-9.-]+)/(?P<path>[a-z0-9._/-]+)@sha256:(?P<digest>[0-9a-f]{64})$"
    )
    assert PLUGIN.managed_service_drivers[("faas", "azure_functions")] is AzureFunctionsDriver
    entry = next(
        item
        for item in MATRIX.managed_services
        if (item.plugin_id, item.kind, item.variant) == ("azure", "faas", "azure_functions")
    )
    assert entry.status == "preview"
    assert set(entry.binding_envs) == set(driver.binding_schema().env_vars)


@dataclass
class FakeToken:
    token: str = "test-token"


class FakeCredential:
    def get_token(self, scope: str) -> FakeToken:
        assert scope == "https://management.azure.com/.default"
        return FakeToken()


def test_rest_client_builds_authenticated_api_version_request() -> None:
    calls: list[tuple[str, str, dict[str, str], bytes | None, float]] = []

    def transport(
        method: str,
        url: str,
        headers: Any,
        body: bytes | None,
        timeout: float,
    ) -> ArmHttpResponse:
        calls.append((method, url, dict(headers), body, timeout))
        return ArmHttpResponse(200, {}, b'{"id":"/resource"}')

    client = AzureFunctionsRestClient(credential=FakeCredential(), transport=transport)
    assert client.get("/resource", "2024-11-01") == {"id": "/resource"}
    assert calls[0][0] == "GET"
    assert calls[0][1] == "https://management.azure.com/resource?api-version=2024-11-01"
    assert calls[0][2]["Authorization"] == "Bearer test-token"


def test_rest_client_polls_same_origin_and_rejects_foreign_poll_url() -> None:
    responses = [
        ArmHttpResponse(202, {"Azure-AsyncOperation": "https://management.azure.com/ops/1"}),
        ArmHttpResponse(200, {}, b'{"status":"Succeeded"}'),
    ]

    def transport(*_args: Any) -> ArmHttpResponse:
        return responses.pop(0)

    client = AzureFunctionsRestClient(
        credential=FakeCredential(),
        transport=transport,
        sleep=lambda _: None,
    )
    assert client.put("/resource", "2024-11-01", {})["status"] == "Succeeded"

    def foreign(*_args: Any) -> ArmHttpResponse:
        return ArmHttpResponse(202, {"Location": "https://evil.example/steal"})

    client = AzureFunctionsRestClient(credential=FakeCredential(), transport=foreign)
    with pytest.raises(AzureFunctionsError, match="untrusted"):
        client.put("/resource", "2024-11-01", {})


def test_rest_client_maps_not_found_conflict_and_provider_error() -> None:
    for status, expected in (
        (404, AzureFunctionsNotFound),
        (409, AzureFunctionsError),
        (500, AzureFunctionsError),
    ):
        client = AzureFunctionsRestClient(
            credential=FakeCredential(),
            transport=lambda *_args, code=status: ArmHttpResponse(
                code,
                {},
                b'{"error":{"message":"boom"}}',
            ),
        )
        with pytest.raises(expected, match=r"boom|resource"):
            client.get("/resource", "2024-11-01")
