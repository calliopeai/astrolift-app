"""Every path that writes or reads an environment's objects follows its
namespace (#1922).

An environment with a namespace of its own renders there, so each consumer
that used to compute ``namespace_for_app`` would otherwise look in, delete
from or patch the app namespace: the other environments' objects, or
nothing at all. Each test places one environment in its own namespace next
to the app's primary environment and checks where the consumer went.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ManagedDomain, TenantCluster
from astrolift_lifecycle.models import AppEnvironment, Deployment, PreviewEnvironment
from astrolift_registry.models import Workload

pytestmark = pytest.mark.django_db

APP_NS = "acme-test-hello-app"
STAGING_NS = "acme-test-hello-app-staging"

_MANIFEST = """
name = "hello-app"

[[workloads]]
name = "web"
kind = "deployment"
is_public = true

  [[workloads.containers]]
  name = "web"
  is_primary = true
  port = 8080
"""


class _Result:
    ok = True
    errors: list = []
    created: list = []
    updated: list = []
    unchanged: list = []

    def summary(self):
        return []


class _Driver:
    def __init__(self):
        self.calls: list = []

    def ensure_namespace(self, cluster_slug, namespace, labels, annotations):
        self.calls.append(("ensure_namespace", namespace, dict(labels)))

    def apply_manifests(self, cluster_slug, namespace, manifests, dry_run=False):
        if not dry_run:
            self.calls.append(("apply", namespace, [(m["kind"], m["metadata"]["name"]) for m in manifests]))
        return _Result()

    def delete_manifests(self, cluster_slug, namespace, manifests, **_):
        self.calls.append(("delete", namespace, [(m["kind"], m["metadata"]["name"]) for m in manifests]))
        return _Result()

    def delete_namespace(self, cluster_slug, namespace, wait=False):
        self.calls.append(("delete_namespace", namespace))

    def list_storage_classes(self, cluster_slug):
        return []

    def list_workloads(self, cluster_slug, namespace):
        self.calls.append(("list_workloads", namespace))
        return [("Deployment", "web")]

    def patch_workload(self, cluster_slug, namespace, kind, name, patch):
        self.calls.append(("patch", namespace, kind, name))
        return {}

    def namespaces(self, verb):
        return [call[1] for call in self.calls if call[0] == verb]


@pytest.fixture
def driver(monkeypatch):
    fake = _Driver()
    ctx = SimpleNamespace(slug="the-cluster")
    for target in (
        "core.app_deploy",
        "core.cluster_management",
        "astrolift_workflows.activities.force_redeploy",
    ):
        monkeypatch.setattr(f"{target}._driver_for_cluster", lambda _cluster: fake)
        monkeypatch.setattr(f"{target}._context_for_cluster", lambda _cluster: ctx)
    return fake


@pytest.fixture
def staging(app, env, cluster):
    """A second environment of the app on the same cluster, placed the way
    a new one is now: in a namespace of its own."""
    from astrolift_registry.namespaces import namespace_for_new_environment

    app.manifest_raw = _MANIFEST
    app.save(update_fields=["manifest_raw"])
    namespace = namespace_for_new_environment(app, name="staging", cluster=cluster)
    assert namespace == STAGING_NS
    return AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="staging", k8s_namespace=namespace
    )


def _deployment(app, env, status=Deployment.Status.RUNNING.value):
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=status,
        image_tag="v1",
    )


def _other_cluster(org, cluster):
    return TenantCluster.objects.create(
        organization=org,
        name="second",
        slug="second-1922",
        provider_plugin=cluster.provider_plugin,
        provider_config={},
        endpoint="https://second.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


# ---- deploy path ------------------------------------------------------


def test_every_deploy_step_of_an_environment_uses_its_own_namespace(app, env, staging, driver):
    from astrolift_workflows.activities.app_lifecycle import (
        _apply_manifests_sync,
        _provision_namespace_sync,
        _update_secrets_sync,
    )
    from core.app_deploy import driver_for_deployment, render_resources_for_deployment

    d = _deployment(app, staging, Deployment.Status.PENDING.value)

    assert driver_for_deployment(d)[2] == STAGING_NS
    assert {r["metadata"]["namespace"] for r in render_resources_for_deployment(d)} == {STAGING_NS}
    assert _provision_namespace_sync(app.pk, staging.pk) == STAGING_NS
    _update_secrets_sync(d.pk)
    _apply_manifests_sync(d.pk)
    assert set(driver.namespaces("ensure_namespace")) | set(driver.namespaces("apply")) == {STAGING_NS}


def test_the_render_manifests_activity_renders_into_the_environments_namespace(app, env, staging):
    from asgiref.sync import async_to_sync
    from temporalio.testing import ActivityEnvironment

    from astrolift_workflows.activities.app_lifecycle import render_manifests

    d = _deployment(app, staging, Deployment.Status.PENDING.value)

    result = async_to_sync(ActivityEnvironment().run)(render_manifests, d.pk)

    assert {r["metadata"]["namespace"] for r in result["resources"]} == {STAGING_NS}


def test_a_preview_namespace_carries_one_label_set_from_both_provisioners(app, env, cluster, driver):
    """``ensure_namespace`` is a server-side apply under one field manager:
    two callers applying different label sets would each strip the other's.
    The preview build and every deploy of the preview now apply one set; the
    app namespace keeps exactly the labels it always had."""
    from astrolift_workflows.activities.app_lifecycle import (
        _provision_namespace_sync,
        _provision_preview_namespace_sync,
    )

    preview_env = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="preview-pr-2",
        k8s_namespace="acme-test-hello-app-pr-2",
    )
    preview = PreviewEnvironment.objects.create(
        registered_app=app,
        pr_number=2,
        branch="feature",
        hostname="pr-2.hello-app.acme-test",
        namespace="acme-test-hello-app-pr-2",
        app_environment=preview_env,
    )

    _provision_preview_namespace_sync(preview.pk)
    _provision_namespace_sync(app.pk, preview_env.pk)
    _provision_namespace_sync(app.pk, env.pk)

    build, deploy, primary = (call for call in driver.calls if call[0] == "ensure_namespace")
    assert build[1] == deploy[1] == "acme-test-hello-app-pr-2"
    assert build[2] == deploy[2]
    assert build[2]["preview-pr"] == "2"
    assert primary[1:] == (
        APP_NS,
        {
            "astrolift.io/managed-by": "astrolift",
            "astrolift.io/organization": "acme-test",
            "astrolift.io/app": "hello-app",
        },
    )


# ---- teardown ---------------------------------------------------------


def test_preview_teardown_never_deletes_the_app_namespace(app, env, cluster, driver):
    """A preview row whose namespace is the app's (data from before the
    preview namespace was ever used) would have taken production with it."""
    from astrolift_workflows.activities.app_lifecycle import _delete_preview_namespace_sync

    preview_env = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="preview-old"
    )
    preview = PreviewEnvironment.objects.create(
        registered_app=app,
        pr_number=11,
        branch="old",
        hostname="pr-11.hello-app.acme-test",
        namespace=APP_NS,
        app_environment=preview_env,
    )

    _delete_preview_namespace_sync(preview.pk)

    assert driver.namespaces("delete_namespace") == []


def test_app_teardown_deletes_each_environments_namespace(app, env, staging, driver):
    from astrolift_workflows.activities.app_teardown import _delete_app_namespaces_sync

    _delete_app_namespaces_sync(app.pk)

    assert sorted(driver.namespaces("delete_namespace")) == [APP_NS, STAGING_NS]


def test_force_redeploy_deletes_the_environments_own_objects(app, env, staging, driver):
    from astrolift_workflows.activities.force_redeploy import _delete_app_k8s_objects_sync

    summary = _delete_app_k8s_objects_sync(app.pk, staging.pk)

    assert driver.namespaces("delete") == [STAGING_NS]
    assert summary["namespaces"] == [STAGING_NS]


# ---- migration --------------------------------------------------------


def test_a_migration_refuses_a_target_where_another_environment_uses_the_namespace(org, app, env, cluster):
    """Staging in the app namespace on its own cluster, moved to production's
    cluster, would overwrite production's workloads there."""
    from astrolift_workflows.activities.migration import _validate_migration_target_sync
    from core.app_deploy import AppDeployError

    elsewhere = _other_cluster(org, cluster)
    legacy_staging = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=elsewhere, name="staging"
    )

    with pytest.raises(AppDeployError, match="already renders into namespace"):
        _validate_migration_target_sync(legacy_staging.pk, cluster.pk)


def test_a_migration_allows_an_environment_with_its_own_namespace(org, app, env, cluster):
    from astrolift_workflows.activities.migration import _validate_migration_target_sync

    elsewhere = _other_cluster(org, cluster)
    own = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=elsewhere, name="staging", k8s_namespace=STAGING_NS
    )

    _validate_migration_target_sync(own.pk, cluster.pk)


def test_a_drain_leaves_workloads_another_environment_still_renders(org, app, env, cluster, driver):
    """Two environments that predate #1922 share the app namespace on the
    source; draining the one that moved must not delete the other's."""
    from astrolift_workflows.activities.migration import _drain_source_cluster_sync

    app.manifest_raw = _MANIFEST
    app.save(update_fields=["manifest_raw"])
    target = _other_cluster(org, cluster)
    moved = AppEnvironment.objects.create(registered_app=app, tenant_cluster=target, name="staging")
    _deployment(app, moved)

    errors = _drain_source_cluster_sync(app.pk, moved.pk, cluster.pk)

    [(verb, namespace, objects)] = driver.calls
    assert (verb, namespace) == ("delete", APP_NS)
    assert objects == [("Secret", "astrolift-app-env-hello-app-staging")]
    assert any("prod" in e for e in errors)


def test_a_drain_of_an_environment_with_its_own_namespace_deletes_there(org, app, env, cluster, driver):
    from astrolift_workflows.activities.migration import _drain_source_cluster_sync

    app.manifest_raw = _MANIFEST
    app.save(update_fields=["manifest_raw"])
    target = _other_cluster(org, cluster)
    moved = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=target, name="staging", k8s_namespace=STAGING_NS
    )
    _deployment(app, moved)

    assert _drain_source_cluster_sync(app.pk, moved.pk, cluster.pk) == []

    [(verb, namespace, objects)] = driver.calls
    assert (verb, namespace) == ("delete", STAGING_NS)
    assert ("Deployment", "web") in objects


# ---- rotation, bounces, runs, live ops --------------------------------


def test_secret_bundle_rotation_bounces_and_deletes_in_the_environments_namespace(
    app, env, staging, cluster, driver
):
    from astrolift_workflows.activities.secret_rotation import (
        _bounce_workloads_sync,
        _delete_from_cluster_sync,
    )

    target = {
        "tenant_cluster_id": cluster.pk,
        "registered_app_id": app.pk,
        "app_environment_id": staging.pk,
        "bundle_slug": "payments",
    }

    _bounce_workloads_sync(target)
    _delete_from_cluster_sync(target)

    assert driver.namespaces("patch") == [STAGING_NS]
    assert driver.namespaces("delete") == [STAGING_NS]


def test_a_rebound_managed_service_bounces_the_environment_in_its_namespace(app, env, staging, driver):
    from astrolift_services.models import ManagedService, ManagedServiceBinding
    from astrolift_workflows.activities.managed_service_lifecycle import _bounce_dependent_workloads_sync

    svc = ManagedService.objects.create(
        registered_app=app, app_environment=staging, kind=ManagedService.Kind.POSTGRES, name="db"
    )
    binding = ManagedServiceBinding.objects.create(managed_service=svc, env_key="DB", env_value_ref="x")

    assert _bounce_dependent_workloads_sync(svc.pk, [binding.pk]) == 1
    assert driver.namespaces("patch") == [STAGING_NS]


def test_a_task_run_is_reconciled_in_its_environments_namespace(app, env, staging, monkeypatch):
    from astrolift_lifecycle.models import TaskRun
    from astrolift_lifecycle.services import run_reconciler

    seen = []
    monkeypatch.setattr(
        run_reconciler, "_read_job_status", lambda cluster, *, namespace, job_name: seen.append(namespace)
    )
    workload = Workload.objects.create(registered_app=app, name="migrate", slug="migrate", kind="task")
    run = TaskRun.objects.create(
        workload=workload, app_environment=staging, k8s_job_name="migrate-1", status=TaskRun.Status.RUNNING
    )

    run_reconciler._reconcile_task_run(run)

    assert seen == [STAGING_NS]


def test_live_workload_ops_follow_the_primary_environments_namespace(app, env, staging, driver):
    """No environment is named on these ops; they act on the app's primary
    (oldest live) environment, and in that environment's namespace."""
    from astrolift_lifecycle.services.k8s_ops import _resolve_driver_and_namespace

    workload = Workload.objects.create(registered_app=app, name="web", slug="web", kind="deployment")
    assert _resolve_driver_and_namespace(workload)[1] == APP_NS

    env.soft_delete()

    assert _resolve_driver_and_namespace(workload)[1] == STAGING_NS


def test_the_edge_auth_reconcile_lists_each_environments_ingresses(
    org, app, env, staging, cluster, monkeypatch
):
    from core.ingress_reconcile import reconcile_cluster_ingresses

    zone = ManagedDomain.objects.create(organization=org, zone="apps.example.net", dns_driver="route53")
    AppEnvironment.objects.filter(pk__in=[env.pk, staging.pk]).update(managed_domain=zone)
    listed = []

    class _Client:
        def list_namespaced_ingress(self, namespace, label_selector):
            listed.append(namespace)
            return SimpleNamespace(items=[])

    monkeypatch.setattr(
        "core.cluster_management._driver_for_cluster",
        lambda _cluster: SimpleNamespace(_k8s=lambda slug: _Client()),
    )

    reconcile_cluster_ingresses(cluster)

    assert sorted(listed) == [APP_NS, STAGING_NS]


# ---- hostnames --------------------------------------------------------


@pytest.fixture
def zoned(org, env, staging):
    zone = ManagedDomain.objects.create(
        organization=org, zone="apps.example.net", dns_driver="route53", dns_config={}
    )
    AppEnvironment.objects.filter(pk__in=[env.pk, staging.pk]).update(managed_domain=zone)
    env.refresh_from_db()
    staging.refresh_from_db()
    return zone


def _rendered_hosts(deployment):
    from core.app_deploy import render_resources_for_deployment

    rendered = render_resources_for_deployment(Deployment.objects.get(pk=deployment.pk))
    return {rule["host"] for r in rendered if r["kind"] == "Ingress" for rule in r["spec"]["rules"]}


def test_an_environment_in_its_own_namespace_serves_its_own_hostname(app, env, staging, zoned):
    assert _rendered_hosts(_deployment(app, env)) == {"hello-app.apps.example.net"}
    assert _rendered_hosts(_deployment(app, staging)) == {"hello-app-staging.apps.example.net"}


def test_the_workflow_renderer_agrees_on_every_hostname(app, env, staging, zoned):
    from astrolift_manifest.normalize import NormalizationDefaults, normalize
    from astrolift_manifest.parser import parse_raw
    from astrolift_workflows.activities.app_lifecycle import _render_app_ingresses_and_tls

    manifest = normalize(parse_raw(_MANIFEST), defaults=NormalizationDefaults())
    for environment, host in [
        (env, "hello-app.apps.example.net"),
        (staging, "hello-app-staging.apps.example.net"),
    ]:
        d = _deployment(app, environment)
        rendered = _render_app_ingresses_and_tls(d.pk, "ns", manifest)
        assert {rule["host"] for r in rendered for rule in r["spec"]["rules"]} == {host}


def test_a_rename_plans_each_environments_own_hostnames(app, env, staging, zoned):
    from astrolift_workflows.activities.app_domain_sync import _plan_app_domain_sync_sync

    _deployment(app, env)
    _deployment(app, staging)
    app.subdomain = "shop"
    app.save(update_fields=["subdomain"])

    plans = {p["app_environment_id"]: p for p in _plan_app_domain_sync_sync(app.pk, "")["plans"]}

    assert plans[env.pk]["old_hostnames"] == ["hello-app.apps.example.net"]
    assert plans[env.pk]["new_hostnames"] == ["shop.apps.example.net"]
    assert plans[staging.pk]["old_hostnames"] == ["hello-app-staging.apps.example.net"]
    assert plans[staging.pk]["new_hostnames"] == ["shop-staging.apps.example.net"]


def test_the_hostname_ledger_claims_what_each_environment_serves(app, env, staging, zoned):
    from astrolift_registry.hostname_claims import sync_workload_hostname_claims
    from astrolift_registry.models import HostnameClaim

    Workload.objects.create(registered_app=app, name="web", slug="web", kind="deployment", is_public=True)

    assert sync_workload_hostname_claims(app) == []

    assert set(HostnameClaim.objects.filter(registered_app=app).values_list("hostname", flat=True)) == {
        "hello-app.apps.example.net",
        "hello-app-staging.apps.example.net",
    }


def test_the_managed_domain_backfill_advertises_what_each_environment_serves(org, app, env, staging):
    """Binding a zone later recomputes each row's URL from the host it will
    render, so an environment in its own namespace does not advertise the
    primary environment's address."""
    from io import StringIO

    from django.core.management import call_command

    zone = ManagedDomain.objects.create(
        organization=org,
        zone="apps.example.net",
        dns_driver="route53",
        default_for=ManagedDomain.DefaultFor.TENANT_APPS,
    )

    call_command("backfill_managed_domain", stdout=StringIO())

    env.refresh_from_db()
    staging.refresh_from_db()
    assert (env.managed_domain, env.url) == (zone, "https://hello-app.apps.example.net")
    assert (staging.managed_domain, staging.url) == (zone, "https://hello-app-staging.apps.example.net")
