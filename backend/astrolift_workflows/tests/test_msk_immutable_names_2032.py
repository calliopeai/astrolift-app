"""Saved MSK targets reach real registry and boto3/native storage with PG rows."""

from __future__ import annotations

import dataclasses
from unittest.mock import patch

import pytest
from aws.managed._base import ManagedServiceError
from tests.aws._msk_native_2032 import native_msk

from astrolift_drivers.registry import PluginManifest, PluginRegistry
from astrolift_workflows.activities.managed_service_lifecycle import (
    _deprovision_sync,
    _provision_sync,
    _update_sync,
    build_provision_spec,
)
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["msk", "msk_serverless"])
def cloud(request, monkeypatch):
    with native_msk(monkeypatch, request.param) as state:
        base = state.cls

        class Driver(base):
            def __init__(self, *, config):
                super().__init__(config=config, client=state.api, sleep=lambda _: None)

        registry = PluginRegistry()
        registry.register(
            PluginManifest(
                plugin_id="aws",
                display_name="aws",
                version="fixture",
                drivers={f"managed:event_stream:{state.variant}": Driver},
            )
        )
        monkeypatch.setattr("astrolift_drivers.registry.plugins", registry)
        monkeypatch.setattr("core.cluster_observability.managed_config_for", lambda *_, **__: state.cfg)
        state.driver = Driver
        yield state


def service(cloud, org_slug, app_slug="api"):
    row = _service(org_slug=org_slug, plugin_slug="aws", variant=cloud.variant, backend_ref="")
    row.kind, row.name = "event_stream", "kafka"
    row.registered_app.slug = app_slug
    row.registered_app.save(update_fields=["slug"])
    row.save(update_fields=["kind", "name"])
    return row


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_saved_organizations_with_actual_previous_collision_get_distinct_owned_cluster_incarnations(
    cloud, collision
):
    if collision == "joined":
        first, second = service(cloud, "alpha-beta", "gamma"), service(cloud, "alpha", "beta-gamma")
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, cluster_name_prefix="p" * 100)
        first, second = service(cloud, "alpha"), service(cloud, "beta")
    old = [
        "-".join(
            (
                cloud.cfg.cluster_name_prefix,
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
        arn = result["handle"].partition("/")[2]
        metadata = cloud.api.describe_cluster_v2(ClusterArn=arn)["ClusterInfo"]
        assert metadata["ClusterName"].endswith(row.guid.hex) and len(metadata["ClusterName"]) <= 64
        assert cloud.api.list_tags_for_resource(ResourceArn=arn)["Tags"][
            "astrolift.io/managed_service_id"
        ] == str(row.guid)
        assert _provision_sync(row.pk)["handle"] == result["handle"]
    assert len(cloud.api.list_clusters_v2()["ClusterInfoList"]) == 2


@pytest.mark.parametrize("name", ["Old_Cluster", "Old_Café"])
def test_saved_legacy_incarnation_survives_labels_prefix_and_real_policy_lifecycle(cloud, name):
    row = service(cloud, "legacy")
    spec = build_provision_spec(row, cluster=row.app_environment.tenant_cluster)
    driver = cloud.driver(config=cloud.cfg)
    arn = cloud.api.create_cluster_v2(**driver._create_request(name, spec))["ClusterArn"]
    locator = "event_stream/" + arn
    row.backend_ref = locator
    row.save(update_fields=["backend_ref"])
    row.registered_app.slug = "changed-app"
    row.registered_app.save(update_fields=["slug"])
    cloud.cfg = dataclasses.replace(cloud.cfg, cluster_name_prefix="new-prefix")
    with (
        patch.object(cloud.api, "create_cluster_v2", wraps=cloud.api.create_cluster_v2) as create,
        patch.object(cloud.api, "list_clusters_v2", wraps=cloud.api.list_clusters_v2) as discover,
    ):
        result = _provision_sync(row.pk)
        assert result["ok"] and result["handle"] == locator
        create.assert_not_called()
        discover.assert_not_called()
    row.refresh_from_db()
    assert row.backend_ref == locator
    row.config = {"resource_policy": {"Version": "2012-10-17", "Statement": []}}
    row.save(update_fields=["config"])
    assert _update_sync(row.pk)["ok"]
    assert cloud.api.get_cluster_policy(ClusterArn=arn)["Policy"] == '{"Statement":[],"Version":"2012-10-17"}'
    assert _deprovision_sync(row.pk, delete_data=True, force_destroy=True)["ok"]
    assert cloud.api.list_clusters_v2()["ClusterInfoList"] == []


def test_foreign_saved_service_cannot_reconfigure_retag_or_force_delete_cluster(cloud):
    first, second = service(cloud, "owner"), service(cloud, "other")
    initial = _provision_sync(first.pk)
    assert initial["ok"]
    arn = initial["handle"].partition("/")[2]
    second.backend_ref = initial["handle"]
    second.config = {"resource_policy": {"Statement": []}}
    second.save(update_fields=["backend_ref", "config"])
    before = cloud.api.describe_cluster_v2(ClusterArn=arn)["ClusterInfo"]
    assert not _provision_sync(second.pk)["ok"]
    updated, deleted = (
        _update_sync(second.pk),
        _deprovision_sync(second.pk, delete_data=True, force_destroy=True),
    )
    assert not updated["ok"] and not updated["retryable"]
    assert not deleted["ok"] and not deleted["retryable"]
    assert cloud.api.describe_cluster_v2(ClusterArn=arn)["ClusterInfo"] == before


def test_missing_saved_incarnation_cannot_adopt_a_new_incarnation_with_legacy_name(cloud):
    row = service(cloud, "replaced")
    initial = _provision_sync(row.pk)
    assert initial["ok"]
    row.backend_ref = initial["handle"]
    row.save(update_fields=["backend_ref"])
    arn = initial["handle"].partition("/")[2]
    name = cloud.api.describe_cluster_v2(ClusterArn=arn)["ClusterInfo"]["ClusterName"]
    cloud.api.delete_cluster(ClusterArn=arn)
    replacement = service(cloud, "replacement")
    spec = build_provision_spec(replacement, cluster=replacement.app_environment.tenant_cluster)
    new_arn = cloud.api.create_cluster_v2(**cloud.driver(config=cloud.cfg)._create_request(name, spec))[
        "ClusterArn"
    ]
    with (
        patch.object(cloud.api, "tag_resource", wraps=cloud.api.tag_resource) as retag,
        patch.object(cloud.api, "create_cluster_v2", wraps=cloud.api.create_cluster_v2) as create,
        patch.object(cloud.api, "list_clusters_v2", wraps=cloud.api.list_clusters_v2) as discover,
    ):
        assert not _provision_sync(row.pk)["ok"]
        for call in (retag, create, discover):
            call.assert_not_called()
    row.refresh_from_db()
    assert row.backend_ref == initial["handle"]
    assert cloud.api.list_tags_for_resource(ResourceArn=new_arn)["Tags"][
        "astrolift.io/managed_service_id"
    ] == str(replacement.guid)


@pytest.mark.parametrize(
    "target", ["kind", "account", "region", "partition", "path", "length", "incarnation"]
)
def test_invalid_saved_incarnation_never_discovers_or_creates_a_fallback(cloud, target):
    row = service(cloud, "invalid")
    locator = "event_stream/arn:aws:kafka:us-west-2:123456789012:cluster/Original/old-incarnation"
    old, new = {
        "kind": ("event_stream/", "topic/"),
        "account": ("123456789012", "999999999999"),
        "region": ("us-west-2", "us-east-1"),
        "partition": ("arn:aws:", "arn:aws-cn:"),
        "path": (":cluster/", ":topic/"),
        "length": ("Original", "x" * 65),
        "incarnation": ("old-incarnation", "invalid:uuid"),
    }[target]
    row.backend_ref = locator.replace(old, new)
    row.save(update_fields=["backend_ref"])
    with pytest.raises(ManagedServiceError):
        _provision_sync(row.pk)
    assert cloud.api.list_clusters_v2()["ClusterInfoList"] == []


@pytest.mark.parametrize("field", ["ClusterArn", "ClusterName", "ClusterType"])
def test_contaminated_saved_cluster_metadata_never_drives_policy_tag_or_deletion(cloud, field):
    row = service(cloud, "owner")
    initial = _provision_sync(row.pk)
    assert initial["ok"]
    row.backend_ref = initial["handle"]
    row.config = {"resource_policy": {"Statement": []}}
    row.save(update_fields=["backend_ref", "config"])
    arn = initial["handle"].partition("/")[2]
    before = cloud.api.describe_cluster_v2(ClusterArn=arn)["ClusterInfo"]
    with (
        patch.object(
            cloud.api,
            "describe_cluster_v2",
            return_value={"ClusterInfo": {**before, field: before[field] + "-foreign"}},
        ),
        patch.object(cloud.api, "tag_resource", wraps=cloud.api.tag_resource) as retag,
        patch.object(cloud.api, "put_cluster_policy", wraps=cloud.api.put_cluster_policy) as policy,
        patch.object(cloud.api, "delete_cluster", wraps=cloud.api.delete_cluster) as delete,
    ):
        assert not _provision_sync(row.pk)["ok"]
        assert not _update_sync(row.pk)["ok"]
        assert not _deprovision_sync(row.pk, delete_data=True, force_destroy=True)["ok"]
        for call in (retag, policy, delete):
            call.assert_not_called()
    assert cloud.api.describe_cluster_v2(ClusterArn=arn)["ClusterInfo"] == before
