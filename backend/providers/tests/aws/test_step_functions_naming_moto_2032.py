"""Real boto3/Moto state-machine lifecycle boundaries; no external AWS calls."""

from __future__ import annotations

import dataclasses

import boto3
import pytest
from moto import mock_aws

from _sdk.managed_service import DeprovisionSpec, UpdateSpec
from aws._naming import iam_role_name
from aws.managed._base import ManagedServiceError
from aws.managed.workflow_step_functions import (
    StepFunctionsConfig,
    StepFunctionsExpressDriver,
    StepFunctionsStandardDriver,
)
from tests.aws.test_managed_step_functions import _spec


class NativeLifecycle:
    """Bridge Moto's dropped machine type and absent optional ASL validator."""

    def __init__(self, client):
        self.client = client

    def create_state_machine(self, **params):
        from moto.stepfunctions.models import stepfunctions_backends

        result = self.client.create_state_machine(**params)
        arn = result["stateMachineArn"]
        account, region = arn.split(":")[4], self.client.meta.region_name
        machine = stepfunctions_backends[account][region].describe_state_machine(arn)
        machine.type = machine.sm_type = params["type"]
        return result

    def __getattr__(self, name):
        if name == "validate_state_machine_definition":
            raise AttributeError(name)
        return getattr(self.client, name)


@pytest.fixture(params=[StepFunctionsStandardDriver, StepFunctionsExpressDriver])
def cloud(request):
    with mock_aws():
        client = boto3.client("stepfunctions", region_name="us-east-1")
        config = StepFunctionsConfig(region="us-east-1")
        native = NativeLifecycle(client)
        yield request.param(config=config, client=native), native, config


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_two_org_names_that_collided_before_create_distinct_owned_state_machines(cloud, collision):
    driver, client, config = cloud
    first = dataclasses.replace(_spec(), organization_slug="alpha-beta", app_slug="gamma")
    second = dataclasses.replace(
        _spec(identity="22222222-2222-4222-8222-222222222222"), organization_slug="alpha", app_slug="beta-gamma"
    )
    if collision == "truncated":
        config = dataclasses.replace(config, state_machine_name_prefix="p" * 100)
        first = dataclasses.replace(first, organization_slug="org-49061", app_slug="checkout")
        second = dataclasses.replace(second, organization_slug="org-74558", app_slug="checkout")
        driver = type(driver)(config=config, client=NativeLifecycle(client))
    old = [
        iam_role_name(
            config.state_machine_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
            spec.service_handle_hint,
            max_len=80,
        )
        for spec in (first, second)
    ]
    assert old[0] == old[1]
    results = [driver.provision(spec) for spec in (first, second)]
    assert all(result.ok for result in results), results
    assert results[0].handle != results[1].handle
    for spec, result in zip((first, second), results, strict=True):
        arn = result.handle.partition("/")[2]
        name = client.describe_state_machine(stateMachineArn=arn)["name"]
        assert name.endswith(spec.managed_service_id.replace("-", "")) and len(name) <= 80
        tags = {tag["key"]: tag["value"] for tag in client.list_tags_for_resource(resourceArn=arn)["tags"]}
        assert tags["astrolift.io/managed_service_id"] == spec.managed_service_id
        assert driver.provision(dataclasses.replace(spec, recorded_handle=result.handle)).handle == result.handle
    assert len(client.list_state_machines()["stateMachines"]) == 2


def test_recorded_legacy_machine_survives_slug_and_prefix_changes_without_fallback(cloud):
    driver, client, config = cloud
    spec = _spec()
    legacy = client.create_state_machine(
        name="Legacy_Workflow",
        type=driver.WORKFLOW_TYPE,
        definition='{"StartAt":"Done","States":{"Done":{"Type":"Succeed"}}}',
        roleArn=spec.config["role_arn"],
        tags=[
            {"key": "astrolift.io/managed-by", "value": "platform"},
            {"key": "astrolift.io/managed_service_id", "value": spec.managed_service_id},
        ],
    )
    locator = "workflow_engine/" + legacy["stateMachineArn"]
    driver = type(driver)(
        config=dataclasses.replace(config, state_machine_name_prefix="changed"), client=NativeLifecycle(client)
    )
    result = driver.provision(
        dataclasses.replace(spec, recorded_handle=locator, app_slug="renamed", organization_slug="renamed")
    )
    assert result.ok and result.handle == locator
    assert [row["name"] for row in client.list_state_machines()["stateMachines"]] == ["Legacy_Workflow"]


def test_foreign_recorded_machine_is_not_reconfigured_deleted_or_replaced_even_with_force(cloud):
    driver, client, _ = cloud
    first = _spec()
    created = driver.provision(first)
    assert created.ok
    arn = created.handle.partition("/")[2]
    before = client.describe_state_machine(stateMachineArn=arn)
    before.pop("ResponseMetadata", None)
    owner_tags = client.list_tags_for_resource(resourceArn=arn)["tags"]
    second = dataclasses.replace(
        _spec(identity="22222222-2222-4222-8222-222222222222"),
        recorded_handle=created.handle,
        config={**first.config, "definition": {"StartAt": "Changed", "States": {"Changed": {"Type": "Succeed"}}}},
    )
    assert not driver.provision(second).ok
    assert not driver.update(
        UpdateSpec(handle=created.handle, managed_service_id=second.managed_service_id, config=second.config)
    ).ok
    assert not driver.deprovision(
        DeprovisionSpec(handle=created.handle, managed_service_id=second.managed_service_id), force_destroy=True
    ).ok
    after = client.describe_state_machine(stateMachineArn=arn)
    after.pop("ResponseMetadata", None)
    assert after == before
    assert client.list_tags_for_resource(resourceArn=arn)["tags"] == owner_tags
    assert len(client.list_state_machines()["stateMachines"]) == 1


def test_missing_recorded_machine_is_refused_without_guessing_a_new_destination(cloud):
    driver, client, _ = cloud
    spec = dataclasses.replace(
        _spec(), recorded_handle="workflow_engine/arn:aws:states:us-east-1:123456789012:stateMachine:Missing_Legacy"
    )
    result = driver.provision(spec)
    assert not result.ok and "refusing a replacement" in result.message
    assert client.list_state_machines()["stateMachines"] == []


@pytest.mark.parametrize(
    "locator",
    [
        "queue/arn:aws:states:us-east-1:123456789012:stateMachine:Legacy",
        "workflow_engine/arn:aws:states:us-west-2:123456789012:stateMachine:Legacy",
        "workflow_engine/arn:aws-cn:states:us-east-1:123456789012:stateMachine:Legacy",
        "workflow_engine/arn:aws:states:us-east-1:invalid:stateMachine:Legacy",
        "workflow_engine/arn:aws:states:us-east-1:123456789012:stateMachine:Legacy:1",
        "workflow_engine/arn:aws:states:us-east-1:123456789012:stateMachine:Legacy:PROD",
        "workflow_engine/arn:aws:states:us-east-1:123456789012:execution:Legacy",
    ],
)
def test_recorded_wrong_scope_or_qualified_target_refuses_without_create(cloud, locator):
    driver, client, _ = cloud
    with pytest.raises(ManagedServiceError, match="configured driver target"):
        driver.provision(dataclasses.replace(_spec(), recorded_handle=locator))
    assert client.list_state_machines()["stateMachines"] == []


def test_create_idempotency_race_cannot_retag_or_reconfigure_foreign_machine(cloud):
    driver, client, _ = cloud
    spec = _spec()
    created = client.create_state_machine(
        name=driver._name(spec),
        type=driver.WORKFLOW_TYPE,
        definition='{"StartAt":"Done","States":{"Done":{"Type":"Succeed"}}}',
        roleArn=spec.config["role_arn"],
        tags=[
            {"key": "astrolift.io/managed-by", "value": "platform"},
            {"key": "astrolift.io/managed_service_id", "value": "22222222-2222-4222-8222-222222222222"},
        ],
    )
    arn = created["stateMachineArn"]
    before = client.describe_state_machine(stateMachineArn=arn)
    before.pop("ResponseMetadata", None)
    tags = client.list_tags_for_resource(resourceArn=arn)["tags"]

    class StaleListing(NativeLifecycle):
        def list_state_machines(self, **params):
            return {"stateMachines": []}

    driver._sfn = StaleListing(client)
    result = driver.provision(spec)
    assert not result.ok
    after = client.describe_state_machine(stateMachineArn=arn)
    after.pop("ResponseMetadata", None)
    assert after == before
    assert client.list_tags_for_resource(resourceArn=arn)["tags"] == tags


def test_duplicate_live_ownership_tags_refuse_force_delete_without_effect(cloud):
    driver, client, _ = cloud
    spec = _spec()
    created = driver.provision(spec)
    assert created.ok
    arn = created.handle.partition("/")[2]

    class UnverifiableTags(NativeLifecycle):
        def list_tags_for_resource(self, **params):
            body = self.client.list_tags_for_resource(**params)
            body["tags"].append({"key": "astrolift.io/managed_service_id", "value": spec.managed_service_id})
            return body

    driver._sfn = UnverifiableTags(client)
    result = driver.deprovision(
        DeprovisionSpec(handle=created.handle, managed_service_id=spec.managed_service_id), force_destroy=True
    )
    assert not result.ok and result.retryable is False
    assert result.errors == ["ownership_verification_failed"]
    assert client.describe_state_machine(stateMachineArn=arn)["stateMachineArn"] == arn
