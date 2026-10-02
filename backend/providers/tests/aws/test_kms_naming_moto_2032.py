"""KMS exact saved root/child identities exercised through native boto3 calls."""

from __future__ import annotations

import dataclasses
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from _sdk.managed_service import DeprovisionSpec, UpdateSpec
from aws._naming import iam_role_name
from aws.managed.encryption_kms import KMSDriver
from tests.aws._kms_native_2032 import OTHER_ID, native_kms, spies
from tests.aws.test_managed_kms import SERVICE_ID, _spec


@pytest.fixture
def cloud(monkeypatch):
    with native_kms(monkeypatch) as state:
        yield state


def assert_refused_without_writes(cloud, spec):
    stack, calls = spies(cloud)
    with stack:
        assert not cloud.driver.provision(spec).ok
        for method in (
            lambda: cloud.driver.update(
                UpdateSpec(spec.recorded_handle, config=spec.config, managed_service_id=spec.managed_service_id)
            ),
            lambda: cloud.driver.deprovision(
                DeprovisionSpec(spec.recorded_handle, spec.config, managed_service_id=spec.managed_service_id),
                delete_data=True,
                force_destroy=True,
            ),
        ):
            result = method()
            assert not result.ok and not result.retryable
        for call in calls:
            call.assert_not_called()


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_native_alias_defaults_split_old_collisions_and_retries_keep_each_owned_key(cloud, collision):
    cfg = cloud.cfg
    if collision == "truncated":
        cfg = dataclasses.replace(cfg, alias_name_prefix="alias/" + "p" * 200)
    cloud.driver = KMSDriver(config=cfg, client=cloud.api, regional_clients=cloud.clients)
    first = dataclasses.replace(_spec(), organization_slug="alpha-beta", app_slug="gamma")
    second = dataclasses.replace(_spec(), managed_service_id=OTHER_ID, organization_slug="alpha", app_slug="beta-gamma")
    prefix = cfg.alias_name_prefix.removeprefix("alias/").rstrip("-/")
    old = [
        "alias/"
        + prefix
        + "-"
        + iam_role_name(
            s.organization_slug,
            s.app_slug,
            s.environment_name,
            s.service_handle_hint,
            max_len=max(1, 250 - len(prefix)),
        )
        for s in (first, second)
    ]
    assert old[0] == old[1]
    results = [cloud.driver.provision(s) for s in (first, second)]
    assert all(r.ok for r in results), results
    assert results[0].handle != results[1].handle
    for spec, result in zip((first, second), results, strict=True):
        aliases = cloud.api.list_aliases(KeyId=result.handle.partition("/")[2])["Aliases"]
        assert aliases[0]["AliasName"].endswith(spec.managed_service_id.replace("-", ""))
        assert len(aliases[0]["AliasName"]) <= 256
        assert cloud.driver.provision(spec).handle == result.handle
    assert len(cloud.api.list_keys()["Keys"]) == 2


def test_recorded_legacy_key_and_alias_survive_prefix_labels_and_native_grant_lifecycle(cloud):
    initial = cloud.driver.provision(_spec({"alias": "alias/old-service", "aliases": ["alias/old-secondary"]}))
    assert initial.ok
    cloud.driver = KMSDriver(
        config=dataclasses.replace(cloud.cfg, alias_name_prefix="alias/new-prefix"), client=cloud.api
    )
    spec = dataclasses.replace(
        _spec(), recorded_handle=initial.handle, app_slug="renamed-app", organization_slug="renamed-org"
    )
    with patch.object(cloud.api, "create_key", wraps=cloud.api.create_key) as create:
        assert cloud.driver.provision(spec).handle == initial.handle
        create.assert_not_called()
    aliases = cloud.api.list_aliases(KeyId=initial.handle.partition("/")[2])["Aliases"]
    assert {a["AliasName"] for a in aliases} == {"alias/old-service", "alias/old-secondary"}
    config = {
        "description": "updated",
        "grants": [
            {
                "name": "worker",
                "request": {"GranteePrincipal": "arn:aws:iam::123456789012:role/fixture", "Operations": ["Encrypt"]},
            }
        ],
    }
    assert cloud.driver.update(UpdateSpec(initial.handle, config=config, managed_service_id=SERVICE_ID)).ok
    grants = cloud.api.list_grants(KeyId=initial.handle.partition("/")[2])["Grants"]
    assert len(grants) == 1 and grants[0]["Name"] == "astrolift-worker"
    assert cloud.driver.deprovision(
        DeprovisionSpec(initial.handle, {}, managed_service_id=SERVICE_ID), delete_data=True, force_destroy=True
    ).ok
    assert cloud.api.list_grants(KeyId=initial.handle.partition("/")[2])["Grants"] == []


@pytest.mark.parametrize("owner", ["foreign", "missing", "marker"])
def test_even_force_cannot_mutate_recorded_key_without_canonical_live_owner(cloud, owner):
    initial = cloud.driver.provision(_spec())
    arn = initial.handle.partition("/")[2]
    if owner == "missing":
        cloud.api.untag_resource(KeyId=arn, TagKeys=["astrolift.io/managed_service_id"])
    else:
        name, value = (
            ("astrolift.io/managed_service_id", OTHER_ID)
            if owner == "foreign"
            else ("astrolift.io/managed-by", "other")
        )
        cloud.api.tag_resource(KeyId=arn, Tags=[{"TagKey": name, "TagValue": value}])
    assert_refused_without_writes(
        cloud, dataclasses.replace(_spec({"description": "refused"}), recorded_handle=initial.handle)
    )


@pytest.mark.parametrize("field", ["kind", "account", "region", "partition", "path", "key"])
def test_invalid_recorded_key_cannot_discover_or_create_an_alias_fallback(cloud, field):
    handle = "encryption_key/arn:aws:kms:us-east-1:123456789012:key/11111111-1111-4111-8111-111111111111"
    old, new = {
        "kind": ("encryption_key/", "topic/"),
        "account": ("123456789012", "999999999999"),
        "region": ("us-east-1", "us-west-2"),
        "partition": ("arn:aws:", "arn:aws-cn:"),
        "path": (":key/", ":alias/"),
        "key": ("11111111-1111-4111-8111-111111111111", "invalid-key"),
    }[field]
    spec = dataclasses.replace(_spec(), recorded_handle=handle.replace(old, new))
    with (
        patch.object(cloud.api, "create_key", wraps=cloud.api.create_key) as create,
        patch.object(cloud.api, "list_keys", wraps=cloud.api.list_keys) as discover,
    ):
        assert not cloud.driver.provision(spec).ok
        create.assert_not_called()
        discover.assert_not_called()


@pytest.mark.parametrize("child", ["alias", "grant"])
def test_foreign_child_on_filtered_native_page_refuses_every_effect(cloud, child):
    first, second = (
        cloud.driver.provision(_spec()),
        cloud.driver.provision(dataclasses.replace(_spec(), managed_service_id=OTHER_ID)),
    )
    a, b = (r.handle.partition("/")[2] for r in (first, second))
    if child == "alias":
        original = cloud.api.list_aliases
        foreign = original(KeyId=b)["Aliases"][0]

        def corrupt(**kw):
            r = original(**kw)
            if kw.get("KeyId") in {a, a.rsplit("/", 1)[1]}:
                r["Aliases"].append(foreign)
            return r

        name = "list_aliases"
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
            r = original(**kw)
            if kw.get("KeyId") in {a, a.rsplit("/", 1)[1]}:
                r["Grants"].append(foreign)
            return r

        name = "list_grants"
    with patch.object(cloud.api, name, side_effect=corrupt):
        spec = dataclasses.replace(_spec({"description": "refused"}), recorded_handle=first.handle)
        stack, calls = spies(cloud)
        with stack:
            assert not cloud.driver.provision(spec).ok
            assert not cloud.driver.update(
                UpdateSpec(first.handle, config=spec.config, managed_service_id=SERVICE_ID)
            ).ok
            assert not cloud.driver.deprovision(
                DeprovisionSpec(first.handle, {}, managed_service_id=SERVICE_ID),
                delete_data=True,
                force_destroy=True,
            ).ok
            for call in calls:
                call.assert_not_called()


def test_native_replicas_have_exact_primary_ownership_and_independent_alias_grants(cloud):
    cfg = {"multi_region": True, "replica_regions": ["us-west-2", "eu-west-1"]}
    initial = cloud.driver.provision(_spec(cfg))
    assert initial.ok, initial
    metadata = cloud.api.describe_key(KeyId=initial.handle.partition("/")[2])["KeyMetadata"]
    for region in cfg["replica_regions"]:
        replica = cloud.clients[region].describe_key(KeyId=metadata["KeyId"])["KeyMetadata"]
        assert replica["MultiRegionConfiguration"]["PrimaryKey"]["Arn"] == metadata["Arn"]
        tags = cloud.clients[region].list_resource_tags(KeyId=metadata["KeyId"])["Tags"]
        assert {t["TagKey"]: t["TagValue"] for t in tags}["astrolift.io/managed_service_id"] == SERVICE_ID
    assert cloud.driver.deprovision(
        DeprovisionSpec(initial.handle, {}, managed_service_id=SERVICE_ID), delete_data=True, force_destroy=True
    ).ok
    assert all(
        api.describe_key(KeyId=metadata["KeyId"])["KeyMetadata"]["KeyState"] == "PendingDeletion"
        for api in cloud.clients.values()
    )


@pytest.mark.parametrize("failure", ["owner", "parent", "foreign_arn"])
def test_every_replica_is_preflighted_before_any_primary_or_replica_mutation(cloud, failure):
    cfg = {"multi_region": True, "replica_regions": ["us-west-2", "eu-west-1"]}
    initial = cloud.driver.provision(_spec(cfg))
    assert initial.ok
    arn = initial.handle.partition("/")[2]
    metadata = cloud.api.describe_key(KeyId=arn)["KeyMetadata"]
    api = cloud.clients["eu-west-1"]
    if failure == "owner":
        api.tag_resource(
            KeyId=metadata["KeyId"], Tags=[{"TagKey": "astrolift.io/managed_service_id", "TagValue": OTHER_ID}]
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
    with context:
        assert_refused_without_writes(
            cloud, dataclasses.replace(_spec({**cfg, "description": "refused"}), recorded_handle=initial.handle)
        )


@pytest.mark.parametrize("existing_replica", [False, True])
def test_foreign_desired_replica_alias_refuses_before_any_primary_or_child_effect(cloud, existing_replica):
    cfg = {"multi_region": True, "replica_regions": ["us-west-2"] if existing_replica else []}
    initial = cloud.driver.provision(_spec(cfg))
    assert initial.ok
    api = cloud.clients["us-west-2"]
    foreign = api.create_key()["KeyMetadata"]
    api.create_alias(AliasName="alias/shared-explicit", TargetKeyId=foreign["KeyId"])
    proposed = {
        "multi_region": True,
        "replica_regions": ["us-west-2"],
        "alias": "alias/shared-explicit",
        "description": "refused",
    }
    stack, calls = spies(cloud)
    with stack:
        for result in (
            cloud.driver.provision(dataclasses.replace(_spec(proposed), recorded_handle=initial.handle)),
            cloud.driver.update(UpdateSpec(initial.handle, config=proposed, managed_service_id=SERVICE_ID)),
        ):
            assert not result.ok
        for call in calls:
            call.assert_not_called()


def test_normalized_grant_collision_is_refused_before_key_creation(cloud):
    grant = {"GranteePrincipal": "arn:aws:iam::123456789012:role/fixture", "Operations": ["Encrypt"]}
    cfg = {"grants": [{"name": "worker-one", "request": grant}, {"name": "worker one", "request": grant}]}
    with patch.object(cloud.api, "create_key", wraps=cloud.api.create_key) as create:
        result = cloud.driver.provision(_spec(cfg))
        assert not result.ok and "normalization" in result.message
        create.assert_not_called()


@pytest.mark.parametrize("operation", ["list_keys", "list_aliases", "list_grants", "list_resource_tags"])
def test_cyclic_native_metadata_pages_are_refused_without_writes(cloud, operation):
    initial = cloud.driver.provision(_spec())
    assert initial.ok
    keys = {"list_keys": "Keys", "list_aliases": "Aliases", "list_grants": "Grants", "list_resource_tags": "Tags"}
    if operation == "list_keys":
        spec = dataclasses.replace(_spec(), managed_service_id=OTHER_ID)
    else:
        spec = dataclasses.replace(_spec(), recorded_handle=initial.handle)
    original = getattr(cloud.api, operation)
    requests = []

    def cycle(**kw):
        original(**{key: value for key, value in kw.items() if key != "Marker"})
        requests.append(kw.get("Marker", ""))
        marker = "beta" if kw.get("Marker") == "alpha" else "alpha"
        return {keys[operation]: [], "Truncated": True, "NextMarker": marker}

    with patch.object(cloud.api, operation, side_effect=cycle):
        stack, calls = spies(cloud)
        with stack:
            result = cloud.driver.provision(spec)
            assert not result.ok and "pagination" in result.message
            assert requests == ["", "alpha", "beta"]
            for call in calls:
                call.assert_not_called()


def test_partial_native_key_without_alias_recovers_by_guid_despite_label_changes(cloud):
    with patch.object(cloud.api, "create_alias", side_effect=RuntimeError("fixture interrupted alias acknowledgement")):
        first = cloud.driver.provision(_spec())
    assert not first.ok and first.handle
    changed = dataclasses.replace(_spec(), app_slug="renamed-app", organization_slug="renamed-org")
    with patch.object(cloud.api, "create_key", wraps=cloud.api.create_key) as create:
        recovered = cloud.driver.provision(changed)
        assert recovered.ok and recovered.handle == first.handle
        create.assert_not_called()
    assert len(cloud.api.list_keys()["Keys"]) == 1


def test_multiple_native_keys_with_same_live_guid_refuse_ambiguous_recovery(cloud):
    request = cloud.driver._create_request(_spec(), {})
    cloud.api.create_key(**request)
    cloud.api.create_key(**request)
    stack, calls = spies(cloud)
    with stack:
        result = cloud.driver.provision(_spec())
        assert not result.ok and "multiple KMS keys" in result.message
        for call in calls:
            call.assert_not_called()


@pytest.mark.parametrize("field", ["Arn", "KeyId", "AWSAccountId", "KeyManager"])
def test_wrong_live_key_metadata_refuses_before_every_effect(cloud, field):
    first = cloud.driver.provision(_spec())
    assert first.ok
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
        assert_refused_without_writes(
            cloud, dataclasses.replace(_spec({"description": "refused"}), recorded_handle=first.handle)
        )


@pytest.mark.parametrize("region", ["cn-north-1", "us-gov-west-1", "invalid", "us-east-1/"])
def test_cross_partition_or_invalid_replica_region_cannot_create_a_key(cloud, region):
    with patch.object(cloud.api, "create_key", wraps=cloud.api.create_key) as create:
        result = cloud.driver.provision(_spec({"multi_region": True, "replica_regions": [region]}))
        assert not result.ok and "partition" in result.message
        create.assert_not_called()
