from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from jsonschema import Draft202012Validator

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


RUNTIME_ACCOUNT = "function@project-1.iam.gserviceaccount.com"
BUILD_ACCOUNT = "builder@project-1.iam.gserviceaccount.com"
TRIGGER_ACCOUNT = "trigger@project-1.iam.gserviceaccount.com"


@pytest.fixture
def config() -> CloudFunctionsConfig:
    return CloudFunctionsConfig(
        project_id="project-1",
        region="us-central1",
        operation_timeout_seconds=1,
        poll_interval_seconds=0,
        allowed_service_accounts=(RUNTIME_ACCOUNT, BUILD_ACCOUNT, TRIGGER_ACCOUNT),
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
        "build_service_account": f"projects/project-1/serviceAccounts/{BUILD_ACCOUNT}",
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
        "service_account_email": RUNTIME_ACCOUNT,
        "ingress": "ALLOW_INTERNAL_AND_GCLB",
        "vpc_connector": "projects/project-1/locations/us-central1/connectors/functions",
        "vpc_connector_egress": "ALL_TRAFFIC",
        "binary_authorization_policy": "projects/project-1/policyPlatform/binauthzPolicy",
        "kms_key": "projects/project-1/locations/us-central1/keyRings/app/cryptoKeys/functions",
        "event_trigger": {
            "event_type": "google.cloud.pubsub.topic.v1.messagePublished",
            "trigger_region": "us-central1",
            "pubsub_topic": "projects/project-1/topics/billing-events",
            "service_account_email": TRIGGER_ACCOUNT,
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


def test_collision_is_refused_and_no_config_flag_adopts_or_reassigns(
    driver: CloudFunctionsDriver,
    client: FakeFunctions,
) -> None:
    name = "projects/project-1/locations/us-central1/functions/billing-webhook"
    client.resources[name] = {"name": name, "state": "ACTIVE", "labels": {"owner": "customer"}}
    denied = driver.provision(replace(SPEC, config=_full_config()))
    assert not denied.ok
    assert "operator-authorized" in denied.message

    # The flags are gone entirely: the schema tenant config is validated
    # against rejects them, and a driver handed one anyway still refuses
    # (#2021).
    validator = Draft202012Validator(driver.config_schema())
    assert validator.is_valid(_full_config())
    for flag in ("adopt_existing", "reassign_existing"):
        assert not validator.is_valid({**_full_config(), flag: True})
    still_denied = driver.provision(replace(SPEC, config={**_full_config(), "adopt_existing": True}))
    assert not still_denied.ok
    assert client.resources[name]["labels"] == {"owner": "customer"}

    del client.resources[name]
    assert driver.provision(replace(SPEC, config=_full_config())).ok
    owned_labels = dict(client.resources[name]["labels"])
    other = replace(SPEC, managed_service_id="other-id")
    for cfg in (_full_config(), {**_full_config(), "reassign_existing": True}):
        refused = driver.provision(replace(other, config=cfg))
        assert not refused.ok
        assert "another managed service" in refused.message
    assert client.resources[name]["labels"] == owned_labels


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


def _minimal(**extra: Any) -> dict[str, Any]:
    return {"runtime": "python314", "storage_source": {"bucket": "b", "object": "o"}, **extra}


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        pytest.param(
            {"service_account_email": "platform-admin@project-1.iam.gserviceaccount.com"},
            "service_account_email 'platform-admin@project-1.iam.gserviceaccount.com' is not allowed",
            id="runtime-account-not-allowlisted",
        ),
        pytest.param(
            {"build_service_account": "projects/-/serviceAccounts/platform-admin@project-1.iam.gserviceaccount.com"},
            "build_service_account 'platform-admin@project-1.iam.gserviceaccount.com' is not allowed",
            id="build-account-not-allowlisted",
        ),
        pytest.param(
            {"secret_environment": [{"key": "T", "secret": "token", "version": "7", "project_id": "victim"}]},
            "secret ids in the function project project-1",
            id="secret-env-in-another-project",
        ),
        pytest.param(
            {"secret_environment": [{"key": "T", "secret": "token", "version": "7", "project_id": "123456789012"}]},
            "secret ids in the function project project-1",
            id="secret-env-project-number",
        ),
        pytest.param(
            {"secret_environment": [{"key": "T", "secret": "projects/victim/secrets/token", "version": "7"}]},
            "secret ids in the function project project-1",
            id="secret-env-resource-path",
        ),
        pytest.param(
            {
                "secret_volumes": [
                    {
                        "mount_path": "/etc/s",
                        "secret": "token",
                        "project_id": "victim",
                        "versions": [{"version": "7", "path": "t"}],
                    }
                ]
            },
            "secret volume secrets must be secret ids in the function project project-1",
            id="secret-volume-in-another-project",
        ),
    ],
)
def test_identity_and_secret_project_escapes_fail_closed(
    driver: CloudFunctionsDriver,
    client: FakeFunctions,
    extra: dict[str, Any],
    message: str,
) -> None:
    """The tenant's code runs as the function's runtime and build accounts and
    Google reads each secret with that identity, so an account outside the
    install's allowlist, or a secret outside the install project, is refused
    before any API call (#1921)."""
    result = driver.provision(replace(SPEC, config=_minimal(**extra)))
    assert not result.ok and message in result.message
    assert client.calls == []


def test_an_empty_allowlist_refuses_every_config_supplied_account(client: FakeFunctions) -> None:
    driver = CloudFunctionsDriver(
        config=CloudFunctionsConfig(project_id="project-1", region="us-central1"),
        client=client,
        sleep=lambda _: None,
    )
    refused = driver.provision(replace(SPEC, config=_minimal(service_account_email=RUNTIME_ACCOUNT)))
    assert not refused.ok and "cloud_functions_allowed_service_accounts" in refused.message
    # Omitted, Google runs it as the project's default account, as before.
    assert driver.provision(replace(SPEC, config=_minimal())).ok


def test_allowlisted_accounts_and_install_project_secrets_are_accepted(
    driver: CloudFunctionsDriver,
    client: FakeFunctions,
) -> None:
    result = driver.provision(
        replace(
            SPEC,
            config=_minimal(
                service_account_email=RUNTIME_ACCOUNT.upper(),
                build_service_account=f"projects/-/serviceAccounts/{BUILD_ACCOUNT}",
                secret_environment=[{"key": "T", "secret": "token", "version": "7", "project_id": "project-1"}],
                secret_volumes=[{"mount_path": "/etc/s", "secret": "ca", "versions": [{"version": "3", "path": "ca"}]}],
            ),
        ),
    )
    assert result.ok, result.message


def test_update_refuses_an_account_outside_the_allowlist(driver: CloudFunctionsDriver, client: FakeFunctions) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    updated = driver.update(
        UpdateSpec(result.handle, config={"service_account_email": "platform-admin@project-1.iam.gserviceaccount.com"}),
    )
    assert not updated.ok and "not allowed by the cluster install policy" in updated.message
    assert not [call for call in client.calls if call[0] == "patch"]


VICTIM = "platform-admin@project-1.iam.gserviceaccount.com"


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        # Google's JSON parser takes a field's proto name as well as its JSON
        # name, so a raw key in either spelling reaches the same field (#1921).
        pytest.param(
            {"service_raw_fields": {"service_account_email": VICTIM}},
            "service_raw_fields must use the API's lowerCamelCase JSON field names, not service_account_email",
            id="runtime-account-proto-name",
        ),
        pytest.param(
            {"build_raw_fields": {"service_account": f"projects/-/serviceAccounts/{VICTIM}"}},
            "build_raw_fields must use the API's lowerCamelCase JSON field names, not service_account",
            id="build-account-proto-name",
        ),
        pytest.param(
            {
                "service_raw_fields": {
                    "secret_environment_variables": [
                        {"key": "T", "secret": "token", "version": "7", "project_id": "other-project"}
                    ]
                }
            },
            "not secret_environment_variables",
            id="secret-env-proto-name",
        ),
        pytest.param(
            {"service_raw_fields": {"ServiceAccountEmail": VICTIM}},
            "service_raw_fields cannot set structured or output fields: ServiceAccountEmail",
            id="runtime-account-other-case",
        ),
        pytest.param(
            {"raw_fields": {"service_config": {"serviceAccountEmail": VICTIM}}},
            "raw_fields must use the API's lowerCamelCase JSON field names, not service_config",
            id="service-config-proto-name",
        ),
        pytest.param(
            {"raw_fields": {"build_config": {"serviceAccount": f"projects/-/serviceAccounts/{VICTIM}"}}},
            "raw_fields must use the API's lowerCamelCase JSON field names, not build_config",
            id="build-config-proto-name",
        ),
        pytest.param(
            {"event_trigger": {"event_type": "t", "service_account_email": VICTIM}},
            f"event_trigger.service_account_email {VICTIM!r} is not allowed",
            id="trigger-account-not-allowlisted",
        ),
        pytest.param(
            {"event_trigger": {"event_type": "t", "raw_fields": {"service_account_email": VICTIM}}},
            "event_trigger.raw_fields must use the API's lowerCamelCase JSON field names",
            id="trigger-account-proto-name",
        ),
        # _camelize let the later spelling win, and the project check read
        # only project_id.
        pytest.param(
            {
                "secret_environment": [
                    {"key": "T", "secret": "token", "version": "7", "project_id": None, "projectId": "victim"}
                ]
            },
            "secret_environment spells projectId more than one way",
            id="secret-env-both-spellings",
        ),
        pytest.param(
            {"secret_environment": [{"key": "T", "secret": "token", "version": "7", "projectId": "victim"}]},
            "secret_environment secrets must be secret ids in the function project project-1",
            id="secret-env-json-spelling-only",
        ),
        pytest.param(
            {
                "secret_volumes": [
                    {
                        "mount_path": "/etc/s",
                        "mountPath": "/etc/t",
                        "secret": "token",
                        "versions": [{"version": "7", "path": "t"}],
                    }
                ]
            },
            "secret_volumes spells mountPath more than one way",
            id="secret-volume-both-spellings",
        ),
        pytest.param(
            {
                "secret_volumes": [
                    {
                        "mount_path": "/etc/s",
                        "secret": "token",
                        "projectId": "victim",
                        "versions": [{"version": "7", "path": "t"}],
                    }
                ]
            },
            "secret volume secrets must be secret ids in the function project project-1",
            id="secret-volume-json-spelling-only",
        ),
        pytest.param(
            {"clear_fields": ["service_config"]},
            "clear_fields must use the API's lowerCamelCase JSON field names, not service_config",
            id="clear-proto-name",
        ),
        pytest.param(
            {"clear_fields": ["ServiceConfig"]},
            "clear_fields cannot clear structured or output fields: ServiceConfig",
            id="clear-other-case",
        ),
    ],
)
def test_a_second_spelling_cannot_carry_a_field_past_its_check(
    driver: CloudFunctionsDriver,
    client: FakeFunctions,
    extra: dict[str, Any],
    message: str,
) -> None:
    result = driver.provision(replace(SPEC, config=_minimal(**extra)))
    assert not result.ok and message in result.message
    assert client.calls == []


@pytest.mark.parametrize(
    "raw_fields",
    [
        pytest.param({"service_config": {"serviceAccountEmail": VICTIM}}, id="service-config"),
        pytest.param({"build_config": {"serviceAccount": f"projects/-/serviceAccounts/{VICTIM}"}}, id="build-config"),
    ],
)
def test_update_refuses_a_proto_named_raw_field(
    driver: CloudFunctionsDriver,
    client: FakeFunctions,
    raw_fields: dict[str, Any],
) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    updated = driver.update(UpdateSpec(result.handle, config={"raw_fields": raw_fields}))
    assert not updated.ok and "lowerCamelCase JSON field names" in updated.message
    assert not [call for call in client.calls if call[0] == "patch"]


def test_the_body_refuses_a_field_spelled_twice() -> None:
    """The backstop under _validate: _camelize no longer lets one spelling
    silently win."""
    from gcp.managed.faas_cloud_functions import _camelize

    with pytest.raises(CloudFunctionsError, match="projectId is spelled more than one way"):
        _camelize([{"secret": "token", "project_id": None, "projectId": "victim"}])


def test_a_json_named_raw_field_the_driver_does_not_model_still_passes(
    driver: CloudFunctionsDriver,
    client: FakeFunctions,
) -> None:
    result = driver.provision(replace(SPEC, config=_minimal(service_raw_fields={"futureKnob": 3})))
    assert result.ok, result.message
    created = next(call for call in client.calls if call[0] == "create")
    assert created[2]["serviceConfig"]["futureKnob"] == 3


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
