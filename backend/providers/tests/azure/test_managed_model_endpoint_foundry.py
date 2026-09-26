"""Azure AI Foundry model endpoint (#2041), on the aoai driver's fakes."""

from __future__ import annotations

from typing import Any

import pytest

from _sdk.azure_ownership import OWNERSHIP_ERROR_CODE
from _sdk.managed_service import DeprovisionSpec, ServiceHandle, UpdateSpec
from azure.managed.model_endpoint_foundry import AzureFoundryConfig, AzureFoundryDriver
from tests.azure.test_managed_model_endpoint_aoai import FakeMgmtClient, FakeSecretClient, _spec

MODEL = {"model_format": "Meta", "model_name": "Llama-3.3-70B-Instruct", "model_version": "4"}


@pytest.fixture
def mgmt() -> FakeMgmtClient:
    return FakeMgmtClient()


@pytest.fixture
def driver(mgmt: FakeMgmtClient) -> AzureFoundryDriver:
    return AzureFoundryDriver(
        config=AzureFoundryConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            account_name="foundry-acme",
            location="eastus2",
            keyvault_url="https://kv.vault.azure.net",
            mgmt_client=mgmt,
            secret_client=FakeSecretClient(),
        ),
    )


def _foundry_spec(**config: Any):
    return _spec(config={**MODEL, **config})


def test_provision_deploys_the_publishers_model_on_global_standard(driver, mgmt) -> None:
    assert driver.provision(_foundry_spec()).ok
    params = mgmt.deployments_obj.create_calls[0]["parameters"]
    assert params["properties"]["model"] == {"format": "Meta", "name": "Llama-3.3-70B-Instruct", "version": "4"}
    assert params["sku"]["name"] == "GlobalStandard"


@pytest.mark.parametrize("missing", ["model_format", "model_name"])
def test_a_model_without_its_publisher_or_name_is_refused(driver, mgmt, missing) -> None:
    config = {k: v for k, v in MODEL.items() if k != missing}
    result = driver.provision(_spec(config=config))
    assert not result.ok and "model_format" in result.message
    assert mgmt.deployments_obj.create_calls == []


def test_binding_is_the_shared_model_envelope(driver) -> None:
    handle = driver.provision(_foundry_spec()).handle
    env = driver.binding(ServiceHandle(handle)).env_vars
    assert env["MODEL_ENDPOINT_URL"].literal == "https://foundry-acme.services.ai.azure.com/models"
    assert env["MODEL_API_KEY"].secret_ref
    assert env["MODEL_DEPLOYMENT_NAME"].literal == handle.split("/", 1)[1]
    assert env["MODEL_REGION"].literal == "eastus2"
    assert env["MODEL_API_STYLE"].literal == "azure_ai_inference"
    assert env["MODEL_AUTH_MODE"].literal == "api_key"
    assert env["MODEL_ENDPOINT_PROVIDER"].literal == "azure_foundry"
    assert not any(key.startswith("AZURE_OPENAI_") for key in env)


def test_update_keeps_the_publisher_format(driver, mgmt) -> None:
    handle = driver.provision(_foundry_spec()).handle
    owner = _spec().managed_service_id
    assert driver.update(UpdateSpec(handle, config={"model_version": "5"}, managed_service_id=owner)).ok
    assert mgmt.deployments_obj.update_calls[0]["parameters"]["properties"]["model"]["format"] == "Meta"


def test_deprovision_deletes_the_deployment(driver, mgmt) -> None:
    handle = driver.provision(_foundry_spec()).handle
    assert driver.deprovision(DeprovisionSpec(handle, managed_service_id=_spec().managed_service_id)).ok
    assert mgmt.deployments_obj.delete_calls


def test_another_services_deployment_is_not_adopted(driver) -> None:
    assert driver.provision(_foundry_spec()).ok
    refused = driver.provision(_spec(config=MODEL, managed_service_id="someone-else"))
    assert not refused.ok and refused.errors == [OWNERSHIP_ERROR_CODE]
