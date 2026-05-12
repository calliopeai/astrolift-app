"""Tests for the rollback + promotion activity primitives (#124).

The full workflow round-trip needs the Temporal time-skipping
fixture; this file exercises the data-side primitives that the
workflows orchestrate. Together with the existing
test_control_plane_mutations coverage for the user-facing mutations,
these tests pin the platform's rollback + promotion semantics
without spinning a worker.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import Deployment

pytestmark = pytest.mark.django_db


# ---- helpers used across the file -------------------------------------


def _make_running(app, env, *, image_tag="v1.0.0", commit_sha="aaaaaaa"):
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind="manual",
        status=Deployment.Status.RUNNING.value,
        image_tag=image_tag,
        image_digest=f"sha256:{image_tag}-digest",
        config_snapshot={"image": image_tag},
        commit_sha=commit_sha,
        branch="main",
        ci_provider="github_actions",
    )


def _make_failed(app, env, *, image_tag="v2.0.0"):
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind="manual",
        status=Deployment.Status.FAILED.value,
        image_tag=image_tag,
    )


# ---- rollback ----------------------------------------------------------


def test_rollback_creates_new_deployment_from_prior_running(app, env):
    """The rollback activity must create a brand-new ``rollback``
    deployment row that copies image_tag + config_snapshot from the
    most-recent prior running deployment, and mark the bad deploy
    SUPERSEDED so the state machine reflects the lineage."""
    from astrolift_workflows.activities.app_lifecycle import _create_rollback_deployment_sync

    prior = _make_running(app, env, image_tag="v1.0.0")
    bad = _make_failed(app, env, image_tag="v2.0.0")
    # prior must be older than bad — otherwise the activity would
    # pick `bad` itself as the rollback target.
    Deployment.objects.filter(pk=prior.pk).update(
        created_at=prior.created_at - __import__("datetime").timedelta(seconds=10)
    )
    # bad has to be RUNNING-ish to be transitioned to SUPERSEDED;
    # the activity uses the state machine and FAILED→SUPERSEDED isn't
    # a legal transition. Use a RUNNING bad for the test.
    Deployment.objects.filter(pk=bad.pk).update(status=Deployment.Status.RUNNING.value)

    new_id = _create_rollback_deployment_sync(bad.pk)
    new_deploy = Deployment.objects.get(pk=new_id)

    assert new_deploy.trigger_kind == Deployment.TriggerKind.ROLLBACK.value
    assert new_deploy.image_tag == "v1.0.0"
    assert new_deploy.config_snapshot == {"image": "v1.0.0"}
    assert new_deploy.commit_sha == "aaaaaaa"
    assert new_deploy.ci_actor_kind == "rollback"

    bad.refresh_from_db()
    assert bad.status == Deployment.Status.SUPERSEDED.value


def test_rollback_fails_when_no_prior_running(app, env):
    """First-deploy-ever has nothing to roll back to. The activity
    raises so the workflow can surface a friendly error instead of
    silently creating a duplicate of the bad row."""
    from astrolift_workflows.activities.app_lifecycle import _create_rollback_deployment_sync

    only = _make_running(app, env, image_tag="v1.0.0")
    Deployment.objects.filter(pk=only.pk).update(status=Deployment.Status.FAILED.value)

    with pytest.raises(RuntimeError, match="no prior running revision"):
        _create_rollback_deployment_sync(only.pk)


# ---- promote -----------------------------------------------------------


@pytest.fixture
def env_staging(app, cluster):
    from astrolift_lifecycle.models import AppEnvironment

    return AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="staging",
        url="https://staging.example.com",
        required_approvals=0,
    )


@pytest.fixture
def env_prod_with_approvals(app, cluster):
    from astrolift_lifecycle.models import AppEnvironment

    return AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://prod.example.com",
        required_approvals=1,
    )


def test_promote_creates_new_deployment_in_target_env(app, env_staging, env):
    """Promotion: image_tag + config copy across envs, lineage via
    ``promoted_from``."""
    from astrolift_workflows.activities.app_lifecycle import _create_promotion_deployment_sync

    source = _make_running(app, env_staging, image_tag="v1.0.0", commit_sha="abc1234")
    new_id = _create_promotion_deployment_sync(source.pk, env.pk)
    new_deploy = Deployment.objects.get(pk=new_id)

    assert new_deploy.app_environment_id == env.pk
    assert new_deploy.trigger_kind == Deployment.TriggerKind.PROMOTION.value
    assert new_deploy.image_tag == "v1.0.0"
    assert new_deploy.commit_sha == "abc1234"
    assert new_deploy.promoted_from_id == source.pk
    # Target env requires no approvals → starts pending (ready to deploy).
    assert new_deploy.status == Deployment.Status.PENDING.value


def test_promote_into_approval_env_starts_pending_approval(app, env_staging, env_prod_with_approvals):
    from astrolift_workflows.activities.app_lifecycle import _create_promotion_deployment_sync

    source = _make_running(app, env_staging)
    new_id = _create_promotion_deployment_sync(source.pk, env_prod_with_approvals.pk)
    new_deploy = Deployment.objects.get(pk=new_id)
    assert new_deploy.status == Deployment.Status.PENDING_APPROVAL.value
    assert new_deploy.approvals_required == 1


def test_promote_rejects_cross_app(app, env, org, project, team):
    """Source + target must belong to the same app — promoting
    across apps is a footgun (different secrets, different bindings).
    """
    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp
    from astrolift_workflows.activities.app_lifecycle import _create_promotion_deployment_sync

    source = _make_running(app, env)
    other_app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Other",
        slug="other-app",
        provisioning_status="ready",
    )
    [plugin] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(name="P", slug="p2", capabilities_manifest={}, config_schema={}),
        ]
    )
    other_cluster = TenantCluster.objects.create(
        organization=org,
        name="c2",
        slug="c2",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )
    other_env = AppEnvironment.objects.create(
        registered_app=other_app,
        tenant_cluster=other_cluster,
        name="prod",
        url="https://x.example",
        required_approvals=0,
    )

    with pytest.raises(RuntimeError, match="same app"):
        _create_promotion_deployment_sync(source.pk, other_env.pk)


def test_promote_rejects_paused_target(app, env_staging, env):
    """Pause is the operator's signal that no deploys should land
    here. Promotion respects it."""
    from astrolift_workflows.activities.app_lifecycle import _create_promotion_deployment_sync

    env.deploys_paused = True
    env.save(update_fields=["deploys_paused"])

    source = _make_running(app, env_staging)
    with pytest.raises(RuntimeError, match="paused"):
        _create_promotion_deployment_sync(source.pk, env.pk)
