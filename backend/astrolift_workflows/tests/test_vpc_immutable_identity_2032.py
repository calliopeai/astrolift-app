"""Saved endpoint identity reaches actual EC2 through the production registry."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from aws.managed.private_endpoint_vpc import VpcEndpointDriver

from astrolift_drivers.registry import PluginManifest, PluginRegistry
from astrolift_workflows.activities.managed_service_lifecycle import (
    _deprovision_sync,
    _managed_binding_for,
    _provision_sync,
    _update_sync,
)
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service
from providers.tests.aws._vpc_native_2032 import endpoint, endpoint_config, native_vpc, no_effects

pytestmark = pytest.mark.django_db
TYPES = ("Interface", "Gateway", "GatewayLoadBalancer", "Resource", "ServiceNetwork")


@pytest.fixture
def cloud(monkeypatch):
    with native_vpc() as state:

        class Driver(VpcEndpointDriver):
            def __init__(self, *, config):
                super().__init__(config=config, client=state.api)

        registry = PluginRegistry()
        registry.register(
            PluginManifest(
                plugin_id="aws",
                display_name="aws",
                version="fixture",
                drivers={"managed:private_endpoint:vpc_endpoint": Driver},
            )
        )
        monkeypatch.setattr("astrolift_drivers.registry.plugins", registry)
        # Use the real core managed_config_for, including consumer account.
        yield state


def service(cloud, org, endpoint_type="Interface", app="api"):
    row = _service(org_slug=org, plugin_slug="aws", variant="vpc_endpoint", backend_ref="")
    row.kind, row.name, row.config = "private_endpoint", "private", endpoint_config(cloud, endpoint_type)
    row.registered_app.slug = app
    row.registered_app.save(update_fields=["slug"])
    row.save(update_fields=["kind", "name", "config"])
    cluster = row.app_environment.tenant_cluster
    cluster.provider_config = {"region": "us-east-1", "account_id": cloud.cfg.account_id, "vpc_id": cloud.vpc}
    cluster.save(update_fields=["provider_config"])
    return row


def remember(row, result):
    assert result["ok"], result
    row.backend_ref = result["handle"]
    row.save(update_fields=["backend_ref"])
    return row.backend_ref


@pytest.mark.parametrize("endpoint_type", TYPES)
def test_two_saved_orgs_with_joined_slug_collision_own_distinct_native_endpoints(cloud, endpoint_type):
    first = service(cloud, "alpha-beta", endpoint_type, "gamma")
    second = service(cloud, "alpha", endpoint_type, "beta-gamma")
    handles = [remember(row, _provision_sync(row.pk)) for row in (first, second)]
    assert handles[0] != handles[1]
    for row in (first, second):
        ep = endpoint(cloud, row.backend_ref)
        assert ep["OwnerId"] == cloud.cfg.account_id
        assert {t["Key"]: t["Value"] for t in ep["Tags"]}["astrolift.io/managed_service_id"] == str(row.guid)
        assert _provision_sync(row.pk)["handle"] == row.backend_ref


@pytest.mark.parametrize("endpoint_type", TYPES)
def test_saved_recorded_endpoint_full_lifecycle_preserves_root_after_label_changes(cloud, endpoint_type):
    row = service(cloud, "legacy", endpoint_type)
    handle = remember(row, _provision_sync(row.pk))
    row.registered_app.slug = "renamed"
    row.registered_app.save(update_fields=["slug"])
    row.name = "renamed-service"
    row.save(update_fields=["name"])
    stack, spies = no_effects(cloud)
    with stack:
        assert _provision_sync(row.pk)["handle"] == handle
        for spy in spies:
            spy.assert_not_called()
    binding = _managed_binding_for(row)
    assert binding.env_vars["VPC_ENDPOINT_ID"].literal == handle.partition("/")[2]
    if endpoint_type in {"Interface", "Gateway"}:
        row.config["policy"] = {"Version": "2012-10-17", "Statement": []}
        row.save(update_fields=["config"])
    assert _update_sync(row.pk)["ok"]
    assert _deprovision_sync(row.pk, delete_data=True, force_destroy=True)["ok"]
    assert endpoint(cloud, handle)["State"] == "deleted"
    row.refresh_from_db()
    assert row.backend_ref == handle


@pytest.mark.parametrize("marker", ["foreign", "missing"])
def test_other_saved_org_refuses_before_update_force_delete_or_retagging(cloud, marker):
    first, second = service(cloud, "owner"), service(cloud, "other")
    handle = remember(first, _provision_sync(first.pk))
    second.backend_ref = handle
    second.save(update_fields=["backend_ref"])
    if marker == "missing":
        cloud.api.delete_tags(
            Resources=[handle.partition("/")[2]], Tags=[{"Key": "astrolift.io/managed_service_id"}]
        )
    stack, spies = no_effects(cloud)
    with stack:
        assert not _provision_sync(second.pk)["ok"]
        update, delete = (
            _update_sync(second.pk),
            _deprovision_sync(second.pk, delete_data=True, force_destroy=True),
        )
        assert not update["ok"] and not update["retryable"]
        assert not delete["ok"] and not delete["retryable"]
        for spy in spies:
            spy.assert_not_called()
    second.refresh_from_db()
    assert second.backend_ref == handle
    assert endpoint(cloud, handle)["State"] == "available"


@pytest.mark.parametrize("missing", ["deleted", "absent"])
def test_recorded_missing_or_deleted_root_never_creates_replacement(cloud, missing):
    row = service(cloud, "missing")
    handle = remember(row, _provision_sync(row.pk))
    cloud.api.delete_vpc_endpoints(VpcEndpointIds=[handle.partition("/")[2]])
    if missing == "absent":
        # Native NotFound after deletion is an AWS possibility; Moto retains
        # tombstones, so remove only the deleted model for this boundary.
        from moto.ec2.models import ec2_backends

        ec2_backends[cloud.cfg.account_id][cloud.cfg.region].vpc_end_points.pop(handle.partition("/")[2])
    stack, spies = no_effects(cloud)
    with stack:
        assert not _provision_sync(row.pk)["ok"]
        assert not _update_sync(row.pk)["ok"]
        for spy in spies:
            spy.assert_not_called()
    row.refresh_from_db()
    assert row.backend_ref == handle


def test_fresh_saved_guid_recovery_detects_changed_parent_before_any_effect(cloud):
    row = service(cloud, "parent")
    result = _provision_sync(row.pk)
    assert result["ok"]
    assert not row.backend_ref
    row.config["vpc_id"] = cloud.api.create_vpc(CidrBlock="10.1.0.0/16")["Vpc"]["VpcId"]
    row.save(update_fields=["config"])
    stack, spies = no_effects(cloud)
    with stack:
        result = _provision_sync(row.pk)
        assert not result["ok"] and "immutable" in result["message"]
        for spy in spies:
            spy.assert_not_called()


@pytest.mark.parametrize(
    "handle", ["topic/vpce-12345678", "private_endpoint/wrong", "private_endpoint/vpce-12345678/path"]
)
def test_saved_malformed_reference_never_probes_provider_or_retargets(cloud, handle):
    row = service(cloud, "malformed")
    row.backend_ref = handle
    row.save(update_fields=["backend_ref"])
    stack, spies = no_effects(cloud)
    with (
        stack,
        patch.object(cloud.api, "describe_vpc_endpoints", wraps=cloud.api.describe_vpc_endpoints) as read,
    ):
        assert not _provision_sync(row.pk)["ok"]
        assert not _update_sync(row.pk)["ok"]
        assert not _deprovision_sync(row.pk, delete_data=True, force_destroy=True)["ok"]
        read.assert_not_called()
        for spy in spies:
            spy.assert_not_called()
