"""Actual EC2 endpoint identity, recovery and ownership boundaries (#2032)."""

from __future__ import annotations

import dataclasses
from unittest.mock import patch

import pytest

from _sdk.managed_service import DeprovisionSpec, ServiceHandle, UpdateSpec
from aws.managed.private_endpoint_vpc import VpcEndpointDriver, _client_token
from tests.aws._vpc_native_2032 import (
    OTHER_ID,
    SERVICE_ID,
    endpoint,
    endpoint_config,
    native_vpc,
    no_effects,
    spec,
)

TYPES = ("Interface", "Gateway", "GatewayLoadBalancer", "Resource", "ServiceNetwork")


@pytest.fixture
def cloud():
    with native_vpc() as state:
        yield state


def provision(cloud, endpoint_type="Interface", **kwargs):
    result = cloud.driver.provision(spec(cloud, endpoint_type, **kwargs))
    assert result.ok, result
    return result.handle


@pytest.mark.parametrize("endpoint_type", TYPES)
def test_native_all_types_have_distinct_uuid_tokens_and_owned_root_identity(cloud, endpoint_type):
    first = spec(cloud, endpoint_type, organization_slug="alpha-beta", app_slug="gamma")
    second = spec(cloud, endpoint_type, organization_slug="alpha", app_slug="beta-gamma", managed_service_id=OTHER_ID)
    assert "-".join((first.organization_slug, first.app_slug)) == "-".join((second.organization_slug, second.app_slug))
    with patch.object(cloud.api, "create_vpc_endpoint", wraps=cloud.api.create_vpc_endpoint) as create:
        results = [cloud.driver.provision(s) for s in (first, second)]
        assert all(r.ok for r in results), results
        assert results[0].handle != results[1].handle
        tokens = [c.kwargs["ClientToken"] for c in create.call_args_list]
        assert len(set(tokens)) == 2 and all(len(t) <= 64 for t in tokens)
        for s, token, result in zip((first, second), tokens, results, strict=True):
            assert s.managed_service_id.replace("-", "") in token
            row = endpoint(cloud, result.handle)
            assert row["OwnerId"] == cloud.cfg.account_id and row["VpcId"] == cloud.vpc
            tags = {t["Key"]: t["Value"] for t in row["Tags"]}
            assert tags["astrolift.io/managed_service_id"] == s.managed_service_id
            assert tags["astrolift.io/managed-by"] == "platform"
            assert row["VpcEndpointType"] == endpoint_type
            changed = dataclasses.replace(s, organization_slug="renamed", app_slug="renamed", service_handle_hint="new")
            assert _client_token(s, s.config, vpc_id=cloud.vpc) == _client_token(
                changed, changed.config, vpc_id=cloud.vpc
            )
            assert cloud.driver.provision(changed).handle == result.handle
        assert create.call_count == 2


@pytest.mark.parametrize("endpoint_type", TYPES)
def test_recorded_native_id_survives_changed_labels_binding_update_and_delete(cloud, endpoint_type):
    handle = provision(cloud, endpoint_type)
    stack, spies = no_effects(cloud)
    with stack:
        r = cloud.driver.provision(
            spec(cloud, endpoint_type, recorded_handle=handle, organization_slug="changed", app_slug="new")
        )
        assert r.ok and r.handle == handle
        for spy in spies:
            spy.assert_not_called()
    binding = cloud.driver.binding(ServiceHandle(handle, managed_service_id=SERVICE_ID))
    assert binding.env_vars["VPC_ENDPOINT_ID"].literal == handle.partition("/")[2]
    config = {"policy": {"Version": "2012-10-17", "Statement": []}} if endpoint_type in {"Interface", "Gateway"} else {}
    update = cloud.driver.update(UpdateSpec(handle, config=config, managed_service_id=SERVICE_ID))
    assert update.ok, update
    assert cloud.driver.status(ServiceHandle(handle)).state == "available"
    delete = cloud.driver.deprovision(DeprovisionSpec(handle, managed_service_id=SERVICE_ID), force_destroy=True)
    assert delete.ok, delete
    assert endpoint(cloud, handle)["State"] == "deleted"
    assert cloud.driver.deprovision(DeprovisionSpec(handle, managed_service_id=SERVICE_ID), force_destroy=True).ok


@pytest.mark.parametrize("operation", ["provision", "update", "delete", "binding"])
def test_foreign_guid_cannot_adopt_modify_force_delete_or_bind_native_endpoint(cloud, operation):
    handle = provision(cloud)
    stack, spies = no_effects(cloud)
    with stack:
        if operation == "provision":
            result = cloud.driver.provision(spec(cloud, recorded_handle=handle, managed_service_id=OTHER_ID))
        elif operation == "update":
            result = cloud.driver.update(
                UpdateSpec(handle, config={"private_dns_enabled": True}, managed_service_id=OTHER_ID)
            )
        elif operation == "delete":
            result = cloud.driver.deprovision(DeprovisionSpec(handle, managed_service_id=OTHER_ID), force_destroy=True)
        else:
            with pytest.raises(Exception, match="ownership"):
                cloud.driver.binding(ServiceHandle(handle, managed_service_id=OTHER_ID))
            result = None
        if result:
            assert not result.ok
            if operation in {"update", "delete"}:
                assert not result.retryable and result.errors == ["ownership_refused"]
        for spy in spies:
            spy.assert_not_called()
    assert endpoint(cloud, handle)["State"] == "available"


@pytest.mark.parametrize("operation", ["provision", "update", "delete"])
def test_missing_guid_tag_refuses_without_retagging_or_force_bypass(cloud, operation):
    handle = provision(cloud)
    cloud.api.delete_tags(Resources=[handle.partition("/")[2]], Tags=[{"Key": "astrolift.io/managed_service_id"}])
    stack, spies = no_effects(cloud)
    with stack:
        if operation == "provision":
            result = cloud.driver.provision(spec(cloud, recorded_handle=handle))
        elif operation == "update":
            result = cloud.driver.update(UpdateSpec(handle, config={}, managed_service_id=SERVICE_ID))
        else:
            result = cloud.driver.deprovision(
                DeprovisionSpec(handle, managed_service_id=SERVICE_ID), force_destroy=True
            )
        assert not result.ok
        for spy in spies:
            spy.assert_not_called()


def test_owned_recorded_deleted_endpoint_never_creates_replacement(cloud):
    handle = provision(cloud)
    cloud.api.delete_vpc_endpoints(VpcEndpointIds=[handle.partition("/")[2]])
    stack, spies = no_effects(cloud)
    with stack:
        result = cloud.driver.provision(spec(cloud, recorded_handle=handle))
        assert not result.ok and "refusing replacement" in result.message
        update = cloud.driver.update(UpdateSpec(handle, managed_service_id=SERVICE_ID))
        assert not update.ok and not update.retryable
        for spy in spies:
            spy.assert_not_called()


def test_changed_vpc_fresh_recovery_refuses_instead_of_creating_duplicate(cloud):
    handle = provision(cloud)
    vpc = cloud.api.create_vpc(CidrBlock="10.1.0.0/16")["Vpc"]["VpcId"]
    desired = endpoint_config(cloud)
    desired["vpc_id"] = vpc
    stack, spies = no_effects(cloud)
    with stack:
        result = cloud.driver.provision(spec(cloud, config=desired))
        assert not result.ok and "immutable" in result.message
        for spy in spies:
            spy.assert_not_called()
    assert endpoint(cloud, handle)["VpcId"] == cloud.vpc


@pytest.mark.parametrize(
    "handle",
    [
        "topic/vpce-12345678",
        "private_endpoint/arn:aws:ec2:x",
        "private_endpoint/vpce-x",
        "private_endpoint/vpce-12345678/path",
    ],
)
def test_invalid_recorded_native_reference_refuses_before_reads_or_effects(cloud, handle):
    stack, spies = no_effects(cloud)
    with stack, patch.object(cloud.api, "describe_vpc_endpoints", wraps=cloud.api.describe_vpc_endpoints) as read:
        assert not cloud.driver.provision(spec(cloud, recorded_handle=handle)).ok
        assert not cloud.driver.update(UpdateSpec(handle, managed_service_id=SERVICE_ID)).ok
        assert not cloud.driver.deprovision(
            DeprovisionSpec(handle, managed_service_id=SERVICE_ID), force_destroy=True
        ).ok
        read.assert_not_called()
        for spy in spies:
            spy.assert_not_called()


@pytest.mark.parametrize("account", ["", "wrong", "999999999999"])
def test_configured_native_account_is_required_and_wrong_account_never_mutates(cloud, account):
    handle = provision(cloud)
    driver = VpcEndpointDriver(config=dataclasses.replace(cloud.cfg, account_id=account), client=cloud.api)
    stack, spies = no_effects(cloud)
    with stack:
        assert not driver.provision(spec(cloud, recorded_handle=handle)).ok
        assert not driver.update(UpdateSpec(handle, managed_service_id=SERVICE_ID)).ok
        assert not driver.deprovision(DeprovisionSpec(handle, managed_service_id=SERVICE_ID), force_destroy=True).ok
        for spy in spies:
            spy.assert_not_called()


@pytest.mark.parametrize("mutation", ["foreign_page", "duplicate_root", "cycle"])
def test_complete_paged_discovery_refuses_contamination_duplicates_and_cycles_before_effects(cloud, mutation):
    handle = provision(cloud)
    owned = endpoint(cloud, handle)
    foreign_handle = provision(cloud, managed_service_id=OTHER_ID)
    foreign = endpoint(cloud, foreign_handle)
    responses = (
        [{"VpcEndpoints": [owned], "NextToken": "alpha"}, {"VpcEndpoints": [foreign]}]
        if mutation == "foreign_page"
        else [{"VpcEndpoints": [owned], "NextToken": "alpha"}, {"VpcEndpoints": [owned]}]
        if mutation == "duplicate_root"
        else [
            {"VpcEndpoints": [], "NextToken": "alpha"},
            {"VpcEndpoints": [], "NextToken": "beta"},
            {"VpcEndpoints": [], "NextToken": "alpha"},
        ]
    )
    stack, spies = no_effects(cloud)
    with stack, patch.object(cloud.api, "describe_vpc_endpoints", side_effect=responses) as read:
        result = cloud.driver.provision(spec(cloud))
        assert not result.ok
        assert read.call_count == len(responses)
        for spy in spies:
            spy.assert_not_called()


def test_two_native_roots_for_one_guid_refuse_fresh_recovery_without_choosing(cloud):
    provision(cloud)
    cloud.api.create_vpc_endpoint(**cloud.driver._create_request(spec(cloud), endpoint_config(cloud)))
    stack, spies = no_effects(cloud)
    with stack:
        result = cloud.driver.provision(spec(cloud))
        assert not result.ok and "multiple owned" in result.message
        for spy in spies:
            spy.assert_not_called()


@pytest.mark.parametrize("mutation", ["id", "account", "vpc", "missing", "duplicate"])
def test_binding_preflights_entire_native_eni_identity_and_parent_before_returning_ips(cloud, mutation):
    handle = provision(cloud)
    ep = endpoint(cloud, handle)
    interface = cloud.api.describe_network_interfaces(NetworkInterfaceIds=ep["NetworkInterfaceIds"])[
        "NetworkInterfaces"
    ][0]
    if mutation in {"id", "account", "vpc"}:
        interface = dict(interface)
        interface[{"id": "NetworkInterfaceId", "account": "OwnerId", "vpc": "VpcId"}[mutation]] = "foreign"
    interfaces = [] if mutation == "missing" else [interface, interface] if mutation == "duplicate" else [interface]
    with (
        patch.object(cloud.api, "describe_network_interfaces", return_value={"NetworkInterfaces": interfaces}),
        pytest.raises(Exception, match="network-interface"),
    ):
        cloud.driver.binding(ServiceHandle(handle, managed_service_id=SERVICE_ID))


@pytest.mark.parametrize("identity", ["", "invalid", "11111111111141118111111111111111"])
def test_invalid_saved_guid_is_rejected_before_native_reads_or_effects(cloud, identity):
    stack, spies = no_effects(cloud)
    with stack, patch.object(cloud.api, "describe_vpc_endpoints", wraps=cloud.api.describe_vpc_endpoints) as read:
        assert not cloud.driver.provision(spec(cloud, managed_service_id=identity)).ok
        read.assert_not_called()
        for spy in spies:
            spy.assert_not_called()


def test_missing_account_fresh_create_is_rejected_before_discovery_or_effects(cloud):
    driver = VpcEndpointDriver(config=dataclasses.replace(cloud.cfg, account_id=""), client=cloud.api)
    stack, spies = no_effects(cloud)
    with stack, patch.object(cloud.api, "describe_vpc_endpoints", wraps=cloud.api.describe_vpc_endpoints) as read:
        assert not driver.provision(spec(cloud)).ok
        read.assert_not_called()
        for spy in spies:
            spy.assert_not_called()


@pytest.mark.parametrize("mutation", ["id", "owner", "type", "parent", "ambiguous"])
@pytest.mark.parametrize("operation", ["provision", "update", "delete"])
def test_returned_native_root_metadata_must_match_before_any_effect(cloud, mutation, operation):
    handle = provision(cloud)
    row = dict(endpoint(cloud, handle))
    if mutation != "ambiguous":
        key, value = {
            "id": ("VpcEndpointId", "vpce-00000000000000000"),
            "owner": ("OwnerId", "999999999999"),
            "type": ("VpcEndpointType", "unknown"),
            "parent": ("VpcId", ""),
        }[mutation]
        row[key] = value
    rows = [row, row] if mutation == "ambiguous" else [row]
    stack, spies = no_effects(cloud)
    with stack, patch.object(cloud.api, "describe_vpc_endpoints", return_value={"VpcEndpoints": rows}):
        if operation == "provision":
            result = cloud.driver.provision(spec(cloud, recorded_handle=handle))
        elif operation == "update":
            result = cloud.driver.update(UpdateSpec(handle, managed_service_id=SERVICE_ID))
        else:
            result = cloud.driver.deprovision(
                DeprovisionSpec(handle, managed_service_id=SERVICE_ID), force_destroy=True
            )
        assert not result.ok
        for spy in spies:
            spy.assert_not_called()


def test_status_with_saved_guid_does_not_report_foreign_root_as_available(cloud):
    handle = provision(cloud)
    assert cloud.driver.status(ServiceHandle(handle, managed_service_id=SERVICE_ID)).state == "available"
    assert cloud.driver.status(ServiceHandle(handle, managed_service_id=OTHER_ID)).state == "error"
