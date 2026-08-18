"""Tests for AzureOpenAIDriver (#376).

Mirrors the AzureAISearchFullTextDriver test surface: provision
(idempotent, tagged, api key persisted to Key Vault), update,
four-corner deprovision matrix (delete_data x force_destroy with
resource-lock + studio-reference guards), status state-mapping,
binding env-var shape, snapshot + restore.

Uses in-process fakes for the CognitiveServicesManagementClient
+ Key Vault SecretClient -- no Azure account required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk import UnsupportedOperationError
from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from azure.managed.model_endpoint_aoai import (
    KIND,
    AzureOpenAIConfig,
    AzureOpenAIDriver,
    AzureOpenAIError,
)

# ---- fakes ------------------------------------------------------


class _NotFound(Exception):
    pass


_NotFound.__name__ = "ResourceNotFoundError"


@dataclass
class FakePoller:
    value: Any = None

    def result(self) -> Any:
        return self.value


OWNER = "managed-service-guid"
BINDING = "binding-guid"


@dataclass
class FakeDeployment:
    name: str
    sku: dict[str, Any] = field(default_factory=dict)
    properties: dict[str, Any] = field(default_factory=dict)
    provisioning_state: str = "Succeeded"
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeAccountKeys:
    key1: str = "primary-aoai-key-zzzzzzzzzzzzzzzz"
    key2: str = "secondary-aoai-key-yyyyyyyyyyyy"


@dataclass
class FakeDeploymentsOperations:
    deployments: dict[str, FakeDeployment] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

    def get(
        self,
        *,
        resource_group_name: str,
        account_name: str,
        deployment_name: str,
    ) -> FakeDeployment:
        if deployment_name not in self.deployments:
            raise _NotFound(deployment_name)
        return self.deployments[deployment_name]

    def begin_create_or_update(
        self,
        *,
        resource_group_name: str,
        account_name: str,
        deployment_name: str,
        deployment: dict[str, Any],
    ) -> FakePoller:
        self.create_calls.append(
            {"name": deployment_name, "parameters": deployment},
        )
        d = FakeDeployment(
            name=deployment_name,
            sku=dict(deployment.get("sku", {})),
            properties=dict(deployment.get("properties", {})),
            provisioning_state="Succeeded",
            tags=dict(deployment.get("tags") or {}),
        )
        self.deployments[deployment_name] = d
        return FakePoller(value=d)

    def begin_update(
        self,
        *,
        resource_group_name: str,
        account_name: str,
        deployment_name: str,
        deployment: dict[str, Any],
    ) -> FakePoller:
        self.update_calls.append(
            {"name": deployment_name, "parameters": deployment},
        )
        d = self.deployments.get(deployment_name)
        if d is None:
            raise _NotFound(deployment_name)
        if "sku" in deployment:
            d.sku.update(deployment["sku"])
        if "properties" in deployment:
            d.properties.update(deployment["properties"])
        return FakePoller(value=d)

    def begin_delete(
        self,
        *,
        resource_group_name: str,
        account_name: str,
        deployment_name: str,
    ) -> FakePoller:
        self.delete_calls.append(deployment_name)
        if deployment_name not in self.deployments:
            raise _NotFound(deployment_name)
        del self.deployments[deployment_name]
        return FakePoller(value=None)


@dataclass
class FakeAccountsOperations:
    fail: bool = False

    def list_keys(
        self,
        *,
        resource_group_name: str,
        account_name: str,
    ) -> FakeAccountKeys:
        if self.fail:
            raise RuntimeError("list_keys boom")
        return FakeAccountKeys()


@dataclass
class FakeResourceLocksOperations:
    locks: list[Any] = field(default_factory=list)

    def list_at_resource_level(
        self,
        *,
        resource_group_name: str,
        resource_provider_namespace: str,
        parent_resource_path: str,
        resource_type: str,
        resource_name: str,
    ) -> list[Any]:
        return list(self.locks)


@dataclass
class FakeStudioWorkflowsOperations:
    refs: list[str] = field(default_factory=list)

    def list_referencing_deployment(
        self,
        *,
        resource_group_name: str,
        account_name: str,
        deployment_name: str,
    ) -> list[str]:
        return list(self.refs)


@dataclass
class FakeMgmtClient:
    deployments_obj: FakeDeploymentsOperations = field(
        default_factory=FakeDeploymentsOperations,
    )
    accounts_obj: FakeAccountsOperations = field(
        default_factory=FakeAccountsOperations,
    )
    resource_locks_obj: FakeResourceLocksOperations | None = None
    studio_workflows_obj: FakeStudioWorkflowsOperations | None = None

    @property
    def deployments(self) -> FakeDeploymentsOperations:
        return self.deployments_obj

    @property
    def accounts(self) -> FakeAccountsOperations:
        return self.accounts_obj

    @property
    def resource_locks(self) -> FakeResourceLocksOperations | None:
        return self.resource_locks_obj

    @property
    def studio_workflows(self) -> FakeStudioWorkflowsOperations | None:
        return self.studio_workflows_obj


@dataclass
class FakeSecretClient:
    secrets: dict[str, str] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)

    def set_secret(self, name: str, value: str) -> None:
        self.secrets[name] = value

    def begin_delete_secret(self, name: str) -> Any:
        if name not in self.secrets:
            raise _NotFound(name)
        del self.secrets[name]
        self.deleted.append(name)
        return FakePoller(value=None)


# ---- fixtures ---------------------------------------------------


@pytest.fixture
def mgmt() -> FakeMgmtClient:
    return FakeMgmtClient()


@pytest.fixture
def secrets_client() -> FakeSecretClient:
    return FakeSecretClient()


@pytest.fixture
def driver(
    mgmt: FakeMgmtClient,
    secrets_client: FakeSecretClient,
) -> AzureOpenAIDriver:
    return AzureOpenAIDriver(
        config=AzureOpenAIConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            account_name="aoai-acme",
            location="eastus",
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            secret_client=secrets_client,
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
        service_handle_hint="model",
        size="small",
        binding_id=BINDING,
        managed_service_id=OWNER,
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_deployment(
    driver: AzureOpenAIDriver,
    mgmt: FakeMgmtClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    assert result.handle.startswith(f"{KIND}/")
    assert len(mgmt.deployments_obj.deployments) == 1


def test_provision_defaults_to_standard_sku_gpt35turbo(
    driver: AzureOpenAIDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec())
    create = mgmt.deployments_obj.create_calls[0]
    assert create["parameters"]["sku"]["name"] == "Standard"
    assert create["parameters"]["sku"]["capacity"] == 10
    assert create["parameters"]["properties"]["model"]["name"] == "gpt-35-turbo"


def test_provision_idempotent(
    driver: AzureOpenAIDriver,
    mgmt: FakeMgmtClient,
) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message
    assert len(mgmt.deployments_obj.create_calls) == 1


def test_provision_persists_api_key_in_key_vault(
    driver: AzureOpenAIDriver,
    secrets_client: FakeSecretClient,
) -> None:
    result = driver.provision(_spec())
    deployment_name = result.handle.split("/", 1)[1]
    secret = f"astrolift-aoai-aoai-acme-{deployment_name}-key"
    assert secret in secrets_client.secrets
    assert secrets_client.secrets[secret]


def test_provision_xlarge_picks_provisioned_managed(
    driver: AzureOpenAIDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec(size="xlarge"))
    create = mgmt.deployments_obj.create_calls[0]
    assert create["parameters"]["sku"]["name"] == "ProvisionedManaged"


def test_provision_large_picks_gpt4(
    driver: AzureOpenAIDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec(size="large"))
    create = mgmt.deployments_obj.create_calls[0]
    assert create["parameters"]["properties"]["model"]["name"] == "gpt-4"


def test_provision_honours_spec_config_override(
    driver: AzureOpenAIDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(
        _spec(
            config={
                "sku": "Standard",
                "capacity": 50,
                "model_name": "gpt-4",
                "model_version": "1106-preview",
                "rai_policy_name": "Custom.Strict",
            },
        ),
    )
    params = mgmt.deployments_obj.create_calls[0]["parameters"]
    assert params["sku"]["name"] == "Standard"
    assert params["sku"]["capacity"] == 50
    assert params["properties"]["model"]["name"] == "gpt-4"
    assert params["properties"]["model"]["version"] == "1106-preview"
    assert params["properties"]["rai_policy_name"] == "Custom.Strict"


def test_provision_tags_deployment_with_astrolift_namespace(
    driver: AzureOpenAIDriver,
    mgmt: FakeMgmtClient,
) -> None:
    driver.provision(_spec())
    tags = mgmt.deployments_obj.create_calls[0]["parameters"]["tags"]
    assert tags["astrolift-managed-by"] == "platform"
    assert tags["astrolift-app"] == "api"


def test_provision_without_keyvault_returns_error() -> None:
    d = AzureOpenAIDriver(
        config=AzureOpenAIConfig(
            subscription_id="sub-1",
            resource_group="rg",
            account_name="acct",
            mgmt_client=FakeMgmtClient(),
        ),
    )
    result = d.provision(_spec())
    assert not result.ok
    assert "Key Vault" in result.message


def test_provision_surfaces_list_keys_failure(
    secrets_client: FakeSecretClient,
) -> None:
    mgmt = FakeMgmtClient(
        accounts_obj=FakeAccountsOperations(fail=True),
    )
    d = AzureOpenAIDriver(
        config=AzureOpenAIConfig(
            subscription_id="sub-1",
            resource_group="rg",
            account_name="acct",
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            secret_client=secrets_client,
        ),
    )
    result = d.provision(_spec())
    assert not result.ok
    assert "list_keys" in result.message


# ---- update -----------------------------------------------------


def test_update_resize_bumps_capacity(
    driver: AzureOpenAIDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(handle=provisioned.handle, size="large", managed_service_id=OWNER),
    )
    assert result.ok
    last = mgmt.deployments_obj.update_calls[-1]
    assert last["parameters"]["sku"]["capacity"] == 100


def test_update_model_version_passes_through(
    driver: AzureOpenAIDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"model_version": "0301"},
            managed_service_id=OWNER,
        ),
    )
    assert result.ok
    last = mgmt.deployments_obj.update_calls[-1]
    assert last["parameters"]["properties"]["model"]["version"] == "0301"


def test_update_noop_when_nothing_to_change(
    driver: AzureOpenAIDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    before = len(mgmt.deployments_obj.update_calls)
    result = driver.update(UpdateSpec(handle=provisioned.handle, managed_service_id=OWNER))
    assert result.ok
    assert "no-op" in result.message
    assert len(mgmt.deployments_obj.update_calls) == before


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_keeps_api_key(
    driver: AzureOpenAIDriver,
    secrets_client: FakeSecretClient,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    deployment_name = provisioned.handle.split("/", 1)[1]
    secret = f"astrolift-aoai-aoai-acme-{deployment_name}-key"
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle, managed_service_id=OWNER),
    )
    assert result.ok
    assert "api_key=retained" in result.message
    assert secret in secrets_client.secrets
    assert deployment_name in mgmt.deployments_obj.delete_calls


def test_deprovision_delete_data_purges_api_key(
    driver: AzureOpenAIDriver,
    secrets_client: FakeSecretClient,
) -> None:
    provisioned = driver.provision(_spec())
    deployment_name = provisioned.handle.split("/", 1)[1]
    secret = f"astrolift-aoai-aoai-acme-{deployment_name}-key"
    assert secret in secrets_client.secrets

    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle, managed_service_id=OWNER),
        delete_data=True,
    )
    assert result.ok
    assert "api_key=purged" in result.message
    assert secret not in secrets_client.secrets


def test_deprovision_refuses_with_resource_lock(
    driver: AzureOpenAIDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    mgmt.resource_locks_obj = FakeResourceLocksOperations(
        locks=["some-lock"],
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle, managed_service_id=OWNER),
    )
    assert not result.ok
    assert "resource lock" in result.message


def test_deprovision_force_destroy_bypasses_resource_lock(
    driver: AzureOpenAIDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    mgmt.resource_locks_obj = FakeResourceLocksOperations(
        locks=["some-lock"],
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle, managed_service_id=OWNER),
        force_destroy=True,
    )
    assert result.ok


def test_deprovision_refuses_when_studio_workflow_references(
    driver: AzureOpenAIDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    mgmt.studio_workflows_obj = FakeStudioWorkflowsOperations(
        refs=["ai-studio-flow-1", "ai-studio-flow-2"],
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle, managed_service_id=OWNER),
    )
    assert not result.ok
    assert "Studio" in result.message
    assert "2" in result.message


def test_deprovision_force_destroy_bypasses_studio_reference(
    driver: AzureOpenAIDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    mgmt.studio_workflows_obj = FakeStudioWorkflowsOperations(
        refs=["ai-studio-flow-1"],
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle, managed_service_id=OWNER),
        force_destroy=True,
    )
    assert result.ok


def test_deprovision_atomic_both_flags(
    driver: AzureOpenAIDriver,
    secrets_client: FakeSecretClient,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    deployment_name = provisioned.handle.split("/", 1)[1]
    secret = f"astrolift-aoai-aoai-acme-{deployment_name}-key"
    mgmt.resource_locks_obj = FakeResourceLocksOperations(
        locks=["lock"],
    )
    mgmt.studio_workflows_obj = FakeStudioWorkflowsOperations(
        refs=["flow"],
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle, managed_service_id=OWNER),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert secret not in secrets_client.secrets


def test_deprovision_idempotent_when_already_gone(
    driver: AzureOpenAIDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle=f"{KIND}/never-existed", managed_service_id=OWNER),
    )
    assert result.ok
    assert "already gone" in result.message


def test_deprovision_surfaces_delete_failure(
    driver: AzureOpenAIDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())

    def boom(**_kwargs):
        raise RuntimeError("ResourceConflict: in-use")

    mgmt.deployments_obj.begin_delete = boom  # type: ignore[assignment]
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle, managed_service_id=OWNER),
        force_destroy=True,
    )
    assert not result.ok
    assert "ResourceConflict" in result.message


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: AzureOpenAIDriver,
) -> None:
    state = driver.status(ServiceHandle(handle=f"{KIND}/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_succeeded_to_available(
    driver: AzureOpenAIDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


def test_status_maps_creating_to_provisioning(
    driver: AzureOpenAIDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    deployment_name = provisioned.handle.split("/", 1)[1]
    mgmt.deployments_obj.deployments[deployment_name].provisioning_state = "Creating"
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "provisioning"


def test_status_maps_failed_to_error(
    driver: AzureOpenAIDriver,
    mgmt: FakeMgmtClient,
) -> None:
    provisioned = driver.provision(_spec())
    deployment_name = provisioned.handle.split("/", 1)[1]
    mgmt.deployments_obj.deployments[deployment_name].provisioning_state = "Failed"
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "error"


# ---- binding ----------------------------------------------------


def test_binding_returns_connection_envelope(
    driver: AzureOpenAIDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    env = binding.env_vars
    for key in (
        "MODEL_ENDPOINT_URL",
        "MODEL_ENDPOINT_MODEL_ID",
        "MODEL_ENDPOINT_PROVIDER",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT_NAME",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_MODEL_NAME",
    ):
        assert key in env
    assert env["MODEL_ENDPOINT_PROVIDER"].literal == "azure_openai"
    assert env["AZURE_OPENAI_API_KEY"].secret_ref is not None
    assert env["AZURE_OPENAI_API_KEY"].secret_ref.startswith("azure-kv://kv.vault.azure.net/secrets/")
    assert env["AZURE_OPENAI_API_KEY"].literal is None
    assert env["AZURE_OPENAI_ENDPOINT"].literal.endswith(
        ".openai.azure.com",
    )
    assert env["AZURE_OPENAI_API_VERSION"].literal == "2024-02-15-preview"


def test_binding_iam_grants_cover_account_and_keyvault(
    driver: AzureOpenAIDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    actions = {a for g in binding.iam_grants for a in g.actions}
    assert any("Microsoft.CognitiveServices" in a for a in actions)
    assert any("KeyVault" in a for a in actions)


def test_binding_for_missing_raises(
    driver: AzureOpenAIDriver,
) -> None:
    with pytest.raises(AzureOpenAIError):
        driver.binding(ServiceHandle(handle=f"{KIND}/missing"))


# ---- snapshot + restore -----------------------------------------


def test_snapshot_is_explicitly_unsupported(
    driver: AzureOpenAIDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError, match="stateless configuration"):
        driver.snapshot(ServiceHandle(handle=f"{KIND}/anything", managed_service_id=OWNER))


def test_restore_is_explicitly_unsupported(
    driver: AzureOpenAIDriver,
) -> None:
    from _sdk.managed_service import SnapshotHandle

    snapshot = SnapshotHandle(f"{KIND}/source", "not-a-backup", "2026-08-14T00:00:00Z")
    with pytest.raises(UnsupportedOperationError, match="declarative spec"):
        driver.restore(snapshot, _spec(service_handle_hint="restored"))


# ---- naming + helpers -------------------------------------------


def test_deployment_name_canonicalization(
    driver: AzureOpenAIDriver,
) -> None:
    name = driver._deployment_name_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
            service_handle_hint="model",
        ),
    )
    assert 2 <= len(name) <= 64
    assert name[0].isalnum()
    for c in name:
        assert c.islower() or c.isdigit() or c in "-_"
    assert "--" not in name
    assert not name.startswith("-") and not name.endswith("-")


def test_handle_round_trip(driver: AzureOpenAIDriver) -> None:
    handle = driver._handle_for(  # type: ignore[attr-defined]
        deployment_name="my-deploy",
    )
    assert handle.startswith(f"{KIND}/")
    assert (
        driver._deployment_name_from_handle(handle)  # type: ignore[attr-defined]
        == "my-deploy"
    )


def test_handle_rejects_malformed(
    driver: AzureOpenAIDriver,
) -> None:
    with pytest.raises(AzureOpenAIError):
        driver._deployment_name_from_handle("no-slash")  # type: ignore[attr-defined]
    with pytest.raises(AzureOpenAIError):
        driver._deployment_name_from_handle("model_endpoint/")  # type: ignore[attr-defined]


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(driver: AzureOpenAIDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in (
        "sku",
        "capacity",
        "model_name",
        "model_version",
        "rai_policy_name",
        "version_upgrade_option",
    ):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: AzureOpenAIDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "MODEL_ENDPOINT_URL",
        "MODEL_ENDPOINT_MODEL_ID",
        "MODEL_ENDPOINT_PROVIDER",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT_NAME",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_MODEL_NAME",
    ):
        assert key in schema.env_vars
