"""Actual preview activity persists a slice; later deploy resolves its own credential envelope."""

import base64
from copy import deepcopy
from types import SimpleNamespace

import pytest
from _sdk.cluster import ApplyResult
from asgiref.sync import async_to_sync
from k8s_native.managed.postgres_cnpg import CNPGConfig
from temporalio.testing import ActivityEnvironment

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment, PreviewEnvironment
from astrolift_lifecycle.services.preview_service_provisioning import provision_preview_managed_services
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService, ManagedServiceAttachment, ManagedServiceBinding
from astrolift_workflows.activities.app_lifecycle import (
    _update_secrets_sync,
    cleanup_preview_managed_services_activity,
    provision_preview_managed_services_activity,
)
from astrolift_workflows.activities.managed_service_lifecycle import _sync_binding_rows
from core.app_deploy import AppDeployError

pytestmark = pytest.mark.django_db(transaction=True)


class Store:
    def __init__(self):
        self.values, self.reads, self.writes = {}, [], []

    def get(self, path):
        self.reads.append(path)
        return deepcopy(self.values.get(path))

    def upsert(self, path, payload):
        self.writes.append(path)
        self.values[path] = deepcopy(payload)


class Kubernetes:
    def __init__(self, cluster, parent):
        self.cluster, self.parent = cluster, parent
        self.objects, self.reads, self.applies, self.deletes = {}, [], [], []
        self.consumer_deletes = []
        self.fail_database = False

    def get_manifest(self, cluster, namespace, kind, name):
        assert cluster == str(self.cluster.guid)
        assert namespace == "primary-service-2092"
        self.reads.append((cluster, namespace, kind, name))
        if kind.endswith("/Cluster"):
            return deepcopy(self.parent)
        return deepcopy(self.objects.get((kind.rsplit("/", 1)[-1], name)))

    def apply_manifests(self, cluster, namespace, manifests, **kwargs):
        self.applies.append((cluster, namespace, deepcopy(manifests), kwargs))
        if namespace == "primary-service-2092":
            assert cluster == str(self.cluster.guid)
            for manifest in manifests:
                if manifest["kind"] == "Database" and self.fail_database:
                    self.fail_database = False
                    return SimpleNamespace(errors=["controlled database create failure"])
                obj = deepcopy(manifest)
                obj["metadata"].setdefault("uid", "recorded-owned-uid")
                obj["metadata"].setdefault("resourceVersion", "2")
                if obj["kind"] == "Cluster":
                    assert obj["metadata"]["uid"] == "actual-parent-uid"
                    assert obj["metadata"]["resourceVersion"] == "1"
                    self.parent = obj
                else:
                    key = (obj["kind"], obj["metadata"]["name"])
                    assert not kwargs.get("create_only") or key not in self.objects
                    self.objects[key] = obj
        return ApplyResult(created=[], updated=[], unchanged=[], errors=[])

    def delete_manifests(self, cluster, namespace, manifests):
        if namespace != "primary-service-2092":
            assert cluster == self.cluster.slug
            assert namespace in ("preview-consumer-2092", "current-consumer-2092", "second-consumer-2092")
            assert all(
                obj["kind"] == "Secret" and obj["metadata"]["namespace"] == namespace for obj in manifests
            )
            self.consumer_deletes.extend(deepcopy(manifests))
            return SimpleNamespace(errors=[])
        assert cluster == str(self.cluster.guid) and namespace == "primary-service-2092"
        for manifest in manifests:
            assert manifest["metadata"]["uid"] == "recorded-owned-uid"
            assert manifest["metadata"]["resourceVersion"] == "2"
            self.deletes.append(deepcopy(manifest))
            self.objects.pop((manifest["kind"], manifest["metadata"]["name"]))
        return SimpleNamespace(errors=[])


@pytest.fixture
def world(monkeypatch):
    org = Organization.objects.create(name="Slice", slug="slice-org-2092")
    team = Team.objects.create(organization=org, name="Team", slug="team")
    project = Project.objects.create(organization=org, team=team, name="Project", slug="project")
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="k8s_native", defaults={"name": "Native", "plugin_version": "test"}
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        provider_plugin=plugin,
        name="Native",
        slug="slice-cluster-2092",
        lifecycle="managed",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug="slice-app-2092",
        provisioning_status="ready",
    )
    primary = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="production", k8s_namespace="primary-app-2092"
    )
    preview = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        previewed_environment=primary,
        name="preview-pr-2092",
        k8s_namespace="preview-consumer-2092",
    )
    service = ManagedService.objects.create(
        registered_app=app,
        app_environment=primary,
        kind="postgres",
        variant="cnpg",
        name="database",
        status="active",
        backend_ref=f"postgres/{cluster.guid}/primary-service-2092/main",
    )
    parent = {
        "apiVersion": "postgresql.cnpg.io/v1",
        "kind": "Cluster",
        "metadata": {
            "name": "main",
            "namespace": "primary-service-2092",
            "uid": "actual-parent-uid",
            "resourceVersion": "1",
            "labels": {
                "astrolift.io/managed-by": "platform",
                "astrolift.io/organization": org.slug,
                "astrolift.io/app": app.slug,
            },
        },
        "spec": {
            "instances": 2,
            "storage": {"size": "1Gi"},
            "bootstrap": {"initdb": {"owner": "app", "database": "app"}},
        },
    }
    k8s, store = Kubernetes(cluster, parent), Store()
    configs, capability_calls, deploy_calls = [], [], []

    def config_for(plugin, target, **kwargs):
        configs.append((plugin, target.pk, kwargs))
        assert target.pk == cluster.pk
        return CNPGConfig(cluster_driver=k8s)

    def capability(target, capability):
        capability_calls.append((target.pk, capability))
        assert target.pk == cluster.pk and capability == "secrets"
        return store

    def deployment_driver(deployment):
        deploy_calls.append(deployment.pk)
        return k8s, SimpleNamespace(slug=cluster.slug), deployment.app_environment.k8s_namespace

    monkeypatch.setattr("core.cluster_observability.managed_config_for", config_for)
    monkeypatch.setattr("core.app_deploy.driver_for_capability", capability)
    monkeypatch.setattr("core.app_deploy.driver_for_deployment", deployment_driver)
    monkeypatch.setattr(
        "astrolift_workflows.activities.app_lifecycle._dry_run_deploy_set", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "k8s_native.managed._cnpg_slices.secrets.token_urlsafe", lambda size: "slice-only-p@ss:word"
    )
    _sync_binding_rows(service)
    ManagedServiceBinding.objects.create(
        managed_service=service, env_key="UNRELATED_LITERAL", env_value_ref="kept", is_secret=False
    )
    deployment = Deployment.objects.create(
        registered_app=app, app_environment=preview, trigger_kind="manual", status="pending", image_tag="2092"
    )
    configs.clear()
    return SimpleNamespace(
        org=org,
        team=team,
        project=project,
        app=app,
        cluster=cluster,
        primary=primary,
        preview=preview,
        service=service,
        k8s=k8s,
        store=store,
        configs=configs,
        capability_calls=capability_calls,
        deploy_calls=deploy_calls,
        deployment=deployment,
    )


def provision(world):
    outcome = provision_preview_managed_services(world.preview)
    assert outcome.errors == {}
    assert outcome.sliced == ["database"]
    return ManagedServiceAttachment.objects.get(managed_service=world.service, app_environment=world.preview)


def test_actual_temporal_activity_then_fresh_deploy_materializes_only_slice_aliases(world):
    row = PreviewEnvironment.objects.create(
        registered_app=world.app,
        app_environment=world.preview,
        branch="feature",
        pr_number=2092,
        hostname="preview.invalid",
        namespace=world.preview.k8s_namespace,
    )
    result = async_to_sync(ActivityEnvironment().run)(provision_preview_managed_services_activity, row.pk)
    assert result == {
        "attached": ["database"],
        "sliced": ["database"],
        "shared_unsliced": [],
        "skipped": [],
        "errors": {},
    }
    attachment = ManagedServiceAttachment.objects.get(
        managed_service=world.service, app_environment=world.preview
    )
    assert attachment.slice_handle.startswith(f"postgres_slice/{world.cluster.guid}/primary-service-2092/")
    # No in-memory activity override is passed to this fresh deployment call.
    _update_secrets_sync(world.deployment.pk)
    manifests = [
        obj for _, ns, objs, _ in world.k8s.applies if ns == world.preview.k8s_namespace for obj in objs
    ]
    (secret,) = (
        obj
        for obj in manifests
        if obj["kind"] == "Secret" and obj["metadata"]["name"] == "astrolift-bindings-slice-app-2092"
    )
    data = {key: base64.b64decode(value).decode() for key, value in secret["data"].items()}
    path = f"services/{world.org.guid}/{world.app.guid}/cnpg-slices/{world.service.guid}/{world.preview.guid}"
    envelope = world.store.values[path]
    assert data == {
        "POSTGRES_HOST": "main-rw.primary-service-2092.svc",
        "DATABASE_HOST": "main-rw.primary-service-2092.svc",
        "POSTGRES_PORT": "5432",
        "DATABASE_PORT": "5432",
        "POSTGRES_DB": envelope["database"],
        "DATABASE_NAME": envelope["database"],
        "POSTGRES_USER": envelope["username"],
        "DATABASE_USER": envelope["username"],
        "POSTGRES_PASSWORD": "slice-only-p@ss:word",
        "DATABASE_PASSWORD": "slice-only-p@ss:word",
        "DATABASE_URL": envelope["uri"],
        "POSTGRES_SSL_MODE": "require",
        "UNRELATED_LITERAL": "kept",
    }
    assert "slice-only-p%40ss%3Aword@main-rw.primary-service-2092.svc" in data["DATABASE_URL"]
    assert "main-app" not in world.store.reads
    assert set(world.store.reads) == {path}
    assert all(
        ns in ("primary-service-2092", world.preview.k8s_namespace) for _, ns, _, _ in world.k8s.applies
    )
    before = deepcopy(world.store.values)
    provision(world)
    assert world.store.values == before
    assert world.store.writes == [path]
    assert (
        len(
            [
                obj
                for _, ns, objs, _ in world.k8s.applies
                if ns == "primary-service-2092"
                for obj in objs
                if obj["kind"] == "Secret"
            ]
        )
        == 1
    )


def test_partial_create_failure_has_no_attachment_and_retry_keeps_credentials(world):
    world.k8s.fail_database = True
    outcome = provision_preview_managed_services(world.preview)
    assert outcome.errors and not outcome.attached
    assert not ManagedServiceAttachment.objects.filter(app_environment=world.preview).exists()
    before = deepcopy(world.store.values)
    provision(world)
    assert world.store.values == before
    assert len(world.store.writes) == 1


@pytest.mark.parametrize(
    "target", ["org", "team", "project", "app", "primary", "preview", "cluster", "service"]
)
def test_retired_current_ancestry_refuses_before_any_provider_or_store_call(world, target):
    getattr(world, target).soft_delete()
    outcome = provision_preview_managed_services(world.preview)
    assert not outcome.attached
    # A retired service is excluded before iteration; the others yield refusal.
    if target != "service":
        assert outcome.errors
    assert not world.configs and not world.capability_calls
    assert not world.k8s.reads and not world.k8s.applies
    assert not world.store.reads and not world.store.writes


@pytest.mark.parametrize("change", ["app", "source", "cluster", "handle", "legacy_handle", "decommissioned"])
def test_reassignment_refuses_before_side_effects(world, change):
    if change == "app":
        foreign = Organization.objects.create(name="Foreign", slug="foreign")
        RegisteredApp.objects.filter(pk=world.app.pk).update(organization=foreign)
    elif change == "source":
        AppEnvironment.objects.filter(pk=world.preview.pk).update(previewed_environment=None)
    elif change == "cluster":
        other = TenantCluster.objects.create(
            organization=world.org,
            provider_plugin=world.cluster.provider_plugin,
            name="Other",
            slug="other-cluster-2092",
            lifecycle="managed",
        )
        AppEnvironment.objects.filter(pk=world.preview.pk).update(tenant_cluster=other)
    elif change == "handle":
        ManagedService.objects.filter(pk=world.service.pk).update(
            backend_ref="postgres/foreign/elsewhere/main"
        )
    elif change == "legacy_handle":
        ManagedService.objects.filter(pk=world.service.pk).update(backend_ref="postgres/main")
    else:
        TenantCluster.objects.filter(pk=world.cluster.pk).update(lifecycle="decommissioned")
    outcome = provision_preview_managed_services(world.preview)
    assert not outcome.attached and outcome.errors
    assert not world.configs and not world.capability_calls
    assert not world.k8s.reads and not world.store.reads


@pytest.mark.parametrize("change", ["service", "consumer", "slice", "blank", "target"])
def test_deploy_rechecks_persisted_slice_before_any_store_or_kubernetes_call(world, change):
    attachment = provision(world)
    if change == "service":
        ManagedService.objects.filter(pk=world.service.pk).update(app_environment=world.preview)
    elif change == "consumer":
        AppEnvironment.objects.filter(pk=world.preview.pk).update(previewed_environment=None)
    elif change == "slice":
        ManagedServiceAttachment.objects.filter(pk=attachment.pk).update(
            slice_handle="postgres_slice/foreign/elsewhere/other"
        )
    elif change == "blank":
        ManagedServiceAttachment.objects.filter(pk=attachment.pk).update(slice_handle="")
    world.k8s.reads.clear()
    world.k8s.applies.clear()
    world.store.reads.clear()
    world.capability_calls.clear()
    with pytest.raises((ValueError, AppDeployError)):
        _update_secrets_sync(
            world.deployment.pk, target_cluster_id=world.cluster.pk + 123 if change == "target" else None
        )
    assert not world.k8s.reads and not world.k8s.applies
    assert not world.store.reads and not world.capability_calls and not world.deploy_calls


@pytest.mark.parametrize("change", ["foreign_app", "retired", "wrong_old_slice"])
def test_actual_activity_refuses_current_preview_or_attachment_mismatch(world, change):
    if change == "wrong_old_slice":
        ManagedServiceAttachment.objects.create(
            managed_service=world.service,
            app_environment=world.preview,
            slice_handle=f"postgres_slice/{world.cluster.guid}/primary-service-2092/legacy_slug_slice",
        )
    app = world.app
    if change == "foreign_app":
        app = RegisteredApp.objects.create(
            organization=world.org, team=world.team, project=world.project, name="Other", slug="other-app"
        )
    row = PreviewEnvironment.objects.create(
        registered_app=app,
        app_environment=world.preview,
        branch="feature",
        pr_number=2092,
        hostname="preview.invalid",
        namespace=world.preview.k8s_namespace,
        status="torn_down" if change == "retired" else "building",
    )
    result = async_to_sync(ActivityEnvironment().run)(provision_preview_managed_services_activity, row.pk)
    assert result["errors"] and not result["attached"]
    assert not world.configs and not world.capability_calls
    assert not world.k8s.reads and not world.k8s.applies
    assert not world.store.reads and not world.store.writes


def test_deploy_uses_current_namespace_when_target_changes_before_admission_lock(world, monkeypatch):
    provision(world)
    from astrolift_lifecycle.services import preview_slice_bindings as module

    original = module.binding_for_preview
    changed = []

    def change_before_lock(service, preview):
        if not changed:
            changed.append(True)
            AppEnvironment.objects.filter(pk=preview.pk).update(k8s_namespace="current-consumer-2092")
        return original(service, preview)

    monkeypatch.setattr(module, "binding_for_preview", change_before_lock)
    _update_secrets_sync(world.deployment.pk)
    consumer = [obj for _, ns, objs, _ in world.k8s.applies if ns != "primary-service-2092" for obj in objs]
    assert consumer
    assert {obj["metadata"]["namespace"] for obj in consumer} == {"current-consumer-2092"}


def test_current_policy_change_before_parent_lock_refuses_without_provider_calls(world, monkeypatch):
    from astrolift_lifecycle.services import preview_slice_bindings as module

    original = module.live_slice_source

    def change_before_lock(service, preview):
        ManagedService.objects.filter(pk=service.pk).update(config={"preview_policy": "dedicated"})
        return original(service, preview)

    monkeypatch.setattr(module, "live_slice_source", change_before_lock)
    outcome = provision_preview_managed_services(world.preview)
    assert outcome.errors and not outcome.attached
    assert not world.configs and not world.capability_calls
    assert not world.k8s.reads and not world.k8s.applies
    assert not world.store.reads and not world.store.writes


def test_real_database_locks_serialize_two_previews_on_the_same_parent(world, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.db import close_old_connections, connection

    second = AppEnvironment.objects.create(
        registered_app=world.app,
        tenant_cluster=world.cluster,
        previewed_environment=world.primary,
        name="second-preview",
        k8s_namespace="second-consumer-2092",
    )
    parent_entered, release_parent, second_lock_attempted, second_parent_read = (Event() for _ in range(4))
    get_manifest = world.k8s.get_manifest
    parent_reads = []

    def paused_parent(cluster, namespace, kind, name):
        if kind.endswith("/Cluster"):
            parent_reads.append(True)
            if len(parent_reads) == 1:
                parent_entered.set()
                assert release_parent.wait(10)
            else:
                second_parent_read.set()
        return get_manifest(cluster, namespace, kind, name)

    monkeypatch.setattr(world.k8s, "get_manifest", paused_parent)

    def run(env, is_second=False):
        close_old_connections()

        def record_lock(execute, sql, params, many, context):
            if is_second and "FOR UPDATE" in sql:
                second_lock_attempted.set()
            return execute(sql, params, many, context)

        try:
            with connection.execute_wrapper(record_lock):
                return provision_preview_managed_services(env)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, world.preview)
        assert parent_entered.wait(10)
        other = pool.submit(run, second, True)
        try:
            assert second_lock_attempted.wait(10)
            assert not second_parent_read.wait(0.2)
        finally:
            release_parent.set()
        assert first.result(timeout=20).errors == {}
        assert other.result(timeout=20).errors == {}
    roles = world.k8s.parent["spec"]["managed"]["roles"]
    assert len(roles) == 2 and len({role["name"] for role in roles}) == 2
    assert ManagedServiceAttachment.objects.filter(managed_service=world.service).count() == 2
    assert len(world.store.writes) == 2 and len(set(world.store.writes)) == 2


@pytest.mark.parametrize("foreign", [False, True])
def test_actual_cleanup_activity_uses_owned_persisted_handle_and_source_config(world, foreign):
    attachment = provision(world)
    app = world.app
    if foreign:
        app = RegisteredApp.objects.create(
            organization=world.org, team=world.team, project=world.project, name="Other", slug="other-app"
        )
    row = PreviewEnvironment.objects.create(
        registered_app=app,
        app_environment=world.preview,
        branch="feature",
        pr_number=2092,
        hostname="preview.invalid",
        namespace=world.preview.k8s_namespace,
    )
    credentials = deepcopy(world.store.values)
    world.configs.clear()
    world.capability_calls.clear()
    world.k8s.reads.clear()
    world.store.reads.clear()
    result = async_to_sync(ActivityEnvironment().run)(cleanup_preview_managed_services_activity, row.pk)
    attachment.refresh_from_db()
    if foreign:
        assert result["errors"] and not result["dropped"]
        assert attachment.slice_handle
        assert not world.configs and not world.capability_calls
        assert not world.k8s.reads and not world.k8s.deletes
    else:
        assert result == {"dropped": ["database"], "leaked": [], "no_slice": [], "errors": {}}
        assert attachment.slice_handle == ""
        assert [obj["kind"] for obj in world.k8s.deletes] == ["Database", "Secret"]
        assert world.configs
        assert world.k8s.parent["spec"]["instances"] == 2
    assert not world.store.reads and not world.capability_calls
    assert world.store.values == credentials


def test_a_shared_non_sliceable_native_driver_stays_unattached_without_config_loading(world):
    ManagedService.objects.filter(pk=world.service.pk).update(
        kind="redis", variant="operator", backend_ref=f"redis/{world.cluster.guid}/primary-service-2092/main"
    )
    outcome = provision_preview_managed_services(world.preview)
    assert outcome.shared_unsliced == ["database"] and not outcome.attached
    assert outcome.errors == {}
    assert not world.configs and not world.capability_calls
    assert not world.k8s.reads and not world.k8s.applies
    assert not world.store.reads and not world.store.writes


def test_unsliced_project_attachment_keeps_its_explicit_preview_binding(world):
    shared = ManagedService.objects.create(
        project=world.project,
        tenant_cluster=world.cluster,
        kind="cache",
        variant="memcached",
        name="shared-cache",
        status="active",
        backend_ref=f"cache/{world.cluster.guid}/project-cache-2092/shared",
    )
    ManagedServiceAttachment.objects.create(managed_service=shared, app_environment=world.preview)
    ManagedServiceBinding.objects.create(
        managed_service=shared,
        env_key="PROJECT_CACHE_HOST",
        env_value_ref="shared.project-cache-2092.svc",
        is_secret=False,
    )
    _update_secrets_sync(world.deployment.pk)
    manifests = [
        obj for _, ns, objs, _ in world.k8s.applies if ns == world.preview.k8s_namespace for obj in objs
    ]
    (secret,) = (obj for obj in manifests if obj["kind"] == "Secret")
    assert {key: base64.b64decode(value).decode() for key, value in secret["data"].items()} == {
        "PROJECT_CACHE_HOST": "shared.project-cache-2092.svc"
    }
    assert not world.store.reads and not world.store.writes
