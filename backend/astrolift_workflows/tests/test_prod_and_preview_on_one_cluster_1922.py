"""Prod and a preview of the same app, on one cluster, share no object (#1922).

Before #1922 every environment rendered into ``namespace_for_app`` with the
manifest's workload names, and the PR webhook binds a preview to the app's
default cluster. So deploying a preview wrote the production Deployment,
Service, HPA, StatefulSet (and the PVCs its claim template names), Ingress,
bindings Secret and ServiceAccount: the next request to the app hit the PR's
code. And the preview's own namespace, the one ``BuildPreviewWorkflow``
provisions and teardown deletes, stayed empty.

These deploy both environments the way ``DeployAppWorkflow`` does, activity
by activity against one recording cluster, and check what landed where.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ManagedDomain, ProviderPlugin
from astrolift_lifecycle.models import Deployment
from astrolift_services.models import (
    ManagedService,
    ManagedServiceAttachment,
    ManagedServiceBinding,
)

pytestmark = pytest.mark.django_db

_MANIFEST = """
name = "hello-app"

[env]
LOG_LEVEL = "info"

[[workloads]]
name = "web"
kind = "deployment"
is_public = true
hpa_min = 1
hpa_max = 3

  [[workloads.containers]]
  name = "web"
  is_primary = true
  port = 8080

[[workloads]]
name = "db"
kind = "statefulset"
storage_size = "5Gi"
storage_class = "gp3"

  [[workloads.containers]]
  name = "db"
  is_primary = true
  port = 5432
"""


class _Result:
    def __init__(self, names=()):
        self.ok = True
        self.errors: list = []
        self.created = list(names)
        self.updated: list = []
        self.unchanged: list = []

    def summary(self):
        return []


class _Cluster:
    """One cluster. Records which environment wrote each object, keyed the
    way the apiserver keys it: kind, namespace, name."""

    def __init__(self):
        self.deploying = ""
        self.objects: dict[tuple[str, str, str], list[str]] = {}
        self.namespaces: dict[str, dict] = {}
        self.deleted_namespaces: list[str] = []
        self.deleted_objects: list[tuple[str, str, str]] = []

    def ensure_namespace(self, cluster_slug, namespace, labels, annotations):
        self.namespaces[namespace] = dict(labels)

    def apply_manifests(self, cluster_slug, namespace, manifests, dry_run=False):
        if dry_run:
            return _Result()
        for m in manifests:
            meta = m.get("metadata") or {}
            key = (m["kind"], meta.get("namespace") or namespace, meta["name"])
            self.objects.setdefault(key, []).append(self.deploying)
        return _Result(m["metadata"]["name"] for m in manifests)

    def delete_manifests(self, cluster_slug, namespace, manifests, **_):
        for m in manifests:
            self.deleted_objects.append((m["kind"], namespace, m["metadata"]["name"]))
        return _Result()

    def delete_namespace(self, cluster_slug, namespace, wait=False):
        self.deleted_namespaces.append(namespace)

    def list_storage_classes(self, cluster_slug):
        return []

    def poll_rollout(self, cluster_slug, namespace, kind, name, timeout):
        return SimpleNamespace(success=(kind, namespace, name) in self.objects, message="", timed_out=False)

    def get_workload_status(self, cluster_slug, namespace, kind, name):
        return SimpleNamespace(ready_replicas=1, desired_replicas=1)

    def written_by(self, env_name):
        return {key for key, owners in self.objects.items() if env_name in owners}


class _Iam:
    """The IRSA driver's trust behaviour: creating an existing role resets
    its trust to the subject-less base, and each binding adds one subject."""

    def __init__(self):
        self.trust: set[tuple[str, str]] = set()

    def create_identity_role(self, name, permissions):
        self.trust = set()
        return f"arn:aws:iam::111122223333:role/{name}"

    def bind_service_account(self, cluster, namespace, sa_name, identity_role):
        self.trust.add((namespace, sa_name))
        return {"eks.amazonaws.com/role-arn": f"arn:aws:iam::111122223333:role/{identity_role}"}


class _Secrets:
    def get(self, ref):
        return None


@pytest.fixture
def one_cluster(org, app, env, cluster, monkeypatch):
    aws, _ = ProviderPlugin.objects.get_or_create(
        slug="aws",
        defaults={"name": "AWS", "plugin_version": "0.0.1", "capabilities_manifest": {}, "config_schema": {}},
    )
    cluster.provider_plugin = aws
    cluster.provider_config = {"account_id": "111122223333", "region": "us-east-1"}
    cluster.auth_config = {"cluster_oidc_issuer": "oidc.eks.us-east-1.amazonaws.com/id/X"}
    cluster.save()
    zone = ManagedDomain.objects.create(
        organization=org,
        zone="apps.example.net",
        dns_driver="route53",
        dns_config={},
        default_for=ManagedDomain.DefaultFor.BOTH,
    )
    app.default_tenant_cluster = cluster
    app.manifest_raw = _MANIFEST
    app.save(update_fields=["default_tenant_cluster", "manifest_raw"])
    env.managed_domain = zone
    env.save()
    database = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="main-db",
        status=ManagedService.Status.ACTIVE,
    )
    ManagedServiceBinding.objects.create(
        managed_service=database,
        env_key="DATABASE_HOST",
        env_value_ref="main-db.internal",
        is_secret=False,
    )

    fake = _Cluster()
    iam = _Iam()
    ctx = SimpleNamespace(slug="shared-cluster")
    for target in ("core.app_deploy", "core.cluster_management"):
        monkeypatch.setattr(f"{target}._driver_for_cluster", lambda _cluster: fake)
        monkeypatch.setattr(f"{target}._context_for_cluster", lambda _cluster: ctx)
    monkeypatch.setattr(
        "core.app_deploy.driver_for_capability",
        lambda _cluster, capability: iam if capability == "identity" else _Secrets(),
    )
    return SimpleNamespace(app=app, prod=env, cluster=fake, iam=iam, database=database)


def _open_pull_request(app, database, number=7):
    """The preview the PR webhook creates, with the production database
    attached the way preview service provisioning attaches a slice."""
    from astrolift_scm.github_pr_dispatch import PrEventContext
    from astrolift_scm.webhook_views import _ensure_preview_environment

    preview, created = _ensure_preview_environment(
        app,
        PrEventContext(
            raw_action="opened",
            repo_full_name="acme/hello-app",
            pr_number=number,
            head_sha="abc123",
            head_branch="feature/login",
            is_merge=False,
            is_bot_author=False,
        ),
    )
    assert created
    ManagedServiceAttachment.objects.create(managed_service=database, app_environment=preview.app_environment)
    return preview


def _deploy(state, env, image_tag):
    """``DeployAppWorkflow``'s cluster-touching activities, in its order."""
    from astrolift_workflows.activities.app_lifecycle import (
        _apply_manifests_sync,
        _health_check_sync,
        _poll_rollout_sync,
        _pre_flight_sync,
        _provision_namespace_sync,
        _update_secrets_sync,
    )
    from astrolift_workflows.activities.workload_identity import _ensure_workload_identity_sync

    deployment = Deployment.objects.create(
        registered_app=state.app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.PENDING.value,
        image_tag=image_tag,
    )
    state.cluster.deploying = env.name
    _pre_flight_sync(deployment.pk)
    _provision_namespace_sync(state.app.pk, env.pk)
    _ensure_workload_identity_sync(state.app.pk, env.pk)
    _update_secrets_sync(deployment.pk)
    _apply_manifests_sync(deployment.pk)
    _poll_rollout_sync(deployment.pk, 600)
    _health_check_sync(deployment.pk)
    return deployment


def test_prod_and_a_preview_deployed_to_one_cluster_share_no_object(one_cluster):
    state = one_cluster
    preview = _open_pull_request(state.app, state.database)

    _deploy(state, state.prod, "v1")
    _deploy(state, preview.app_environment, "pr-7")

    prod_objects = state.cluster.written_by(state.prod.name)
    preview_objects = state.cluster.written_by(preview.app_environment.name)
    assert prod_objects and preview_objects
    assert prod_objects.isdisjoint(preview_objects), sorted(prod_objects & preview_objects)
    # Everything each one wrote is in its own namespace, and the preview's
    # is the one the preview row recorded, provisioned and tears down.
    assert {ns for _kind, ns, _name in prod_objects} == {"acme-test-hello-app"}
    assert {ns for _kind, ns, _name in preview_objects} == {preview.namespace}
    assert preview.namespace == "acme-test-hello-app-pr-7"
    assert set(state.cluster.namespaces) == {"acme-test-hello-app", preview.namespace}
    # The kinds that used to collide by name are each there twice, once per
    # namespace: the objects are separate, not shared.
    for kind, name in [
        ("Deployment", "web"),
        ("Service", "web"),
        ("HorizontalPodAutoscaler", "web"),
        ("StatefulSet", "db"),
        ("Secret", "astrolift-bindings-hello-app"),
        ("ServiceAccount", "astrolift-acme-test-hello-app"),
        ("Ingress", "hello-app-web"),
    ]:
        assert (kind, "acme-test-hello-app", name) in prod_objects, (kind, name)
        assert (kind, preview.namespace, name) in preview_objects, (kind, name)


def test_the_preview_does_not_claim_the_production_hostname(one_cluster):
    """Two Ingresses for one host on one cluster is still a collision, one
    level up: ingress-nginx refuses the second, an ALB splits the host. The
    preview serves the hostname its row records and its URL names."""
    from core.app_deploy import render_resources_for_deployment

    state = one_cluster
    preview = _open_pull_request(state.app, state.database)
    prod_deploy = _deploy(state, state.prod, "v1")
    preview_deploy = _deploy(state, preview.app_environment, "pr-7")

    def _ingress_hosts(deployment):
        rendered = render_resources_for_deployment(Deployment.objects.get(pk=deployment.pk))
        return {rule["host"] for r in rendered if r["kind"] == "Ingress" for rule in r["spec"]["rules"]}

    assert _ingress_hosts(prod_deploy) == {"hello-app.apps.example.net"}
    assert _ingress_hosts(preview_deploy) == {preview.hostname}
    assert preview.app_environment.url == f"https://{preview.hostname}"


def test_deploying_the_preview_keeps_production_trusted_by_the_app_role(one_cluster):
    """One workload-identity role per app, and the IRSA driver resets its
    trust on every create. Binding only the environment being deployed
    would leave the role trusting the preview's ServiceAccount alone, and
    production's pods would lose AWS access at their next token refresh."""
    state = one_cluster
    preview = _open_pull_request(state.app, state.database)

    _deploy(state, state.prod, "v1")
    _deploy(state, preview.app_environment, "pr-7")

    sa = "astrolift-acme-test-hello-app"
    assert ("acme-test-hello-app", sa) in state.iam.trust
    assert (preview.namespace, sa) in state.iam.trust


def test_tearing_down_the_preview_leaves_production_standing(one_cluster):
    from astrolift_workflows.activities.app_lifecycle import _delete_preview_namespace_sync

    state = one_cluster
    preview = _open_pull_request(state.app, state.database)
    _deploy(state, state.prod, "v1")
    _deploy(state, preview.app_environment, "pr-7")

    _delete_preview_namespace_sync(preview.pk)

    assert state.cluster.deleted_namespaces == [preview.namespace]
    # The legacy location of the preview's literal Secret is cleaned; no
    # production object is touched.
    prod_objects = state.cluster.written_by(state.prod.name)
    assert not {obj for obj in state.cluster.deleted_objects if obj in prod_objects}
