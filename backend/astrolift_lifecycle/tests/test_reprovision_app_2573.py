"""reprovisionApp: put the platform half back without a rebuild (#2573).

The workflow tests run the real ``ReprovisionAppWorkflow`` and its real
activities on the time-skipping Temporal server, against real Postgres. Only
the cluster is faked, at the driver seam the force-redeploy tests use: it
server-side applies with generation tracking, and issues a Ready certificate
for an Ingress that asks cert-manager for one, as cert-manager would.
"""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.core.management import call_command
from temporalio import activity

from astrolift_clusters.models import ManagedDomain, ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_lifecycle.schema.mutations import (
    DeploymentByIdInput,
    ForceRedeployInput,
    LifecycleMutation,
    ReprovisionAppInput,
    StartDeploymentInput,
)
from astrolift_lifecycle.schema.types import ReachabilityStatus
from astrolift_manifest.normalize import NormalizationDefaults, normalize
from astrolift_manifest.parser import parse_raw
from astrolift_manifest.persist import persist_manifest
from astrolift_registry.models import HostnameClaim, RegisteredApp
from astrolift_registry.schema.types import app_to_type
from astrolift_workflows import client as workflow_client
from astrolift_workflows.activities import (
    apply_manifests,
    ensure_workload_identity,
    health_check,
    mark_deploying,
    mark_failed,
    mark_running,
    poll_rollout,
    pre_flight,
    provision_namespace,
    restore_previous_secrets,
    update_secrets,
)
from astrolift_workflows.activities.app_lifecycle import (
    _create_rollback_deployment_sync,
    _mark_app_ready_sync,
)
from astrolift_workflows.activities.faas import ensure_faas_services
from astrolift_workflows.activities.static_site import (
    ensure_cloudfront_cert,
    ensure_static_dns,
    ensure_static_site_services,
)
from astrolift_workflows.client import WorkflowHandle, WorkflowOutcome
from astrolift_workflows.workflows.reprovision_app import ReprovisionAppWorkflow
from core.app_deploy import namespace_for_environment, render_resources_for_deployment
from core.mutations import AuditEntry, register_audit_writer
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.testing.temporal import temporal_worker

ZONE = "apps-2573.example"
HOST = f"hello-app.{ZONE}"
DIGEST = "sha256:" + "4" * 64

MANIFEST = """
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

REPROVISION_ACTIVITIES = [
    pre_flight,
    mark_deploying,
    provision_namespace,
    ensure_cloudfront_cert,
    ensure_static_site_services,
    ensure_faas_services,
    ensure_workload_identity,
    update_secrets,
    apply_manifests,
    ensure_static_dns,
    poll_rollout,
    health_check,
    mark_running,
    mark_failed,
    restore_previous_secrets,
]


class FakeCluster:
    """The cluster driver: server-side apply with generations, and cert-manager."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str, str], dict] = {}
        self.namespaces: set[str] = set()

    @staticmethod
    def _desired(manifest: dict, namespace: str) -> dict:
        body = copy.deepcopy(manifest)
        meta = body.setdefault("metadata", {})
        meta["namespace"] = meta.get("namespace") or namespace
        for server_field in ("generation", "creationTimestamp"):
            meta.pop(server_field, None)
        body.pop("status", None)
        return body

    def apply_manifests(self, cluster, namespace, manifests, *, dry_run=False, create_only=False):
        from _sdk.cluster import ApplyResult

        created, updated, unchanged = [], [], []
        for manifest in manifests:
            desired = self._desired(manifest, namespace)
            key = (desired["kind"], desired["metadata"]["namespace"], desired["metadata"]["name"])
            ref = f"{key[0]}/{key[2]}"
            current = self.objects.get(key)
            if current is None:
                created.append(ref)
            elif self._desired(current, namespace) != desired:
                updated.append(ref)
            else:
                unchanged.append(ref)
                continue
            if dry_run:
                continue
            stored = copy.deepcopy(desired)
            stored["metadata"]["generation"] = 1 if current is None else current["metadata"]["generation"] + 1
            stored["metadata"]["creationTimestamp"] = (
                current["metadata"]["creationTimestamp"] if current else "2026-10-30T12:00:00Z"
            )
            self.objects[key] = stored
            if key[0] == "Ingress":
                self._issue_certificates(stored)
        return ApplyResult(created=created, updated=updated, unchanged=unchanged, errors=[])

    def _issue_certificates(self, ingress: dict) -> None:
        if not (ingress["metadata"].get("annotations") or {}).get("cert-manager.io/cluster-issuer"):
            return
        namespace = ingress["metadata"]["namespace"]
        for tls in ingress["spec"].get("tls") or []:
            secret = tls.get("secretName")
            if secret and ("Certificate", namespace, secret) not in self.objects:
                self.objects[("Certificate", namespace, secret)] = {
                    "apiVersion": "cert-manager.io/v1",
                    "kind": "Certificate",
                    "metadata": {"name": secret, "namespace": namespace, "generation": 1},
                    "spec": {"secretName": secret, "dnsNames": list(tls.get("hosts") or [])},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                }

    def delete_manifests(self, cluster, namespace, manifests, **_):
        return SimpleNamespace(errors=[], ok=True)

    def ensure_namespace(self, cluster, name, labels=None, annotations=None):
        self.namespaces.add(name)

    def list_manifests(self, cluster, namespace, kind):
        wanted = kind.rsplit("/", 1)[-1]
        return [
            copy.deepcopy(obj)
            for (k, ns, _name), obj in self.objects.items()
            if k == wanted and (namespace is None or ns == namespace)
        ]

    def list_storage_classes(self, cluster):
        return []

    def poll_rollout(self, cluster, namespace, kind, name, timeout_seconds=600):
        from _sdk.cluster import RolloutResult

        return RolloutResult(
            success=True, kind=kind, name=name, namespace=namespace, message="rolled out", timed_out=False
        )

    def get_workload_status(self, cluster, namespace, kind, name):
        from _sdk.cluster import WorkloadStatus

        return WorkloadStatus(
            kind=kind, name=name, namespace=namespace, ready_replicas=1, desired_replicas=1, conditions=[]
        )

    def generations(self) -> dict[tuple[str, str, str], int]:
        return {key: obj["metadata"]["generation"] for key, obj in self.objects.items()}


@pytest.fixture
def cluster_api(monkeypatch):
    fake = FakeCluster()
    for module in ("core.cluster_management", "core.app_deploy", "astrolift_lifecycle.reachability"):
        monkeypatch.setattr(f"{module}._driver_for_cluster", lambda cluster: fake)
        monkeypatch.setattr(
            f"{module}._context_for_cluster", lambda cluster: SimpleNamespace(slug=cluster.slug)
        )
    return fake


@pytest.fixture(autouse=True)
def _no_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


def _world(suffix: str, *, zone: bool, slug: str = "hello-app") -> SimpleNamespace:
    """An app registered with one public workload and deployed once, on a
    cluster whose objects are exactly what that deploy applied."""
    org = Organization.objects.create(name=f"Acme {suffix}", slug=f"acme-{suffix}")
    team = Team.objects.create(organization=org, name="Platform", slug=f"platform-{suffix}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-{suffix}")
    [plugin] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="Test",
                slug=f"test-{suffix}",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        name=f"cluster-{suffix}",
        slug=f"cluster-{suffix}",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    domain = None
    if zone:
        domain = ManagedDomain.objects.create(
            zone=ZONE, dns_driver="route53", default_for=ManagedDomain.DefaultFor.TENANT_APPS
        )
    app = RegisteredApp.objects.create(
        organization=org, team=team, project=project, name="Hello", slug=slug, provisioning_status="ready"
    )
    persist_manifest(app, normalize(parse_raw(MANIFEST), defaults=NormalizationDefaults()), raw_text=MANIFEST)
    env = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="production",
        managed_domain=domain,
        url=f"https://{HOST}" if zone else "",
    )
    from astrolift_registry.hostname_claims import sync_workload_hostname_claims

    sync_workload_hostname_claims(app)
    running = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.RUNNING.value,
        image_tag="v1",
        image_digest=DIGEST,
        commit_sha="a" * 40,
        config_snapshot={"manifest_hash": app.manifest_hash, "image_tag": "v1"},
    )
    user = get_user_model().objects.create(username=f"operator-{suffix}", email=f"operator-{suffix}@test")
    return SimpleNamespace(org=org, app=app, env=env, cluster=cluster, running=running, user=user)


def _deployed(world, cluster_api) -> None:
    """Apply what the running deployment renders, as its deploy did."""
    running = Deployment.objects.select_related(
        "registered_app__organization", "app_environment__tenant_cluster", "app_environment__managed_domain"
    ).get(pk=world.running.pk)
    cluster_api.apply_manifests(
        world.cluster.slug, namespace_for_environment(world.env), render_resources_for_deployment(running)
    )


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user), user=user))


def _reprovision(world, environment_name=None):
    with tenant_context(TenantContext(organization_id=world.org.id, actor_user_id=world.user.id)):
        return LifecycleMutation().reprovision_app(
            _info(world.user),
            input=ReprovisionAppInput(app_slug=world.app.slug, environment_name=environment_name),
        )


def _app_status(world):
    with tenant_context(TenantContext(organization_id=world.org.id, actor_user_id=world.user.id)):
        app = RegisteredApp.objects.select_related("organization", "team", "project").get(pk=world.app.pk)
        return app_to_type(app, info=_info(world.user)).reachability_status()


def _use_temporal(monkeypatch, temporal_env) -> None:
    async def get_client():
        return temporal_env.client

    monkeypatch.setattr(workflow_client, "_get_client_async", get_client)
    monkeypatch.setattr(workflow_client, "_temporal_enabled", lambda: True)
    monkeypatch.setattr(workflow_client, "_task_queue", lambda: "astrolift-test")


@pytest.mark.django_db(transaction=True)
async def test_a_registration_without_a_zone_reaches_reachable_after_reprovision(
    temporal_env, monkeypatch, permission_resolver, cluster_api
):
    """The #2572 incident: registered and deployed while the platform zone was
    missing. Once the zone is back and the environment bound, reprovision
    creates the Ingress, its certificate request and the hostname claim on the
    image already running, and the app reads reachable."""
    world = await sync_to_async(_world)("nozone", zone=False)
    await sync_to_async(_deployed)(world, cluster_api)
    await sync_to_async(_mark_app_ready_sync)(world.app.pk)
    assert await sync_to_async(_app_status)(world) is ReachabilityStatus.NO_MANAGED_DOMAIN
    await sync_to_async(AppEnvironment.objects.get)(pk=world.env.pk, reachability_status="no_managed_domain")

    await sync_to_async(ManagedDomain.objects.create)(
        zone=ZONE, dns_driver="route53", default_for=ManagedDomain.DefaultFor.TENANT_APPS
    )
    await sync_to_async(call_command)("backfill_managed_domain")
    assert await sync_to_async(_app_status)(world) is ReachabilityStatus.NEEDS_REPROVISION

    permission_resolver.grant(Permission.APP_DEPLOY)
    _use_temporal(monkeypatch, temporal_env)
    monkeypatch.setattr("astrolift_lifecycle.reprovision.WAIT_SECONDS", 120)
    async with temporal_worker(
        temporal_env, workflows=[ReprovisionAppWorkflow], activities=REPROVISION_ACTIVITIES
    ):
        result = await sync_to_async(_reprovision, thread_sensitive=False)(world)

    assert result.ok, result.errors
    [outcome] = result.data.environments
    assert outcome.reachability_status is ReachabilityStatus.REACHABLE
    assert "Ingress/hello-app-web" in outcome.objects_created
    assert f"HostnameClaim/{HOST}" in outcome.objects_created
    assert outcome.unchanged is False

    namespace = await sync_to_async(namespace_for_environment)(world.env)
    ingress = cluster_api.objects[("Ingress", namespace, "hello-app-web")]
    assert ingress["spec"]["rules"][0]["host"] == HOST
    assert ingress["metadata"]["annotations"]["cert-manager.io/cluster-issuer"] == "letsencrypt-prod"
    assert ingress["spec"]["tls"] == [{"hosts": [HOST], "secretName": "hello-app-web-tls"}]
    assert ("Certificate", namespace, "hello-app-web-tls") in cluster_api.objects
    assert await sync_to_async(
        HostnameClaim.objects.filter(environment=world.env, hostname=HOST, deleted_at__isnull=True).exists
    )()

    row = await sync_to_async(Deployment.objects.get)(guid=outcome.deployment_id)
    assert (row.trigger_kind, row.status) == ("reprovision", "running")
    assert (row.image_tag, row.image_digest) == ("v1", DIGEST)
    previous = await sync_to_async(Deployment.objects.get)(pk=world.running.pk)
    assert previous.status == Deployment.Status.SUPERSEDED.value
    workload = cluster_api.objects[("Deployment", namespace, "web")]
    assert workload["metadata"]["generation"] == 1, "no new image and no pod restart"
    assert DIGEST in workload["spec"]["template"]["spec"]["containers"][0]["image"]
    assert await sync_to_async(_app_status)(world) is ReachabilityStatus.REACHABLE


@pytest.mark.django_db(transaction=True)
async def test_reprovision_on_a_healthy_app_changes_no_object_generation(
    temporal_env, monkeypatch, permission_resolver, cluster_api
):
    world = await sync_to_async(_world)("healthy", zone=True)
    await sync_to_async(_deployed)(world, cluster_api)
    assert await sync_to_async(_app_status)(world) is ReachabilityStatus.REACHABLE
    before = cluster_api.generations()

    permission_resolver.grant(Permission.APP_DEPLOY)
    _use_temporal(monkeypatch, temporal_env)
    monkeypatch.setattr("astrolift_lifecycle.reprovision.WAIT_SECONDS", 120)
    async with temporal_worker(
        temporal_env, workflows=[ReprovisionAppWorkflow], activities=REPROVISION_ACTIVITIES
    ):
        result = await sync_to_async(_reprovision, thread_sensitive=False)(world, "production")

    assert result.ok, result.errors
    [outcome] = result.data.environments
    assert (outcome.objects_created, outcome.objects_updated, outcome.unchanged) == ([], [], True)
    assert outcome.reachability_status is ReachabilityStatus.REACHABLE
    assert cluster_api.generations() == before
    row = await sync_to_async(Deployment.objects.get)(guid=outcome.deployment_id)
    assert (row.trigger_kind, row.status, row.image_digest) == ("reprovision", "running", DIGEST)


@pytest.mark.django_db(transaction=True)
async def test_the_call_returns_before_the_run_and_the_app_reads_provisioning_until_it_ends(
    temporal_env, monkeypatch, permission_resolver, cluster_api
):
    """The CLI's --wait polls reachabilityStatus and stops on NEEDS_REPROVISION,
    so the app must read PROVISIONING for as long as the run goes on; and a
    second call meanwhile joins the run instead of starting another."""
    world = await sync_to_async(_world)("held", zone=True)
    await sync_to_async(_deployed)(world, cluster_api)
    namespace = await sync_to_async(namespace_for_environment)(world.env)
    del cluster_api.objects[("Ingress", namespace, "hello-app-web")]
    assert await sync_to_async(_app_status)(world) is ReachabilityStatus.NEEDS_REPROVISION
    released = asyncio.Event()

    @activity.defn(name="astrolift.deploy.health_check")
    async def held_health_check(deployment_id: int) -> bool:
        while not released.is_set():
            activity.heartbeat()
            await asyncio.sleep(0.05)
        return True

    permission_resolver.grant(Permission.APP_DEPLOY)
    _use_temporal(monkeypatch, temporal_env)
    monkeypatch.setattr("astrolift_lifecycle.reprovision.WAIT_SECONDS", 0.5)
    activities = [a for a in REPROVISION_ACTIVITIES if a is not health_check] + [held_health_check]
    async with temporal_worker(temporal_env, workflows=[ReprovisionAppWorkflow], activities=activities):
        first = await sync_to_async(_reprovision, thread_sensitive=False)(world)
        [started] = first.data.environments
        assert first.ok, first.errors
        assert started.deployment_id is not None
        assert started.reachability_status is ReachabilityStatus.PROVISIONING
        assert started.unchanged is False
        assert await sync_to_async(_app_status)(world) is ReachabilityStatus.PROVISIONING

        again = await sync_to_async(_reprovision, thread_sensitive=False)(world)
        assert again.ok, again.errors
        assert again.data.environments[0].deployment_id == started.deployment_id
        assert (
            await sync_to_async(
                Deployment.objects.filter(app_environment=world.env, trigger_kind="reprovision").count
            )()
            == 1
        )

        released.set()
        handle = temporal_env.client.get_workflow_handle(
            f"DeployAppWorkflow-{world.app.guid}-{world.env.guid}"
        )
        assert (await handle.result())["ok"] is True

    assert await sync_to_async(_app_status)(world) is ReachabilityStatus.REACHABLE


# ---- the mutation's contract, without a Temporal server ---------------


@pytest.fixture
def no_server(monkeypatch, settings):
    """Starts record instead of running; the run reads as still going."""
    starts: list[tuple[str, list, str]] = []
    settings.ASTROLIFT_TEMPORAL_ENABLED = True

    def start(name, args, *, workflow_id, task_queue=None):
        starts.append((name, list(args), workflow_id))
        return WorkflowHandle(workflow_id=workflow_id, run_id=f"run-{len(starts)}", enqueued=True)

    monkeypatch.setattr("astrolift_lifecycle.reprovision.start_workflow_exclusive", start)
    monkeypatch.setattr(
        "astrolift_lifecycle.reprovision.wait_for_workflows",
        lambda handles, timeout_seconds: [WorkflowOutcome("running") for _ in handles],
    )
    monkeypatch.setattr("astrolift_workflows.client._temporal_enabled", lambda: True)
    return starts


@pytest.fixture
def audit_log():
    from core.mutations import _audit_writer

    entries: list[AuditEntry] = []
    previous = _audit_writer
    register_audit_writer(entries.append)
    yield entries
    register_audit_writer(previous)


@pytest.mark.django_db
def test_app_deploy_alone_is_enough_and_starts_one_run_per_running_environment(
    permission_resolver, no_server, cluster_api
):
    world = _world("deployonly", zone=True)
    permission_resolver.grant(Permission.APP_DEPLOY)

    result = _reprovision(world)

    assert result.ok, result.errors
    [(name, [input], workflow_id)] = no_server
    assert name == "ReprovisionAppWorkflow"
    assert workflow_id == f"DeployAppWorkflow-{world.app.guid}-{world.env.guid}"
    row = Deployment.objects.get(pk=input.deployment_id)
    assert (row.trigger_kind, row.status, row.promoted_from_id) == (
        "reprovision",
        "pending",
        world.running.pk,
    )
    assert (row.image_tag, row.image_digest, row.commit_sha) == ("v1", DIGEST, "a" * 40)
    assert row.workflow_run is not None and row.workflow_run.workflow_kind == "ReprovisionAppWorkflow"
    [outcome] = result.data.environments
    assert outcome.deployment_id == str(row.guid)
    assert outcome.reachability_status is ReachabilityStatus.PROVISIONING


@pytest.mark.django_db
def test_without_app_deploy_the_call_is_refused_and_writes_nothing(permission_resolver, no_server):
    world = _world("denied", zone=True)
    permission_resolver.grant(Permission.APP_UPDATE)

    result = _reprovision(world)

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert no_server == []
    assert not Deployment.objects.filter(trigger_kind="reprovision").exists()


@pytest.mark.django_db
def test_another_organizations_app_reads_as_missing(permission_resolver, no_server):
    theirs = _world("theirs", zone=True)
    mine = _world("mine", zone=False, slug="mine-app")
    permission_resolver.grant(Permission.APP_DEPLOY)

    with tenant_context(TenantContext(organization_id=mine.org.id, actor_user_id=mine.user.id)):
        missing = LifecycleMutation().reprovision_app(
            _info(mine.user), input=ReprovisionAppInput(app_slug="no-such-app")
        )
        foreign = LifecycleMutation().reprovision_app(
            _info(mine.user), input=ReprovisionAppInput(app_slug=theirs.app.slug)
        )

    assert (missing.errors[0].code, missing.errors[0].message) == ("NOT_FOUND", "app 'no-such-app' not found")
    assert (foreign.errors[0].code, foreign.errors[0].message) == ("NOT_FOUND", "app 'hello-app' not found")
    assert no_server == []
    assert not Deployment.objects.filter(trigger_kind="reprovision").exists()


@pytest.mark.django_db
def test_an_unknown_environment_is_not_found(permission_resolver, no_server):
    world = _world("noenv", zone=True)
    permission_resolver.grant(Permission.APP_DEPLOY)

    result = _reprovision(world, "staging")

    assert (result.errors[0].code, result.errors[0].field) == ("NOT_FOUND", "environmentName")


@pytest.mark.django_db
def test_paused_deploys_refuse_before_anything_is_written(permission_resolver, no_server):
    world = _world("paused", zone=False)
    ManagedDomain.objects.create(
        zone=ZONE, dns_driver="route53", default_for=ManagedDomain.DefaultFor.TENANT_APPS
    )
    AppEnvironment.objects.filter(pk=world.env.pk).update(deploys_paused=True)
    permission_resolver.grant(Permission.APP_DEPLOY)

    result = _reprovision(world)

    assert (result.errors[0].code, result.errors[0].field) == ("PRECONDITION", "environmentName")
    assert no_server == []
    assert AppEnvironment.objects.get(pk=world.env.pk).managed_domain_id is None


@pytest.mark.django_db
def test_a_deploy_in_flight_refuses_and_a_reprovision_in_flight_is_joined(permission_resolver, no_server):
    world = _world("inflight", zone=True)
    permission_resolver.grant(Permission.APP_DEPLOY)
    deploying = Deployment.objects.create(
        registered_app=world.app,
        app_environment=world.env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.DEPLOYING.value,
        image_tag="v2",
    )

    refused = _reprovision(world)
    assert (refused.errors[0].code, refused.errors[0].field) == ("PRECONDITION", "environmentName")

    deploying.transition_to(Deployment.Status.FAILED)
    started = _reprovision(world)
    joined = _reprovision(world)

    assert len(no_server) == 1
    assert joined.ok, joined.errors
    assert joined.data.environments[0].deployment_id == started.data.environments[0].deployment_id


@pytest.mark.django_db
def test_an_approval_gated_environment_refuses_a_manifest_its_approvers_never_saw(
    permission_resolver, no_server
):
    world = _world("gated", zone=True)
    AppEnvironment.objects.filter(pk=world.env.pk).update(required_approvals=1)
    permission_resolver.grant(Permission.APP_DEPLOY)
    assert _reprovision(world).ok

    Deployment.objects.get(trigger_kind="reprovision").transition_to(Deployment.Status.FAILED)
    RegisteredApp.objects.filter(pk=world.app.pk).update(manifest_hash="sha256:changed-since")

    result = _reprovision(world)
    assert (result.errors[0].code, result.errors[0].field) == ("PRECONDITION", "environmentName")
    assert "requires deploy approval" in result.errors[0].message


@pytest.mark.django_db
def test_a_native_endpoint_environment_is_refused(permission_resolver, no_server, monkeypatch):
    world = _world("native", zone=True)
    permission_resolver.grant(Permission.APP_DEPLOY)
    monkeypatch.setattr(
        "astrolift_lifecycle.deployment_identity_origin.native_origin_required",
        lambda app, env=None, **_: True,
    )

    result = _reprovision(world)

    assert result.errors[0].code == "PRECONDITION"
    assert no_server == []


@pytest.mark.django_db
def test_with_the_workflow_engine_off_a_running_environment_is_refused(permission_resolver, settings):
    world = _world("engineoff", zone=True)
    permission_resolver.grant(Permission.APP_DEPLOY)
    settings.ASTROLIFT_TEMPORAL_ENABLED = False

    result = _reprovision(world)

    assert result.errors[0].code == "PRECONDITION"
    assert not Deployment.objects.filter(trigger_kind="reprovision").exists()


@pytest.mark.django_db
def test_a_never_deployed_environment_is_bound_and_claimed_without_a_run(permission_resolver, no_server):
    world = _world("fresh", zone=False)
    world.running.transition_to(Deployment.Status.FAILED)
    ManagedDomain.objects.create(
        zone=ZONE, dns_driver="route53", default_for=ManagedDomain.DefaultFor.TENANT_APPS
    )
    permission_resolver.grant(Permission.APP_DEPLOY)

    result = _reprovision(world)

    assert result.ok, result.errors
    [outcome] = result.data.environments
    assert outcome.deployment_id is None
    assert outcome.objects_updated == ["AppEnvironment/production"]
    assert outcome.objects_created == [f"HostnameClaim/{HOST}"]
    assert outcome.reachability_status is ReachabilityStatus.PROVISIONING
    env = AppEnvironment.objects.get(pk=world.env.pk)
    assert (env.managed_domain.zone, env.url) == (ZONE, f"https://{HOST}")
    assert no_server == []


@pytest.mark.django_db
def test_a_run_that_cannot_start_fails_its_row_and_says_why(permission_resolver, no_server, monkeypatch):
    from astrolift_workflows.client import WorkflowAlreadyRunning

    world = _world("taken", zone=True)
    permission_resolver.grant(Permission.APP_DEPLOY)

    def taken(name, args, *, workflow_id, task_queue=None):
        raise WorkflowAlreadyRunning(workflow_id)

    monkeypatch.setattr("astrolift_lifecycle.reprovision.start_workflow_exclusive", taken)

    result = _reprovision(world)

    assert not result.ok
    assert result.errors[0].message == "environment 'production': a deployment started first"
    [outcome] = result.data.environments
    row = Deployment.objects.get(guid=outcome.deployment_id)
    assert (row.status, row.aborted_reason) == ("failed", "a deployment started first")


@pytest.mark.django_db
def test_reprovision_and_force_redeploy_are_distinct_audit_actions(
    permission_resolver, no_server, audit_log, monkeypatch
):
    world = _world("audit", zone=True)
    permission_resolver.grant(Permission.APP_DEPLOY)
    permission_resolver.grant(Permission.APP_UPDATE)
    for step, value in (
        ("_cancel_in_flight_deploys_sync", 0),
        ("_delete_app_k8s_objects_sync", {"deleted": 0}),
        ("_redispatch_ci_workflow_sync", {"dispatched": False, "run_url": "", "message": "not dispatched"}),
    ):
        monkeypatch.setattr(
            f"astrolift_workflows.activities.force_redeploy.{step}", lambda *a, _v=value, **k: _v
        )

    assert _reprovision(world).ok
    with tenant_context(TenantContext(organization_id=world.org.id, actor_user_id=world.user.id)):
        forced = LifecycleMutation().force_astrolift_redeploy(
            _info(world.user), input=ForceRedeployInput(app_slug=world.app.slug, confirm_slug=world.app.slug)
        )
    assert forced.ok, forced.errors

    actions = [entry.action for entry in audit_log]
    assert actions.count("app.reprovision") == 1
    assert actions.count("app.force_redeploy") == 1
    [entry] = [e for e in audit_log if e.action == "app.reprovision"]
    assert (entry.target_kind, entry.target_id) == ("RegisteredApp", world.app.slug)
    assert entry.extra["environments"][0]["environment"] == "production"


@pytest.mark.django_db
def test_start_deployment_refuses_the_reprovision_trigger(permission_resolver, no_server):
    world = _world("trigger", zone=True)
    permission_resolver.grant(Permission.APP_DEPLOY)

    with tenant_context(TenantContext(organization_id=world.org.id, actor_user_id=world.user.id)):
        result = LifecycleMutation().start_deployment(
            _info(world.user),
            input=StartDeploymentInput(
                app_slug=world.app.slug,
                environment_name="production",
                image_tag="v2",
                trigger_kind="reprovision",
            ),
        )

    assert (result.errors[0].code, result.errors[0].field) == ("VALIDATION", "triggerKind")


@pytest.mark.django_db
def test_rolling_back_from_a_reprovision_goes_to_the_release_before_the_one_it_repeated(permission_resolver):
    """A reprovision repeats the release it was pinned to. Rolling back from it
    to that release would change nothing, so the target is the one before."""
    world = _world("rollback", zone=True)
    permission_resolver.grant(Permission.APP_ROLLBACK)
    older = Deployment.objects.create(
        registered_app=world.app,
        app_environment=world.env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.SUPERSEDED.value,
        image_tag="v0",
    )
    Deployment.objects.filter(pk=older.pk).update(created_at=world.running.created_at.replace(year=2025))
    reprovision = Deployment.objects.create(
        registered_app=world.app,
        app_environment=world.env,
        trigger_kind=Deployment.TriggerKind.REPROVISION.value,
        status=Deployment.Status.RUNNING.value,
        image_tag="v1",
        image_digest=DIGEST,
        promoted_from=world.running,
    )
    world.running.transition_to(Deployment.Status.SUPERSEDED)

    with tenant_context(TenantContext(organization_id=world.org.id, actor_user_id=world.user.id)):
        result = LifecycleMutation().rollback_deployment(
            _info(world.user), input=DeploymentByIdInput(id=str(reprovision.guid))
        )

    assert result.ok, result.errors
    target = Deployment.objects.get(guid=result.data.id)
    assert (target.image_tag, target.promoted_from_id) == ("v0", older.pk)
    # The rollback workflow's own first step must keep that target too.
    applied = Deployment.objects.get(pk=_create_rollback_deployment_sync(target.pk))
    assert applied.image_tag == "v0"


@pytest.mark.django_db
def test_the_mcp_tool_reprovisions_through_the_same_resolver(permission_resolver, no_server):
    from astrolift_agents.tests.test_mcp_gateway import _call, _token
    from astrolift_identity.api_tokens import SCOPE_MCP_WRITE, SCOPE_WRITE_APPS

    world = _world("mcp", zone=True)
    permission_resolver.grant(Permission.APP_DEPLOY)
    user, token = _token(world.org, scopes=[SCOPE_MCP_WRITE])
    call = {"name": "astrolift_reprovision_app", "arguments": {"slug": world.app.slug}}

    _, refused = _call(world.org, user, token, "tools/call", call)
    assert refused["result"]["structuredContent"] == {
        "code": "permission_denied",
        "message": "API token is missing scope 'write:apps'",
    }

    token.scopes = [SCOPE_MCP_WRITE, SCOPE_WRITE_APPS]
    token.save(update_fields=["scopes", "updated_at", "version"])
    _, done = _call(world.org, user, token, "tools/call", call)

    assert done["result"]["isError"] is False
    [environment] = done["result"]["structuredContent"]["environments"]
    row = Deployment.objects.get(trigger_kind="reprovision")
    assert environment == {
        "environment_name": "production",
        "deployment_id": str(row.guid),
        "reachability_status": "PROVISIONING",
        "objects_created": [],
        "objects_updated": [],
        "unchanged": False,
    }
