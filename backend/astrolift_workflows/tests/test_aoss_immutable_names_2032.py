"""Persisted collection identities cross the real registry into AWS-shaped requests."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from types import SimpleNamespace

import pytest
from aws.managed._base import ManagedServiceError

from astrolift_drivers.registry import PluginManifest, PluginRegistry
from astrolift_workflows.activities.managed_service_lifecycle import _provision_sync, build_provision_spec
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service
from providers.tests.aws.test_managed_opensearch_serverless import driver

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["SEARCH", "VECTORSEARCH"])
def cloud(request, monkeypatch):
    original, api = driver(request.param)
    cls = type(original)

    class Driver(cls):
        def __init__(self, *, config):
            super().__init__(config=config, opensearch_serverless_client=api)

    kind = "search" if request.param == "SEARCH" else "vector_index"
    variant = "opensearch_serverless" if kind == "search" else "opensearch_serverless_vector"
    registry = PluginRegistry()
    registry.register(
        PluginManifest(
            plugin_id="aws",
            display_name="aws",
            version="fixture",
            drivers={f"managed:{kind}:{variant}": Driver},
        )
    )
    state = SimpleNamespace(api=api, cfg=original._config, cls=Driver, kind=kind, variant=variant)
    monkeypatch.setattr("astrolift_drivers.registry.plugins", registry)
    monkeypatch.setattr("core.cluster_observability.managed_config_for", lambda *_, **__: state.cfg)
    return state


def new_service(cloud, org_slug, app_slug="api"):
    svc = _service(org_slug=org_slug, plugin_slug="aws", variant=cloud.variant, backend_ref="")
    svc.kind, svc.name = cloud.kind, "primary"
    svc.registered_app.slug = app_slug
    svc.registered_app.save(update_fields=["slug"])
    svc.save(update_fields=["kind", "name"])
    return svc


def mutations(cloud):
    return [call for call in cloud.api.calls if call[0].startswith(("Create", "Update", "Delete"))]


def spec_for(row):
    return build_provision_spec(row, cluster=row.app_environment.tenant_cluster)


@pytest.mark.parametrize("collision", ["joined", "truncated"])
def test_colliding_old_slug_names_keep_independent_collections_and_policy_identity(cloud, collision):
    if collision == "joined":
        first, second = new_service(cloud, "alpha-beta", "gamma"), new_service(cloud, "alpha", "beta-gamma")
    else:
        cloud.cfg = dataclasses.replace(cloud.cfg, collection_name_prefix="p" * 300)
        first, second = new_service(cloud, "alpha"), new_service(cloud, "beta")
    old = [
        "-".join(
            (
                cloud.cfg.collection_name_prefix,
                row.registered_app.organization.slug,
                row.registered_app.slug,
                row.app_environment.name,
                row.name,
            )
        )[:32]
        for row in (first, second)
    ]
    assert old[0] == old[1]
    results = [_provision_sync(row.pk) for row in (first, second)]
    assert all(result["ok"] for result in results), results
    assert results[0]["handle"] != results[1]["handle"]
    for row, result in zip((first, second), results, strict=True):
        name = result["handle"].split("/")[1]
        suffix = hashlib.sha256(row.guid.bytes).hexdigest()[:20]
        assert name.endswith(suffix) and len(name) <= 30
        for prefix in ("e", "n", "d"):
            child = cloud.cls._policy_name(prefix, name)
            assert child.endswith(suffix) and len(child) <= 32
        assert _provision_sync(row.pk)["handle"] == result["handle"]
    assert len(cloud.api.collections) == 2
    assert len(cloud.api.security_policies) == 4 and len(cloud.api.access_policies) == 2


def test_recorded_legacy_collection_and_policies_survive_slug_and_prefix_changes(cloud):
    row = new_service(cloud, "legacy")
    locator = f"{cloud.kind}/old-human-collection"
    assert (
        cloud.cls(config=cloud.cfg).provision(dataclasses.replace(spec_for(row), recorded_handle=locator)).ok
    )
    row.backend_ref = locator
    row.save(update_fields=["backend_ref"])
    row.registered_app.slug = "renamed-app"
    row.registered_app.save(update_fields=["slug"])
    cloud.cfg = dataclasses.replace(cloud.cfg, collection_name_prefix="renamed-prefix")
    before = copy.deepcopy((cloud.api.collections, cloud.api.security_policies, cloud.api.access_policies))
    writes_before = mutations(cloud)
    result = _provision_sync(row.pk)
    assert result["ok"] and result["handle"] == locator
    assert before == (cloud.api.collections, cloud.api.security_policies, cloud.api.access_policies)
    assert mutations(cloud) == writes_before
    row.refresh_from_db()
    assert row.backend_ref == locator


def test_recorded_foreign_collection_is_refused_before_any_policy_change(cloud):
    first, second = new_service(cloud, "owner"), new_service(cloud, "contender")
    result = _provision_sync(first.pk)
    assert result["ok"]
    second.backend_ref, second.config = result["handle"], {"managed_service_id": str(first.guid)}
    second.save(update_fields=["backend_ref", "config"])
    before = copy.deepcopy((cloud.api.collections, cloud.api.security_policies, cloud.api.access_policies))
    writes_before = mutations(cloud)
    refused = _provision_sync(second.pk)
    assert not refused["ok"] and "refusing to adopt" in refused["message"]
    assert before == (cloud.api.collections, cloud.api.security_policies, cloud.api.access_policies)
    assert mutations(cloud) == writes_before


def test_legacy_policy_name_collision_never_rewrites_other_collections_rules(cloud):
    first, second = new_service(cloud, "first"), new_service(cloud, "second")
    name_a, name_b = "shared" + "a" * 24 + "aa", "shared" + "a" * 24 + "bb"
    assert cloud.cls._policy_name("e", name_a) == cloud.cls._policy_name("e", name_b)
    locator_a, locator_b = f"{cloud.kind}/{name_a}", f"{cloud.kind}/{name_b}"
    assert (
        cloud.cls(config=cloud.cfg)
        .provision(dataclasses.replace(spec_for(first), recorded_handle=locator_a))
        .ok
    )
    from aws.managed.opensearch_serverless import _aoss_tags

    cloud.api.create_collection(
        name=name_b, type=cloud.cfg.collection_type, tags=_aoss_tags(spec_for(second))
    )
    second.backend_ref = locator_b
    second.save(update_fields=["backend_ref"])
    before = copy.deepcopy((cloud.api.collections, cloud.api.security_policies, cloud.api.access_policies))
    writes_before = mutations(cloud)
    refused = _provision_sync(second.pk)
    assert not refused["ok"] and "different collection" in refused["message"]
    assert before == (cloud.api.collections, cloud.api.security_policies, cloud.api.access_policies)
    assert mutations(cloud) == writes_before


def test_preexisting_foreign_policy_without_collection_is_refused_before_any_create(cloud):
    row = new_service(cloud, "policy-conflict")
    name = cloud.cls(config=cloud.cfg)._collection_name(spec_for(row))
    cloud.api.security_policies[("network", cloud.cls._policy_name("n", name))] = {
        "policyVersion": "v1",
        "policy": json.dumps(
            [{"Rules": [{"ResourceType": "collection", "Resource": ["collection/foreign"]}]}]
        ),
    }
    before = copy.deepcopy(cloud.api.security_policies)
    refused = _provision_sync(row.pk)
    assert not refused["ok"] and "different collection" in refused["message"]
    assert cloud.api.security_policies == before and cloud.api.collections == {} and mutations(cloud) == []


def test_recorded_wrong_kind_never_calls_aws(cloud):
    row = new_service(cloud, "wrong-kind")
    row.backend_ref = "other_kind/recorded-collection"
    row.save(update_fields=["backend_ref"])
    with pytest.raises(ManagedServiceError, match="different driver kind"):
        _provision_sync(row.pk)
    assert cloud.api.calls == []


@pytest.mark.parametrize("name", ["parent/extra", "UPPERCASE", "a" * 33])
def test_invalid_recorded_name_is_refused_without_normalizing_or_truncating(cloud, name):
    row = new_service(cloud, "invalid-recorded")
    row.backend_ref = f"{cloud.kind}/{name}"
    row.save(update_fields=["backend_ref"])
    with pytest.raises(ManagedServiceError, match="name is invalid"):
        _provision_sync(row.pk)
    assert cloud.api.calls == []
