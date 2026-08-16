from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from gcp.managed.faas_cloud_functions import (
    CloudFunctionsConfig,
    CloudFunctionsConflict,
    CloudFunctionsDriver,
    CloudFunctionsError,
    CloudFunctionsNotFound,
    CloudFunctionsRestClient,
)

SPEC = ProvisionSpec(
    organization_id="org-id",
    organization_slug="acme",
    app_id="app-id",
    app_slug="billing",
    environment_id="env-id",
    environment_name="production",
    tenant_cluster_id="cluster-id",
    service_handle_hint="webhook",
    size="small",
    binding_id="binding-id",
    managed_service_id="managed-id",
)


class FakeFunctions:
    def __init__(self) -> None:
        self.resources: dict[str, dict[str, Any]] = {}
        self.operations: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[Any, ...]] = []
        self._operation = 0

    def get(self, name: str) -> dict[str, Any]:
        self.calls.append(("get", name))
        if name not in self.resources:
            raise CloudFunctionsNotFound(name)
        return deepcopy(self.resources[name])

    def create(self, parent: str, function_id: str, body: dict[str, Any]) -> dict[str, Any]:
        name = f"{parent}/functions/{function_id}"
        self.calls.append(("create", name, deepcopy(body)))
        if name in self.resources:
            raise CloudFunctionsConflict(name)
        resource = {
            "name": name,
            "state": "ACTIVE",
            "url": f"https://{function_id}-legacy.example.test",
            **deepcopy(body),
        }
        resource.setdefault("serviceConfig", {})["uri"] = f"https://{function_id}.example.test"
        self.resources[name] = resource
        return self._done(resource)

    def patch(self, name: str, body: dict[str, Any], update_mask: list[str]) -> dict[str, Any]:
        self.calls.append(("patch", name, deepcopy(body), list(update_mask)))
        if name not in self.resources:
            raise CloudFunctionsNotFound(name)
        for key, value in deepcopy(body).items():
            if key in {"buildConfig", "serviceConfig", "eventTrigger"} and isinstance(value, dict):
                self.resources[name].setdefault(key, {}).update(value)
            else:
                self.resources[name][key] = value
        self.resources[name]["state"] = "ACTIVE"
        return self._done(self.resources[name])

    def delete(self, name: str) -> dict[str, Any]:
        self.calls.append(("delete", name))
        if name not in self.resources:
            raise CloudFunctionsNotFound(name)
        self.resources.pop(name)
        return self._done({})

    def get_operation(self, name: str) -> dict[str, Any]:
        return deepcopy(self.operations[name])

    def _done(self, response: dict[str, Any]) -> dict[str, Any]:
        self._operation += 1
        operation = {
            "name": f"projects/p/locations/l/operations/{self._operation}",
            "done": True,
            "response": deepcopy(response),
        }
        self.operations[operation["name"]] = operation
        return operation


@pytest.fixture
def config() -> CloudFunctionsConfig:
    return CloudFunctionsConfig(
        project_id="project-1",
        region="us-central1",
        operation_timeout_seconds=1,
        poll_interval_seconds=0,
    )


@pytest.fixture
def client() -> FakeFunctions:
    return FakeFunctions()


@pytest.fixture
def driver(config: CloudFunctionsConfig, client: FakeFunctions) -> CloudFunctionsDriver:
    return CloudFunctionsDriver(config=config, client=client, sleep=lambda _: None)


def _full_config() -> dict[str, Any]:
    return {
        "function_id": "billing-webhook",
        "runtime": "python314",
        "entry_point": "receive",
        "storage_source": {
            "bucket": "function-sources",
            "object": "billing/sha256.tar.gz",
            "generation": "7",
        },
        "build_environment": {"GOOGLE_FUNCTION_SOURCE": "main.py"},
        "build_service_account": "projects/project-1/serviceAccounts/builder@project-1.iam.gserviceaccount.com",
        "worker_pool": "projects/build/locations/us-central1/workerPools/private",
        "docker_repository": "projects/project-1/locations/us-central1/repositories/functions",
        "automatic_runtime_updates": True,
        "available_memory": "1Gi",
        "available_cpu": "1",
        "timeout_seconds": 120,
        "min_instances": 1,
        "max_instances": 20,
        "max_instance_concurrency": 40,
        "environment": {"MODE": "production"},
        "secret_environment": [
            {
                "key": "API_TOKEN",
                "secret": "billing-api-token",
                "version": "7",
                "project_id": "project-1",
            },
        ],
        "secret_volumes": [
            {
                "mount_path": "/etc/certs",
                "secret": "billing-ca",
                "versions": [{"version": "3", "path": "ca.pem"}],
            },
        ],
        "service_account_email": "function@project-1.iam.gserviceaccount.com",
        "ingress": "ALLOW_INTERNAL_AND_GCLB",
        "vpc_connector": "projects/project-1/locations/us-central1/connectors/functions",
        "vpc_connector_egress": "ALL_TRAFFIC",
        "binary_authorization_policy": "projects/project-1/policyPlatform/binauthzPolicy",
        "kms_key": "projects/project-1/locations/us-central1/keyRings/app/cryptoKeys/functions",
        "event_trigger": {
            "event_type": "google.cloud.pubsub.topic.v1.messagePublished",
            "trigger_region": "us-central1",
            "pubsub_topic": "projects/project-1/topics/billing-events",
            "service_account_email": "trigger@project-1.iam.gserviceaccount.com",
            "retry_policy": "RETRY_POLICY_RETRY",
            "event_filters": [{"attribute": "type", "value": "invoice", "operator": "match-path-pattern"}],
        },
        "description": "Billing webhook",
        "labels": {"team": "payments"},
    }


def test_provision_reconciles_full_gen2_function(
    driver: CloudFunctionsDriver,
    client: FakeFunctions,
) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    assert result.ok and result.ready
    assert result.handle == "faas/us-central1/billing-webhook"
    name = "projects/project-1/locations/us-central1/functions/billing-webhook"
    function = client.resources[name]
    assert function["environment"] == "GEN_2"
    assert function["buildConfig"]["runtime"] == "python314"
    assert function["buildConfig"]["source"]["storageSource"]["generation"] == "7"
    assert function["buildConfig"]["automaticUpdatePolicy"] == {}
    assert function["serviceConfig"]["secretEnvironmentVariables"][0]["projectId"] == "project-1"
    assert function["serviceConfig"]["vpcConnectorEgressSettings"] == "ALL_TRAFFIC"
    assert function["eventTrigger"]["eventFilters"][0]["attribute"] == "type"
    assert function["kmsKeyName"].endswith("/functions")
    assert function["labels"]["astrolift-io-managed-service-id"] == "managed-id"


def test_repeated_provision_is_idempotent(
    driver: CloudFunctionsDriver,
    client: FakeFunctions,
) -> None:
    assert driver.provision(replace(SPEC, config=_full_config())).ok
    assert driver.provision(replace(SPEC, config=_full_config())).ok
    assert len([call for call in client.calls if call[0] == "create"]) == 1


def test_update_uses_only_changed_top_level_fields(
    driver: CloudFunctionsDriver,
    client: FakeFunctions,
) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    updated = driver.update(
        UpdateSpec(
            result.handle,
            config={
                "available_memory": "2Gi",
                "timeout_seconds": 240,
                "description": "Updated",
            },
        ),
    )
    assert updated.ok
    patch = [call for call in client.calls if call[0] == "patch"][-1]
    assert set(patch[3]) == {
        "description",
        "serviceConfig.availableMemory",
        "serviceConfig.timeoutSeconds",
    }
    assert patch[2]["serviceConfig"] == {"availableMemory": "2Gi", "timeoutSeconds": 240}
    function = client.resources["projects/project-1/locations/us-central1/functions/billing-webhook"]
    assert function["serviceConfig"]["vpcConnector"].endswith("/functions")
    assert function["serviceConfig"]["secretEnvironmentVariables"][0]["key"] == "API_TOKEN"
    assert function["buildConfig"]["source"]["storageSource"]["generation"] == "7"


def test_binding_exposes_portable_contract_and_scoped_roles(
    driver: CloudFunctionsDriver,
) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    binding = driver.binding(ServiceHandle(result.handle), {"access_mode": "manage"})
    assert binding.env_vars["FUNCTION_NAME"].literal == "billing-webhook"
    assert binding.env_vars["FUNCTION_URL"].literal == "https://billing-webhook.example.test"
    assert binding.env_vars["GCP_CLOUD_FUNCTION_NAME"].literal.endswith("/billing-webhook")
    # #1402: the rest of the faas envelope. FUNCTION_ARN is the portable
    # resource-locator slot, which on GCP is the fully qualified resource name.
    assert binding.env_vars["FUNCTION_ARN"].literal == binding.env_vars["GCP_CLOUD_FUNCTION_NAME"].literal
    assert binding.env_vars["FUNCTION_REGION"].literal == binding.env_vars["GOOGLE_CLOUD_REGION"].literal
    assert [grant.actions for grant in binding.iam_grants] == [
        ["roles/run.invoker"],
        ["roles/cloudfunctions.developer"],
    ]
    assert all(grant.resource == "projects/project-1" for grant in binding.iam_grants)


def test_collision_requires_explicit_adoption_and_reassignment(
    driver: CloudFunctionsDriver,
    client: FakeFunctions,
) -> None:
    name = "projects/project-1/locations/us-central1/functions/billing-webhook"
    client.resources[name] = {"name": name, "state": "ACTIVE", "labels": {"owner": "customer"}}
    denied = driver.provision(replace(SPEC, config=_full_config()))
    assert not denied.ok and "adopt_existing" in denied.message
    adopted_cfg = _full_config()
    adopted_cfg["adopt_existing"] = True
    assert driver.provision(replace(SPEC, config=adopted_cfg)).ok
    assert client.resources[name]["labels"]["astrolift-io-adopted"] == "true"

    other = replace(SPEC, managed_service_id="other-id")
    denied = driver.provision(replace(other, config=adopted_cfg))
    assert not denied.ok and "another managed service" in denied.message
    adopted_cfg["reassign_existing"] = True
    assert driver.provision(replace(other, config=adopted_cfg)).ok


def test_deprovision_guards_adopted_and_protected_functions(
    driver: CloudFunctionsDriver,
    client: FakeFunctions,
) -> None:
    cfg = _full_config()
    result = driver.provision(replace(SPEC, config=cfg))
    protected = driver.deprovision(DeprovisionSpec(result.handle, cfg))
    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]
    name = "projects/project-1/locations/us-central1/functions/billing-webhook"
    client.resources[name]["labels"]["astrolift-io-adopted"] = "true"
    adopted = driver.deprovision(
        DeprovisionSpec(result.handle, {"deletion_protection": False}),
        force_destroy=True,
    )
    assert not adopted.ok and adopted.errors == ["adopted_resource_guard"]
    deleted = driver.deprovision(
        DeprovisionSpec(
            result.handle,
            {"deletion_protection": False, "delete_adopted": True},
        ),
        force_destroy=True,
    )
    assert deleted.ok and name not in client.resources
    assert driver.deprovision(DeprovisionSpec(result.handle, {}), force_destroy=True).ok


def test_status_surfaces_provider_state_messages(
    driver: CloudFunctionsDriver,
    client: FakeFunctions,
) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    name = "projects/project-1/locations/us-central1/functions/billing-webhook"
    client.resources[name]["state"] = "FAILED"
    client.resources[name]["stateMessages"] = [{"message": "build failed"}]
    status = driver.status(ServiceHandle(result.handle))
    assert status.state == "error" and "build failed" in status.message


@pytest.mark.parametrize(
    ("manifest_config", "message"),
    [
        ({}, "requires runtime"),
        ({"runtime": "python314"}, "exactly one"),
        (
            {
                "runtime": "python314",
                "storage_source": {"bucket": "b", "object": "o"},
                "repo_source": {"repo_name": "r", "commit_sha": "a"},
            },
            "mutually exclusive",
        ),
        (
            {"runtime": "python314", "storage_source": {"bucket": "b"}},
            "bucket and object",
        ),
        (
            {
                "runtime": "python314",
                "storage_source": {"bucket": "b", "object": "o", "generation": "latest"},
            },
            "generation must be numeric",
        ),
        (
            {
                "runtime": "python314",
                "repo_source": {"repo_name": "r", "branch_name": "main", "commit_sha": "a"},
            },
            "exactly one of branch_name",
        ),
        (
            {
                "runtime": "python314",
                "repo_source": {"repo_name": "r", "commit_sha": "a", "dir": "../outside"},
            },
            "source root",
        ),
        (
            {
                "runtime": "python314",
                "storage_source": {"bucket": "b", "object": "o"},
                "function_id": "Bad_Name",
            },
            "function_id",
        ),
        (
            {
                "runtime": "python314",
                "storage_source": {"bucket": "b", "object": "o"},
                "vpc_connector": "connector",
                "direct_vpc_network_interface": [{"network": "network"}],
            },
            "mutually exclusive",
        ),
        (
            {
                "runtime": "python314",
                "storage_source": {"bucket": "b", "object": "o"},
                "min_instances": 5,
                "max_instances": 2,
            },
            "min_instances",
        ),
        (
            {
                "runtime": "python314",
                "storage_source": {"bucket": "b", "object": "o"},
                "secret_environment": [{"key": "TOKEN", "secret": "token", "version": "latest"}],
            },
            "allow_latest",
        ),
        (
            {
                "runtime": "python314",
                "storage_source": {"bucket": "b", "object": "o"},
                "secret_volumes": [
                    {
                        "mount_path": "/etc/secrets",
                        "secret": "token",
                        "versions": [{"version": "7", "path": "../token"}],
                    },
                ],
            },
            "stay under mount_path",
        ),
        (
            {
                "runtime": "python314",
                "storage_source": {"bucket": "b", "object": "o"},
                "environment": {"TOKEN": "plaintext"},
                "secret_environment": [{"key": "TOKEN", "secret": "token", "version": "7"}],
            },
            "same key",
        ),
        (
            {
                "runtime": "python314",
                "storage_source": {"bucket": "b", "object": "o"},
                "raw_fields": {"labels": {"owner": "attacker"}},
            },
            "raw_fields",
        ),
        (
            {
                "runtime": "python314",
                "storage_source": {"bucket": "b", "object": "o"},
                "labels": {"astrolift.io/managed-by": "attacker"},
            },
            "reserved keys",
        ),
        (
            {
                "runtime": "python314",
                "storage_source": {"bucket": "b", "object": "o"},
                "service_raw_fields": {"serviceAccountEmail": "attacker@example.test"},
            },
            "service_raw_fields",
        ),
        (
            {
                "runtime": "python314",
                "storage_source": {"bucket": "b", "object": "o"},
                "event_trigger": {
                    "event_type": "example.event",
                    "raw_fields": {"eventType": "attacker.event"},
                },
            },
            "event_trigger.raw_fields",
        ),
        (
            {
                "runtime": "python314",
                "storage_source": {"bucket": "b", "object": "o"},
                "clear_fields": ["name"],
            },
            "clear_fields",
        ),
    ],
)
def test_invalid_configs_fail_closed(
    driver: CloudFunctionsDriver,
    manifest_config: dict[str, Any],
    message: str,
) -> None:
    result = driver.provision(replace(SPEC, config=manifest_config))
    assert not result.ok and message in result.message


def test_schema_and_snapshot_contract_are_honest(driver: CloudFunctionsDriver) -> None:
    properties = driver.config_schema()["properties"]
    for field in (
        "storage_source",
        "repo_source",
        "event_trigger",
        "secret_environment",
        "secret_volumes",
        "direct_vpc_network_interface",
        "kms_key",
        "raw_fields",
    ):
        assert field in properties
    assert "FUNCTION_URL" in driver.binding_schema().env_vars
    with pytest.raises(CloudFunctionsError, match="no snapshot API"):
        driver.snapshot(ServiceHandle("faas/us-central1/billing-webhook"))
    restored = driver.restore(SimpleNamespace(), SPEC)  # type: ignore[arg-type]
    assert not restored.ok and restored.errors == ["not_supported"]


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None, *, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = b"json" if payload is not None else b""

    def json(self) -> dict[str, Any]:
        return deepcopy(self._payload or {})


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def test_rest_client_uses_v2_paths_and_update_masks() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"name": "operations/create"}),
            FakeResponse(200, {"name": "operations/update"}),
            FakeResponse(200, {"name": "operations/delete"}),
        ],
    )
    client = CloudFunctionsRestClient(session=session)
    parent = "projects/p/locations/l"
    name = f"{parent}/functions/fn-name"
    client.create(parent, "fn-name", {"environment": "GEN_2"})
    client.patch(name, {"description": "new"}, ["description"])
    client.delete(name)
    assert session.calls[0]["params"] == {"functionId": "fn-name"}
    assert session.calls[1]["params"] == {"updateMask": "description"}
    assert session.calls[1]["json"]["name"] == name
    assert session.calls[2]["method"] == "DELETE"


def test_rest_client_maps_provider_errors_and_non_object_responses() -> None:
    session = FakeSession(
        [
            FakeResponse(404, {"error": {"message": "gone"}}),
            FakeResponse(409, {"error": {"message": "exists"}}),
            FakeResponse(403, {"error": {"message": "denied"}}),
        ],
    )
    client = CloudFunctionsRestClient(session=session)
    with pytest.raises(CloudFunctionsNotFound):
        client.get("missing")
    with pytest.raises(CloudFunctionsConflict):
        client.create("projects/p/locations/l", "fn-name", {})
    with pytest.raises(CloudFunctionsError, match="denied"):
        client.get("forbidden")


def test_operation_error_is_not_success(
    driver: CloudFunctionsDriver,
    client: FakeFunctions,
) -> None:
    client.get_operation = lambda name: {
        "name": name,
        "done": True,
        "error": {"message": "build quota exhausted"},
    }
    with pytest.raises(CloudFunctionsError, match="quota exhausted"):
        driver._wait({"name": "operations/wait", "done": False})
