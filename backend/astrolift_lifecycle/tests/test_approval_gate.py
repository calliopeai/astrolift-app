"""
Approval-gate-on-app-strategy tests (#291).

The deploy resolver gates on the stricter of env-level
``required_approvals`` and app-level (``requires_approval`` +
``minimum_approvals``). When the app sets approver users / a team,
``approve_deployment`` refuses non-approvers with PERMISSION_DENIED
and a ``reject_deployment`` mutation flips the deploy to FAILED.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Member
from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.mutations import (
    DeploymentByIdInput,
    LifecycleMutation,
    StartDeploymentInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _grant_all(resolver):
    for p in (
        Permission.APP_DEPLOY,
        Permission.APP_APPROVE_DEPLOY,
        Permission.APP_ROLLBACK,
    ):
        resolver.grant(p)


def _tenant_for(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


# ---------------------------------------------------------------------------
# Happy path: deploy succeeds when an approver approves
# ---------------------------------------------------------------------------


def test_app_requires_approval_holds_deploy_pending(
    org, app, env, actor, fake_info, permission_resolver, temporal_recorder
):
    """env.required_approvals == 0 but app.requires_approval == True →
    deploy still lands in pending_approval and the workflow is not
    enqueued until approvers vote."""
    _grant_all(permission_resolver)
    app.requires_approval = True
    app.minimum_approvals = 1
    app.save(update_fields=["requires_approval", "minimum_approvals"])

    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="v1",
            ),
        )

    assert result.ok
    assert result.data.status == Deployment.Status.PENDING_APPROVAL.value
    deploy = Deployment.objects.get(guid=str(result.data.id))
    assert deploy.approvals_required == 1
    assert temporal_recorder.starts == []  # not enqueued


def test_app_approver_user_can_approve_and_workflow_starts(
    org,
    app,
    env,
    actor,
    other_actor,
    fake_info,
    fake_info_other,
    permission_resolver,
    temporal_recorder,
):
    _grant_all(permission_resolver)
    app.requires_approval = True
    app.minimum_approvals = 1
    app.save(update_fields=["requires_approval", "minimum_approvals"])
    app.approver_users.set([other_actor.pk])

    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        start = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="v1",
            ),
        )
    assert start.ok, start.errors

    with _tenant_for(org, other_actor):
        approve = mut.approve_deployment(
            fake_info_other,
            input=DeploymentByIdInput(id=start.data.id),
        )

    assert approve.ok, approve.errors
    assert approve.data.status == Deployment.Status.PENDING.value
    assert len(temporal_recorder.starts) == 1


def test_app_approver_team_member_can_approve(
    org,
    team,
    app,
    env,
    actor,
    other_actor,
    fake_info,
    fake_info_other,
    permission_resolver,
    temporal_recorder,
):
    _grant_all(permission_resolver)
    app.requires_approval = True
    app.minimum_approvals = 1
    app.approver_team = team
    app.save(update_fields=["requires_approval", "minimum_approvals", "approver_team"])
    # other_actor joins the approver team.
    Member.objects.create(
        user=other_actor,
        scope_kind=Member.ScopeKind.TEAM,
        scope_id=team.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )

    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        start = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="v1",
            ),
        )
    with _tenant_for(org, other_actor):
        approve = mut.approve_deployment(
            fake_info_other,
            input=DeploymentByIdInput(id=start.data.id),
        )

    assert approve.ok, approve.errors
    assert approve.data.status == Deployment.Status.PENDING.value


# ---------------------------------------------------------------------------
# Permission gate: non-approver refused
# ---------------------------------------------------------------------------


def test_non_approver_user_cannot_approve(
    org,
    app,
    env,
    actor,
    other_actor,
    fake_info,
    fake_info_other,
    permission_resolver,
    no_temporal,
):
    """Approver set lists nobody else; other_actor holds the
    APP_APPROVE_DEPLOY perm but isn't in the eligibility set."""
    _grant_all(permission_resolver)
    app.requires_approval = True
    app.minimum_approvals = 1
    # actor is the deployer; pick a third party — neither in the
    # approver_users set nor a member of approver_team.
    app.approver_users.set([])  # explicit empty
    # Add a *different* approver — not other_actor.
    from django.contrib.auth import get_user_model

    User = get_user_model()
    chosen = User.objects.create(username="chosen", email="chosen@test")
    app.approver_users.set([chosen.pk])
    app.save(update_fields=["requires_approval", "minimum_approvals"])

    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        start = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="v1",
            ),
        )
    with _tenant_for(org, other_actor):
        approve = mut.approve_deployment(
            fake_info_other,
            input=DeploymentByIdInput(id=start.data.id),
        )

    assert not approve.ok
    assert approve.errors[0].code == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# Threshold: deploy stays pending below quorum
# ---------------------------------------------------------------------------


def test_minimum_approvals_two_holds_after_first_vote(
    org,
    app,
    env,
    actor,
    other_actor,
    fake_info,
    fake_info_other,
    permission_resolver,
    temporal_recorder,
):
    _grant_all(permission_resolver)
    from django.contrib.auth import get_user_model

    User = get_user_model()
    approver_b = User.objects.create(username="approver-b", email="approver-b@test")

    app.requires_approval = True
    app.minimum_approvals = 2
    app.save(update_fields=["requires_approval", "minimum_approvals"])
    app.approver_users.set([other_actor.pk, approver_b.pk])

    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        start = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="v1",
            ),
        )
    with _tenant_for(org, other_actor):
        first = mut.approve_deployment(
            fake_info_other,
            input=DeploymentByIdInput(id=start.data.id),
        )

    assert first.ok
    deploy = Deployment.objects.get(guid=str(start.data.id))
    # Below quorum: stays pending_approval, no workflow.
    assert deploy.status == Deployment.Status.PENDING_APPROVAL.value
    assert deploy.approvals_received == 1
    assert temporal_recorder.starts == []


# ---------------------------------------------------------------------------
# Reject path: in-band reject halts workflow
# ---------------------------------------------------------------------------


def test_reject_deployment_marks_failed(
    org,
    app,
    env,
    actor,
    other_actor,
    fake_info,
    fake_info_other,
    permission_resolver,
    no_temporal,
):
    _grant_all(permission_resolver)
    app.requires_approval = True
    app.minimum_approvals = 1
    app.save(update_fields=["requires_approval", "minimum_approvals"])
    app.approver_users.set([other_actor.pk])

    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        start = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="v1",
            ),
        )
    with _tenant_for(org, other_actor):
        reject = mut.reject_deployment(
            fake_info_other,
            input=DeploymentByIdInput(id=start.data.id),
        )

    assert reject.ok, reject.errors
    assert reject.data.status == Deployment.Status.FAILED.value


def test_reject_deployment_refuses_self_reject(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    no_temporal,
):
    _grant_all(permission_resolver)
    app.requires_approval = True
    app.minimum_approvals = 1
    app.save(update_fields=["requires_approval", "minimum_approvals"])

    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        start = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="v1",
            ),
        )
        reject = mut.reject_deployment(
            fake_info,
            input=DeploymentByIdInput(id=start.data.id),
        )

    assert not reject.ok
    assert reject.errors[0].code == "PRECONDITION"


def test_env_required_approvals_still_gates_when_app_unset(
    org,
    app,
    env_requires_approval,
    actor,
    other_actor,
    fake_info,
    fake_info_other,
    permission_resolver,
    temporal_recorder,
):
    """Backwards compatibility: env-level required_approvals still
    holds the deploy even when the app has requires_approval=False."""
    _grant_all(permission_resolver)
    assert app.requires_approval is False  # default

    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        start = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env_requires_approval.name,
                image_tag="v1",
            ),
        )
    assert start.data.status == Deployment.Status.PENDING_APPROVAL.value
    deploy = Deployment.objects.get(guid=str(start.data.id))
    assert deploy.approvals_required == env_requires_approval.required_approvals
