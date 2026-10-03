"""MSK defaults, saved incarnations and owner boundaries through actual SDK calls."""

from __future__ import annotations

import dataclasses
from unittest.mock import patch

import pytest

from _sdk.managed_service import DeprovisionSpec, UpdateSpec
from aws.managed._base import ManagedServiceError
from tests.aws._msk_native_2032 import native_msk
from tests.aws.test_managed_msk import SERVICE_ID, _spec

OTHER = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(params=["msk", "msk_serverless"])
def cloud(request, monkeypatch):
    with native_msk(monkeypatch, request.param) as value:
        yield value


def driver(cloud):
    return cloud.cls(config=cloud.cfg, client=cloud.api, sleep=lambda _: None)


def provision(cloud, **kwargs):
    return driver(cloud).provision(_spec(**kwargs))


def target(cloud, handle):
    return cloud.api.describe_cluster_v2(ClusterArn=handle.partition("/")[2])["ClusterInfo"]


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_prior_colliding_name_tuples_get_distinct_owned_cluster_incarnations(cloud, collision):
    if collision == "joined":
        first = _spec(organization_slug="alpha-beta", app_slug="gamma")
        second = _spec(managed_service_id=OTHER, organization_slug="alpha", app_slug="beta-gamma")
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, cluster_name_prefix="p" * 100)
        first, second = _spec(), _spec(managed_service_id=OTHER, organization_slug="other")
    old = [
        "-".join(
            (cloud.cfg.cluster_name_prefix, s.organization_slug, s.app_slug, s.environment_name, s.service_handle_hint)
        )[:64]
        for s in (first, second)
    ]
    assert old[0] == old[1]
    results = [driver(cloud).provision(s) for s in (first, second)]
    assert all(r.ok for r in results), results
    assert results[0].handle != results[1].handle
    for spec, result in zip((first, second), results, strict=True):
        metadata = target(cloud, result.handle)
        assert metadata["ClusterName"].endswith(spec.managed_service_id.replace("-", ""))
        assert len(metadata["ClusterName"]) <= 64
        tags = cloud.api.list_tags_for_resource(ResourceArn=metadata["ClusterArn"])["Tags"]
        assert tags["astrolift.io/managed_service_id"] == spec.managed_service_id
        assert driver(cloud).provision(spec).handle == result.handle
    assert len(cloud.api.list_clusters_v2()["ClusterInfoList"]) == 2


@pytest.mark.parametrize("name", ["Old_Cluster", "Old_Café"])
def test_recorded_legacy_incarnation_survives_labels_and_prefix_changes(cloud, name):
    response = cloud.api.create_cluster_v2(**driver(cloud)._create_request(name, _spec()))
    handle = "event_stream/" + response["ClusterArn"]
    cloud.cfg = dataclasses.replace(cloud.cfg, cluster_name_prefix="changed-prefix")
    with (
        patch.object(cloud.api, "create_cluster_v2", wraps=cloud.api.create_cluster_v2) as create,
        patch.object(cloud.api, "list_clusters_v2", wraps=cloud.api.list_clusters_v2) as discover,
    ):
        result = provision(cloud, recorded_handle=handle, organization_slug="changed-org", app_slug="changed-app")
        assert result.ok and result.handle == handle
        create.assert_not_called()
        discover.assert_not_called()
    arn = response["ClusterArn"]
    policy = {"Version": "2012-10-17", "Statement": []}
    result = driver(cloud).update(
        UpdateSpec(handle=handle, managed_service_id=SERVICE_ID, config={"resource_policy": policy})
    )
    assert result.ok, result
    assert cloud.api.get_cluster_policy(ClusterArn=arn)["Policy"] == '{"Statement":[],"Version":"2012-10-17"}'
    assert (
        driver(cloud)
        .deprovision(
            DeprovisionSpec(handle=handle, managed_service_id=SERVICE_ID), delete_data=True, force_destroy=True
        )
        .ok
    )
    assert cloud.api.list_clusters_v2()["ClusterInfoList"] == []


@pytest.mark.parametrize("corruption", ["owner", "marker", "missing_owner"])
def test_foreign_incarnation_never_drives_retag_policy_or_forced_deletion(cloud, corruption):
    initial = provision(cloud)
    assert initial.ok
    arn = initial.handle.partition("/")[2]
    if corruption == "missing_owner":
        cloud.api.untag_resource(ResourceArn=arn, TagKeys=["astrolift.io/managed_service_id"])
    else:
        key, value = (
            ("astrolift.io/managed_service_id", OTHER)
            if corruption == "owner"
            else ("astrolift.io/managed-by", "foreign")
        )
        cloud.api.tag_resource(ResourceArn=arn, Tags={key: value})
    before, tags = target(cloud, initial.handle), cloud.api.list_tags_for_resource(ResourceArn=arn)["Tags"]
    with (
        patch.object(cloud.api, "tag_resource", wraps=cloud.api.tag_resource) as retag,
        patch.object(cloud.api, "put_cluster_policy", wraps=cloud.api.put_cluster_policy) as policy,
        patch.object(cloud.api, "delete_cluster", wraps=cloud.api.delete_cluster) as delete,
    ):
        assert not provision(cloud, recorded_handle=initial.handle).ok
        updated = driver(cloud).update(
            UpdateSpec(
                handle=initial.handle, managed_service_id=SERVICE_ID, config={"resource_policy": {"Statement": []}}
            )
        )
        deleted = driver(cloud).deprovision(
            DeprovisionSpec(handle=initial.handle, managed_service_id=SERVICE_ID), delete_data=True, force_destroy=True
        )
        assert not updated.ok and not updated.retryable
        assert not deleted.ok and not deleted.retryable
        for call in (retag, policy, delete):
            call.assert_not_called()
    assert target(cloud, initial.handle) == before
    assert cloud.api.list_tags_for_resource(ResourceArn=arn)["Tags"] == tags


@pytest.mark.parametrize("corruption", ["kind", "account", "region", "partition", "path", "name", "incarnation"])
def test_invalid_recorded_cluster_never_discovers_or_creates_a_guessed_target(cloud, corruption):
    locator = "event_stream/arn:aws:kafka:us-west-2:123456789012:cluster/Old_Cluster/legacy-incarnation"
    old, new = {
        "kind": ("event_stream/", "topic/"),
        "account": ("123456789012", "999999999999"),
        "region": ("us-west-2", "us-east-1"),
        "partition": ("arn:aws:", "arn:aws-cn:"),
        "path": (":cluster/", ":topic/"),
        "name": ("Old_Cluster", "x" * 65),
        "incarnation": ("legacy-incarnation", "invalid:uuid"),
    }[corruption]
    with (
        patch.object(cloud.api, "create_cluster_v2", wraps=cloud.api.create_cluster_v2) as create,
        patch.object(cloud.api, "describe_cluster_v2", wraps=cloud.api.describe_cluster_v2) as read,
        patch.object(cloud.api, "list_clusters_v2", wraps=cloud.api.list_clusters_v2) as discover,
    ):
        with pytest.raises(ManagedServiceError):
            provision(cloud, recorded_handle=locator.replace(old, new))
        for call in (create, read, discover):
            call.assert_not_called()


@pytest.mark.parametrize("field", ["ClusterArn", "ClusterName", "ClusterType"])
def test_contaminated_live_metadata_never_drives_child_or_root_writes(cloud, field):
    initial = provision(cloud)
    assert initial.ok
    before = target(cloud, initial.handle)
    bad = {**before, field: before[field] + "-foreign"}
    with (
        patch.object(cloud.api, "describe_cluster_v2", return_value={"ClusterInfo": bad}),
        patch.object(cloud.api, "tag_resource", wraps=cloud.api.tag_resource) as retag,
        patch.object(cloud.api, "put_cluster_policy", wraps=cloud.api.put_cluster_policy) as policy,
        patch.object(cloud.api, "delete_cluster", wraps=cloud.api.delete_cluster) as delete,
    ):
        assert not provision(cloud, recorded_handle=initial.handle).ok
        assert (
            not driver(cloud)
            .update(
                UpdateSpec(
                    handle=initial.handle, managed_service_id=SERVICE_ID, config={"resource_policy": {"Statement": []}}
                )
            )
            .ok
        )
        assert (
            not driver(cloud)
            .deprovision(
                DeprovisionSpec(handle=initial.handle, managed_service_id=SERVICE_ID),
                delete_data=True,
                force_destroy=True,
            )
            .ok
        )
        for call in (retag, policy, delete):
            call.assert_not_called()
    assert target(cloud, initial.handle) == before


def test_missing_recorded_incarnation_does_not_adopt_replacement_with_same_name(cloud):
    original = provision(cloud)
    assert original.ok
    arn = original.handle.partition("/")[2]
    name = target(cloud, original.handle)["ClusterName"]
    cloud.api.delete_cluster(ClusterArn=arn)
    replacement = cloud.api.create_cluster_v2(**driver(cloud)._create_request(name, _spec(managed_service_id=OTHER)))
    assert replacement["ClusterArn"] != arn
    with (
        patch.object(cloud.api, "create_cluster_v2", wraps=cloud.api.create_cluster_v2) as create,
        patch.object(cloud.api, "list_clusters_v2", wraps=cloud.api.list_clusters_v2) as discover,
        patch.object(cloud.api, "tag_resource", wraps=cloud.api.tag_resource) as retag,
    ):
        result = provision(cloud, recorded_handle=original.handle)
        assert not result.ok
        for call in (create, discover, retag):
            call.assert_not_called()
    tags = cloud.api.list_tags_for_resource(ResourceArn=replacement["ClusterArn"])["Tags"]
    assert tags["astrolift.io/managed_service_id"] == OTHER


def test_generated_topic_group_and_transactional_grants_keep_native_parent_incarnation(cloud):
    first, second = provision(cloud), provision(cloud, managed_service_id=OTHER)
    assert first.ok and second.ok
    for result in (first, second):
        arn = result.handle.partition("/")[2]
        metadata = target(cloud, result.handle)
        grants = driver(cloud)._binding_grants(arn, metadata, {"access_mode": "manage"}, auth="iam")
        prefix, identity = arn.split(":cluster/", 1)
        expected = {arn, *[f"{prefix}:{kind}/{identity}/*" for kind in ("topic", "group", "transactional-id")]}
        assert {grant.resource for grant in grants} == expected
        other = second if result is first else first
        other_identity = other.handle.partition(":cluster/")[2]
        assert all(other_identity not in grant.resource for grant in grants)


@pytest.mark.parametrize("case", ["same_incarnation", "distinct_incarnations", "repeated_token"])
def test_name_discovery_handles_duplicate_rows_and_refuses_ambiguous_or_cyclic_pages(cloud, case):
    initial, other = provision(cloud), provision(cloud, managed_service_id=OTHER)
    assert initial.ok and other.ok
    own, foreign = target(cloud, initial.handle), target(cloud, other.handle)
    second = dict(own) if case != "distinct_incarnations" else {**foreign, "ClusterName": own["ClusterName"]}
    responses = [
        {"ClusterInfoList": [own], "NextToken": "page-2"},
        {"ClusterInfoList": [second], **({"NextToken": "page-2"} if case == "repeated_token" else {})},
    ]
    with (
        patch.object(cloud.api, "list_clusters_v2", side_effect=responses) as discover,
        patch.object(cloud.api, "create_cluster_v2", wraps=cloud.api.create_cluster_v2) as create,
        patch.object(cloud.api, "tag_resource", wraps=cloud.api.tag_resource) as retag,
    ):
        result = provision(cloud)
        assert discover.call_count == 2
        create.assert_not_called()
        if case == "same_incarnation":
            assert result.ok and result.handle == initial.handle
        else:
            assert not result.ok
            assert "ambiguous" in result.message if case == "distinct_incarnations" else "pagination" in result.message
            retag.assert_not_called()
