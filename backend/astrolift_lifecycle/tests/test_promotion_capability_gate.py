"""promoteDeployment consults the target cluster's capability surface (#59).

A promotion can cross clusters and clouds, and the source's managed
services resolved against the *source* cluster's plugin catalogue. These
tests pin both directions of the gate: a cloud-bound variant the target
cannot expose is refused before any row or workflow exists, and an
in-cluster variant every tenant cluster borrows still promotes.

The plugin registry is synthetic (same shape the catalogue's own tests
use) so the assertions describe the rule rather than whichever drivers
happen to be installed in this process.
"""

from __future__ import annotations

import pytest
from _sdk.managed_service import BindingSchema

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_drivers.registry import PluginManifest, plugins
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_lifecycle.schema.mutations import (
    LifecycleMutation,
    PromoteDeploymentInput,
)
from astrolift_services.models import ManagedService
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


class _Drv:
    def config_schema(self):
        return {"type": "object", "properties": {}}

    def binding_schema(self):
        return BindingSchema(env_vars={"DATABASE_URL": "Connection URI"})


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    """aws sells RDS, gcp sells Cloud SQL, k8s_native sells CloudNativePG.

    Only the last is reachable from all three: every tenant cluster is a
    Kubernetes cluster, so the cloud plugins borrow it (#1484).
    """
    monkeypatch.setattr(
        plugins,
        "_plugins",
        {
            "aws": PluginManifest(
                plugin_id="aws",
                display_name="AWS",
                version="test",
                drivers={"managed:postgres:rds": _Drv},
            ),
            "gcp": PluginManifest(
                plugin_id="gcp",
                display_name="GCP",
                version="test",
                drivers={"managed:postgres:cloudsql": _Drv},
            ),
            "k8s_native": PluginManifest(
                plugin_id="k8s_native",
                display_name="In-cluster",
                version="test",
                drivers={"managed:postgres:cnpg": _Drv},
            ),
        },
    )


def _plugin(slug):
    row = ProviderPlugin(name=slug, slug=slug, plugin_version="0.0.1")
    ProviderPlugin.objects.bulk_create([row])
    return ProviderPlugin.objects.get(slug=slug)


def _cluster(org, slug, plugin_slug):
    return TenantCluster.objects.create(
        organization=org,
        name=slug,
        slug=slug,
        provider_plugin=_plugin(plugin_slug),
        provider_config={},
        endpoint=f"https://{slug}.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


@pytest.fixture
def aws_env(org, app):
    return AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=_cluster(org, "aws-east", "aws"),
        name="staging",
        url="https://hello.staging.example.com",
        required_approvals=0,
    )


@pytest.fixture
def gcp_env(org, app):
    return AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=_cluster(org, "gcp-central", "gcp"),
        name="prod",
        url="https://hello.example.com",
        required_approvals=0,
    )


def _running_in(env, app, actor):
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user=actor,
        trigger_kind="manual",
        status=Deployment.Status.RUNNING.value,
        image_tag="v1.0.0",
        image_digest="sha256:abc",
        config_snapshot={"replicas": 2},
    )


def _promote(mut, fake_info, app, source, target):
    return mut.promote_deployment(
        fake_info,
        input=PromoteDeploymentInput(
            app_slug=app.slug,
            source_environment_name=source.name,
            target_environment_name=target.name,
        ),
    )


def test_promote_refuses_variant_the_target_cloud_cannot_expose(
    org, app, aws_env, gcp_env, actor, fake_info, permission_resolver, temporal_recorder
):
    permission_resolver.grant(Permission.APP_DEPLOY)
    _running_in(aws_env, app, actor)
    ManagedService.objects.create(
        registered_app=app,
        app_environment=aws_env,
        kind=ManagedService.Kind.POSTGRES,
        name="main",
        variant="rds",
        status=ManagedService.Status.ACTIVE,
    )
    mut = LifecycleMutation()

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = _promote(mut, fake_info, app, aws_env, gcp_env)

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "targetEnvironmentName"
    assert "aws/rds" in result.errors[0].message
    assert "gcp-central" in result.errors[0].message
    # Refused before any side effect: no promotion row, no apply workflow.
    assert not Deployment.objects.filter(app_environment=gcp_env).exists()
    assert temporal_recorder.starts == []


def test_promote_allows_an_in_cluster_variant_across_clouds(
    org, app, aws_env, gcp_env, actor, fake_info, permission_resolver, temporal_recorder
):
    """CloudNativePG runs in the cluster on either cloud, so the pin is
    owned by k8s_native and the cross-cloud rule must not fire."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    _running_in(aws_env, app, actor)
    ManagedService.objects.create(
        registered_app=app,
        app_environment=aws_env,
        kind=ManagedService.Kind.POSTGRES,
        name="main",
        variant="cnpg",
        status=ManagedService.Status.ACTIVE,
    )
    mut = LifecycleMutation()

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = _promote(mut, fake_info, app, aws_env, gcp_env)

    assert result.ok, result.errors
    assert Deployment.objects.filter(app_environment=gcp_env).count() == 1
    assert [name for name, _args, _wf in temporal_recorder.starts] == ["DeployAppWorkflow"]


def test_promote_refuses_a_variant_whose_driver_is_gone(
    org, app, aws_env, actor, fake_info, permission_resolver, temporal_recorder
):
    """Same cluster both sides, so nothing about clouds is at play: a
    variant no installed driver carries can no longer be promoted onto,
    which is the case a source/target comparison alone would miss."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    target = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=aws_env.tenant_cluster,
        name="prod",
        url="https://hello.example.com",
        required_approvals=0,
    )
    _running_in(aws_env, app, actor)
    ManagedService.objects.create(
        registered_app=app,
        app_environment=aws_env,
        kind=ManagedService.Kind.POSTGRES,
        name="main",
        variant="aurora_postgres",
        status=ManagedService.Status.ACTIVE,
    )
    mut = LifecycleMutation()

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = _promote(mut, fake_info, app, aws_env, target)

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert "aurora_postgres" in result.errors[0].message
    assert not Deployment.objects.filter(app_environment=target).exists()
    assert temporal_recorder.starts == []


def test_promote_reports_every_blocker_at_once(
    org, app, aws_env, gcp_env, actor, fake_info, permission_resolver, temporal_recorder
):
    """The validators collect issues so an operator does not bisect; the
    envelope carries one message, so it has to carry all of them."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    _running_in(aws_env, app, actor)
    for name, variant in (("main", "rds"), ("reports", "aurora_postgres")):
        ManagedService.objects.create(
            registered_app=app,
            app_environment=aws_env,
            kind=ManagedService.Kind.POSTGRES,
            name=name,
            variant=variant,
            status=ManagedService.Status.ACTIVE,
        )
    mut = LifecycleMutation()

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = _promote(mut, fake_info, app, aws_env, gcp_env)

    assert not result.ok
    assert "aws/rds" in result.errors[0].message
    assert "aurora_postgres" in result.errors[0].message


def test_promote_with_no_managed_services_is_unaffected(
    org, app, aws_env, gcp_env, actor, fake_info, permission_resolver, temporal_recorder
):
    """An app with no provisioned dependencies has nothing to satisfy, so
    the gate must stay out of the way even across clouds."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    _running_in(aws_env, app, actor)
    mut = LifecycleMutation()

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = _promote(mut, fake_info, app, aws_env, gcp_env)

    assert result.ok, result.errors
    assert Deployment.objects.filter(app_environment=gcp_env).count() == 1
