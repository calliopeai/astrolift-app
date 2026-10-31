"""Reachability: deployed means reachable (#2573).

An app registered while the platform's zone was missing read ``Ready`` and
``Running`` with no Ingress, certificate or hostname claim behind its host.
These pin the verdict over real rows, with the cluster's API faked at the
driver seam the way the force-redeploy tests fake it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ManagedDomain
from astrolift_lifecycle.models import AppEnvironment, Deployment, PreviewEnvironment
from astrolift_lifecycle.reachability import (
    NEEDS_REPROVISION,
    NO_MANAGED_DOMAIN,
    PROVISIONING,
    REACHABLE,
    ClusterReader,
    app_verdicts,
    environment_verdicts,
)
from astrolift_registry.models import HostnameClaim, RegisteredApp, Workload
from astrolift_workflows.activities.app_lifecycle import (
    _mark_app_ready_sync,
    _mark_failed_sync,
    _mark_running_sync,
)
from core.app_deploy import namespace_for_environment

pytestmark = pytest.mark.django_db

ZONE = "apps-2573.example"
HOST = f"hello-app.{ZONE}"


class FakeClusterApi:
    """A cluster's routing objects, and every list call made against them."""

    def __init__(self) -> None:
        self.objects: list[dict] = []
        self.lists: list[tuple[str, str | None, str]] = []
        self.error: Exception | None = None

    def list_manifests(self, cluster, namespace, kind):
        self.lists.append((cluster, namespace, kind))
        if self.error is not None:
            raise self.error
        wanted = kind.rsplit("/", 1)[-1]
        return [
            o
            for o in self.objects
            if o["kind"] == wanted and (namespace is None or o["metadata"]["namespace"] == namespace)
        ]


def ingress(
    namespace: str, host: str = HOST, *, cert_manager: bool = True, created: str = "2026-01-01T00:00:00Z"
):
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": "hello-app-web",
            "namespace": namespace,
            "creationTimestamp": created,
            "annotations": {"cert-manager.io/cluster-issuer": "letsencrypt-prod"} if cert_manager else {},
        },
        "spec": {
            "rules": [{"host": host}],
            "tls": [{"hosts": [host], "secretName": "hello-app-web-tls"}],
        },
    }


def certificate(
    namespace: str, host: str = HOST, *, ready: str = "True", issuing: str | None = None, message=""
):
    conditions = [{"type": "Ready", "status": ready, "message": message}]
    if issuing is not None:
        conditions.append({"type": "Issuing", "status": issuing})
    return {
        "apiVersion": "cert-manager.io/v1",
        "kind": "Certificate",
        "metadata": {"name": "hello-app-web-tls", "namespace": namespace},
        "spec": {"secretName": "hello-app-web-tls", "dnsNames": [host]},
        "status": {"conditions": conditions},
    }


@pytest.fixture
def api(monkeypatch):
    fake = FakeClusterApi()
    monkeypatch.setattr("astrolift_lifecycle.reachability._driver_for_cluster", lambda cluster: fake)
    monkeypatch.setattr(
        "astrolift_lifecycle.reachability._context_for_cluster",
        lambda cluster: SimpleNamespace(slug=cluster.slug),
    )
    return fake


@pytest.fixture
def web(app):
    return Workload.objects.create(
        registered_app=app, name="web", slug="web", kind="deployment", is_public=True
    )


def zone(**fields):
    return ManagedDomain.objects.create(
        zone=fields.pop("name", ZONE),
        dns_driver="route53",
        default_for=ManagedDomain.DefaultFor.TENANT_APPS,
        **fields,
    )


def serve(env, domain, workload, *, claim=True):
    """The database half a registration with a zone leaves behind."""
    env.managed_domain = domain
    env.url = f"https://{HOST}"
    env.save(update_fields=["managed_domain", "url", "updated_at", "version"])
    if claim:
        HostnameClaim.objects.create(
            managed_domain=domain,
            hostname=HOST,
            organization=env.registered_app.organization,
            registered_app=env.registered_app,
            environment=env,
            workload=workload,
        )


def deployment(env, *, status=Deployment.Status.RUNNING.value, **fields):
    return Deployment.objects.create(
        registered_app=env.registered_app,
        app_environment=env,
        trigger_kind=fields.pop("trigger_kind", Deployment.TriggerKind.MANUAL.value),
        status=status,
        image_tag="v1",
        image_digest="sha256:" + "a" * 64,
        **fields,
    )


def verdict(env, reader=None):
    return environment_verdicts([env], reader=reader)[env.pk]


def test_an_app_without_a_public_workload_is_reachable(app, env):
    Workload.objects.create(registered_app=app, name="worker", slug="worker", kind="deployment")

    assert verdict(env).status == REACHABLE
    assert app_verdicts([app])[0][app.pk].status == REACHABLE


def test_no_zone_for_the_organization_reads_no_managed_domain(app, env, web):
    deployment(env)

    found = verdict(env, ClusterReader())

    assert found.status == NO_MANAGED_DOMAIN
    assert "No managed domain resolves" in found.detail


def test_a_resolvable_zone_the_environment_is_not_bound_to_needs_reprovision(app, env, web):
    """The #2572 shape: the zone came back after registration, the environment never bound it."""
    zone()
    deployment(env)

    found = verdict(env, ClusterReader())

    assert found.status == NEEDS_REPROVISION
    assert f"not bound to the managed domain {ZONE}" in found.detail


def test_a_bound_environment_waiting_for_its_first_deploy_is_provisioning(app, env, web):
    serve(env, zone(), web)

    found = verdict(env)

    assert found.status == PROVISIONING
    assert "Waiting for the first deploy" in found.detail


def test_ingress_ready_certificate_claim_and_url_is_reachable(app, env, web, api):
    serve(env, zone(), web)
    deployment(env)
    namespace = namespace_for_environment(env)
    api.objects += [ingress(namespace), certificate(namespace)]

    found = verdict(env, ClusterReader())

    assert (found.status, found.detail) == (REACHABLE, "")


def test_a_deployed_environment_with_no_ingress_needs_reprovision(app, env, web, api):
    serve(env, zone(), web)
    deployment(env)

    found = verdict(env, ClusterReader())

    assert found.status == NEEDS_REPROVISION
    assert f"No Ingress serves {HOST}" in found.detail


def test_an_ingress_in_another_namespace_does_not_count(app, env, web, api):
    serve(env, zone(), web)
    deployment(env)
    api.objects += [ingress("someone-else"), certificate("someone-else")]

    assert verdict(env, ClusterReader()).status == NEEDS_REPROVISION


def test_a_certificate_being_issued_is_provisioning(app, env, web, api):
    serve(env, zone(), web)
    deployment(env)
    namespace = namespace_for_environment(env)
    api.objects += [ingress(namespace), certificate(namespace, ready="False", issuing="True")]

    found = verdict(env, ClusterReader())

    assert found.status == PROVISIONING
    assert "being issued" in found.detail


def test_a_failed_certificate_needs_reprovision(app, env, web, api):
    serve(env, zone(), web)
    deployment(env)
    namespace = namespace_for_environment(env)
    api.objects += [ingress(namespace), certificate(namespace, ready="False", message="rate limited")]

    found = verdict(env, ClusterReader())

    assert found.status == NEEDS_REPROVISION
    assert "is not Ready: rate limited" in found.detail


def test_a_certificate_request_past_its_grace_with_nothing_issued_needs_reprovision(app, env, web, api):
    serve(env, zone(), web)
    deployment(env)
    api.objects.append(ingress(namespace_for_environment(env)))

    found = verdict(env, ClusterReader())

    assert found.status == NEEDS_REPROVISION
    assert f"no certificate was issued for {HOST}" in found.detail


def test_a_missing_hostname_claim_needs_reprovision(app, env, web, api):
    serve(env, zone(), web, claim=False)
    deployment(env)
    namespace = namespace_for_environment(env)
    api.objects += [ingress(namespace), certificate(namespace)]

    found = verdict(env, ClusterReader())

    assert found.status == NEEDS_REPROVISION
    assert f"{HOST} has no hostname claim" in found.detail


def test_an_unreadable_cluster_gives_the_database_verdict_and_says_so(app, env, web, api):
    serve(env, zone(), web)
    deployment(env)
    api.error = ConnectionError("connection refused")

    found = verdict(env, ClusterReader())

    assert found.status == REACHABLE
    assert "Ingress and certificate not verified" in found.detail
    assert "connection refused" in found.detail


def test_a_cluster_without_a_usable_driver_gives_the_database_verdict(app, env, web):
    """The lifecycle fixture's provider plugin registers no driver at all."""
    serve(env, zone(), web)
    deployment(env)

    found = verdict(env, ClusterReader())

    assert found.status == REACHABLE
    assert "not verified" in found.detail


def test_a_deployment_in_flight_reads_provisioning_unless_last_recorded_reachable(app, env, web):
    serve(env, zone(), web)
    deployment(env)
    deployment(env, status=Deployment.Status.DEPLOYING.value)

    found = verdict(env, ClusterReader())
    assert (found.status, found.in_flight) == (PROVISIONING, True)

    AppEnvironment.objects.filter(pk=env.pk).update(reachability_status=REACHABLE)
    assert verdict(env, ClusterReader()).status == REACHABLE


def test_the_app_reads_provisioning_while_any_environment_is_deploying(app, cluster, env, web, api):
    """A caller waiting on the app must not stop on another environment's state mid-run."""
    domain = zone()
    staging = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="staging", url=""
    )
    deployment(env)
    in_flight = deployment(staging, status=Deployment.Status.DEPLOYING.value)

    assert app_verdicts([app], reader=ClusterReader())[0][app.pk].status == PROVISIONING

    in_flight.transition_to(Deployment.Status.FAILED)
    serve(staging, domain, web, claim=False)
    assert app_verdicts([app], reader=ClusterReader())[0][app.pk].status == NEEDS_REPROVISION


def test_a_preview_does_not_decide_the_app(app, cluster, env, web, api):
    serve(env, zone(), web)
    deployment(env)
    namespace = namespace_for_environment(env)
    api.objects += [ingress(namespace), certificate(namespace)]
    preview = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="preview-pr-7",
        url="",
        previewed_environment=env,
        k8s_namespace="acme-hello-app-pr-7",
    )
    PreviewEnvironment.objects.create(
        registered_app=app,
        app_environment=preview,
        pr_number=7,
        branch="feat",
        hostname=f"pr-7-hello-app.{ZONE}",
        namespace="acme-hello-app-pr-7",
    )
    deployment(preview)

    apps, envs = app_verdicts([app], reader=ClusterReader())
    assert envs[preview.pk].status != REACHABLE
    assert apps[app.pk].status == REACHABLE


def test_registration_records_the_database_verdict(app, env, web):
    RegisteredApp.objects.filter(pk=app.pk).update(provisioning_status="provisioning")

    _mark_app_ready_sync(app.pk)

    env.refresh_from_db()
    assert env.reachability_status == NO_MANAGED_DOMAIN
    assert env.reachability_checked_at is not None


def test_deploy_completion_records_the_verdict_with_the_cluster_read(app, env, web, api):
    serve(env, zone(), web)
    namespace = namespace_for_environment(env)
    api.objects += [ingress(namespace), certificate(namespace)]
    finishing = deployment(env, status=Deployment.Status.DEPLOYING.value)
    version = AppEnvironment.objects.get(pk=env.pk).version

    _mark_running_sync(finishing.pk)

    env.refresh_from_db()
    assert env.reachability_status == REACHABLE
    assert any(kind.endswith("/Ingress") for _cluster, _namespace, kind in api.lists)
    assert env.version == version, "a derived verdict must never bump the row an operator edits"


def test_deploy_completion_records_a_missing_host_rule(app, env, web, api):
    serve(env, zone(), web)
    finishing = deployment(env, status=Deployment.Status.DEPLOYING.value)

    _mark_running_sync(finishing.pk)

    env.refresh_from_db()
    assert env.reachability_status == NEEDS_REPROVISION
    assert f"No Ingress serves {HOST}" in env.reachability_detail


def test_a_failed_run_records_what_it_left_not_what_it_started_with(app, env, web, api):
    serve(env, zone(), web)
    deployment(env)
    AppEnvironment.objects.filter(pk=env.pk).update(
        reachability_status=PROVISIONING, reachability_detail="Reprovision started."
    )
    failing = deployment(
        env, status=Deployment.Status.DEPLOYING.value, trigger_kind=Deployment.TriggerKind.REPROVISION.value
    )

    _mark_failed_sync(failing.pk, "rollout timed out")

    env.refresh_from_db()
    assert env.reachability_status == NEEDS_REPROVISION
    assert f"No Ingress serves {HOST}" in env.reachability_detail


def test_an_app_registered_after_its_zone_was_deleted_reads_no_managed_domain_until_bound(
    app, cluster, web, settings
):
    """#2572, criterion 3: the platform zone is deleted, the next app registers
    with no hostname, and it reads no managed domain rather than Ready, in its
    own status and in the Apps list. Once the zone is back and the backfill
    binds it, it reads needs reprovision, which reprovisionApp clears."""
    from django.utils import timezone

    from astrolift_clusters.managed_domain_bindings import backfill_managed_domain_bindings
    from astrolift_registry.schema.mutations.helpers import _bootstrap_app_environments
    from astrolift_registry.schema.queries import _annotate_apps_list

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    platform = zone()
    ManagedDomain.objects.filter(pk=platform.pk).update(deleted_at=timezone.now())
    RegisteredApp.objects.filter(pk=app.pk).update(
        provisioning_status="provisioning", default_tenant_cluster=cluster
    )
    _bootstrap_app_environments(RegisteredApp.objects.get(pk=app.pk), ["production"])
    _mark_app_ready_sync(app.pk)

    def app_state():
        row = RegisteredApp.objects.get(pk=app.pk)
        listed = _annotate_apps_list(RegisteredApp.objects.filter(pk=app.pk)).values_list(
            "_list_state", flat=True
        )
        return app_verdicts([row])[0][app.pk].status, listed.get()

    [env] = AppEnvironment.objects.filter(registered_app=app)
    assert (env.managed_domain_id, env.url, env.reachability_status) == (None, "", NO_MANAGED_DOMAIN)
    assert app_state() == (NO_MANAGED_DOMAIN, "no_managed_domain")

    ManagedDomain.all_objects.get(pk=platform.pk).restore()
    backfill_managed_domain_bindings()

    env.refresh_from_db()
    assert (env.managed_domain_id, env.reachability_status) == (platform.pk, NEEDS_REPROVISION)
    assert app_state() == (NEEDS_REPROVISION, "needs_reprovision")
