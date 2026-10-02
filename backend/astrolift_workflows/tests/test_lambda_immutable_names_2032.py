"""Saved Lambda references reach production registry and native IAM/function APIs."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from aws._naming import iam_role_name
from aws.managed.faas_lambda import LambdaDriver
from tests.aws._lambda_native_2032 import native_lambda
from tests.aws.test_lambda_naming_moto_2032 import no_effects

from astrolift_drivers.registry import PluginManifest, PluginRegistry
from astrolift_workflows.activities.managed_service_lifecycle import (
    _deprovision_sync,
    _provision_sync,
    _update_sync,
    build_provision_spec,
)
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service

pytestmark = pytest.mark.django_db


@pytest.fixture
def cloud(monkeypatch):
    with native_lambda() as state:

        class Driver(LambdaDriver):
            def __init__(self, *, config):
                super().__init__(config=config, client=state.api, iam_client=state.iam)

        registry = PluginRegistry()
        registry.register(
            PluginManifest(
                plugin_id="aws",
                display_name="aws",
                version="fixture",
                drivers={"managed:faas:lambda": Driver},
            )
        )
        monkeypatch.setattr("astrolift_drivers.registry.plugins", registry)
        monkeypatch.setattr("core.cluster_observability.managed_config_for", lambda *_, **__: state.cfg)
        state.cls = Driver
        yield state


def service(cloud, org_slug, app_slug="api"):
    row = _service(org_slug=org_slug, plugin_slug="aws", variant="lambda", backend_ref="")
    row.kind, row.name, row.config = "faas", "function", dict(cloud.code)
    row.registered_app.slug = app_slug
    row.registered_app.save(update_fields=["slug"])
    row.save(update_fields=["kind", "name", "config"])
    return row


def remember(row, result):
    assert result["ok"], result
    row.backend_ref = result["handle"]
    row.save(update_fields=["backend_ref"])
    return row.backend_ref.partition("/")[2]


@pytest.mark.parametrize("collision", ["joined", "long_joined"])
def test_saved_two_org_slug_collisions_create_distinct_function_role_and_url(cloud, collision):
    if collision == "joined":
        first, second = service(cloud, "alpha-beta", "gamma"), service(cloud, "alpha", "beta-gamma")
    else:
        first, second = (
            service(cloud, "alpha-beta", "gamma" + "x" * 100),
            service(cloud, "alpha", "beta-gamma" + "x" * 100),
        )
    specs = [build_provision_spec(row, cluster=row.app_environment.tenant_cluster) for row in (first, second)]
    previous = [
        iam_role_name(
            "astrolift",
            s.organization_slug,
            s.app_slug,
            s.environment_name,
            s.service_handle_hint,
            max_len=64,
        )
        for s in specs
    ]
    assert previous[0] == previous[1]
    handles = []
    for row in (first, second):
        fn = remember(row, _provision_sync(row.pk))
        handles.append(row.backend_ref)
        metadata = cloud.api.get_function(FunctionName=fn)
        assert fn.endswith(row.guid.hex) and len(fn) <= 64
        assert metadata["Tags"]["astrolift.io/managed_service_id"] == str(row.guid)
        role = cloud.iam.get_role(RoleName=cloud.driver._role_name_for(fn))["Role"]
        assert role["Arn"] == metadata["Configuration"]["Role"]
        assert {t["Key"]: t["Value"] for t in role["Tags"]}["astrolift.io/managed_service_id"] == str(
            row.guid
        )
        assert _provision_sync(row.pk)["handle"] == row.backend_ref
    assert handles[0] != handles[1]
    assert len(cloud.api.list_functions()["Functions"]) == 2


def test_saved_owned_legacy_function_is_not_renamed_after_labels_change(cloud):
    row = service(cloud, "legacy")
    driver = cloud.cls(config=cloud.cfg)
    fn = "Old_Lambda_2032"
    spec = build_provision_spec(row, cluster=row.app_environment.tenant_cluster)
    role_arn = driver._ensure_exec_role(role_name=driver._role_name_for(fn), function_name=fn, spec=spec)
    cloud.api.create_function(**driver._build_create_args(spec, fn, role_arn))
    cloud.api.create_function_url_config(FunctionName=fn, AuthType="AWS_IAM")
    row.backend_ref = "faas/" + fn
    row.save(update_fields=["backend_ref"])
    row.registered_app.slug = "renamed-app"
    row.registered_app.save(update_fields=["slug"])
    with patch.object(cloud.api, "create_function", wraps=cloud.api.create_function) as create:
        result = _provision_sync(row.pk)
        assert result["ok"] and result["handle"] == row.backend_ref
        create.assert_not_called()
    row.config["memory_mb"] = 1024
    row.save(update_fields=["config"])
    assert _update_sync(row.pk)["ok"]
    assert cloud.api.get_function_configuration(FunctionName=fn)["MemorySize"] == 1024
    assert _deprovision_sync(row.pk, delete_data=True, force_destroy=True)["ok"]
    assert cloud.api.list_functions()["Functions"] == []
    assert cloud.iam.list_roles()["Roles"] == []


@pytest.mark.parametrize("target", ["function", "role"])
def test_second_saved_org_cannot_adopt_update_or_force_delete_first_org(cloud, target):
    first, second = service(cloud, "owner"), service(cloud, "other")
    fn = remember(first, _provision_sync(first.pk))
    # Isolate each parent fence: the other parent carries the attempted actor's
    # GUID, so the untouched target alone must reject the foreign operation.
    if target == "role":
        cloud.api.tag_resource(
            Resource=cloud.driver._function_arn(fn),
            Tags={"astrolift.io/managed_service_id": str(second.guid)},
        )
    else:
        cloud.iam.tag_role(
            RoleName=cloud.driver._role_name_for(fn),
            Tags=[{"Key": "astrolift.io/managed_service_id", "Value": str(second.guid)}],
        )
    second.backend_ref = first.backend_ref
    second.save(update_fields=["backend_ref"])
    stack, spies = no_effects(cloud)
    with stack:
        assert not _provision_sync(second.pk)["ok"]
        update = _update_sync(second.pk)
        delete = _deprovision_sync(second.pk, delete_data=True, force_destroy=True)
        assert not update["ok"] and not update["retryable"]
        assert not delete["ok"] and not delete["retryable"]
        for spy in spies:
            spy.assert_not_called()
    second.refresh_from_db()
    assert second.backend_ref == first.backend_ref


@pytest.mark.parametrize("target", ["function", "role"])
def test_saved_missing_parent_does_not_create_replacement(cloud, target):
    row = service(cloud, "missing")
    fn = remember(row, _provision_sync(row.pk))
    if target == "function":
        cloud.api.delete_function(FunctionName=fn)
    else:
        role = cloud.driver._role_name_for(fn)
        cloud.iam.delete_role_policy(RoleName=role, PolicyName="astrolift-faas-policy")
        cloud.iam.delete_role(RoleName=role)
    stack, spies = no_effects(cloud)
    with stack:
        assert not _provision_sync(row.pk)["ok"]
        assert not _update_sync(row.pk)["ok"]
        for spy in spies:
            spy.assert_not_called()
    row.refresh_from_db()
    assert row.backend_ref == "faas/" + fn


@pytest.mark.parametrize("handle", ["topic/name", "faas/a:b", "faas/a/b", "faas/" + "x" * 65])
def test_saved_invalid_reference_never_probes_or_replaces_target(cloud, handle):
    row = service(cloud, "invalid")
    row.backend_ref = handle
    row.save(update_fields=["backend_ref"])
    stack, spies = no_effects(cloud)
    with stack, patch.object(cloud.api, "get_function", wraps=cloud.api.get_function) as read:
        assert not _provision_sync(row.pk)["ok"]
        assert not _update_sync(row.pk)["ok"]
        assert not _deprovision_sync(row.pk, delete_data=True, force_destroy=True)["ok"]
        read.assert_not_called()
        for spy in spies:
            spy.assert_not_called()


def test_unverifiable_historical_role_refuses_before_function_or_policy_writes(cloud):
    row = service(cloud, "legacy-role")
    fn = remember(row, _provision_sync(row.pk))
    cloud.iam.untag_role(
        RoleName=cloud.driver._role_name_for(fn), TagKeys=["astrolift.io/managed_service_id"]
    )
    stack, spies = no_effects(cloud)
    with stack:
        result = _provision_sync(row.pk)
        assert not result["ok"] and "ownership" in result["message"]
        assert not _update_sync(row.pk)["ok"]
        assert not _deprovision_sync(row.pk, delete_data=True, force_destroy=True)["ok"]
        for spy in spies:
            spy.assert_not_called()
    row.refresh_from_db()
    assert row.backend_ref == "faas/" + fn
    role = cloud.iam.get_role(RoleName=cloud.driver._role_name_for(fn))["Role"]
    assert "astrolift.io/managed_service_id" not in {t["Key"] for t in role["Tags"]}
