"""
Cross-org isolation for ``migrate_app_to_cluster`` (#1183).

``LifecycleMutation.migrate_app_to_cluster`` resolves the TARGET
``TenantCluster`` by guid. ``TenantCluster.organization`` is a NULLABLE
FK — org-owned clusters carry an ``organization``; platform-shared
clusters have a null one. A bare by-guid fetch (no org clause) would let
a caller migrate their app onto ANOTHER org's private cluster, since
guids are globally unique.

These tests pin the fail-closed contract the clusters partition
established: migrating onto a foreign org's private cluster reads as
NOT_FOUND (identical to a non-existent guid) and enqueues no workflow,
while a same-org cluster and a platform-shared null-org cluster are both
valid targets.
"""

from __future__ import annotations

import pytest

from astrolift_clusters.models import TenantCluster
from astrolift_identity.models import Organization
from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.mutations import LifecycleMutation, MigrateAppInputGql
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _tenant_for(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


def _managed_cluster(*, slug, organization, provider_plugin):
    return TenantCluster.objects.create(
        organization=organization,
        name=slug,
        slug=slug,
        provider_plugin=provider_plugin,
        provider_config={},
        endpoint=f"https://{slug}.cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


def _running_deploy(env, app, actor):
    """A source-of-truth deployment so the resolver reaches the target-
    cluster resolution + workflow start (it picks the latest non-
    PENDING_APPROVAL deploy to migrate)."""
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user=actor,
        trigger_kind="manual",
        status=Deployment.Status.RUNNING.value,
        image_tag="v1.0.0",
    )


def _migrate(mut, fake_info, env, target):
    return mut.migrate_app_to_cluster(
        fake_info,
        input=MigrateAppInputGql(
            app_environment_id=env.guid,
            target_cluster_id=target.guid,
        ),
    )


def test_migrate_to_foreign_org_private_cluster_is_not_found(
    org, app, env, actor, provider_plugin, fake_info, permission_resolver, temporal_recorder
):
    """Target is another org's PRIVATE cluster → NOT_FOUND, and no
    migration workflow is enqueued (#1183). This is the leak: before the
    fix the bare by-guid fetch resolved the foreign cluster and the
    resolver enqueued a migrate onto it."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    _running_deploy(env, app, actor)

    other_org = Organization.objects.create(name="Globex", slug="globex-mig")
    foreign_cluster = _managed_cluster(
        slug="globex-private", organization=other_org, provider_plugin=provider_plugin
    )

    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = _migrate(mut, fake_info, env, foreign_cluster)

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    # No side effect: the migration must not have been enqueued.
    assert temporal_recorder.starts == []


def test_migrate_to_platform_shared_null_org_cluster_is_allowed(
    org, app, env, actor, provider_plugin, fake_info, permission_resolver, temporal_recorder
):
    """Target is a platform-shared (null-org) managed cluster → allowed;
    the migration workflow is enqueued. The null-org branch of the
    org-path clause is what keeps shared clusters reachable (#1183)."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    _running_deploy(env, app, actor)

    shared_cluster = _managed_cluster(slug="shared-pool", organization=None, provider_plugin=provider_plugin)

    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = _migrate(mut, fake_info, env, shared_cluster)

    assert result.ok, result.errors
    assert any(s[0] == "MigrateAppWorkflow" for s in temporal_recorder.starts)


def test_migrate_to_same_org_cluster_is_allowed(
    org, app, env, actor, provider_plugin, fake_info, permission_resolver, temporal_recorder
):
    """Target is another managed cluster in the caller's OWN org →
    allowed; the migration workflow is enqueued (#1183)."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    _running_deploy(env, app, actor)

    same_org_cluster = _managed_cluster(slug="acme-second", organization=org, provider_plugin=provider_plugin)

    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = _migrate(mut, fake_info, env, same_org_cluster)

    assert result.ok, result.errors
    assert any(s[0] == "MigrateAppWorkflow" for s in temporal_recorder.starts)
