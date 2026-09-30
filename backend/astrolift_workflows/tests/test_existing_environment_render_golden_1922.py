"""An environment that predates #1922 renders exactly as it did before (#1922).

#1922 gives previews, and environments created on a cluster where another
environment of the same app already renders into the app namespace, a
namespace of their own. Every environment that existed before it keeps
rendering into the app namespace, and nothing it writes may change: on a live
install a changed name or namespace is a second object next to the first, an
orphaned volume, a moved Ingress and a DNS change.

The digest was recorded on main at ``f1f9f11a``, before any #1922 change, by
running this file unchanged. It covers everything the platform writes for the
environment, not just the workload render: the render ``apply_manifests``
ships (Deployment, Service, HPA, StatefulSet with its claim template, CronJob,
managed-subdomain Ingress, workload-identity ServiceAccount), the Secrets
``update_secrets`` applies, the namespace ``provision_namespace`` ensures, the
``render_manifests`` activity's output, the service accounts workload identity
trusts, what a migration drain deletes and what app teardown deletes. A
deliberate change to any of those updates the digest; #1922 must not.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import boto3
import pytest
from asgiref.sync import async_to_sync
from aws.registry_ecr import ECRConfig, ECRDriver
from botocore.stub import Stubber
from temporalio.testing import ActivityEnvironment

from astrolift_clusters.models import ManagedDomain, ProviderPlugin, TenantCluster
from astrolift_lifecycle.models import Deployment
from astrolift_services.models import ManagedService, ManagedServiceBinding

pytestmark = pytest.mark.django_db

# sha256 of the canonical JSON of ``_capture()`` on main at f1f9f11a.
_GOLDEN_SHA256 = "043e403a127dfe667e21d056220d5a6707e028fa4afbe085940bc9757b699b12"

_MANIFEST = """
name = "hello-app"

[env]
LOG_LEVEL = "info"

[[workloads]]
name = "web"
kind = "deployment"
is_public = true
replicas = 2
hpa_min = 2
hpa_max = 4

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

[[workloads]]
name = "nightly"
kind = "cronjob"
schedule = "0 3 * * *"

  [[workloads.containers]]
  name = "nightly"
  is_primary = true
"""


class _Result:
    def __init__(self):
        self.ok = True
        self.errors = []
        self.created = []
        self.updated = []
        self.unchanged = []

    def summary(self):
        return []


class _RecordingClusterDriver:
    def __init__(self):
        self.calls: list = []

    def ensure_namespace(self, cluster_slug, namespace, labels, annotations):
        self.calls.append(["ensure_namespace", namespace, labels, annotations])

    def apply_manifests(self, cluster_slug, namespace, manifests, dry_run=False):
        if not dry_run:
            self.calls.append(["apply_manifests", namespace, list(manifests)])
        return _Result()

    def delete_manifests(self, cluster_slug, namespace, manifests, **_):
        self.calls.append(["delete_manifests", namespace, list(manifests)])
        return _Result()

    def delete_namespace(self, cluster_slug, namespace, wait=False):
        self.calls.append(["delete_namespace", namespace])

    def list_storage_classes(self, cluster_slug):
        return []


class _SecretsBackend:
    def get(self, ref):
        return None


class _IdentityDriver:
    def __init__(self):
        self.calls: list = []

    def create_identity_role(self, name, permissions):
        self.calls.append(["create_identity_role", name])
        return f"arn:aws:iam::111122223333:role/{name}"

    def bind_service_account(self, cluster, namespace, sa_name, identity_role):
        self.calls.append(["bind_service_account", namespace, sa_name])
        return {"eks.amazonaws.com/role-arn": f"arn:aws:iam::111122223333:role/{identity_role}"}


@pytest.fixture
def placed(org, app, env, cluster, settings, monkeypatch):
    """An existing primary environment: blank namespace, a managed domain,
    a managed service with a binding, literal secrets, on an AWS cluster."""
    settings.SECRET_KEY = "golden-1922"
    aws, _ = ProviderPlugin.objects.get_or_create(
        slug="aws",
        defaults={
            "name": "AWS",
            "plugin_version": "0.0.1",
            "capabilities_manifest": {},
            "config_schema": {},
        },
    )
    cluster.provider_plugin = aws
    cluster.ingress_class = "nginx"
    cluster.provider_config = {"account_id": "111122223333", "region": "us-east-1"}
    cluster.auth_config = {"cluster_oidc_issuer": "oidc.eks.us-east-1.amazonaws.com/id/GOLDEN"}
    cluster.save()
    domain = ManagedDomain.objects.create(
        organization=org,
        zone="apps.example.net",
        dns_driver="route53",
        dns_config={},
    )
    env.managed_domain = domain
    env.save()
    app.manifest_raw = _MANIFEST
    app.save(update_fields=["manifest_raw"])
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="main-db",
        status=ManagedService.Status.ACTIVE,
    )
    ManagedServiceBinding.objects.create(
        managed_service=svc,
        env_key="DATABASE_HOST",
        env_value_ref="main-db.internal",
        is_secret=False,
    )
    deployment = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.PENDING.value,
        image_tag="v1",
        image_digest="sha256:" + "a" * 64,
    )
    source = TenantCluster.objects.create(
        organization=org,
        name="old-cluster",
        slug="old-cluster",
        provider_plugin=aws,
        provider_config={"account_id": "111122223333", "region": "us-east-1"},
        auth_config={},
        endpoint="https://old.cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )

    driver = _RecordingClusterDriver()
    identity = _IdentityDriver()
    ctx = SimpleNamespace(slug="golden-cluster")
    for target in ("core.app_deploy", "core.cluster_management"):
        monkeypatch.setattr(f"{target}._driver_for_cluster", lambda _cluster: driver)
        monkeypatch.setattr(f"{target}._context_for_cluster", lambda _cluster: ctx)

    client = boto3.client(
        "ecr", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
    )
    registry = ECRDriver(config=ECRConfig(region="us-east-1", account_id="111122223333"), client=client)

    def _capability(_cluster, capability):
        return {"identity": identity, "registry": registry, "secrets": _SecretsBackend()}[capability]

    monkeypatch.setattr("core.app_deploy.driver_for_capability", _capability)
    # Relative images retain their historical render and must never call ECR.
    with Stubber(client):
        yield SimpleNamespace(
            app=app, env=env, deployment=deployment, source=source, driver=driver, identity=identity
        )


def _capture(placed) -> dict:
    from astrolift_workflows.activities.app_lifecycle import (
        _provision_namespace_sync,
        _update_secrets_sync,
        render_manifests,
    )
    from astrolift_workflows.activities.app_teardown import _delete_app_namespaces_sync
    from astrolift_workflows.activities.migration import _drain_source_cluster_sync
    from astrolift_workflows.activities.workload_identity import _ensure_workload_identity_sync
    from core.app_deploy import render_resources_for_deployment

    app, env, d, driver = placed.app, placed.env, placed.deployment, placed.driver
    doc: dict = {"render": render_resources_for_deployment(Deployment.objects.get(pk=d.pk))}

    driver.calls.clear()
    _update_secrets_sync(d.pk)
    doc["update_secrets"] = list(driver.calls)

    driver.calls.clear()
    _provision_namespace_sync(app.pk, env.pk)
    doc["provision_namespace"] = list(driver.calls)

    doc["render_manifests"] = async_to_sync(ActivityEnvironment().run)(render_manifests, d.pk)["resources"]

    identity = _ensure_workload_identity_sync(app.pk, env.pk)
    doc["workload_identity"] = {"calls": list(placed.identity.calls), "namespace": identity["namespace"]}

    driver.calls.clear()
    _drain_source_cluster_sync(app.pk, env.pk, placed.source.pk)
    doc["drain"] = list(driver.calls)

    driver.calls.clear()
    _delete_app_namespaces_sync(app.pk)
    doc["teardown"] = list(driver.calls)

    # The app's primary key rides in the namespace annotation; it depends on
    # the test database's sequence, not on anything the renderer decides.
    for call in doc["provision_namespace"]:
        if call[0] == "ensure_namespace" and call[3].get("astrolift.io/registered-app-id") == str(app.pk):
            call[3]["astrolift.io/registered-app-id"] = "<app-pk>"
    return json.loads(json.dumps(doc, sort_keys=True))


def _digest(doc: dict) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_an_existing_environment_renders_byte_for_byte_as_before(placed):
    doc = _capture(placed)

    # Structural half, so a digest failure says what moved.
    namespaces = {
        r["metadata"]["namespace"]
        for r in [*doc["render"], *doc["render_manifests"]]
        if "namespace" in r["metadata"]
    }
    assert namespaces == {"acme-test-hello-app"}
    assert sorted((r["kind"], r["metadata"]["name"]) for r in doc["render"]) == [
        ("CronJob", "nightly"),
        ("Deployment", "web"),
        ("HorizontalPodAutoscaler", "web"),
        ("Ingress", "hello-app-web"),
        ("Service", "db"),
        ("Service", "db-headless"),
        ("Service", "web"),
        ("ServiceAccount", "astrolift-acme-test-hello-app"),
        ("StatefulSet", "db"),
    ]
    assert doc["provision_namespace"][0][:2] == ["ensure_namespace", "acme-test-hello-app"]
    assert doc["workload_identity"]["namespace"] == "acme-test-hello-app"
    assert {call[1] for call in doc["drain"]} == {"acme-test-hello-app"}
    assert doc["teardown"] == [["delete_namespace", "acme-test-hello-app"]]

    assert _digest(doc) == _GOLDEN_SHA256
