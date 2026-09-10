"""`astroliftApp.latestDeployment` has to reflect the app's deploys (#1691).

Freshness (`latestDeployment` / `lastDeployedAt` / `healthPulse`) is
opt-in on the list resolvers so a 200-row page does not pay for a rollup
nobody asked for. The detail resolver was never wired to it at all, so
the field was structurally null on that path: an app with a healthy pod
and several successful deploys read as though nothing had ever shipped,
which is what made a working install look broken.

A detail request is one row, so there is no list to protect and nothing
to opt into.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.queries import RegistryQuery
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None))


@pytest.fixture
def world():
    org = Organization.objects.create(name="Acme", slug="acme-1691")
    team = Team.objects.create(organization=org, name="Plat", slug="plat-1691")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-1691")
    plugin = ProviderPlugin(
        name="Plugin 1691",
        slug="plugin-1691",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    cluster = TenantCluster.objects.create(
        organization=org,
        name="cluster-1691",
        slug="cluster-1691",
        provider_plugin=ProviderPlugin.objects.get(slug="plugin-1691"),
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="exo-dash",
        slug="exo-dash-1691",
        provisioning_status=RegisteredApp.ProvisioningStatus.READY,
        registry_repo_uri="123456789012.dkr.ecr.us-east-1.amazonaws.com/acme/app",
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://exo-dash.example.com",
        required_approvals=0,
    )
    User = get_user_model()
    viewer = User.objects.create(
        username="viewer-1691", email="viewer-1691@test", is_superuser=True, is_staff=True
    )
    return SimpleNamespace(org=org, app=app, env=env, viewer=viewer)


def _deploy(world, *, status: str, image_tag: str, age: timedelta) -> Deployment:
    row = Deployment.objects.create(
        registered_app=world.app,
        app_environment=world.env,
        trigger_kind="manual",
        status=status,
        image_tag=image_tag,
    )
    Deployment.objects.filter(pk=row.pk).update(created_at=timezone.now() - age)
    row.refresh_from_db()
    return row


def _detail(world):
    with tenant_context(TenantContext(organization_id=world.org.id, actor_user_id=world.viewer.id)):
        return RegistryQuery().astrolift_app(_info(), slug=world.app.slug)


def test_detail_reports_the_most_recent_deployment(world):
    """The bug: this was null for every app, on every install."""

    _deploy(world, status=Deployment.Status.RUNNING.value, image_tag="img-old", age=timedelta(days=3))
    newest = _deploy(
        world, status=Deployment.Status.RUNNING.value, image_tag="img-new", age=timedelta(minutes=5)
    )

    row = _detail(world)

    assert row.latest_deployment is not None
    assert row.latest_deployment.image_tag == newest.image_tag
    assert row.last_deployed_at is not None
    assert row.health_pulse is not None


def test_a_failed_deploy_after_a_success_is_the_latest(world):
    """`latestDeployment` is the most recent attempt whatever its status;
    `lastDeployedAt` is the most recent success. The two disagreeing is
    the case the app overview exists to show."""

    _deploy(world, status=Deployment.Status.RUNNING.value, image_tag="img-good", age=timedelta(days=2))
    _deploy(world, status=Deployment.Status.FAILED.value, image_tag="img-bad", age=timedelta(minutes=1))

    row = _detail(world)

    assert row.latest_deployment.image_tag == "img-bad"
    assert row.latest_deployment.status == Deployment.Status.FAILED.value
    # The successful deploy two days ago is still the last one that shipped.
    assert row.last_deployed_at is not None
    assert row.last_deployed_at < timezone.now() - timedelta(days=1)


def test_an_app_that_never_deployed_reports_a_never_pulse(world):
    """Null used to mean both "never deployed" and "we did not look"."""

    row = _detail(world)

    assert row.latest_deployment is None
    assert row.last_deployed_at is None
    assert row.health_pulse is not None
    assert row.health_pulse.status.value == "never"


def test_another_orgs_deployments_do_not_leak_into_the_rollup(world):
    other_org = Organization.objects.create(name="Other", slug="other-1691")
    other_team = Team.objects.create(organization=other_org, name="T", slug="t-1691")
    other_app = RegisteredApp.objects.create(
        organization=other_org,
        team=other_team,
        name="other",
        slug="other-app-1691",
        provisioning_status=RegisteredApp.ProvisioningStatus.READY,
    )
    other_env = AppEnvironment.objects.create(
        registered_app=other_app,
        tenant_cluster=world.env.tenant_cluster,
        name="prod",
        url="https://other.example.com",
        required_approvals=0,
    )
    Deployment.objects.create(
        registered_app=other_app,
        app_environment=other_env,
        trigger_kind="manual",
        status=Deployment.Status.RUNNING.value,
        image_tag="img-other",
    )

    assert _detail(world).latest_deployment is None
