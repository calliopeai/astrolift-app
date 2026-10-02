"""Real saved identities and native Kubernetes writes against one owned Kind API.

The API reconciles Cluster custom resources; no CNPG operator/database is installed.
"""

from __future__ import annotations

import copy
import os
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from _sdk.k8s_naming import app_namespace, dns_label
from k8s_native.cluster import K8sNativeClusterDriver, K8sNativeConfig, _build_k8s_client
from k8s_native.managed.postgres_cnpg import CNPGConfig, CNPGPostgresDriver
from kubernetes import client, config

from astrolift_drivers.registry import PluginManifest, PluginRegistry
from astrolift_workflows.activities.managed_service_lifecycle import _provision_sync, build_provision_spec
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service

pytestmark = pytest.mark.django_db
CRD = "clusters.postgresql.cnpg.io"
RESOURCE = "postgresql.cnpg.io/v1/Cluster"


@pytest.fixture(scope="module")
def native():
    path = os.environ.get("ASTROLIFT_NAMING_TEST_KUBECONFIG")
    if not path:
        pytest.skip("requires an explicitly selected task-owned Kind cluster")
    context = "kind-astrolift-env-target-2217-a"
    configuration = client.Configuration()
    config.load_kube_config(config_file=path, context=context, client_configuration=configuration)
    assert configuration.host.startswith("https://127.0.0.1:"), "refusing a non-local Kubernetes API"
    helper = _build_k8s_client(kubeconfig_path=path, context=context, in_cluster=False)
    crd_kind = "apiextensions.k8s.io/v1/CustomResourceDefinition"
    created = []
    try:
        for plural, kind in [("clusters", "Cluster"), ("backups", "Backup")]:
            name = f"{plural}.postgresql.cnpg.io"
            if helper.get(kind=crd_kind, namespace=None, name=name) is None:
                helper.create_manifest(
                    namespace=None,
                    dry_run=False,
                    manifest={
                        "apiVersion": "apiextensions.k8s.io/v1",
                        "kind": "CustomResourceDefinition",
                        "metadata": {"name": name, "labels": {"astrolift-test": "physical-naming-2032"}},
                        "spec": {
                            "group": "postgresql.cnpg.io",
                            "scope": "Namespaced",
                            "names": {"plural": plural, "singular": plural.removesuffix("s"), "kind": kind},
                            "versions": [
                                {
                                    "name": "v1",
                                    "served": True,
                                    "storage": True,
                                    "schema": {
                                        "openAPIV3Schema": {
                                            "type": "object",
                                            "properties": {
                                                "spec": {
                                                    "type": "object",
                                                    "x-kubernetes-preserve-unknown-fields": True,
                                                },
                                                "status": {
                                                    "type": "object",
                                                    "x-kubernetes-preserve-unknown-fields": True,
                                                },
                                            },
                                        }
                                    },
                                }
                            ],
                        },
                    },
                )
                created.append(name)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                current = helper.get(kind=crd_kind, namespace=None, name=name)
                if any(
                    row.get("type") == "Established" and row.get("status") == "True"
                    for row in (current.get("status") or {}).get("conditions") or []
                ):
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("task-owned CNPG test CRD did not establish")
        yield SimpleNamespace(helper=helper, config=K8sNativeConfig(kubeconfig_path=path, context=context))
    finally:
        for name in reversed(created):
            current = helper.get(kind=crd_kind, namespace=None, name=name)
            assert current["metadata"]["labels"].get("astrolift-test") == "physical-naming-2032"
            helper.delete(
                kind=crd_kind,
                namespace=None,
                name=name,
                uid=current["metadata"]["uid"],
                resource_version=current["metadata"]["resourceVersion"],
            )
        helper._api_client.close()


@pytest.fixture
def world(native, monkeypatch):
    cluster_driver = K8sNativeClusterDriver(config=native.config)
    registry = PluginRegistry()
    registry.register(
        PluginManifest(
            plugin_id="k8s_native",
            display_name="Native fixture",
            version="fixture",
            drivers={"managed:postgres:cnpg": CNPGPostgresDriver, "cluster": K8sNativeClusterDriver},
        )
    )
    monkeypatch.setattr("astrolift_drivers.registry.plugins", registry)
    monkeypatch.setattr("core.cluster_management.plugins", registry)
    monkeypatch.setattr(
        "core.cluster_observability.managed_config_for",
        lambda *_, **__: CNPGConfig(cluster_driver=cluster_driver),
    )
    namespaces = set()
    state = SimpleNamespace(
        native=native, cluster_driver=cluster_driver, namespaces=namespaces, nonce=uuid4().hex[:8]
    )
    try:
        yield state
    finally:
        for namespace in namespaces:
            native.helper.delete(kind="Namespace", namespace=None, name=namespace)
        for helper in cluster_driver._k8s_cache.values():
            helper._api_client.close()


def service(world, org, app="api"):
    row = _service(
        org_slug=f"n2032-{world.nonce}-{org}", plugin_slug="k8s_native", variant="cnpg", backend_ref=""
    )
    cluster = row.app_environment.tenant_cluster
    cluster.provider_config = {
        "kubeconfig_path": world.native.config.kubeconfig_path,
        "context": world.native.config.context,
    }
    cluster.auth_method = "kubeconfig"
    cluster.auth_config = {
        "kubeconfig": Path(world.native.config.kubeconfig_path).read_text(),
        "context": world.native.config.context,
    }
    cluster.save(update_fields=["provider_config", "auth_method", "auth_config"])
    row.kind, row.name = "postgres", "primary"
    row.registered_app.slug = app
    row.registered_app.save(update_fields=["slug"])
    row.save(update_fields=["kind", "name"])
    namespace = app_namespace(organization_slug=row.registered_app.organization.slug, app_slug=app)
    if namespace not in world.namespaces:
        world.native.helper.create_manifest(
            namespace=None,
            dry_run=False,
            manifest={
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": namespace},
            },
        )
        world.namespaces.add(namespace)
    return row, namespace


def resource(world, namespace, name):
    return world.native.helper.get(kind=RESOURCE, namespace=namespace, name=name)


def save_handle(row, handle):
    row.backend_ref = handle
    row.save(update_fields=["backend_ref"])


def seed(world, row, namespace, name, *, marked=True):
    spec = build_provision_spec(row, cluster=row.app_environment.tenant_cluster)
    manifest = CNPGPostgresDriver()._render_cluster(spec=spec, cluster_name=name)
    if not marked:
        for key in list(manifest["metadata"]["labels"]):
            if key.startswith("ai.astrolift/"):
                manifest["metadata"]["labels"].pop(key)
    manifest["metadata"]["annotations"] = {"preserved": "provider-owned-data"}
    manifest["spec"]["managed"] = {"roles": [{"name": "existing-preview-owner", "ensure": "present"}]}
    world.native.helper.create_manifest(namespace=namespace, manifest=manifest, dry_run=False)
    handle = f"postgres/{row.app_environment.tenant_cluster.guid}/{namespace}/{name}"
    save_handle(row, handle)
    return handle


def test_two_org_join_collisions_create_two_native_resources_in_the_same_namespace(world):
    first, ns = service(world, "alpha-beta", "beta")
    second, other_ns = service(world, "alpha", "beta-beta")
    first.app_environment.name = "beta-prod"
    first.app_environment.save(update_fields=["name"])
    assert ns == other_ns
    assert dns_label(first.registered_app.slug, first.app_environment.name, first.name) == dns_label(
        second.registered_app.slug, second.app_environment.name, second.name
    )
    results = [_provision_sync(row.pk) for row in [first, second]]
    assert all(result["ok"] for result in results), results
    names = [result["handle"].split("/")[-1] for result in results]
    assert len(set(names)) == 2
    for row, result, name in zip([first, second], results, names, strict=True):
        observed = resource(world, ns, name)
        assert observed["metadata"]["labels"]["ai.astrolift/managed-service-id"] == str(row.guid)
        assert name.endswith(row.guid.hex)
        uid = observed["metadata"]["uid"]
        assert _provision_sync(row.pk)["handle"] == result["handle"]
        assert resource(world, ns, name)["metadata"]["uid"] == uid


@pytest.mark.parametrize("marked", [True, False])
def test_recorded_legacy_locator_retains_uid_namespace_and_operator_owned_fields(world, marked):
    row, ns = service(world, "legacy")
    handle = seed(world, row, ns, "old-human-postgres", marked=marked)
    previous = resource(world, ns, "old-human-postgres")
    if marked:
        row.registered_app.slug = "renamed-app"
        row.registered_app.save(update_fields=["slug"])
    result = _provision_sync(row.pk)
    assert result["ok"] and result["handle"] == handle, result
    current = resource(world, ns, "old-human-postgres")
    assert current["metadata"]["uid"] == previous["metadata"]["uid"]
    assert current["metadata"]["annotations"]["preserved"] == "provider-owned-data"
    assert current["spec"]["managed"] == previous["spec"]["managed"]
    assert current["metadata"]["labels"]["ai.astrolift/managed-service-id"] == str(row.guid)


def test_foreign_recorded_resource_is_refused_before_any_native_write(world):
    owner, ns = service(world, "owner")
    handle = seed(world, owner, ns, "primary")
    contender, _ = service(world, "contender")
    contender.app_environment.tenant_cluster = owner.app_environment.tenant_cluster
    contender.app_environment.save(update_fields=["tenant_cluster"])
    save_handle(contender, handle)
    contender.config = {"managed_service_id": str(owner.guid), "recorded_handle_exclusive": True}
    contender.save(update_fields=["config"])
    before = resource(world, ns, "primary")
    refused = _provision_sync(contender.pk)
    assert not refused["ok"]
    assert resource(world, ns, "primary") == before


def test_alternate_cluster_registration_does_not_grant_unmarked_legacy_ownership(world):
    owner, ns = service(world, "legacy-alias")
    seed(world, owner, ns, "primary", marked=False)
    duplicate, _ = service(world, "legacy-contender")
    save_handle(duplicate, f"postgres/{duplicate.app_environment.tenant_cluster.guid}/{ns}/primary")
    before = resource(world, ns, "primary")
    refused = _provision_sync(owner.pk)
    assert not refused["ok"]
    assert resource(world, ns, "primary") == before


@pytest.mark.parametrize(
    "handle",
    ["postgres/old-human-postgres", "mysql/cluster/ns/postgres", "postgres/foreign-cluster/ns/postgres"],
)
def test_incomplete_or_retargeted_recorded_locator_never_guesses_a_destination(world, handle):
    row, ns = service(world, "invalid")
    save_handle(row, handle)
    with pytest.raises(ValueError, match="recorded CNPG"):
        _provision_sync(row.pk)
    assert world.native.helper.list(kind=RESOURCE, namespace=ns) == []


@pytest.mark.parametrize("race", ["create", "replace", "update"])
def test_native_create_and_reconcile_races_never_overwrite_a_changed_incarnation(world, monkeypatch, race):
    row, ns = service(world, "racing")
    name = "pg-" + row.guid.hex
    if race != "create":
        seed(world, row, ns, name)
    original_apply = world.cluster_driver.apply_manifests
    replacement = None

    def race_apply(cluster, namespace, manifests, **kwargs):
        nonlocal replacement
        desired = copy.deepcopy(manifests[0])
        if race == "replace":
            current = resource(world, ns, name)
            world.native.helper.delete(kind=RESOURCE, namespace=ns, name=name, uid=current["metadata"]["uid"])
        if race == "update":
            current = resource(world, ns, name)
            current["metadata"].setdefault("annotations", {})["concurrent-write"] = "must-survive"
            current["metadata"].pop("managedFields", None)
            current.pop("status", None)
            world.native.helper.server_side_apply(namespace=ns, manifest=current, dry_run=False)
        else:
            desired["metadata"] = {"name": name, "labels": {"foreign": "true"}}
            desired["spec"] = {"instances": 9}
            world.native.helper.create_manifest(namespace=ns, manifest=desired, dry_run=False)
        replacement = resource(world, ns, name)
        return original_apply(cluster, namespace, manifests, **kwargs)

    monkeypatch.setattr(world.cluster_driver, "apply_manifests", race_apply)
    result = _provision_sync(row.pk)
    assert not result["ok"]
    assert resource(world, ns, name) == replacement
