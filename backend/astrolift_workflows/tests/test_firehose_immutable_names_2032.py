"""Persisted Firehose names survive real registry and native provider lifecycle."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import patch

import boto3
import pytest
from aws.managed._base import ManagedServiceError
from aws.managed.stream_firehose import FirehoseConfig, FirehoseDriver
from moto import mock_aws

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
    with mock_aws():
        api = boto3.client("firehose", region_name="us-west-2")
        state = SimpleNamespace(
            api=api,
            cfg=FirehoseConfig(
                region="us-west-2", account_id="123456789012", poll_delay_seconds=0, max_poll_attempts=2
            ),
        )

        class Driver(FirehoseDriver):
            def __init__(self, *, config):
                super().__init__(config=config, client=state.api, sleep=lambda _: None)

        registry = PluginRegistry()
        registry.register(
            PluginManifest(
                plugin_id="aws",
                display_name="aws",
                version="fixture",
                drivers={"managed:stream:firehose": Driver},
            )
        )
        monkeypatch.setattr("astrolift_drivers.registry.plugins", registry)
        monkeypatch.setattr("core.cluster_observability.managed_config_for", lambda *_, **__: state.cfg)
        state.cls = Driver
        yield state


def service(org_slug, app_slug="api"):
    row = _service(org_slug=org_slug, plugin_slug="aws", variant="firehose", backend_ref="")
    row.kind, row.name = "stream", "events"
    row.config = {
        "destination": {
            "type": "extended_s3",
            "configuration": {
                "RoleARN": f"arn:aws:iam::123456789012:role/astrolift/{row.registered_app.organization.guid}/fixture-delivery",
                "BucketARN": "arn:aws:s3:::fixture-events",
                "Prefix": "events/",
            },
        }
    }
    row.registered_app.slug = app_slug
    row.registered_app.save(update_fields=["slug"])
    row.save(update_fields=["kind", "name", "config"])
    return row


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_saved_orgs_with_actual_old_collision_provision_distinct_owned_streams(cloud, collision):
    if collision == "joined":
        first, second = service("alpha-beta", "gamma"), service("alpha", "beta-gamma")
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, delivery_stream_name_prefix="p" * 100)
        first, second = service("alpha"), service("beta")
    old = [
        "-".join(
            (
                cloud.cfg.delivery_stream_name_prefix,
                row.registered_app.organization.slug,
                row.registered_app.slug,
                "prod",
                row.name,
            )
        )[:64]
        for row in (first, second)
    ]
    assert old[0] == old[1]
    results = [_provision_sync(row.pk) for row in (first, second)]
    assert all(r["ok"] for r in results), results
    assert results[0]["handle"] != results[1]["handle"]
    for row, result in zip((first, second), results, strict=True):
        name = result["handle"].rsplit("/", 1)[-1]
        assert name.endswith(row.guid.hex) and len(name) <= 64
        tags = {
            tag["Key"]: tag["Value"]
            for tag in cloud.api.list_tags_for_delivery_stream(DeliveryStreamName=name)["Tags"]
        }
        assert tags["astrolift.io/managed_service_id"] == str(row.guid)
        assert _provision_sync(row.pk)["handle"] == result["handle"]
    assert len(cloud.api.list_delivery_streams()["DeliveryStreamNames"]) == 2


def test_recorded_case_sensitive_legacy_stream_survives_metadata_changes_and_exact_children(cloud):
    row = service("legacy")
    spec = build_provision_spec(row, cluster=row.app_environment.tenant_cluster)
    arn = cloud.api.create_delivery_stream(
        **cloud.cls(config=cloud.cfg)._create_request("Old_Stream.v1", spec)
    )["DeliveryStreamARN"]
    locator = "stream/" + arn
    row.backend_ref = locator
    row.save(update_fields=["backend_ref"])
    row.registered_app.slug = "changed-app"
    row.registered_app.save(update_fields=["slug"])
    cloud.cfg = dataclasses.replace(cloud.cfg, delivery_stream_name_prefix="different-prefix")
    with patch.object(cloud.api, "create_delivery_stream", wraps=cloud.api.create_delivery_stream) as create:
        result = _provision_sync(row.pk)
        assert result["ok"] and result["handle"] == locator
        create.assert_not_called()
    row.refresh_from_db()
    assert row.backend_ref == locator
    row.config["destination_update"] = {"type": "extended_s3", "configuration": {"Prefix": "updated/"}}
    row.save(update_fields=["config"])
    assert _update_sync(row.pk)["ok"]
    description = cloud.api.describe_delivery_stream(DeliveryStreamName="Old_Stream.v1")[
        "DeliveryStreamDescription"
    ]
    assert description["Destinations"][0]["ExtendedS3DestinationDescription"]["Prefix"] == "updated/"
    assert _deprovision_sync(row.pk, delete_data=True, force_destroy=True)["ok"]
    assert cloud.api.list_delivery_streams()["DeliveryStreamNames"] == []


def test_other_saved_service_never_adopts_updates_or_force_deletes_the_owned_stream(cloud):
    first, second = service("owner"), service("other")
    initial = _provision_sync(first.pk)
    assert initial["ok"]
    second.backend_ref = initial["handle"]
    second.config["destination_update"] = {"type": "extended_s3", "configuration": {"Prefix": "refused/"}}
    second.save(update_fields=["backend_ref", "config"])
    name = initial["handle"].rsplit("/", 1)[-1]
    before = cloud.api.describe_delivery_stream(DeliveryStreamName=name)["DeliveryStreamDescription"]
    tags = cloud.api.list_tags_for_delivery_stream(DeliveryStreamName=name)["Tags"]
    assert not _provision_sync(second.pk)["ok"]
    updated, deleted = (
        _update_sync(second.pk),
        _deprovision_sync(second.pk, delete_data=True, force_destroy=True),
    )
    assert not updated["ok"] and not updated["retryable"]
    assert not deleted["ok"] and not deleted["retryable"]
    assert cloud.api.describe_delivery_stream(DeliveryStreamName=name)["DeliveryStreamDescription"] == before
    assert cloud.api.list_tags_for_delivery_stream(DeliveryStreamName=name)["Tags"] == tags


def test_missing_saved_stream_never_recreates_its_recorded_root(cloud):
    row = service("missing")
    row.backend_ref = "stream/arn:aws:firehose:us-west-2:123456789012:deliverystream/Recorded_Missing"
    row.save(update_fields=["backend_ref"])
    with patch.object(cloud.api, "create_delivery_stream", wraps=cloud.api.create_delivery_stream) as create:
        assert not _provision_sync(row.pk)["ok"]
        create.assert_not_called()
    row.refresh_from_db()
    assert row.backend_ref.endswith("/Recorded_Missing")
    assert cloud.api.list_delivery_streams()["DeliveryStreamNames"] == []


@pytest.mark.parametrize("target", ["kind", "account", "region", "partition", "path", "length"])
def test_invalid_saved_stream_never_creates_a_fallback(cloud, target):
    row = service("invalid")
    locator = "stream/arn:aws:firehose:us-west-2:123456789012:deliverystream/Original"
    old, new = {
        "kind": ("stream/", "queue/"),
        "account": ("123456789012", "999999999999"),
        "region": ("us-west-2", "us-east-1"),
        "partition": ("arn:aws:", "arn:aws-cn:"),
        "path": ("deliverystream/", "other/"),
        "length": ("Original", "x" * 65),
    }[target]
    row.backend_ref = locator.replace(old, new)
    row.save(update_fields=["backend_ref"])
    with pytest.raises(ManagedServiceError):
        _provision_sync(row.pk)
    assert cloud.api.list_delivery_streams()["DeliveryStreamNames"] == []


@pytest.mark.parametrize("field", ["DeliveryStreamARN", "DeliveryStreamName"])
def test_contaminated_live_description_is_rejected_before_saved_service_mutation(cloud, field):
    row = service("owner")
    initial = _provision_sync(row.pk)
    assert initial["ok"]
    row.backend_ref = initial["handle"]
    row.config["destination_update"] = {"type": "extended_s3", "configuration": {"Prefix": "refused/"}}
    row.save(update_fields=["backend_ref", "config"])
    name = initial["handle"].rsplit("/", 1)[-1]
    original = cloud.api.describe_delivery_stream(DeliveryStreamName=name)["DeliveryStreamDescription"]
    bad = {**original, field: original[field] + "-other"}
    with (
        patch.object(cloud.api, "describe_delivery_stream", return_value={"DeliveryStreamDescription": bad}),
        patch.object(cloud.api, "tag_delivery_stream", wraps=cloud.api.tag_delivery_stream) as tags,
        patch.object(cloud.api, "update_destination", wraps=cloud.api.update_destination) as update,
        patch.object(cloud.api, "delete_delivery_stream", wraps=cloud.api.delete_delivery_stream) as delete,
    ):
        assert not _provision_sync(row.pk)["ok"]
        assert not _update_sync(row.pk)["ok"]
        assert not _deprovision_sync(row.pk, delete_data=True, force_destroy=True)["ok"]
        for call in (tags, update, delete):
            call.assert_not_called()
    assert (
        cloud.api.describe_delivery_stream(DeliveryStreamName=name)["DeliveryStreamDescription"] == original
    )
