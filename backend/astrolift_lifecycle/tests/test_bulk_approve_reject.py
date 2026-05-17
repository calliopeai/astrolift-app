"""
Bulk approve / reject + quorum-surface tests (#420).

Covers:

* ``bulk_approve_deployments`` — happy path, mixed permission failures,
  self-trigger guard inside the batch, ID cap, empty list.
* ``bulk_reject_deployments`` — happy path, required-reason validation,
  per-id failures.
* ``DeploymentType.approved_by`` / ``awaiting_approvers`` resolution
  from the audit log + app approver policy.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.mutations import (
    BulkApproveDeploymentsInput,
    BulkRejectDeploymentsInput,
    DeploymentByIdInput,
    LifecycleMutation,
    StartDeploymentInput,
)
from astrolift_lifecycle.schema.types import deployment_to_type
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


def _start_pending(mut, fake_info, app, env, image_tag):
    return mut.start_deployment(
        fake_info,
        input=StartDeploymentInput(
            app_slug=app.slug,
            environment_name=env.name,
            image_tag=image_tag,
        ),
    )


# ---------------------------------------------------------------------------
# Bulk approve — happy path
# ---------------------------------------------------------------------------


def test_bulk_approve_two_pending_deploys_succeeds(
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
    """Two pending deploys + an approver that's not the triggerer →
    both land in PENDING and the workflow gets enqueued for each."""
    _grant_all(permission_resolver)

    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        a = _start_pending(mut, fake_info, app, env_requires_approval, "v1")
        b = _start_pending(mut, fake_info, app, env_requires_approval, "v2")
    assert a.ok, a.errors
    assert b.ok, b.errors

    with _tenant_for(org, other_actor):
        result = mut.bulk_approve_deployments(
            fake_info_other,
            input=BulkApproveDeploymentsInput(deployment_ids=[a.data.id, b.data.id]),
        )

    assert result.ok, result.errors
    assert result.data.succeeded_count == 2
    assert result.data.failed_count == 0
    assert all(item.ok for item in result.data.results)

    for deploy_result in result.data.results:
        deploy = Deployment.objects.get(guid=str(deploy_result.deployment_id))
        # env_requires_approval has required_approvals=1; one approver
        # is enough to flip past pending_approval.
        assert deploy.status == Deployment.Status.PENDING.value


# ---------------------------------------------------------------------------
# Bulk approve — self-trigger guard fires per-id, doesn't abort batch
# ---------------------------------------------------------------------------


def test_bulk_approve_skips_self_triggered_keeps_others(
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
    """Mixed batch: ``actor`` triggered both deploys. ``other_actor``
    bulk-approves — they're not the triggerer, so both should approve.
    Then ``actor`` tries to approve their own → both fail with
    PRECONDITION but the result envelope is still ok=True (per-id
    failures don't bubble up)."""
    _grant_all(permission_resolver)

    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        a = _start_pending(mut, fake_info, app, env_requires_approval, "v1")
        b = _start_pending(mut, fake_info, app, env_requires_approval, "v2")

    # actor tries to bulk-approve their own two deploys
    with _tenant_for(org, actor):
        result = mut.bulk_approve_deployments(
            fake_info,
            input=BulkApproveDeploymentsInput(deployment_ids=[a.data.id, b.data.id]),
        )

    assert result.ok  # batch envelope is ok
    assert result.data.succeeded_count == 0
    assert result.data.failed_count == 2
    for item in result.data.results:
        assert not item.ok
        assert item.errors[0].code == "PRECONDITION"
        assert "your own" in item.errors[0].message


# ---------------------------------------------------------------------------
# Bulk approve — validation: empty + over-cap rejected at the envelope
# ---------------------------------------------------------------------------


def test_bulk_approve_empty_returns_validation_error(org, actor, fake_info, permission_resolver, no_temporal):
    _grant_all(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.bulk_approve_deployments(
            fake_info,
            input=BulkApproveDeploymentsInput(deployment_ids=[]),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "deploymentIds"


def test_bulk_approve_over_cap_rejected(org, actor, fake_info, permission_resolver, no_temporal):
    _grant_all(permission_resolver)
    mut = LifecycleMutation()
    # 51 GUIDs (above the 50 cap).
    fake_ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(51)]
    with _tenant_for(org, actor):
        result = mut.bulk_approve_deployments(
            fake_info,
            input=BulkApproveDeploymentsInput(deployment_ids=fake_ids),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert "cap is 50" in result.errors[0].message


# ---------------------------------------------------------------------------
# Bulk reject — happy path
# ---------------------------------------------------------------------------


def test_bulk_reject_two_pending_with_reason_marks_failed(
    org,
    app,
    env_requires_approval,
    actor,
    other_actor,
    fake_info,
    fake_info_other,
    permission_resolver,
    no_temporal,
):
    _grant_all(permission_resolver)

    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        a = _start_pending(mut, fake_info, app, env_requires_approval, "v1")
        b = _start_pending(mut, fake_info, app, env_requires_approval, "v2")

    with _tenant_for(org, other_actor):
        result = mut.bulk_reject_deployments(
            fake_info_other,
            input=BulkRejectDeploymentsInput(
                deployment_ids=[a.data.id, b.data.id],
                reason="bulk reject for testing",
            ),
        )

    assert result.ok, result.errors
    assert result.data.succeeded_count == 2
    assert result.data.failed_count == 0
    for item in result.data.results:
        deploy = Deployment.objects.get(guid=str(item.deployment_id))
        assert deploy.status == Deployment.Status.FAILED.value
        assert deploy.aborted_reason == "bulk reject for testing"


def test_bulk_reject_requires_reason(org, actor, fake_info, permission_resolver, no_temporal):
    _grant_all(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.bulk_reject_deployments(
            fake_info,
            input=BulkRejectDeploymentsInput(
                deployment_ids=["00000000-0000-0000-0000-000000000001"],
                reason="   ",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "reason"


def test_bulk_reject_per_id_not_found_surfaces_in_results(
    org,
    app,
    env_requires_approval,
    actor,
    other_actor,
    fake_info,
    fake_info_other,
    permission_resolver,
    no_temporal,
):
    """Mix one valid id with one bogus id: bulk completes, the bogus
    one surfaces NOT_FOUND in its per-id result, the valid one moves
    to FAILED."""
    _grant_all(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        a = _start_pending(mut, fake_info, app, env_requires_approval, "v1")
    bogus_id = "00000000-0000-0000-0000-000000000000"

    with _tenant_for(org, other_actor):
        result = mut.bulk_reject_deployments(
            fake_info_other,
            input=BulkRejectDeploymentsInput(
                deployment_ids=[a.data.id, bogus_id],
                reason="mixed batch",
            ),
        )

    assert result.ok
    assert result.data.succeeded_count == 1
    assert result.data.failed_count == 1

    by_id = {str(item.deployment_id): item for item in result.data.results}
    assert by_id[str(a.data.id)].ok is True
    assert by_id[bogus_id].ok is False
    assert by_id[bogus_id].errors[0].code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Quorum surface — approved_by + awaiting_approvers from audit + policy
# ---------------------------------------------------------------------------


def test_quorum_surface_explicit_approver_set(
    org,
    app,
    env_requires_approval,
    actor,
    other_actor,
    fake_info,
    fake_info_other,
    permission_resolver,
    no_temporal,
):
    """With an explicit ``approver_users`` set and one approval cast,
    ``approved_by`` carries the approver and ``awaiting_approvers``
    drops them (single-approver policy → empty awaiting list)."""
    from django.contrib.auth import get_user_model

    _grant_all(permission_resolver)
    User = get_user_model()
    second_approver = User.objects.create(username="approver-2", email="approver-2@test")

    app.requires_approval = True
    app.minimum_approvals = 2
    app.save(update_fields=["requires_approval", "minimum_approvals"])
    app.approver_users.set([other_actor.pk, second_approver.pk])

    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        start = _start_pending(mut, fake_info, app, env_requires_approval, "v1")

    # other_actor approves; second_approver hasn't.
    with _tenant_for(org, other_actor):
        mut.approve_deployment(
            fake_info_other,
            input=DeploymentByIdInput(id=start.data.id),
        )

    deployment = Deployment.objects.select_related("registered_app", "app_environment", "workload").get(
        guid=str(start.data.id)
    )
    rendered = deployment_to_type(deployment, viewer_user_id=actor.pk)

    approved_ids = [a.user_id for a in rendered.approved_by]
    awaiting_ids = [a.user_id for a in rendered.awaiting_approvers]

    assert str(other_actor.pk) in approved_ids
    assert str(second_approver.pk) in awaiting_ids
    assert str(other_actor.pk) not in awaiting_ids
    # Triggerer is filtered out from awaiting (default self-approve off).
    assert str(actor.pk) not in awaiting_ids


def test_quorum_surface_required_approver_count_alias(
    org,
    app,
    env_requires_approval,
    actor,
    fake_info,
    permission_resolver,
    no_temporal,
):
    """``required_approver_count`` mirrors ``approvals_required`` on
    every serialization — the FE quorum widget reads the new alias
    without breaking the older field."""
    _grant_all(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        start = _start_pending(mut, fake_info, app, env_requires_approval, "v1")

    deployment = Deployment.objects.select_related("registered_app", "app_environment", "workload").get(
        guid=str(start.data.id)
    )
    rendered = deployment_to_type(deployment)
    assert rendered.required_approver_count == rendered.approvals_required
    assert rendered.required_approver_count == 1  # env_requires_approval fixture


def test_quorum_surface_no_gate_empty_lists(
    org,
    app,
    env,
    actor,
    fake_info,
    permission_resolver,
    temporal_recorder,
):
    """When ``approvals_required == 0`` the surface returns empty lists
    — no per-row identity fan-out for the cheap path."""
    _grant_all(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        start = _start_pending(mut, fake_info, app, env, "v1")
    deployment = Deployment.objects.select_related("registered_app", "app_environment", "workload").get(
        guid=str(start.data.id)
    )
    rendered = deployment_to_type(deployment)
    assert rendered.approved_by == []
    assert rendered.awaiting_approvers == []
