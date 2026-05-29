"""Tests for the ``compareDeployments`` query (#737).

Covers commit range surface, manifest diff, image diff summary, not-found
paths, and tenant isolation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.queries import LifecycleQuery, _compute_manifest_diff
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context


def _info():
    return SimpleNamespace(context=SimpleNamespace(request=None))


def _tenant(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


def _grant(resolver):
    resolver.grant(Permission.APP_READ)


@pytest.fixture
def deploy_a(app, env):
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        workload=None,
        trigger_kind=Deployment.TriggerKind.MANUAL,
        status=Deployment.Status.RUNNING,
        commit_sha="aaa111",
        image_tag="v1.0",
        image_digest="sha256:aaa",
        rendered_manifest_snapshot={"replicas": 2, "cpu": "250m"},
    )


@pytest.fixture
def deploy_b(app, env):
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        workload=None,
        trigger_kind=Deployment.TriggerKind.MANUAL,
        status=Deployment.Status.RUNNING,
        commit_sha="bbb222",
        image_tag="v1.1",
        image_digest="sha256:bbb",
        rendered_manifest_snapshot={"replicas": 3, "cpu": "250m", "memory": "512Mi"},
    )


# ---- _compute_manifest_diff unit tests (pure, no DB) ------------------


def test_manifest_diff_replace():
    diff = _compute_manifest_diff({"replicas": 2}, {"replicas": 3})
    assert len(diff) == 1
    assert diff[0].op == "replace"
    assert diff[0].path == "replicas"
    assert diff[0].before == 2
    assert diff[0].after == 3


def test_manifest_diff_add():
    diff = _compute_manifest_diff({}, {"memory": "512Mi"})
    assert len(diff) == 1
    assert diff[0].op == "add"
    assert diff[0].path == "memory"
    assert diff[0].before is None
    assert diff[0].after == "512Mi"


def test_manifest_diff_remove():
    diff = _compute_manifest_diff({"memory": "512Mi"}, {})
    assert len(diff) == 1
    assert diff[0].op == "remove"
    assert diff[0].before == "512Mi"
    assert diff[0].after is None


def test_manifest_diff_no_change():
    diff = _compute_manifest_diff({"replicas": 2}, {"replicas": 2})
    assert diff == []


def test_manifest_diff_empty_both():
    assert _compute_manifest_diff({}, {}) == []


# ---- compareDeployments query integration tests --------------------


@pytest.mark.django_db
def test_compare_deployments_manifest_diff(
    deploy_a, deploy_b, org, actor, permission_resolver
):
    _grant(permission_resolver)
    with _tenant(org, actor):
        result = LifecycleQuery().astrolift_compare_deployments(
            info=_info(),
            id_a=str(deploy_a.guid),
            id_b=str(deploy_b.guid),
        )
    assert result is not None
    assert result.deployment_a_id == str(deploy_a.guid)
    assert result.deployment_b_id == str(deploy_b.guid)
    ops = {e.path: e.op for e in result.manifest_diff}
    assert ops["replicas"] == "replace"
    assert ops["memory"] == "add"
    assert "cpu" not in ops


@pytest.mark.django_db
def test_compare_deployments_commit_range(
    deploy_a, deploy_b, org, actor, permission_resolver
):
    _grant(permission_resolver)
    with _tenant(org, actor):
        result = LifecycleQuery().astrolift_compare_deployments(
            info=_info(),
            id_a=str(deploy_a.guid),
            id_b=str(deploy_b.guid),
        )
    assert result.base_sha == "aaa111"
    assert result.head_sha == "bbb222"


@pytest.mark.django_db
def test_compare_deployments_image_diff(
    deploy_a, deploy_b, org, actor, permission_resolver
):
    _grant(permission_resolver)
    with _tenant(org, actor):
        result = LifecycleQuery().astrolift_compare_deployments(
            info=_info(),
            id_a=str(deploy_a.guid),
            id_b=str(deploy_b.guid),
        )
    assert "v1.0" in result.image_diff_summary
    assert "v1.1" in result.image_diff_summary


@pytest.mark.django_db
def test_compare_deployments_same_image(
    app, env, org, actor, permission_resolver
):
    _grant(permission_resolver)
    da = Deployment.objects.create(
        registered_app=app, app_environment=env, workload=None,
        trigger_kind=Deployment.TriggerKind.MANUAL,
        status=Deployment.Status.RUNNING,
        image_tag="v1.0", image_digest="sha256:same",
    )
    db = Deployment.objects.create(
        registered_app=app, app_environment=env, workload=None,
        trigger_kind=Deployment.TriggerKind.MANUAL,
        status=Deployment.Status.RUNNING,
        image_tag="v1.0", image_digest="sha256:same",
    )
    with _tenant(org, actor):
        result = LifecycleQuery().astrolift_compare_deployments(
            info=_info(),
            id_a=str(da.guid),
            id_b=str(db.guid),
        )
    assert "same" in result.image_diff_summary


@pytest.mark.django_db
def test_compare_deployments_null_snapshot_empty_diff(
    app, env, org, actor, permission_resolver
):
    """Pre-#737 deployments have null snapshot → empty manifest diff."""
    _grant(permission_resolver)
    da = Deployment.objects.create(
        registered_app=app, app_environment=env, workload=None,
        trigger_kind=Deployment.TriggerKind.MANUAL,
        status=Deployment.Status.RUNNING,
        rendered_manifest_snapshot=None,
    )
    db = Deployment.objects.create(
        registered_app=app, app_environment=env, workload=None,
        trigger_kind=Deployment.TriggerKind.MANUAL,
        status=Deployment.Status.RUNNING,
        rendered_manifest_snapshot=None,
    )
    with _tenant(org, actor):
        result = LifecycleQuery().astrolift_compare_deployments(
            info=_info(),
            id_a=str(da.guid),
            id_b=str(db.guid),
        )
    assert result.manifest_diff == []


@pytest.mark.django_db
def test_compare_deployments_not_found(
    deploy_a, org, actor, permission_resolver
):
    _grant(permission_resolver)
    with _tenant(org, actor):
        result = LifecycleQuery().astrolift_compare_deployments(
            info=_info(),
            id_a=str(deploy_a.guid),
            id_b="00000000-0000-0000-0000-000000000000",
        )
    assert result is None


@pytest.mark.django_db
def test_compare_deployments_tenant_isolation(
    deploy_a, deploy_b, actor, permission_resolver
):
    """Cross-tenant call: org_id filter means neither deployment is found."""
    from astrolift_identity.models import Organization

    _grant(permission_resolver)
    other_org = Organization.objects.create(name="Other", slug="other-compare-test")
    with tenant_context(TenantContext(organization_id=other_org.id, actor_user_id=actor.id)):
        result = LifecycleQuery().astrolift_compare_deployments(
            info=_info(),
            id_a=str(deploy_a.guid),
            id_b=str(deploy_b.guid),
        )
    assert result is None
