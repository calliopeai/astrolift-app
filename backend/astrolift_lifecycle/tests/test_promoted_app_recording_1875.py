"""A promoted app's runtime is recorded as a Workload + Deployment (#1875).

``_record_promoted_app_deployment_sync`` does no cluster I/O -- it is pure
DB bookkeeping -- so these exercise it directly, the same way
``test_builder_api.py`` exercises ``_deploy_promoted_app_sync`` directly
rather than through a live Temporal worker.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment, DevEnvironment
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_workflows.activities.dev_environment import _record_promoted_app_deployment_sync

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def org():
    return Organization.objects.create(name="Recording Org", slug="recording-org")


@pytest.fixture
def team(org):
    return Team.objects.create(organization=org, name="Eng", slug="eng")


@pytest.fixture
def user(org):
    return User.objects.create_user(username="recorder", email="recorder@astrolift.dev", password="pw")


@pytest.fixture
def cluster(org):
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="k8s-recording",
        defaults={"name": "K8s", "plugin_version": "0.0.1", "capabilities_manifest": {}, "config_schema": {}},
    )
    return TenantCluster.objects.create(
        organization=org,
        name="recording-cluster",
        slug="recording-cluster",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://recording-cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        is_active=True,
    )


@pytest.fixture
def promoted_app(org, team, cluster):
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        name="Recorded App",
        slug="recorded-app",
        source_kind=RegisteredApp.SourceKind.DIRECT_UPLOAD,
        provisioning_status=RegisteredApp.ProvisioningStatus.PENDING,
        default_tenant_cluster=cluster,
    )
    AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    return app


@pytest.fixture
def dev(org, team, user, cluster, promoted_app):
    return DevEnvironment.objects.create(
        organization=org,
        team=team,
        creator=user,
        tenant_cluster=cluster,
        runtime=DevEnvironment.Runtime.PYTHON,
        resource_profile="small",
        status=DevEnvironment.Status.PROMOTING,
        namespace="builder-dev-recording",
        promoted_app=promoted_app,
    )


def test_records_a_public_deployment_workload_and_a_running_deployment(promoted_app, dev):
    _record_promoted_app_deployment_sync(dev.pk, "")

    workload = Workload.objects.get(registered_app=promoted_app, slug="app")
    assert workload.kind == Workload.Kind.DEPLOYMENT
    assert workload.is_public is True
    assert workload.replicas == 1
    assert workload.cpu_request == "100m"
    assert workload.memory_request == "128Mi"
    assert workload.storage_class == ""
    assert workload.storage_size == ""

    deployment = Deployment.objects.get(registered_app=promoted_app)
    assert deployment.status == Deployment.Status.RUNNING
    assert deployment.workload_id == workload.id
    assert deployment.trigger_kind == Deployment.TriggerKind.MANUAL
    assert deployment.ci_actor_kind == "builder_promote"
    assert deployment.app_environment.name == "production"


def test_a_data_file_records_the_storage_class_and_a_matching_size(promoted_app, dev):
    DevEnvironment.objects.filter(pk=dev.pk).update(
        data_file_path="data.sqlite", data_file=b"x" * (2 * 1024 * 1024)
    )

    _record_promoted_app_deployment_sync(dev.pk, "gp3")

    workload = Workload.objects.get(registered_app=promoted_app, slug="app")
    assert workload.storage_class == "gp3"
    # 4x headroom on a 2 MiB file, rounded up to whole GiB, floored at 1.
    assert workload.storage_size == "1Gi"


def test_a_second_deploy_updates_the_same_workload_and_supersedes_the_prior_deployment(promoted_app, dev):
    _record_promoted_app_deployment_sync(dev.pk, "")
    first_workload = Workload.objects.get(registered_app=promoted_app, slug="app")
    first_deployment = Deployment.objects.get(registered_app=promoted_app)

    # A re-promote after editing the dev env's resource profile -- the
    # existing Workload row updates in place, it does not duplicate.
    DevEnvironment.objects.filter(pk=dev.pk).update(resource_profile="large")
    _record_promoted_app_deployment_sync(dev.pk, "")

    assert Workload.objects.filter(registered_app=promoted_app).count() == 1
    first_workload.refresh_from_db()
    assert first_workload.cpu_request == "500m"
    assert first_workload.memory_request == "512Mi"

    deployments = list(Deployment.objects.filter(registered_app=promoted_app).order_by("pk"))
    assert len(deployments) == 2
    first_deployment.refresh_from_db()
    assert first_deployment.status == Deployment.Status.SUPERSEDED
    assert deployments[1].status == Deployment.Status.RUNNING
    assert deployments[1].workload_id == first_workload.id


def test_no_promoted_app_raises(dev, promoted_app):
    DevEnvironment.objects.filter(pk=dev.pk).update(promoted_app=None)

    with pytest.raises(RuntimeError, match="no promoted app"):
        _record_promoted_app_deployment_sync(dev.pk, "")
