"""Tests for bulk app-level operations (#746):
bulkRollingRestart, bulkPushSecrets, bulkResyncManifest."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_operations.schema.mutations import (
    BulkPushSecretsInput,
    BulkResyncManifestInput,
    BulkRollingRestartInput,
    OperationsMutation,
)
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_services.models.secret_bundle import AppSecretBundleRef, SecretBundle
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

User = get_user_model()


def _info():
    return SimpleNamespace(context=SimpleNamespace(request=None))


def _scaffold(slug_suffix="bulk"):
    org = Organization.objects.create(name=f"Bulk-{slug_suffix}", slug=f"bulk-org-{slug_suffix}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"bulk-eng-{slug_suffix}")
    project = Project.objects.create(
        organization=org, team=team, name="Demo", slug=f"bulk-demo-{slug_suffix}"
    )
    plugin = ProviderPlugin(
        name="Test",
        slug=f"bulk-plugin-{slug_suffix}",
        version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    plugin = ProviderPlugin.objects.get(slug=f"bulk-plugin-{slug_suffix}")
    cluster = TenantCluster.objects.create(
        organization=org,
        name="dev",
        slug=f"bulk-cluster-{slug_suffix}",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://dev.cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="BulkApp",
        slug=f"bulk-app-{slug_suffix}",
        provisioning_status="ready",
        default_tenant_cluster=cluster,
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="production",
        url="https://app.example.com",
    )
    user = User.objects.create_user(
        username=f"bulk-user-{slug_suffix}", email=f"bulk-{slug_suffix}@test.local"
    )
    return org, app, env, cluster, user


def _tenant(org, user):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id))


def _grant(resolver):
    resolver.grant(Permission.APP_DEPLOY)
    resolver.grant(Permission.APP_UPDATE)


# ---- bulkRollingRestart -------------------------------------------


def test_bulk_rolling_restart_fans_out(permission_resolver):
    org, app, env, cluster, user = _scaffold("restart1")
    Workload.objects.create(
        registered_app=app,
        name="web",
        slug="web",
        kind="deployment",
    )
    _grant(permission_resolver)
    restart_result = MagicMock(new_revision=2)
    with (
        _tenant(org, user),
        patch(
            "astrolift_lifecycle.services.k8s_ops.rollout_restart_workload",
            return_value=restart_result,
        ) as mock_restart,
    ):
        result = OperationsMutation().bulk_rolling_restart(
            info=_info(),
            input=BulkRollingRestartInput(app_slugs=[app.slug]),
        )
    assert result.ok_count == 1
    assert result.failed_count == 0
    assert result.per_app[0].ok is True
    assert mock_restart.call_count == 1


def test_bulk_rolling_restart_unknown_app(permission_resolver):
    org, app, env, cluster, user = _scaffold("restart2")
    _grant(permission_resolver)
    with _tenant(org, user):
        result = OperationsMutation().bulk_rolling_restart(
            info=_info(),
            input=BulkRollingRestartInput(app_slugs=["does-not-exist"]),
        )
    assert result.ok_count == 0
    assert result.failed_count == 1
    assert "not found" in result.per_app[0].errors[0]


def test_bulk_rolling_restart_no_workloads(permission_resolver):
    org, app, env, cluster, user = _scaffold("restart3")
    _grant(permission_resolver)
    with _tenant(org, user):
        result = OperationsMutation().bulk_rolling_restart(
            info=_info(),
            input=BulkRollingRestartInput(app_slugs=[app.slug]),
        )
    assert result.ok_count == 0
    assert result.failed_count == 1
    assert "no active workloads" in result.per_app[0].errors[0]


def test_bulk_rolling_restart_caps_at_20(permission_resolver):
    org, _app, _env, _cluster, user = _scaffold("restart4")
    _grant(permission_resolver)
    slugs = [f"nonexist-{i}" for i in range(25)]
    with _tenant(org, user):
        result = OperationsMutation().bulk_rolling_restart(
            info=_info(),
            input=BulkRollingRestartInput(app_slugs=slugs),
        )
    assert len(result.per_app) == 20


# ---- bulkPushSecrets ----------------------------------------------


def test_bulk_push_secrets_attaches_bundle(permission_resolver):
    org, app, env, cluster, user = _scaffold("push1")
    bundle = SecretBundle.objects.create(
        organization=org,
        name="shared-config",
        slug="shared-config-p1",
        backend_ref="vault://shared/config",
    )
    _grant(permission_resolver)
    with _tenant(org, user):
        result = OperationsMutation().bulk_push_secrets(
            info=_info(),
            input=BulkPushSecretsInput(
                app_slugs=[app.slug],
                bundle_slug="shared-config-p1",
                environment_name="production",
            ),
        )
    assert result.ok_count == 1
    assert result.failed_count == 0
    assert AppSecretBundleRef.objects.filter(
        registered_app=app,
        secret_bundle=bundle,
        deleted_at__isnull=True,
    ).exists()


def test_bulk_push_secrets_idempotent(permission_resolver):
    org, app, env, cluster, user = _scaffold("push2")
    bundle = SecretBundle.objects.create(
        organization=org,
        name="shared-config2",
        slug="shared-config-p2",
        backend_ref="vault://shared/config2",
    )
    AppSecretBundleRef.objects.create(
        registered_app=app,
        app_environment=env,
        secret_bundle=bundle,
    )
    _grant(permission_resolver)
    with _tenant(org, user):
        result = OperationsMutation().bulk_push_secrets(
            info=_info(),
            input=BulkPushSecretsInput(
                app_slugs=[app.slug],
                bundle_slug="shared-config-p2",
                environment_name="production",
            ),
        )
    assert result.ok_count == 1
    assert (
        AppSecretBundleRef.objects.filter(
            registered_app=app,
            secret_bundle=bundle,
            deleted_at__isnull=True,
        ).count()
        == 1
    )


def test_bulk_push_secrets_bundle_not_found(permission_resolver):
    org, app, env, cluster, user = _scaffold("push3")
    _grant(permission_resolver)
    with _tenant(org, user):
        result = OperationsMutation().bulk_push_secrets(
            info=_info(),
            input=BulkPushSecretsInput(
                app_slugs=[app.slug],
                bundle_slug="no-such-bundle",
            ),
        )
    assert result.ok_count == 0
    assert result.failed_count == 1
    assert "not found" in result.per_app[0].errors[0]


# ---- bulkResyncManifest -------------------------------------------


def test_bulk_resync_manifest_fans_out(permission_resolver):
    org, app, env, cluster, user = _scaffold("resync1")
    _grant(permission_resolver)
    sync_result = MagicMock(status="applied", error=None)
    with (
        _tenant(org, user),
        patch(
            "astrolift_registry.services.manifest_sync.resync_app_manifest_from_repo",
            return_value=sync_result,
        ),
    ):
        result = OperationsMutation().bulk_resync_manifest(
            info=_info(),
            input=BulkResyncManifestInput(app_slugs=[app.slug]),
        )
    assert result.ok_count == 1
    assert result.failed_count == 0


def test_bulk_resync_manifest_fetch_failed(permission_resolver):
    org, app, env, cluster, user = _scaffold("resync2")
    _grant(permission_resolver)
    sync_result = MagicMock(status="fetch_failed", error="couldn't reach repo")
    with (
        _tenant(org, user),
        patch(
            "astrolift_registry.services.manifest_sync.resync_app_manifest_from_repo",
            return_value=sync_result,
        ),
    ):
        result = OperationsMutation().bulk_resync_manifest(
            info=_info(),
            input=BulkResyncManifestInput(app_slugs=[app.slug]),
        )
    assert result.ok_count == 0
    assert result.failed_count == 1
    assert "couldn't reach repo" in result.per_app[0].errors[0]


def test_bulk_resync_manifest_unknown_app(permission_resolver):
    org, app, env, cluster, user = _scaffold("resync3")
    _grant(permission_resolver)
    with _tenant(org, user):
        result = OperationsMutation().bulk_resync_manifest(
            info=_info(),
            input=BulkResyncManifestInput(app_slugs=["missing-slug"]),
        )
    assert result.ok_count == 0
    assert result.failed_count == 1
    assert "not found" in result.per_app[0].errors[0]


# ---- #1192: fail-closed on null org, isolation across orgs --------
#
# ``@tenant_scoped`` blocks a null org before the body runs, so the
# org_id=None branch is unreachable through the decorated resolver. We
# unwrap to the raw body to prove it fails closed as defense-in-depth
# (would fall through to an UNSCOPED by-slug fetch before #1192).


def _raw(resolver_cls, name):
    return inspect.unwrap(resolver_cls.__dict__[name])


def test_bulk_rolling_restart_null_org_fails_closed(permission_resolver):
    org, app, env, cluster, user = _scaffold("restart-nullorg")
    Workload.objects.create(registered_app=app, name="web", slug="web", kind="deployment")
    raw = _raw(OperationsMutation, "bulk_rolling_restart")
    with (
        tenant_context(TenantContext(organization_id=None, actor_user_id=user.id)),
        patch("astrolift_lifecycle.services.k8s_ops.rollout_restart_workload") as mock_restart,
    ):
        result = raw(
            OperationsMutation(),
            info=_info(),
            input=BulkRollingRestartInput(app_slugs=[app.slug]),
        )
    assert result.ok_count == 0
    assert result.failed_count == 1
    assert "no active organization" in result.per_app[0].errors[0]
    # No restart issued despite the slug existing — the fail-closed guard
    # returns before the (now org-scoped) fetch and the k8s side effect.
    assert mock_restart.call_count == 0


def test_bulk_rolling_restart_cross_org_not_found(permission_resolver):
    org_a, _app_a, _env_a, _cl_a, user_a = _scaffold("restart-xorgA")
    _org_b, app_b, _env_b, _cl_b, _user_b = _scaffold("restart-xorgB")
    Workload.objects.create(registered_app=app_b, name="web", slug="web", kind="deployment")
    _grant(permission_resolver)
    with (
        _tenant(org_a, user_a),
        patch("astrolift_lifecycle.services.k8s_ops.rollout_restart_workload") as mock_restart,
    ):
        result = OperationsMutation().bulk_rolling_restart(
            info=_info(),
            input=BulkRollingRestartInput(app_slugs=[app_b.slug]),
        )
    assert result.ok_count == 0
    assert result.failed_count == 1
    assert "not found" in result.per_app[0].errors[0]
    assert mock_restart.call_count == 0


def test_bulk_resync_manifest_null_org_fails_closed(permission_resolver):
    org, app, env, cluster, user = _scaffold("resync-nullorg")
    raw = _raw(OperationsMutation, "bulk_resync_manifest")
    with (
        tenant_context(TenantContext(organization_id=None, actor_user_id=user.id)),
        patch("astrolift_registry.services.manifest_sync.resync_app_manifest_from_repo") as mock_resync,
    ):
        result = raw(
            OperationsMutation(),
            info=_info(),
            input=BulkResyncManifestInput(app_slugs=[app.slug]),
        )
    assert result.ok_count == 0
    assert result.failed_count == 1
    assert "no active organization" in result.per_app[0].errors[0]
    assert mock_resync.call_count == 0


def test_bulk_resync_manifest_cross_org_not_found(permission_resolver):
    org_a, _app_a, _env_a, _cl_a, user_a = _scaffold("resync-xorgA")
    _org_b, app_b, _env_b, _cl_b, _user_b = _scaffold("resync-xorgB")
    _grant(permission_resolver)
    with (
        _tenant(org_a, user_a),
        patch("astrolift_registry.services.manifest_sync.resync_app_manifest_from_repo") as mock_resync,
    ):
        result = OperationsMutation().bulk_resync_manifest(
            info=_info(),
            input=BulkResyncManifestInput(app_slugs=[app_b.slug]),
        )
    assert result.ok_count == 0
    assert result.failed_count == 1
    assert "not found" in result.per_app[0].errors[0]
    assert mock_resync.call_count == 0
