"""Saved KMS references reach actual registry/configuration and native SDK state."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

import pytest
from aws._naming import iam_role_name
from aws.managed.encryption_kms import KMSDriver
from tests.aws._kms_native_2032 import native_kms
from tests.aws.test_kms_naming_moto_2032 import OTHER_ID, spies

from astrolift_drivers.registry import PluginManifest, PluginRegistry
from astrolift_workflows.activities.managed_service_lifecycle import (
    _deprovision_sync,
    _provision_sync,
    _update_sync,
)
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service

pytestmark = pytest.mark.django_db


@pytest.fixture
def cloud(monkeypatch):
    with native_kms(monkeypatch) as state:

        class Driver(KMSDriver):
            def __init__(self, *, config):
                super().__init__(config=config, client=state.api, regional_clients=state.clients)

        registry = PluginRegistry()
        registry.register(
            PluginManifest(
                plugin_id="aws",
                display_name="aws",
                version="fixture",
                drivers={"managed:encryption_key:kms": Driver},
            )
        )
        monkeypatch.setattr("astrolift_drivers.registry.plugins", registry)
        state.cls = Driver
        yield state


def service(cloud, org_slug, app_slug="api", prefix="alias/platform"):
    row = _service(org_slug=org_slug, plugin_slug="aws", variant="kms", backend_ref="")
    row.kind, row.name = "encryption_key", "data-key"
    row.registered_app.slug = app_slug
    row.registered_app.save(update_fields=["slug"])
    row.save(update_fields=["kind", "name"])
    cluster = row.app_environment.tenant_cluster
    cluster.region = "us-east-1"
    cluster.provider_config = {"account_id": "123456789012", "kms_alias_name_prefix": prefix}
    cluster.save(update_fields=["region", "provider_config"])
    return row


@pytest.mark.parametrize("collision", ["joined", "long_prefix"])
def test_saved_orgs_with_previous_alias_collision_get_distinct_guid_owned_keys(cloud, collision):
    prefix = "alias/" + "p" * 200 if collision == "long_prefix" else "alias/platform"
    first, second = (
        service(cloud, "alpha-beta", "gamma", prefix),
        service(cloud, "alpha", "beta-gamma", prefix),
    )
    cosmetic = prefix.removeprefix("alias/")
    old = [
        "alias/"
        + cosmetic
        + "-"
        + iam_role_name(
            r.registered_app.organization.slug,
            r.registered_app.slug,
            "prod",
            r.name,
            max_len=max(1, 250 - len(cosmetic)),
        )
        for r in (first, second)
    ]
    assert old[0] == old[1]
    results = [_provision_sync(row.pk) for row in (first, second)]
    assert all(r["ok"] for r in results), results
    assert results[0]["handle"] != results[1]["handle"]
    for row, result in zip((first, second), results, strict=True):
        arn = result["handle"].partition("/")[2]
        alias = cloud.api.list_aliases(KeyId=arn)["Aliases"][0]
        assert alias["AliasName"].endswith(row.guid.hex)
        tags = cloud.api.list_resource_tags(KeyId=arn)["Tags"]
        assert {t["TagKey"]: t["TagValue"] for t in tags}["astrolift.io/managed_service_id"] == str(row.guid)
        assert _provision_sync(row.pk)["handle"] == result["handle"]
    assert len(cloud.api.list_keys()["Keys"]) == 2


def test_saved_legacy_root_alias_stays_exact_after_labels_and_prefix_change(cloud):
    row = service(cloud, "legacy")
    row.config = {"alias": "alias/old-service"}
    row.save(update_fields=["config"])
    result = _provision_sync(row.pk)
    assert result["ok"], result
    row.backend_ref = result["handle"]
    row.save(update_fields=["backend_ref"])
    row.refresh_from_db()
    assert row.backend_ref == result["handle"]
    row.config = {}
    row.save(update_fields=["config"])
    row.registered_app.slug = "renamed-app"
    row.registered_app.save(update_fields=["slug"])
    cluster = row.app_environment.tenant_cluster
    cluster.provider_config = {"account_id": "123456789012", "kms_alias_name_prefix": "alias/new-prefix"}
    cluster.save(update_fields=["provider_config"])
    with (
        patch.object(cloud.api, "create_key", wraps=cloud.api.create_key) as create,
        patch.object(cloud.api, "list_keys", wraps=cloud.api.list_keys) as discover,
    ):
        recovered = _provision_sync(row.pk)
        assert recovered["ok"] and recovered["handle"] == result["handle"]
        create.assert_not_called()
        discover.assert_not_called()
    arn = result["handle"].partition("/")[2]
    assert {a["AliasName"] for a in cloud.api.list_aliases(KeyId=arn)["Aliases"]} == {"alias/old-service"}
    row.config = {"description": "updated"}
    row.save(update_fields=["config"])
    assert _update_sync(row.pk)["ok"]
    assert cloud.api.describe_key(KeyId=arn)["KeyMetadata"]["Description"] == "updated"
    assert _deprovision_sync(row.pk, delete_data=True, force_destroy=True)["ok"]
    assert cloud.api.describe_key(KeyId=arn)["KeyMetadata"]["KeyState"] == "PendingDeletion"


def assert_no_effects(cloud, row):
    stack, calls = spies(cloud)
    with stack:
        assert not _provision_sync(row.pk)["ok"]
        for result in (_update_sync(row.pk), _deprovision_sync(row.pk, delete_data=True, force_destroy=True)):
            assert not result["ok"] and not result["retryable"], result
        for call in calls:
            call.assert_not_called()


def test_saved_foreign_root_cannot_be_reconfigured_or_force_deleted(cloud):
    first, second = service(cloud, "owner"), service(cloud, "other")
    result = _provision_sync(first.pk)
    assert result["ok"]
    second.backend_ref = result["handle"]
    second.config = {"description": "refused"}
    second.save(update_fields=["backend_ref", "config"])
    assert_no_effects(cloud, second)


@pytest.mark.parametrize("field", ["kind", "account", "region", "partition", "path", "key"])
def test_saved_invalid_key_arn_never_creates_a_replacement(cloud, field):
    row = service(cloud, "invalid")
    handle = "encryption_key/arn:aws:kms:us-east-1:123456789012:key/11111111-1111-4111-8111-111111111111"
    old, new = {
        "kind": ("encryption_key/", "topic/"),
        "account": ("123456789012", "999999999999"),
        "region": ("us-east-1", "us-west-2"),
        "partition": ("arn:aws:", "arn:aws-cn:"),
        "path": (":key/", ":alias/"),
        "key": ("11111111-1111-4111-8111-111111111111", "invalid-key"),
    }[field]
    row.backend_ref = handle.replace(old, new)
    row.save(update_fields=["backend_ref"])
    with (
        patch.object(cloud.api, "create_key", wraps=cloud.api.create_key) as create,
        patch.object(cloud.api, "list_keys", wraps=cloud.api.list_keys) as discover,
    ):
        assert not _provision_sync(row.pk)["ok"]
        create.assert_not_called()
        discover.assert_not_called()
    row.refresh_from_db()
    assert row.backend_ref == handle.replace(old, new)


def test_saved_missing_key_cannot_retarget_a_live_foreign_legacy_alias(cloud):
    row, other = service(cloud, "missing"), service(cloud, "replacement")
    other.config = {"alias": "alias/old-service"}
    other.save(update_fields=["config"])
    foreign = _provision_sync(other.pk)
    assert foreign["ok"]
    row.backend_ref = (
        "encryption_key/arn:aws:kms:us-east-1:123456789012:key/11111111-1111-4111-8111-111111111111"
    )
    row.config = {"alias": "alias/old-service"}
    row.save(update_fields=["backend_ref", "config"])
    with patch.object(cloud.api, "create_key", wraps=cloud.api.create_key) as create:
        assert not _provision_sync(row.pk)["ok"]
        create.assert_not_called()
    assert len(cloud.api.list_keys()["Keys"]) == 1
    assert (
        cloud.api.describe_key(KeyId=foreign["handle"].partition("/")[2])["KeyMetadata"]["KeyState"]
        == "Enabled"
    )


def test_saved_multi_region_key_replicas_share_exact_identity_with_independent_native_owners(cloud):
    row = service(cloud, "replicated")
    row.config = {"multi_region": True, "replica_regions": ["us-west-2", "eu-west-1"]}
    row.save(update_fields=["config"])
    result = _provision_sync(row.pk)
    assert result["ok"], result
    row.backend_ref = result["handle"]
    row.save(update_fields=["backend_ref"])
    arn = result["handle"].partition("/")[2]
    key_id = cloud.api.describe_key(KeyId=arn)["KeyMetadata"]["KeyId"]
    for region in row.config["replica_regions"]:
        replica = cloud.clients[region].describe_key(KeyId=key_id)["KeyMetadata"]
        assert replica["MultiRegionConfiguration"]["PrimaryKey"] == {"Arn": arn, "Region": "us-east-1"}
        assert replica["KeyId"] == key_id
    assert _deprovision_sync(row.pk, delete_data=True, force_destroy=True)["ok"]
    assert all(
        api.describe_key(KeyId=key_id)["KeyMetadata"]["KeyState"] == "PendingDeletion"
        for api in cloud.clients.values()
    )


@pytest.mark.parametrize("failure", ["owner", "parent", "foreign_arn"])
def test_saved_replica_corruption_refuses_all_primary_and_child_effects(cloud, failure):
    row = service(cloud, "corrupt-replica")
    row.config = {"multi_region": True, "replica_regions": ["us-west-2", "eu-west-1"]}
    row.save(update_fields=["config"])
    result = _provision_sync(row.pk)
    assert result["ok"], result
    row.backend_ref = result["handle"]
    row.save(update_fields=["backend_ref"])
    arn = result["handle"].partition("/")[2]
    metadata = cloud.api.describe_key(KeyId=arn)["KeyMetadata"]
    api = cloud.clients["eu-west-1"]
    if failure == "owner":
        api.tag_resource(
            KeyId=metadata["KeyId"],
            Tags=[{"TagKey": "astrolift.io/managed_service_id", "TagValue": OTHER_ID}],
        )
        context = ExitStack()
    else:
        original = api.describe_key if failure == "parent" else cloud.api.describe_key

        def bad(**kw):
            result = original(**kw)
            if failure == "parent":
                result["KeyMetadata"]["MultiRegionConfiguration"]["PrimaryKey"]["Arn"] = (
                    "arn:aws:kms:us-east-1:123456789012:key/mrk-" + "0" * 32
                )
            else:
                result["KeyMetadata"]["MultiRegionConfiguration"]["ReplicaKeys"][-1]["Arn"] = (
                    "arn:aws:kms:eu-west-1:123456789012:key/mrk-" + "0" * 32
                )
            return result

        context = patch.object(api if failure == "parent" else cloud.api, "describe_key", side_effect=bad)
    row.config["description"] = "refused"
    row.save(update_fields=["config"])
    with context:
        assert_no_effects(cloud, row)


@pytest.mark.parametrize("child", ["alias", "grant"])
def test_saved_root_refuses_foreign_native_child_metadata_before_every_effect(cloud, child):
    row, other = service(cloud, "owned-child"), service(cloud, "foreign-child")
    first, second = _provision_sync(row.pk), _provision_sync(other.pk)
    assert first["ok"] and second["ok"]
    row.backend_ref = first["handle"]
    row.config = {"description": "refused"}
    row.save(update_fields=["backend_ref", "config"])
    a, b = (r["handle"].partition("/")[2] for r in (first, second))
    if child == "alias":
        original = cloud.api.list_aliases
        foreign = original(KeyId=b)["Aliases"][0]

        def corrupt(**kw):
            result = original(**kw)
            if kw.get("KeyId") in {a, a.rsplit("/", 1)[1]}:
                result["Aliases"].append(foreign)
            return result

        method = "list_aliases"
    else:
        cloud.api.create_grant(
            KeyId=b,
            Name="astrolift-worker",
            GranteePrincipal="arn:aws:iam::123456789012:role/fixture",
            Operations=["Encrypt"],
        )
        original = cloud.api.list_grants
        foreign = original(KeyId=b)["Grants"][0]

        def corrupt(**kw):
            result = original(**kw)
            if kw.get("KeyId") in {a, a.rsplit("/", 1)[1]}:
                result["Grants"].append(foreign)
            return result

        method = "list_grants"
    with patch.object(cloud.api, method, side_effect=corrupt):
        assert_no_effects(cloud, row)


def test_saved_dispatch_partial_key_recovers_by_guid_without_duplicate_create(cloud):
    row = service(cloud, "partial")
    with patch.object(
        cloud.api, "create_alias", side_effect=RuntimeError("fixture interrupted alias acknowledgement")
    ):
        first = _provision_sync(row.pk)
    assert not first["ok"] and first["handle"]
    row.registered_app.slug = "renamed-app"
    row.registered_app.save(update_fields=["slug"])
    with patch.object(cloud.api, "create_key", wraps=cloud.api.create_key) as create:
        recovered = _provision_sync(row.pk)
        assert recovered["ok"] and recovered["handle"] == first["handle"]
        create.assert_not_called()
    assert len(cloud.api.list_keys()["Keys"]) == 1


@pytest.mark.parametrize("field", ["Arn", "KeyId", "AWSAccountId", "KeyManager"])
def test_saved_live_metadata_inconsistency_refuses_every_effect(cloud, field):
    row = service(cloud, "wrong-metadata")
    first = _provision_sync(row.pk)
    assert first["ok"]
    row.backend_ref = first["handle"]
    row.config = {"description": "refused"}
    row.save(update_fields=["backend_ref", "config"])
    original = cloud.api.describe_key

    def wrong(**kw):
        result = original(**kw)
        result["KeyMetadata"][field] = {
            "Arn": "arn:aws:kms:us-east-1:123456789012:key/22222222-2222-4222-8222-222222222222",
            "KeyId": OTHER_ID,
            "AWSAccountId": "999999999999",
            "KeyManager": "AWS",
        }[field]
        return result

    with patch.object(cloud.api, "describe_key", side_effect=wrong):
        assert_no_effects(cloud, row)
