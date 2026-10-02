"""Saved stream identities reach real boto3/Moto lifecycle calls through the registry."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import boto3
import pytest
from aws.managed._base import ManagedServiceError
from aws.managed.stream_kinesis import KinesisConfig, KinesisDriver
from botocore.validate import validate_parameters
from moto import mock_aws

from astrolift_drivers.registry import PluginManifest, PluginRegistry
from astrolift_workflows.activities.managed_service_lifecycle import _provision_sync, build_provision_spec
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service

pytestmark = pytest.mark.django_db


class MotoTransport:
    """Bridge Moto's missing create tags and ListTagsForResource through its legacy tag API."""

    def __init__(self, client):
        self.client = client

    def __getattr__(self, name):
        return getattr(self.client, name)

    def create_stream(self, **request):
        result = self.client.create_stream(**request)
        self.client.add_tags_to_stream(StreamName=request["StreamName"], Tags=request["Tags"])
        return result

    def list_tags_for_resource(self, **request):
        operation = self.client.meta.service_model.operation_model("ListTagsForResource")
        validate_parameters(request, operation.input_shape)
        return self.client.list_tags_for_stream(StreamARN=request["ResourceARN"])


@pytest.fixture
def cloud(monkeypatch):
    with mock_aws():
        client = boto3.client("kinesis", region_name="us-west-2")
        api = MotoTransport(client)

        class Driver(KinesisDriver):
            def __init__(self, *, config):
                super().__init__(config=config, client=api)

        registry = PluginRegistry()
        registry.register(
            PluginManifest(
                plugin_id="aws",
                display_name="aws",
                version="fixture",
                drivers={"managed:stream:kinesis": Driver},
            )
        )
        state = SimpleNamespace(
            api=api, cfg=KinesisConfig(region="us-west-2", account_id="123456789012"), cls=Driver
        )
        monkeypatch.setattr("astrolift_drivers.registry.plugins", registry)
        monkeypatch.setattr("core.cluster_observability.managed_config_for", lambda *_, **__: state.cfg)
        yield state


def new_service(org_slug, app_slug="api"):
    svc = _service(org_slug=org_slug, plugin_slug="aws", variant="kinesis", backend_ref="")
    svc.kind, svc.name = "stream", "events"
    svc.registered_app.slug = app_slug
    svc.registered_app.save(update_fields=["slug"])
    svc.save(update_fields=["kind", "name"])
    return svc


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_saved_orgs_with_colliding_previous_names_receive_distinct_owned_streams(cloud, collision):
    if collision == "joined":
        first, second = new_service("alpha-beta", "gamma"), new_service("alpha", "beta-gamma")
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, stream_name_prefix="p" * 300)
        first, second = new_service("alpha"), new_service("beta")
    previous = [
        "-".join(
            (
                cloud.cfg.stream_name_prefix,
                row.registered_app.organization.slug,
                row.registered_app.slug,
                row.app_environment.name,
                row.name,
            )
        )[:128]
        for row in (first, second)
    ]
    assert previous[0] == previous[1]
    results = [_provision_sync(row.pk) for row in (first, second)]
    assert all(result["ok"] for result in results), results
    assert results[0]["handle"] != results[1]["handle"]
    for row, result in zip((first, second), results, strict=True):
        arn = result["handle"].partition("/")[2]
        name = arn.rsplit("/", 1)[1]
        assert name.endswith(row.guid.hex) and len(name) <= 128
        tags = cloud.api.list_tags_for_resource(ResourceARN=arn)["Tags"]
        assert {tag["Key"]: tag["Value"] for tag in tags}["astrolift.io/managed_service_id"] == str(row.guid)
        assert _provision_sync(row.pk)["handle"] == result["handle"]
    assert len(cloud.api.list_streams()["StreamNames"]) == 2


def test_recorded_case_sensitive_legacy_arn_survives_slug_and_prefix_changes(cloud):
    row = new_service("legacy")
    locator = "stream/arn:aws:kinesis:us-west-2:123456789012:stream/Old.Stream_Name"
    spec = dataclasses.replace(
        build_provision_spec(row, cluster=row.app_environment.tenant_cluster), recorded_handle=locator
    )
    initial = cloud.cls(config=cloud.cfg).provision(spec)
    assert initial.ok and initial.handle == locator
    row.backend_ref = locator
    row.save(update_fields=["backend_ref"])
    row.registered_app.slug = "renamed-app"
    row.registered_app.save(update_fields=["slug"])
    cloud.cfg = dataclasses.replace(cloud.cfg, stream_name_prefix="renamed-prefix")
    before = cloud.api.describe_stream_summary(StreamName="Old.Stream_Name")["StreamDescriptionSummary"]
    result = _provision_sync(row.pk)
    assert result["ok"] and result["handle"] == locator
    after = cloud.api.describe_stream_summary(StreamName="Old.Stream_Name")["StreamDescriptionSummary"]
    assert before == after
    assert cloud.api.list_streams()["StreamNames"] == ["Old.Stream_Name"]
    row.refresh_from_db()
    assert row.backend_ref == locator


def test_foreign_recorded_stream_and_config_spoof_cannot_retag_or_reconcile(cloud):
    first, second = new_service("owner"), new_service("contender")
    initial = _provision_sync(first.pk)
    assert initial["ok"]
    arn = initial["handle"].partition("/")[2]
    second.backend_ref = initial["handle"]
    second.config = {"managed_service_id": str(first.guid), "retention_hours": 72}
    second.save(update_fields=["backend_ref", "config"])
    tags_before = cloud.api.list_tags_for_resource(ResourceARN=arn)["Tags"]
    summary_before = cloud.api.describe_stream_summary(StreamARN=arn)["StreamDescriptionSummary"]
    refused = _provision_sync(second.pk)
    assert not refused["ok"] and "outside this resource declaration" in refused["message"]
    assert cloud.api.list_tags_for_resource(ResourceARN=arn)["Tags"] == tags_before
    assert cloud.api.describe_stream_summary(StreamARN=arn)["StreamDescriptionSummary"] == summary_before
    assert len(cloud.api.list_streams()["StreamNames"]) == 1


@pytest.mark.parametrize(
    "locator",
    [
        "other_kind/arn:aws:kinesis:us-west-2:123456789012:stream/original",
        "stream/arn:aws:kinesis:us-west-2:999999999999:stream/original",
        "stream/arn:aws:kinesis:us-east-1:123456789012:stream/original",
        "stream/arn:aws:sns:us-west-2:123456789012:original",
        "stream/arn:aws:kinesis:us-west-2:123456789012:stream/original/extra",
    ],
)
def test_recorded_other_kind_account_region_or_resource_never_creates_fallback(cloud, locator):
    row = new_service("wrong-target")
    row.backend_ref = locator
    row.save(update_fields=["backend_ref"])
    with pytest.raises(ManagedServiceError):
        _provision_sync(row.pk)
    assert cloud.api.list_streams()["StreamNames"] == []
