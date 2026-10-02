"""Firehose names and exact saved targets exercise native boto3/Moto state."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from _sdk.managed_service import DeprovisionSpec, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed.stream_firehose import FirehoseDriver
from tests.aws.test_managed_firehose import SERVICE_ID, _config, _spec

OTHER = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def cloud():
    with mock_aws():
        api = boto3.client("firehose", region_name="us-west-2")
        yield SimpleNamespace(api=api, cfg=_config())


def driver(cloud):
    return FirehoseDriver(config=cloud.cfg, client=cloud.api, sleep=lambda _: None)


def provision(cloud, **kwargs):
    return driver(cloud).provision(_spec(**kwargs))


def target(cloud, handle):
    return cloud.api.describe_delivery_stream(DeliveryStreamName=handle.rsplit("/", 1)[-1])["DeliveryStreamDescription"]


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_two_previous_colliding_name_tuples_provision_distinct_owned_streams(cloud, collision):
    if collision == "joined":
        first = _spec(organization_slug="alpha-beta", app_slug="gamma")
        second = _spec(managed_service_id=OTHER, organization_slug="alpha", app_slug="beta-gamma")
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, delivery_stream_name_prefix="p" * 100)
        first, second = _spec(), _spec(managed_service_id=OTHER, organization_slug="other")
    old = [
        "-".join(
            (
                cloud.cfg.delivery_stream_name_prefix,
                s.organization_slug,
                s.app_slug,
                s.environment_name,
                s.service_handle_hint,
            )
        )[:64]
        for s in (first, second)
    ]
    assert old[0] == old[1]
    results = [driver(cloud).provision(s) for s in (first, second)]
    assert all(r.ok for r in results), results
    assert results[0].handle != results[1].handle
    for spec, result in zip((first, second), results, strict=True):
        name = result.handle.rsplit("/", 1)[-1]
        assert name.endswith(spec.managed_service_id.replace("-", "")) and len(name) <= 64
        tags = {r["Key"]: r["Value"] for r in cloud.api.list_tags_for_delivery_stream(DeliveryStreamName=name)["Tags"]}
        assert tags["astrolift.io/managed_service_id"] == spec.managed_service_id
    assert len(cloud.api.list_delivery_streams()["DeliveryStreamNames"]) == 2


def test_recorded_legacy_name_survives_labels_and_prefix_changes(cloud):
    spec = _spec()
    name = "Old_Stream.v1"
    request = driver(cloud)._create_request(name, spec)
    arn = cloud.api.create_delivery_stream(**request)["DeliveryStreamARN"]
    handle = "stream/" + arn
    cloud.cfg = dataclasses.replace(cloud.cfg, delivery_stream_name_prefix="new-prefix")
    with patch.object(cloud.api, "create_delivery_stream", wraps=cloud.api.create_delivery_stream) as create:
        result = provision(cloud, recorded_handle=handle, organization_slug="new-org", app_slug="new-app")
        assert result.ok and result.handle == handle
        create.assert_not_called()
    assert cloud.api.list_delivery_streams()["DeliveryStreamNames"] == [name]
    changed = driver(cloud).update(
        UpdateSpec(
            handle=handle,
            managed_service_id=SERVICE_ID,
            config={"destination_update": {"type": "extended_s3", "configuration": {"Prefix": "updated/"}}},
        )
    )
    assert changed.ok, changed
    assert target(cloud, handle)["Destinations"][0]["ExtendedS3DestinationDescription"]["Prefix"] == "updated/"
    assert (
        driver(cloud)
        .deprovision(
            DeprovisionSpec(handle=handle, managed_service_id=SERVICE_ID), delete_data=True, force_destroy=True
        )
        .ok
    )
    assert cloud.api.list_delivery_streams()["DeliveryStreamNames"] == []


@pytest.mark.parametrize("corruption", ["owner", "marker", "missing_owner"])
def test_foreign_live_target_cannot_be_reconfigured_retagged_or_force_deleted(cloud, corruption):
    initial = provision(cloud)
    assert initial.ok
    name = initial.handle.rsplit("/", 1)[-1]
    if corruption == "missing_owner":
        cloud.api.untag_delivery_stream(DeliveryStreamName=name, TagKeys=["astrolift.io/managed_service_id"])
    else:
        key, value = (
            ("astrolift.io/managed_service_id", OTHER)
            if corruption == "owner"
            else ("astrolift.io/managed-by", "foreign")
        )
        cloud.api.tag_delivery_stream(DeliveryStreamName=name, Tags=[{"Key": key, "Value": value}])
    before = target(cloud, initial.handle)
    before_tags = cloud.api.list_tags_for_delivery_stream(DeliveryStreamName=name)["Tags"]
    with (
        patch.object(cloud.api, "create_delivery_stream", wraps=cloud.api.create_delivery_stream) as create,
        patch.object(cloud.api, "tag_delivery_stream", wraps=cloud.api.tag_delivery_stream) as tags,
        patch.object(cloud.api, "update_destination", wraps=cloud.api.update_destination) as update,
        patch.object(
            cloud.api, "start_delivery_stream_encryption", wraps=cloud.api.start_delivery_stream_encryption
        ) as encryption,
        patch.object(cloud.api, "delete_delivery_stream", wraps=cloud.api.delete_delivery_stream) as delete,
    ):
        cfg = {"destination_update": {"type": "extended_s3", "configuration": {"Prefix": "refused/"}}}
        assert not provision(cloud, recorded_handle=initial.handle).ok
        result = driver(cloud).update(UpdateSpec(handle=initial.handle, managed_service_id=SERVICE_ID, config=cfg))
        assert not result.ok and not result.retryable
        deleted = driver(cloud).deprovision(
            DeprovisionSpec(handle=initial.handle, managed_service_id=SERVICE_ID), delete_data=True, force_destroy=True
        )
        assert not deleted.ok and not deleted.retryable
        for call in (create, tags, update, encryption, delete):
            call.assert_not_called()
    assert target(cloud, initial.handle) == before
    assert cloud.api.list_tags_for_delivery_stream(DeliveryStreamName=name)["Tags"] == before_tags


@pytest.mark.parametrize("corruption", ["kind", "account", "region", "partition", "path", "length"])
def test_invalid_recorded_target_is_refused_without_any_provider_call(cloud, corruption):
    locator = "stream/arn:aws:firehose:us-west-2:123456789012:deliverystream/Old_Stream"
    old, new = {
        "kind": ("stream/", "queue/"),
        "account": ("123456789012", "999999999999"),
        "region": ("us-west-2", "us-east-1"),
        "partition": ("arn:aws:", "arn:aws-cn:"),
        "path": ("deliverystream/", "other/"),
        "length": ("Old_Stream", "x" * 65),
    }[corruption]
    with (
        patch.object(cloud.api, "create_delivery_stream", wraps=cloud.api.create_delivery_stream) as create,
        patch.object(cloud.api, "describe_delivery_stream", wraps=cloud.api.describe_delivery_stream) as read,
    ):
        with pytest.raises(ManagedServiceError):
            provision(cloud, recorded_handle=locator.replace(old, new))
        create.assert_not_called()
        read.assert_not_called()
    assert cloud.api.list_delivery_streams()["DeliveryStreamNames"] == []


def test_recorded_missing_stream_is_not_recreated(cloud):
    locator = "stream/arn:aws:firehose:us-west-2:123456789012:deliverystream/Missing"
    with patch.object(cloud.api, "create_delivery_stream", wraps=cloud.api.create_delivery_stream) as create:
        result = provision(cloud, recorded_handle=locator)
        assert not result.ok
        create.assert_not_called()
    assert cloud.api.list_delivery_streams()["DeliveryStreamNames"] == []


def test_repeated_destination_id_is_scoped_to_its_exact_stream(cloud):
    first, second = provision(cloud), provision(cloud, managed_service_id=OTHER)
    assert first.ok and second.ok
    before_other = target(cloud, second.handle)
    assert (
        target(cloud, first.handle)["Destinations"][0]["DestinationId"]
        == before_other["Destinations"][0]["DestinationId"]
    )
    result = driver(cloud).update(
        UpdateSpec(
            handle=first.handle,
            managed_service_id=SERVICE_ID,
            config={"destination_update": {"type": "extended_s3", "configuration": {"Prefix": "first-only/"}}},
        )
    )
    assert result.ok, result
    assert target(cloud, first.handle)["Destinations"][0]["ExtendedS3DestinationDescription"]["Prefix"] == "first-only/"
    assert target(cloud, second.handle) == before_other


@pytest.mark.parametrize("field", ["DeliveryStreamARN", "DeliveryStreamName"])
@pytest.mark.parametrize("operation", ["update", "reprovision", "delete"])
def test_wrong_live_description_never_drives_mutation(cloud, field, operation):
    initial = provision(cloud)
    assert initial.ok
    before = target(cloud, initial.handle)
    bad = {**before, field: before[field] + "-foreign"}
    with (
        patch.object(cloud.api, "describe_delivery_stream", return_value={"DeliveryStreamDescription": bad}),
        patch.object(cloud.api, "tag_delivery_stream", wraps=cloud.api.tag_delivery_stream) as tags,
        patch.object(cloud.api, "update_destination", wraps=cloud.api.update_destination) as update,
        patch.object(cloud.api, "delete_delivery_stream", wraps=cloud.api.delete_delivery_stream) as delete,
        patch.object(
            cloud.api, "start_delivery_stream_encryption", wraps=cloud.api.start_delivery_stream_encryption
        ) as encryption,
    ):
        if operation == "update":
            result = driver(cloud).update(
                UpdateSpec(
                    handle=initial.handle,
                    managed_service_id=SERVICE_ID,
                    config={"destination_update": {"type": "extended_s3", "configuration": {"Prefix": "refused/"}}},
                )
            )
        elif operation == "reprovision":
            result = provision(cloud, recorded_handle=initial.handle)
        else:
            result = driver(cloud).deprovision(
                DeprovisionSpec(handle=initial.handle, managed_service_id=SERVICE_ID),
                delete_data=True,
                force_destroy=True,
            )
        assert not result.ok and "exact target" in result.message
        for call in (tags, update, delete, encryption):
            call.assert_not_called()
    assert target(cloud, initial.handle) == before


def test_create_returns_wrong_arn_without_followup_tag_or_destination_actions(cloud):
    actual = cloud.api.create_delivery_stream
    created = []

    def contaminated_create(**params):
        response = actual(**params)
        created.append(response["DeliveryStreamARN"])
        return {**response, "DeliveryStreamARN": response["DeliveryStreamARN"] + "-foreign"}

    with (
        patch.object(cloud.api, "create_delivery_stream", side_effect=contaminated_create),
        patch.object(cloud.api, "tag_delivery_stream", wraps=cloud.api.tag_delivery_stream) as tags,
    ):
        result = provision(cloud)
        assert not result.ok and "exact target" in result.message
        tags.assert_not_called()
    assert len(created) == 1
    assert cloud.api.list_delivery_streams()["DeliveryStreamNames"] == [created[0].rsplit("/", 1)[-1]]
