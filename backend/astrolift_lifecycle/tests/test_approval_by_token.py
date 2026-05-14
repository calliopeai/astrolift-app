"""
Public token-based deployment approval mutations (#125, spec 06 §4.6).

These tests verify:

* ``startDeployment`` against an approval-gated env mints a single-use
  magic link, persists only its SHA-256, and publishes the plaintext
  on ``deployment.approval_token.minted.<org>``.
* ``approveDeploymentByToken`` advances the deploy + records token
  usage; a second call with the same token fails.
* ``rejectDeploymentByToken`` transitions the deploy to FAILED.
* Invalid / expired / unknown / wrong-status tokens all surface as
  ``PERMISSION_DENIED`` with the same message — no timing oracle.

The token resolvers run **without** a tenant context: the token is
the auth proof, so the tests deliberately don't enter
``_tenant_for(...)``.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from django.utils import timezone

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.mutations import (
    ApproveByTokenInput,
    LifecycleMutation,
    RejectByTokenInput,
    StartDeploymentInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _grant_deploy(resolver):
    for p in (Permission.APP_DEPLOY, Permission.APP_APPROVE_DEPLOY):
        resolver.grant(p)


def _tenant_for(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


def _start_with_approval(mut, fake_info, app, env_requires_approval):
    return mut.start_deployment(
        fake_info,
        input=StartDeploymentInput(
            app_slug=app.slug,
            environment_name=env_requires_approval.name,
            image_tag="v1.0.0",
        ),
    )


@pytest.fixture
def pubsub_recorder(monkeypatch):
    """Capture every ``publish_sync`` call so tests can assert the
    plaintext token is surfaced exactly once."""
    events: list[tuple[str, object]] = []

    def fake_publish_sync(topic, event):
        events.append((topic, event))

    # The mutation imports publish_sync inside the function body so
    # patch the module-level binding.
    monkeypatch.setattr("core.pubsub.publish_sync", fake_publish_sync)
    return events


def test_start_deployment_mints_token_when_env_requires_approval(
    org,
    app,
    env_requires_approval,
    actor,
    fake_info,
    permission_resolver,
    temporal_recorder,
    pubsub_recorder,
):
    _grant_deploy(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = _start_with_approval(mut, fake_info, app, env_requires_approval)

    assert result.ok, result.errors

    deploy = Deployment.objects.get(guid=str(result.data.id))
    assert deploy.status == Deployment.Status.PENDING_APPROVAL.value
    assert deploy.approval_token_hash, "token hash should be set"
    assert deploy.approval_token_expires_at is not None
    assert deploy.approval_token_used_at is None

    minted_events = [
        (topic, payload)
        for topic, payload in pubsub_recorder
        if topic.startswith("deployment.approval_token.minted.")
    ]
    assert len(minted_events) == 1, minted_events
    topic, payload = minted_events[0]
    assert topic == f"deployment.approval_token.minted.{org.id}"
    plaintext = payload["approval_token"]
    assert plaintext.startswith("alft_ml_")
    # And the hash matches what we stored.
    assert hashlib.sha256(plaintext.encode("utf-8")).hexdigest() == deploy.approval_token_hash


def test_start_deployment_without_approval_does_not_mint_token(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    temporal_recorder,
    pubsub_recorder,
):
    _grant_deploy(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="v1.0.0",
            ),
        )
    assert result.ok
    deploy = Deployment.objects.get(guid=str(result.data.id))
    assert deploy.status == Deployment.Status.PENDING.value
    assert deploy.approval_token_hash == ""
    assert deploy.approval_token_expires_at is None
    assert not any(topic.startswith("deployment.approval_token.minted.") for topic, _ in pubsub_recorder)


def test_approve_by_token_advances_to_pending_and_enqueues(
    org,
    app,
    env_requires_approval,
    actor,
    fake_info,
    permission_resolver,
    temporal_recorder,
    pubsub_recorder,
):
    _grant_deploy(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        start = _start_with_approval(mut, fake_info, app, env_requires_approval)
    assert start.ok

    minted = next(
        payload for topic, payload in pubsub_recorder if topic.startswith("deployment.approval_token.minted.")
    )
    token = minted["approval_token"]

    # Approve from outside any tenant context.
    result = mut.approve_deployment_by_token(fake_info, input=ApproveByTokenInput(token=token))

    assert result.ok, result.errors
    assert result.data.status == Deployment.Status.PENDING.value
    assert len(temporal_recorder.starts) == 1

    deploy = Deployment.objects.get(guid=str(start.data.id))
    assert deploy.approval_token_used_at is not None


def test_approve_by_token_invalid_token_rejected(
    org,
    app,
    env_requires_approval,
    actor,
    fake_info,
    permission_resolver,
    temporal_recorder,
    pubsub_recorder,
):
    _grant_deploy(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        _start_with_approval(mut, fake_info, app, env_requires_approval)

    result = mut.approve_deployment_by_token(
        fake_info, input=ApproveByTokenInput(token="alft_ml_not_a_real_token")
    )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert result.errors[0].message == "approval token invalid"


def test_approve_by_token_empty_token_rejected(actor, fake_info):
    mut = LifecycleMutation()
    result = mut.approve_deployment_by_token(fake_info, input=ApproveByTokenInput(token=""))
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_approve_by_token_single_use(
    org,
    app,
    env_requires_approval,
    actor,
    fake_info,
    permission_resolver,
    temporal_recorder,
    pubsub_recorder,
):
    _grant_deploy(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        _start_with_approval(mut, fake_info, app, env_requires_approval)

    minted = next(
        payload for topic, payload in pubsub_recorder if topic.startswith("deployment.approval_token.minted.")
    )
    token = minted["approval_token"]

    first = mut.approve_deployment_by_token(fake_info, input=ApproveByTokenInput(token=token))
    assert first.ok

    # Second call: same token. Used_at is set + status is no longer
    # pending_approval, so the resolver refuses with the same generic
    # message.
    second = mut.approve_deployment_by_token(fake_info, input=ApproveByTokenInput(token=token))
    assert not second.ok
    assert second.errors[0].code == "PERMISSION_DENIED"


def test_approve_by_token_expired_rejected(
    org,
    app,
    env_requires_approval,
    actor,
    fake_info,
    permission_resolver,
    temporal_recorder,
    pubsub_recorder,
):
    _grant_deploy(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        start = _start_with_approval(mut, fake_info, app, env_requires_approval)

    minted = next(
        payload for topic, payload in pubsub_recorder if topic.startswith("deployment.approval_token.minted.")
    )
    token = minted["approval_token"]

    # Force-expire the token.
    Deployment.objects.filter(guid=str(start.data.id)).update(
        approval_token_expires_at=timezone.now() - timedelta(seconds=1)
    )

    result = mut.approve_deployment_by_token(fake_info, input=ApproveByTokenInput(token=token))
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_reject_by_token_transitions_to_failed(
    org,
    app,
    env_requires_approval,
    actor,
    fake_info,
    permission_resolver,
    temporal_recorder,
    pubsub_recorder,
):
    _grant_deploy(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        start = _start_with_approval(mut, fake_info, app, env_requires_approval)
    assert start.ok

    minted = next(
        payload for topic, payload in pubsub_recorder if topic.startswith("deployment.approval_token.minted.")
    )
    token = minted["approval_token"]

    result = mut.reject_deployment_by_token(
        fake_info, input=RejectByTokenInput(token=token, reason="compliance hold")
    )
    assert result.ok, result.errors
    assert result.data.status == Deployment.Status.FAILED.value

    deploy = Deployment.objects.get(guid=str(start.data.id))
    assert deploy.approval_token_used_at is not None
    assert deploy.failed_at is not None


def test_reject_by_token_after_approval_rejected(
    org,
    app,
    env_requires_approval,
    actor,
    fake_info,
    permission_resolver,
    temporal_recorder,
    pubsub_recorder,
):
    _grant_deploy(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        _start_with_approval(mut, fake_info, app, env_requires_approval)

    minted = next(
        payload for topic, payload in pubsub_recorder if topic.startswith("deployment.approval_token.minted.")
    )
    token = minted["approval_token"]

    # Approve first.
    approved = mut.approve_deployment_by_token(fake_info, input=ApproveByTokenInput(token=token))
    assert approved.ok

    # Then try to reject with the same (now-consumed) token.
    rejected = mut.reject_deployment_by_token(
        fake_info, input=RejectByTokenInput(token=token, reason="too late")
    )
    assert not rejected.ok
    assert rejected.errors[0].code == "PERMISSION_DENIED"
