"""Lifecycle and request-shape tests for AWS Step Functions drivers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar

import pytest
from botocore.session import Session
from botocore.validate import validate_parameters

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed.workflow_step_functions import (
    StepFunctionsConfig,
    StepFunctionsExpressDriver,
    StepFunctionsStandardDriver,
)

_ROLE_ARN = "arn:aws:iam::123456789012:role/workflow"
_DEFINITION = {
    "Comment": "test",
    "StartAt": "Done",
    "States": {"Done": {"Type": "Succeed"}},
}


class NotFound(Exception):
    response: ClassVar[dict] = {"Error": {"Code": "StateMachineDoesNotExist"}}


class FakeStepFunctions:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.machines: dict[str, dict] = {}
        self.tags: dict[str, list[dict]] = {}
        self.aliases: dict[str, list[dict]] = {}
        self.versions: dict[str, dict] = {}
        self.executions: dict[str, list[dict]] = {}
        self.validation_result = "OK"
        self.validation_diagnostics: list[dict] = []
        self.seq = 0

    def _call(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs))

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def kwargs_for(self, name: str) -> dict:
        return next(kwargs for call_name, kwargs in self.calls if call_name == name)

    def validate_state_machine_definition(self, **kwargs):
        self._call("validate_state_machine_definition", kwargs)
        return {"result": self.validation_result, "diagnostics": self.validation_diagnostics}

    def list_state_machines(self, **kwargs):
        self._call("list_state_machines", kwargs)
        return {
            "stateMachines": [
                {
                    "stateMachineArn": machine["stateMachineArn"],
                    "name": machine["name"],
                    "type": machine["type"],
                    "creationDate": machine["creationDate"],
                }
                for machine in self.machines.values()
            ],
        }

    def create_state_machine(self, **kwargs):
        self._call("create_state_machine", kwargs)
        self.seq += 1
        arn = f"arn:aws:states:us-east-1:123456789012:stateMachine:{kwargs['name']}"
        if arn in self.machines:
            raise RuntimeError("StateMachineAlreadyExists")
        machine = {
            "stateMachineArn": arn,
            "name": kwargs["name"],
            "definition": kwargs["definition"],
            "roleArn": kwargs["roleArn"],
            "type": kwargs["type"],
            "status": "ACTIVE",
            "creationDate": datetime(2026, 8, 14, tzinfo=UTC),
            "loggingConfiguration": kwargs.get("loggingConfiguration"),
            "tracingConfiguration": kwargs.get("tracingConfiguration"),
            "encryptionConfiguration": kwargs.get("encryptionConfiguration"),
        }
        self.machines[arn] = machine
        self.tags[arn] = list(kwargs.get("tags") or [])
        self.aliases[arn] = []
        self.executions[arn] = []
        response = {"stateMachineArn": arn, "creationDate": machine["creationDate"]}
        if kwargs.get("publish"):
            response["stateMachineVersionArn"] = self._publish(arn)
        return response

    def update_state_machine(self, **kwargs):
        self._call("update_state_machine", kwargs)
        arn = kwargs["stateMachineArn"]
        if arn not in self.machines:
            raise NotFound
        machine = self.machines[arn]
        for request_key, response_key in (
            ("definition", "definition"),
            ("roleArn", "roleArn"),
            ("loggingConfiguration", "loggingConfiguration"),
            ("tracingConfiguration", "tracingConfiguration"),
            ("encryptionConfiguration", "encryptionConfiguration"),
        ):
            if request_key in kwargs:
                machine[response_key] = kwargs[request_key]
        response = {"updateDate": datetime(2026, 8, 14, tzinfo=UTC), "revisionId": "r1"}
        if kwargs.get("publish"):
            response["stateMachineVersionArn"] = self._publish(arn)
        return response

    def describe_state_machine(self, **kwargs):
        self._call("describe_state_machine", kwargs)
        arn = kwargs["stateMachineArn"]
        if arn in self.versions:
            return dict(self.versions[arn])
        if arn not in self.machines:
            raise NotFound
        return dict(self.machines[arn])

    def list_tags_for_resource(self, **kwargs):
        self._call("list_tags_for_resource", kwargs)
        arn = kwargs["resourceArn"]
        if arn not in self.tags:
            raise NotFound
        return {"tags": list(self.tags[arn])}

    def delete_state_machine(self, **kwargs):
        self._call("delete_state_machine", kwargs)
        arn = kwargs["stateMachineArn"]
        if arn not in self.machines:
            raise NotFound
        del self.machines[arn]
        del self.tags[arn]

    def list_executions(self, **kwargs):
        self._call("list_executions", kwargs)
        return {
            "executions": [
                item
                for item in self.executions.get(kwargs["stateMachineArn"], [])
                if item.get("status") == kwargs.get("statusFilter")
            ],
        }

    def stop_execution(self, **kwargs):
        self._call("stop_execution", kwargs)
        for executions in self.executions.values():
            for execution in executions:
                if execution["executionArn"] == kwargs["executionArn"]:
                    execution["status"] = "ABORTED"
                    return {"stopDate": datetime(2026, 8, 14, tzinfo=UTC)}
        raise NotFound

    def publish_state_machine_version(self, **kwargs):
        self._call("publish_state_machine_version", kwargs)
        arn = kwargs["stateMachineArn"]
        return {
            "stateMachineVersionArn": self._publish(arn),
            "creationDate": datetime(2026, 8, 14, tzinfo=UTC),
        }

    def _publish(self, arn: str) -> str:
        version = 1 + sum(1 for item in self.versions if item.startswith(f"{arn}:"))
        version_arn = f"{arn}:{version}"
        self.versions[version_arn] = dict(self.machines[arn], stateMachineArn=version_arn)
        return version_arn

    def list_state_machine_aliases(self, **kwargs):
        self._call("list_state_machine_aliases", kwargs)
        return {"stateMachineAliases": list(self.aliases.get(kwargs["stateMachineArn"], []))}

    def create_state_machine_alias(self, **kwargs):
        self._call("create_state_machine_alias", kwargs)
        version_arn = kwargs["routingConfiguration"][0]["stateMachineVersionArn"]
        state_machine_arn = version_arn.rsplit(":", 1)[0]
        alias_arn = f"{state_machine_arn}:{kwargs['name']}"
        item = {
            "stateMachineAliasArn": alias_arn,
            "creationDate": datetime(2026, 8, 14, tzinfo=UTC),
            "description": kwargs.get("description"),
            "routingConfiguration": kwargs["routingConfiguration"],
        }
        self.aliases[state_machine_arn].append(item)
        return {"stateMachineAliasArn": alias_arn, "creationDate": item["creationDate"]}

    def describe_state_machine_alias(self, **kwargs):
        self._call("describe_state_machine_alias", kwargs)
        alias_arn = kwargs["stateMachineAliasArn"]
        state_machine_arn = alias_arn.rsplit(":", 1)[0]
        try:
            return dict(
                next(
                    item
                    for item in self.aliases.get(state_machine_arn, [])
                    if item["stateMachineAliasArn"] == alias_arn
                ),
            )
        except StopIteration as exc:
            raise NotFound from exc

    def update_state_machine_alias(self, **kwargs):
        self._call("update_state_machine_alias", kwargs)
        item = self.describe_state_machine_alias(
            stateMachineAliasArn=kwargs["stateMachineAliasArn"],
        )
        state_machine_arn = kwargs["stateMachineAliasArn"].rsplit(":", 1)[0]
        stored = next(
            row
            for row in self.aliases[state_machine_arn]
            if row["stateMachineAliasArn"] == kwargs["stateMachineAliasArn"]
        )
        stored.update(kwargs)
        return {"updateDate": datetime(2026, 8, 14, tzinfo=UTC), **item}


def _config() -> StepFunctionsConfig:
    return StepFunctionsConfig(region="us-east-1", deletion_protection_default=True)


def _spec(
    config: dict | None = None, *, hint: str = "workflow", identity: str = "11111111-1111-4111-8111-111111111111"
) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org-1",
        organization_slug="acme",
        app_id="app-1",
        app_slug="checkout",
        environment_id="env-1",
        environment_name="prod",
        tenant_cluster_id="cluster-1",
        service_handle_hint=hint,
        size="small",
        config=config if config is not None else {"definition": _DEFINITION, "role_arn": _ROLE_ARN},
        binding_id="binding-1",
        managed_service_id=identity,
    )


def _driver(*, express: bool = False, client: FakeStepFunctions | None = None):
    client = client or FakeStepFunctions()
    driver_class = StepFunctionsExpressDriver if express else StepFunctionsStandardDriver
    return driver_class(config=_config(), client=client), client


def test_standard_provision_creates_tagged_state_machine_with_native_controls() -> None:
    driver, client = _driver()
    config = {
        "definition": _DEFINITION,
        "role_arn": _ROLE_ARN,
        "logging_configuration": {
            "level": "ALL",
            "includeExecutionData": False,
            "destinations": [
                {
                    "cloudWatchLogsLogGroup": {
                        "logGroupArn": "arn:aws:logs:us-east-1:123456789012:log-group:/aws/states/test:*",
                    },
                },
            ],
        },
        "tracing_configuration": {"enabled": True},
        "encryption_configuration": {
            "type": "CUSTOMER_MANAGED_KMS_KEY",
            "kmsKeyId": "arn:aws:kms:us-east-1:123456789012:key/key-1",
            "kmsDataKeyReusePeriodSeconds": 300,
        },
        "state_machine": {"versionDescription": "ignored?"},
    }
    result = driver.provision(_spec(config))
    assert not result.ok and "Astrolift-owned" in result.message

    config["state_machine"] = {}
    result = driver.provision(_spec(config))
    assert result.ok and result.ready
    request = client.kwargs_for("create_state_machine")
    assert request["type"] == "STANDARD"
    assert request["roleArn"] == _ROLE_ARN
    assert json.loads(request["definition"]) == _DEFINITION
    assert request["loggingConfiguration"]["level"] == "ALL"
    assert request["tracingConfiguration"] == {"enabled": True}
    assert request["encryptionConfiguration"]["type"] == "CUSTOMER_MANAGED_KMS_KEY"
    tags = {item["key"]: item["value"] for item in request["tags"]}
    assert tags["astrolift.io/managed-by"] == "platform"
    assert tags["astrolift.io/binding"] == "binding-1"


def test_provision_is_idempotent_and_updates_in_place() -> None:
    driver, client = _driver()
    spec = _spec()
    first = driver.provision(spec)
    changed = dict(spec.config, definition={"StartAt": "Pass", "States": {"Pass": {"Type": "Pass", "End": True}}})
    second = driver.provision(_spec(changed))
    assert first.handle == second.handle
    assert client.names().count("create_state_machine") == 1
    assert client.names().count("update_state_machine") == 1
    arn = first.handle.split("/", 1)[1]
    assert json.loads(client.machines[arn]["definition"])["StartAt"] == "Pass"


def test_publish_and_alias_create_then_update_to_new_version() -> None:
    driver, client = _driver()
    config = {
        "definition": _DEFINITION,
        "role_arn": _ROLE_ARN,
        "publish": True,
        "version_description": "release",
        "alias": {"name": "LIVE"},
    }
    first = driver.provision(_spec(config))
    second = driver.provision(_spec(config))
    assert first.ok and second.ok
    assert client.names().count("create_state_machine_alias") == 1
    assert client.names().count("update_state_machine_alias") == 1
    alias_update = client.kwargs_for("update_state_machine_alias")
    assert alias_update["routingConfiguration"][0]["stateMachineVersionArn"].endswith(":2")
    assert alias_update["routingConfiguration"][0]["weight"] == 100


def test_alias_routing_can_reference_newly_published_version_declaratively() -> None:
    driver, client = _driver()
    config = {
        "definition": _DEFINITION,
        "role_arn": _ROLE_ARN,
        "publish": True,
        "alias": {
            "name": "CANARY",
            "routing_configuration": [
                {"stateMachineVersionArn": "$PUBLISHED", "weight": 100},
            ],
        },
    }
    result = driver.provision(_spec(config))
    assert result.ok
    routing = client.kwargs_for("create_state_machine_alias")["routingConfiguration"]
    assert routing == [
        {
            "stateMachineVersionArn": f"{result.handle.split('/', 1)[1]}:1",
            "weight": 100,
        },
    ]


def test_alias_refuses_to_take_over_external_alias() -> None:
    driver, client = _driver()
    config = {
        "definition": _DEFINITION,
        "role_arn": _ROLE_ARN,
        "publish": True,
        "alias": {"name": "LIVE"},
    }
    created = driver.provision(_spec(config))
    arn = created.handle.split("/", 1)[1]
    client.aliases[arn][0]["description"] = "customer alias"
    result = driver.update(
        UpdateSpec(created.handle, managed_service_id="11111111-1111-4111-8111-111111111111", config=config)
    )
    assert not result.ok and "not owned" in result.message


def test_express_binding_has_sync_execution_but_no_history_actions() -> None:
    driver, client = _driver(express=True)
    provisioned = driver.provision(_spec())
    assert client.kwargs_for("create_state_machine")["type"] == "EXPRESS"
    binding = driver.binding(ServiceHandle(provisioned.handle), {"access_mode": "manage"})
    assert binding.env_vars["WORKFLOW_ENGINE_TYPE"].literal == "EXPRESS"
    assert binding.iam_grants[0].actions == ["states:StartExecution", "states:StartSyncExecution"]
    assert len(binding.iam_grants) == 1


def test_standard_binding_scopes_machine_and_execution_actions_correctly() -> None:
    driver, _ = _driver()
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(provisioned.handle), {"access_mode": "manage"})
    assert binding.env_vars["WORKFLOW_ENGINE_ID"].literal.startswith("astrolift-")
    assert binding.env_vars["STEP_FUNCTIONS_CONSOLE_URL"].literal.startswith("https://")
    assert binding.iam_grants[0].actions == ["states:StartExecution", "states:ListExecutions"]
    assert (
        binding.iam_grants[1].resource
        == provisioned.handle.split("/", 1)[1].replace(":stateMachine:", ":execution:") + ":*"
    )
    assert binding.iam_grants[1].actions == [
        "states:DescribeExecution",
        "states:GetExecutionHistory",
        "states:RedriveExecution",
        "states:StopExecution",
    ]


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"role_arn": _ROLE_ARN}, "definition"),
        ({"definition": "not-json", "role_arn": _ROLE_ARN}, "valid JSON"),
        ({"definition": [], "role_arn": _ROLE_ARN}, "valid JSON"),
        ({"definition": "[]", "role_arn": _ROLE_ARN}, "Amazon States Language object"),
        ({"definition": _DEFINITION, "role_arn": "role"}, "IAM role ARN"),
        (
            {"definition": _DEFINITION, "role_arn": _ROLE_ARN, "alias": {"name": "LIVE"}},
            "publish=true",
        ),
        (
            {"definition": _DEFINITION, "role_arn": _ROLE_ARN, "version_description": "x"},
            "publish=true",
        ),
        (
            {
                "definition": _DEFINITION,
                "role_arn": _ROLE_ARN,
                "state_machine": {"type": "EXPRESS"},
            },
            "Astrolift-owned",
        ),
        ({"definition": _DEFINITION, "role_arn": _ROLE_ARN, "access_mode": "admin"}, "invoke"),
    ],
)
def test_invalid_config_is_rejected_before_mutation(config: dict, message: str) -> None:
    driver, client = _driver()
    result = driver.provision(_spec(config))
    assert not result.ok and message in result.message
    assert "create_state_machine" not in client.names()


def test_aws_definition_diagnostics_block_mutation() -> None:
    driver, client = _driver()
    client.validation_result = "FAIL"
    client.validation_diagnostics = [{"code": "MISSING_TRANSITION_TARGET", "message": "Missing Next state"}]
    result = driver.provision(_spec())
    assert not result.ok and "Missing Next state" in result.message
    assert "create_state_machine" not in client.names()


def test_standard_delete_refuses_protection_and_running_executions_then_force_stops() -> None:
    driver, client = _driver()
    provisioned = driver.provision(_spec())
    arn = provisioned.handle.split("/", 1)[1]
    execution_arn = f"arn:aws:states:us-east-1:123456789012:execution:{arn.rsplit(':', 1)[1]}:run-1"
    client.executions[arn] = [{"executionArn": execution_arn, "status": "RUNNING"}]

    protected = driver.deprovision(
        DeprovisionSpec(provisioned.handle, {}, managed_service_id="11111111-1111-4111-8111-111111111111")
    )
    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]
    unprotected = driver.deprovision(
        DeprovisionSpec(
            provisioned.handle,
            {"deletion_protection": False},
            managed_service_id="11111111-1111-4111-8111-111111111111",
        ),
    )
    assert not unprotected.ok and unprotected.errors == ["running_executions"]
    deleted = driver.deprovision(
        DeprovisionSpec(
            provisioned.handle,
            {"deletion_protection": False},
            managed_service_id="11111111-1111-4111-8111-111111111111",
        ),
        force_destroy=True,
    )
    assert deleted.ok
    assert "stop_execution" in client.names()
    assert "delete_state_machine" in client.names()


def test_express_delete_never_calls_unsupported_list_executions() -> None:
    driver, client = _driver(express=True)
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(
            provisioned.handle,
            {"deletion_protection": False},
            managed_service_id="11111111-1111-4111-8111-111111111111",
        ),
    )
    assert result.ok
    assert "list_executions" not in client.names()


def test_foreign_state_machine_is_never_updated_or_deleted_even_with_force() -> None:
    driver, client = _driver()
    foreign = client.create_state_machine(
        name="foreign",
        definition=json.dumps(_DEFINITION),
        roleArn=_ROLE_ARN,
        type="STANDARD",
        tags=[],
    )
    handle = f"workflow_engine/{foreign['stateMachineArn']}"
    update = driver.update(
        UpdateSpec(
            handle,
            managed_service_id="11111111-1111-4111-8111-111111111111",
            config={"definition": _DEFINITION, "role_arn": _ROLE_ARN},
        )
    )
    delete = driver.deprovision(
        DeprovisionSpec(
            handle, {"deletion_protection": False}, managed_service_id="11111111-1111-4111-8111-111111111111"
        )
    )
    assert not update.ok and update.errors == ["resource_not_owned"]
    assert not delete.ok and delete.errors == ["resource_not_owned"]
    assert not driver.deprovision(
        DeprovisionSpec(
            handle, {"deletion_protection": False}, managed_service_id="11111111-1111-4111-8111-111111111111"
        ),
        force_destroy=True,
    ).ok
    assert "delete_state_machine" not in client.names()


def test_status_maps_active_deleting_and_missing() -> None:
    driver, client = _driver()
    provisioned = driver.provision(_spec())
    handle = ServiceHandle(provisioned.handle)
    assert driver.status(handle).state == "available"
    arn = provisioned.handle.split("/", 1)[1]
    client.machines[arn]["status"] = "DELETING"
    assert driver.status(handle).state == "deprovisioning"
    del client.machines[arn]
    assert driver.status(handle).state == "deprovisioned"


def test_snapshot_publishes_version_and_restore_clones_definition() -> None:
    driver, client = _driver()
    source = driver.provision(_spec(hint="source"))
    snapshot = driver.snapshot(ServiceHandle(source.handle))
    assert snapshot.snapshot_id.endswith(":1")
    restored = driver.restore(snapshot, _spec(hint="restored", identity="22222222-2222-4222-8222-222222222222"))
    assert restored.ok and restored.handle != source.handle
    restored_arn = restored.handle.split("/", 1)[1]
    assert json.loads(client.machines[restored_arn]["definition"]) == _DEFINITION


def test_cross_type_restore_is_rejected() -> None:
    standard, client = _driver()
    source = standard.provision(_spec(hint="source"))
    snapshot = standard.snapshot(ServiceHandle(source.handle))
    express, _ = _driver(express=True, client=client)
    result = express.restore(snapshot, _spec(hint="express"))
    assert not result.ok and result.errors == ["workflow_type_mismatch"]


def test_child_failure_returns_recoverable_partial_handle() -> None:
    driver, client = _driver()
    original = client.create_state_machine_alias

    def fail_alias(**kwargs):
        raise RuntimeError("alias failure")

    client.create_state_machine_alias = fail_alias
    config = {
        "definition": _DEFINITION,
        "role_arn": _ROLE_ARN,
        "publish": True,
        "alias": {"name": "LIVE"},
    }
    result = driver.provision(_spec(config))
    assert not result.ok and result.handle.startswith("workflow_engine/arn:aws:states:")
    client.create_state_machine_alias = original
    assert driver.provision(_spec(config)).ok


def test_generated_requests_match_botocore_stepfunctions_models() -> None:
    driver, client = _driver()
    config = {
        "definition": _DEFINITION,
        "role_arn": _ROLE_ARN,
        "publish": True,
        "alias": {"name": "LIVE"},
    }
    provisioned = driver.provision(_spec(config))
    assert provisioned.ok
    assert driver.provision(_spec(config)).ok
    driver.snapshot(ServiceHandle(provisioned.handle))

    model = Session().get_service_model("stepfunctions")
    operations = {
        "validate_state_machine_definition": "ValidateStateMachineDefinition",
        "list_state_machines": "ListStateMachines",
        "create_state_machine": "CreateStateMachine",
        "update_state_machine": "UpdateStateMachine",
        "list_tags_for_resource": "ListTagsForResource",
        "list_state_machine_aliases": "ListStateMachineAliases",
        "create_state_machine_alias": "CreateStateMachineAlias",
        "describe_state_machine_alias": "DescribeStateMachineAlias",
        "update_state_machine_alias": "UpdateStateMachineAlias",
        "publish_state_machine_version": "PublishStateMachineVersion",
    }
    for name, request in client.calls:
        if name in operations:
            validate_parameters(request, model.operation_model(operations[name]).input_shape)


def test_registration_catalog_cost_and_runtime_config_are_wired() -> None:
    from core.cluster_observability import managed_config_for

    from _sdk.availability import MATRIX
    from _sdk.managed_service_kinds import KINDS
    from aws.cost import SERVICE_CODE_BY_VARIANT
    from aws.plugin import PLUGIN

    for variant in ("step_functions_standard", "step_functions_express"):
        assert ("workflow_engine", variant) in PLUGIN.managed_service_drivers
        assert SERVICE_CODE_BY_VARIANT[("workflow_engine", variant)] == "AWSStepFunctions"
        entry = next(
            item
            for item in MATRIX.managed_services
            if item.plugin_id == "aws" and item.kind == "workflow_engine" and item.variant == variant
        )
        assert entry.status == "preview"
        assert "WORKFLOW_ENGINE_ID" in entry.binding_envs

    cluster = SimpleNamespace(
        slug="aws-prod",
        region="us-west-2",
        provider_config={
            "step_functions_name_prefix": "platform",
            "step_functions_deletion_protection_default": False,
        },
        auth_config={},
    )
    config = managed_config_for("aws", cluster, kind="workflow_engine", variant="step_functions_express")
    assert config.region == "us-west-2"
    assert config.state_machine_name_prefix == "platform"
    assert config.deletion_protection_default is False
    kind = KINDS.get("workflow_engine")
    assert kind is not None and kind.snapshot_supported is True
