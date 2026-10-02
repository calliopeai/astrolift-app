"""Saved workflow-engine targets reach real boto3/Moto calls through the registry."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import boto3
import pytest
from aws._naming import iam_role_name
from aws.managed.workflow_step_functions import (
    StepFunctionsConfig,
    StepFunctionsExpressDriver,
    StepFunctionsStandardDriver,
)
from moto import mock_aws

from astrolift_drivers.registry import PluginManifest, PluginRegistry
from astrolift_workflows.activities.managed_service_lifecycle import (
    _deprovision_sync,
    _provision_sync,
    _update_sync,
)
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service

pytestmark = pytest.mark.django_db

DEFINITION = {"StartAt": "Done", "States": {"Done": {"Type": "Succeed"}}}
ROLE = "arn:aws:iam::123456789012:role/fixture-workflow-role"


class NativeLifecycle:
    """Bridge Moto's dropped machine type and absent optional ASL validator."""

    def __init__(self, client):
        self.client = client

    def __getattr__(self, name):
        if name == "validate_state_machine_definition":
            raise AttributeError(name)
        return getattr(self.client, name)

    def create_state_machine(self, **params):
        from moto.stepfunctions.models import stepfunctions_backends

        result = self.client.create_state_machine(**params)
        arn = result["stateMachineArn"]
        machine = stepfunctions_backends[arn.split(":")[4]][
            self.client.meta.region_name
        ].describe_state_machine(arn)
        machine.type = machine.sm_type = params["type"]
        return result


@pytest.fixture(params=["step_functions_standard", "step_functions_express"])
def cloud(request, monkeypatch):
    with mock_aws():
        api = NativeLifecycle(boto3.client("stepfunctions", region_name="us-east-1"))
        state = SimpleNamespace(api=api, cfg=StepFunctionsConfig(region="us-east-1"), variant=request.param)
        base = (
            StepFunctionsExpressDriver
            if request.param == "step_functions_express"
            else StepFunctionsStandardDriver
        )

        class Driver(base):
            def __init__(self, *, config):
                super().__init__(config=config, client=state.api)

        registry = PluginRegistry()
        registry.register(
            PluginManifest(
                plugin_id="aws",
                display_name="aws",
                version="fixture",
                drivers={f"managed:workflow_engine:{request.param}": Driver},
            )
        )
        monkeypatch.setattr("astrolift_drivers.registry.plugins", registry)
        monkeypatch.setattr("core.cluster_observability.managed_config_for", lambda *_, **__: state.cfg)
        yield state


def new_service(cloud, org, app="api"):
    row = _service(org_slug=org, plugin_slug="aws", variant=cloud.variant, backend_ref="")
    row.kind, row.name = "workflow_engine", "workflow"
    row.config = {
        "definition": DEFINITION,
        "role_arn": f"arn:aws:iam::123456789012:role/astrolift/{row.registered_app.organization.guid}/fixture-workflow",
    }
    row.registered_app.slug = app
    row.registered_app.save(update_fields=["slug"])
    row.save(update_fields=["kind", "name", "config"])
    return row


def snapshot(cloud, arn):
    body = cloud.api.describe_state_machine(stateMachineArn=arn)
    body.pop("ResponseMetadata", None)
    return body


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_actual_saved_orgs_get_distinct_owned_machines_for_previous_name_collisions(cloud, collision):
    if collision == "joined":
        first, second = new_service(cloud, "alpha-beta", "gamma"), new_service(cloud, "alpha", "beta-gamma")
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, state_machine_name_prefix="p" * 100)
        first, second = (
            new_service(cloud, "org-49061", "checkout"),
            new_service(cloud, "org-74558", "checkout"),
        )
    old = [
        iam_role_name(
            cloud.cfg.state_machine_name_prefix,
            row.registered_app.organization.slug,
            row.registered_app.slug,
            row.app_environment.name,
            row.name,
            max_len=80,
        )
        for row in (first, second)
    ]
    assert old[0] == old[1]
    results = [_provision_sync(row.pk) for row in (first, second)]
    assert all(result["ok"] for result in results), results
    assert results[0]["handle"] != results[1]["handle"]
    for row, result in zip((first, second), results, strict=True):
        arn = result["handle"].partition("/")[2]
        assert snapshot(cloud, arn)["name"].endswith(row.guid.hex)
        tags = {
            item["key"]: item["value"] for item in cloud.api.list_tags_for_resource(resourceArn=arn)["tags"]
        }
        assert tags["astrolift.io/managed_service_id"] == str(row.guid)
        row.backend_ref = result["handle"]
        row.save(update_fields=["backend_ref"])
        assert _provision_sync(row.pk)["handle"] == result["handle"]
    assert len(cloud.api.list_state_machines()["stateMachines"]) == 2


def test_exact_recorded_legacy_name_survives_metadata_and_prefix_changes(cloud):
    row = new_service(cloud, "legacy")
    created = cloud.api.create_state_machine(
        name="Legacy_Workflow",
        type="EXPRESS" if cloud.variant.endswith("express") else "STANDARD",
        definition='{"StartAt":"Done","States":{"Done":{"Type":"Succeed"}}}',
        roleArn=ROLE,
        tags=[
            {"key": "astrolift.io/managed-by", "value": "platform"},
            {"key": "astrolift.io/managed_service_id", "value": str(row.guid)},
        ],
    )
    row.backend_ref = "workflow_engine/" + created["stateMachineArn"]
    row.save(update_fields=["backend_ref"])
    row.registered_app.slug = "renamed-app"
    row.registered_app.save(update_fields=["slug"])
    cloud.cfg = dataclasses.replace(cloud.cfg, state_machine_name_prefix="renamed-prefix")
    result = _provision_sync(row.pk)
    assert result["ok"] and result["handle"] == row.backend_ref
    assert [item["name"] for item in cloud.api.list_state_machines()["stateMachines"]] == ["Legacy_Workflow"]


def test_actual_lifecycle_refuses_a_foreign_machine_before_any_mutation_even_with_force(cloud):
    first, second = new_service(cloud, "owner"), new_service(cloud, "contender")
    created = _provision_sync(first.pk)
    assert created["ok"]
    first.backend_ref = second.backend_ref = created["handle"]
    first.save(update_fields=["backend_ref"])
    second.save(update_fields=["backend_ref"])
    arn = created["handle"].partition("/")[2]
    before = snapshot(cloud, arn)
    tags = cloud.api.list_tags_for_resource(resourceArn=arn)["tags"]
    assert not _provision_sync(second.pk)["ok"]
    assert not _update_sync(second.pk)["ok"]
    assert not _deprovision_sync(second.pk, delete_data=True, force_destroy=True)["ok"]
    assert snapshot(cloud, arn) == before
    assert cloud.api.list_tags_for_resource(resourceArn=arn)["tags"] == tags
    assert len(cloud.api.list_state_machines()["stateMachines"]) == 1
    assert _update_sync(first.pk)["ok"]
    assert _deprovision_sync(first.pk, delete_data=True, force_destroy=True)["ok"]
    assert cloud.api.list_state_machines()["stateMachines"] == []


def test_missing_recorded_machine_is_not_replaced_by_a_new_name(cloud):
    row = new_service(cloud, "missing")
    row.backend_ref = "workflow_engine/arn:aws:states:us-east-1:123456789012:stateMachine:Missing_Legacy"
    row.save(update_fields=["backend_ref"])
    result = _provision_sync(row.pk)
    assert not result["ok"] and "refusing a replacement" in result["message"]
    assert cloud.api.list_state_machines()["stateMachines"] == []
