"""Tests for ``start_deployment``'s app-global webhook-pause gate (#399).

Asserts the gate refuses webhook-shaped trigger kinds (``push`` /
``ci`` / ``scheduled``) when ``RegisteredApp.webhook_deploys_paused``
is True, while ``manual`` deploys continue to flow — the explicit
on-call escape valve called out in the issue.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.mutations import (
    LifecycleMutation,
    StartDeploymentInput,
)
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context


def _grant_deploy(resolver):
    resolver.grant(Permission.APP_DEPLOY)


def _tenant_for(org, actor):
    return tenant_context(
        TenantContext(organization_id=org.id, actor_user_id=actor.id),
    )


@pytest.mark.django_db
def test_webhook_deploy_refused_when_app_paused(
    app,
    env,
    fake_info,
    org,
    actor,
    permission_resolver,
    temporal_recorder,
):
    """Webhook-shaped trigger kinds (push / ci / scheduled) are
    refused with PRECONDITION when the app-global pause is on."""
    _grant_deploy(permission_resolver)
    app.webhook_deploys_paused = True
    app.webhook_deploys_pause_reason = "ci storm"
    app.save(
        update_fields=[
            "webhook_deploys_paused",
            "webhook_deploys_pause_reason",
            "updated_at",
            "version",
        ]
    )

    mutation = LifecycleMutation()
    for kind in ("push", "ci", "scheduled"):
        with _tenant_for(org, actor):
            result = mutation.start_deployment(
                info=fake_info,
                input=StartDeploymentInput(
                    app_slug=app.slug,
                    environment_name=env.name,
                    image_tag="sha-deadbeef0001",
                    trigger_kind=kind,
                ),
            )
        assert result.ok is False, f"trigger {kind} should have been refused"
        assert result.errors[0].code == ErrorCode.PRECONDITION.value
        assert "webhook" in result.errors[0].message.lower()
        # Reason is surfaced so the operator at the originating CI
        # gets a useful "why am I locked out" string back.
        assert "ci storm" in result.errors[0].message

    # No deployment rows landed for any of the refused kinds.
    assert Deployment.objects.filter(registered_app=app).count() == 0
    # No workflow enqueued.
    assert temporal_recorder.starts == []


@pytest.mark.django_db
def test_manual_deploy_bypasses_pause(
    app,
    env,
    fake_info,
    org,
    actor,
    permission_resolver,
    temporal_recorder,
):
    """Manual deploys (operator-fired) ignore the pause — explicit
    on-call escape valve so a wedged CI can be stopped without
    locking the operator out of fixing the app."""
    _grant_deploy(permission_resolver)
    app.webhook_deploys_paused = True
    app.save(update_fields=["webhook_deploys_paused", "updated_at", "version"])

    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.start_deployment(
            info=fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="sha-deadbeef0002",
                trigger_kind="manual",
            ),
        )

    assert result.ok is True, result.errors
    assert Deployment.objects.filter(registered_app=app).count() == 1
    # Workflow enqueued exactly once.
    assert len(temporal_recorder.starts) == 1


@pytest.mark.django_db
def test_rollback_promotion_bypass_pause(
    app,
    env,
    fake_info,
    org,
    actor,
    permission_resolver,
    temporal_recorder,
):
    """Rollback + promotion trigger kinds aren't webhook-shaped —
    they're operator-initiated recovery paths and must continue to
    flow even while webhook deploys are paused."""
    _grant_deploy(permission_resolver)
    app.webhook_deploys_paused = True
    app.save(update_fields=["webhook_deploys_paused", "updated_at", "version"])

    mutation = LifecycleMutation()
    for kind in ("rollback", "promotion"):
        with _tenant_for(org, actor):
            result = mutation.start_deployment(
                info=fake_info,
                input=StartDeploymentInput(
                    app_slug=app.slug,
                    environment_name=env.name,
                    image_tag=f"sha-deadbeef-{kind}",
                    trigger_kind=kind,
                ),
            )
        assert result.ok is True, f"trigger {kind} should bypass pause; got {result.errors}"


@pytest.mark.django_db
def test_resume_re_enables_webhook_deploys(
    app,
    env,
    fake_info,
    org,
    actor,
    permission_resolver,
    temporal_recorder,
):
    """After the operator clears the pause, the next webhook-fired
    deploy lands a Deployment row as normal."""
    _grant_deploy(permission_resolver)
    # Start paused, then resume.
    app.webhook_deploys_paused = True
    app.save(update_fields=["webhook_deploys_paused", "updated_at", "version"])

    # Sanity: paused refuses.
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        refused = mutation.start_deployment(
            info=fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="sha-deadbeef0003",
                trigger_kind="ci",
            ),
        )
    assert refused.ok is False

    # Lift the pause inline (mirrors what resumeAstroliftAppWebhookDeploys does).
    app.webhook_deploys_paused = False
    app.save(update_fields=["webhook_deploys_paused", "updated_at", "version"])

    with _tenant_for(org, actor):
        ok = mutation.start_deployment(
            info=fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="sha-deadbeef0004",
                trigger_kind="ci",
            ),
        )
    assert ok.ok is True, ok.errors
    assert Deployment.objects.filter(registered_app=app).count() == 1


@pytest.mark.django_db
def test_per_env_deploys_paused_takes_precedence(
    app,
    env,
    fake_info,
    org,
    actor,
    permission_resolver,
    temporal_recorder,
):
    """When both axes are paused, the per-env check fires first —
    independent axes, but the env-specific message is more useful
    to the operator and lands closer to the input field."""
    _grant_deploy(permission_resolver)
    env.deploys_paused = True
    env.save(update_fields=["deploys_paused", "updated_at", "version"])
    app.webhook_deploys_paused = True
    app.save(update_fields=["webhook_deploys_paused", "updated_at", "version"])

    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.start_deployment(
            info=fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="sha-deadbeef0005",
                trigger_kind="ci",
            ),
        )
    assert result.ok is False
    # Per-env message wins; field tag points at environmentName.
    assert result.errors[0].field == "environmentName"
