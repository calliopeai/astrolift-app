"""Real persisted native bindings materialize into the consumer's own namespace."""

import base64
from types import SimpleNamespace

import pytest
from _sdk.cluster import ApplyResult
from k8s_native.managed.postgres_cnpg import CNPGConfig

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService, ManagedServiceBinding
from astrolift_workflows.activities.app_lifecycle import _update_secrets_sync
from astrolift_workflows.activities.managed_service_lifecycle import _sync_binding_rows
from core.cluster_observability import namespace_for_environment

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    org = Organization.objects.create(name="Native bindings", slug="native-bindings-2092")
    team = Team.objects.create(organization=org, name="Team", slug="team")
    project = Project.objects.create(organization=org, team=team, name="Project", slug="project")
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="k8s_native", defaults={"name": "Native", "plugin_version": "test"}
    )
    cluster = TenantCluster.objects.create(
        organization=org, provider_plugin=plugin, name="Native", slug="native-binding-cluster-2092"
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug="native-binding-app-2092",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="staging", k8s_namespace="consumer-staging-2092"
    )
    service = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind="postgres",
        variant="cnpg",
        name="database",
        status="active",
        backend_ref=f"postgres/{cluster.slug}/original-service-2092/main",
    )
    monkeypatch.setattr("core.cluster_observability.managed_config_for", lambda *args, **kwargs: CNPGConfig())
    return SimpleNamespace(org=org, app=app, env=env, cluster=cluster, service=service)


def test_sync_and_deploy_preserve_service_locator_credentials_and_operator_url(world, monkeypatch):
    uri = "postgresql://app:p%40ss%3Aword@main-rw.original-service-2092.svc.private.internal:5432/app?sslmode=require"

    class Store:
        def __init__(self):
            self.reads = []

        def get(self, path):
            self.reads.append(path)
            assert path == "main-app"
            return {
                "host": "main-rw",
                "uri": "postgresql://main-rw/app",
                "fqdn-uri": uri,
                "port": "5432",
                "dbname": "app",
                "user": "app",
                "password": "p@ss:word",
            }

    class Cluster:
        def __init__(self):
            self.applies = []

        def apply_manifests(self, cluster, namespace, manifests, **kwargs):
            self.applies.append((cluster, namespace, manifests))
            return ApplyResult(created=[], updated=[], unchanged=[], errors=[])

    store, driver = Store(), Cluster()
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda *args: store)
    monkeypatch.setattr(
        "core.app_deploy.driver_for_deployment",
        lambda deployment: (
            driver,
            SimpleNamespace(slug=world.cluster.slug),
            namespace_for_environment(deployment.app_environment),
        ),
    )
    monkeypatch.setattr(
        "astrolift_workflows.activities.app_lifecycle._dry_run_deploy_set", lambda *args, **kwargs: None
    )
    _sync_binding_rows(world.service)
    rows = {
        row.env_key: (row.env_value_ref, row.is_secret)
        for row in ManagedServiceBinding.objects.filter(managed_service=world.service)
    }
    assert rows["POSTGRES_HOST"] == rows["DATABASE_HOST"] == ("main-rw.original-service-2092.svc", False)
    assert rows["DATABASE_URL"] == ("main-app#fqdn-uri", True)
    assert rows["POSTGRES_PASSWORD"] == rows["DATABASE_PASSWORD"] == ("main-app#password", True)
    assert not store.reads
    deployment = Deployment.objects.create(
        registered_app=world.app,
        app_environment=world.env,
        trigger_kind="manual",
        status="pending",
        image_tag="2092",
    )
    _update_secrets_sync(deployment.pk)
    assert driver.applies
    assert all(
        (cluster, namespace) == (world.cluster.slug, world.env.k8s_namespace)
        for cluster, namespace, _ in driver.applies
    )
    secrets = [
        manifest
        for _, _, manifests in driver.applies
        for manifest in manifests
        if manifest["kind"] == "Secret" and manifest.get("data")
    ]
    data = {
        key: base64.b64decode(value).decode()
        for manifest in secrets
        for key, value in manifest["data"].items()
    }
    assert data["POSTGRES_HOST"] == data["DATABASE_HOST"] == "main-rw.original-service-2092.svc"
    assert data["DATABASE_URL"] == uri
    assert data["POSTGRES_PASSWORD"] == data["DATABASE_PASSWORD"] == "p@ss:word"
    assert set(store.reads) == {"main-app"}


def test_legacy_binding_refusal_keeps_existing_rows_intact(world):
    row = ManagedServiceBinding.objects.create(
        managed_service=world.service,
        env_key="POSTGRES_PASSWORD",
        env_value_ref="existing#password",
        is_secret=True,
    )
    world.service.backend_ref = "postgres/main"
    world.service.save(update_fields=["backend_ref"])
    with pytest.raises(ValueError, match="recorded cluster and namespace"):
        _sync_binding_rows(world.service)
    row.refresh_from_db()
    assert row.env_value_ref == "existing#password"
    assert ManagedServiceBinding.objects.filter(managed_service=world.service).count() == 1
