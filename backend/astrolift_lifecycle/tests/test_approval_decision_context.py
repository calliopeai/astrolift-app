"""
#419 — approval decision-context + audit trail tests.

Covers the four scopes shipped in the issue:

  * abort/reject mutations require a non-empty ``reason`` at the
    boundary and persist it on the ``aborted_reason`` field
  * ``approveDeployment`` honours the Constance ``ALLOW_SELF_APPROVE_DEPLOYS``
    override (default False — same-user push+approve is blocked)
  * ``commit_message`` / ``commit_author`` are captured at
    ``start_deployment`` time and surface on the GraphQL type
  * ``astroliftDeploymentApprovalHistory`` returns the audit-log
    timeline scoped to one deployment
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.mutations import (
    AbortDeploymentInput,
    DeploymentByIdInput,
    LifecycleMutation,
    StartDeploymentInput,
)
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_lifecycle.schema.types import deployment_to_type
from astrolift_operations.audit_writer import write_audit_entry
from astrolift_operations.models import AuditEvent
from core.mutations import _audit_writer as _current_audit_writer  # noqa: F401
from core.mutations import register_audit_writer
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def persistent_audit_writer():
    """Install the AuditEvent-persisting writer for the duration of the
    test (the default suite-wide writer is the log-only one when prior
    tests have swapped it out — e.g. test_force_redeploy). Restored
    in teardown so we don't pollute neighbouring tests.

    Per the [[event-writer-fixture-restore]] memory: snapshot the
    writer module global before swapping; restoring to a stale
    reference (e.g. ``register_audit_writer(_log_audit_entry)``)
    breaks every later test that depends on persistence.
    """
    from core import mutations as _mod

    previous = _mod._audit_writer
    register_audit_writer(write_audit_entry)
    yield
    register_audit_writer(previous)


def _grant_all(resolver):
    for p in [
        Permission.APP_DEPLOY,
        Permission.APP_APPROVE_DEPLOY,
        Permission.APP_ROLLBACK,
        Permission.APP_READ,
    ]:
        resolver.grant(p)


def _tenant_for(org, actor):
    return tenant_context(
        TenantContext(organization_id=org.id, actor_user_id=actor.id),
    )


# ---------------------------------------------------------------------------
# Scope A — rejection-reason flow on reject_deployment
# ---------------------------------------------------------------------------


def test_reject_requires_reason(
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
    """Empty / whitespace reason is rejected at the boundary."""
    _grant_all(permission_resolver)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        start = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env_requires_approval.name,
                image_tag="v1.0.0",
            ),
        )
    assert start.ok

    with _tenant_for(org, other_actor):
        # Whitespace-only reason → still empty after .strip().
        rejected = mut.reject_deployment(
            fake_info_other,
            input=AbortDeploymentInput(id=start.data.id, reason="   \n\t  "),
        )

    assert not rejected.ok
    assert rejected.errors[0].code == "VALIDATION"
    assert rejected.errors[0].field == "reason"

    # Status should still be pending_approval — the reject short-
    # circuited before the state transition.
    Deployment.objects.get(guid=str(start.data.id))
    assert Deployment.objects.get(guid=str(start.data.id)).status == Deployment.Status.PENDING_APPROVAL.value


def test_reject_persists_reason_on_deployment_row(
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
    """Reason lands on Deployment.aborted_reason (the history sidebar
    surfaces it from there even after audit-log retention prunes the
    AuditEvent row)."""
    _grant_all(permission_resolver)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        start = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env_requires_approval.name,
                image_tag="v1.0.0",
            ),
        )

    with _tenant_for(org, other_actor):
        rejected = mut.reject_deployment(
            fake_info_other,
            input=AbortDeploymentInput(
                id=start.data.id,
                reason="image scan caught CVE-2025-1234",
            ),
        )

    assert rejected.ok, rejected.errors
    assert rejected.data.status == Deployment.Status.FAILED.value
    assert rejected.data.aborted_reason == "image scan caught CVE-2025-1234"

    row = Deployment.objects.get(guid=str(start.data.id))
    assert row.aborted_reason == "image scan caught CVE-2025-1234"


# ---------------------------------------------------------------------------
# Scope B — commit-message provenance on start_deployment
# ---------------------------------------------------------------------------


def test_start_deployment_captures_commit_provenance(
    org, app, env, actor, fake_info, permission_resolver, no_temporal
):
    _grant_all(permission_resolver)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="v1.0.0",
                commit_sha="abc123def456",
                commit_message="fix(api): null-check the response payload\n\nCloses #99",
                commit_author="Alice <alice@example.com>",
            ),
        )

    assert result.ok, result.errors
    deploy = Deployment.objects.get(guid=str(result.data.id))
    assert deploy.commit_sha == "abc123def456"
    assert deploy.commit_message.startswith("fix(api):")
    assert deploy.commit_author == "Alice <alice@example.com>"
    # And the GraphQL type surfaces them so the FE can render the
    # commit preview card.
    assert result.data.commit_message.startswith("fix(api):")
    assert result.data.commit_author == "Alice <alice@example.com>"


# ---------------------------------------------------------------------------
# Scope C — approval history query
# ---------------------------------------------------------------------------


def test_approval_history_returns_chronological_audit_trail(
    org,
    app,
    env_requires_approval,
    actor,
    other_actor,
    fake_info,
    fake_info_other,
    permission_resolver,
    no_temporal,
    persistent_audit_writer,
):
    """The history query returns the (start, reject) lifecycle events
    in order for a single deployment, with the rejection reason
    surfaced from the audit-log extras payload."""
    _grant_all(permission_resolver)
    mut = LifecycleMutation()
    qry = LifecycleQuery()

    with _tenant_for(org, actor):
        start = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env_requires_approval.name,
                image_tag="v1.0.0",
            ),
        )

    with _tenant_for(org, other_actor):
        rejected = mut.reject_deployment(
            fake_info_other,
            input=AbortDeploymentInput(id=start.data.id, reason="failed smoke test"),
        )
    assert rejected.ok, rejected.errors

    # Audit writer should have captured both lifecycle events. The
    # start row carries the new deployment id in ``data['deployment_id']``;
    # the reject row carries it on ``target_id``. Filter the union.
    deploy = Deployment.objects.get(guid=str(start.data.id))
    from django.db.models import Q

    audit_rows = AuditEvent.objects.filter(
        Q(target_id=str(deploy.guid)) | Q(data__deployment_id=str(deploy.guid)),
    ).order_by("occurred_at")
    assert audit_rows.count() >= 2, list(audit_rows.values("action", "target_id", "data"))

    with _tenant_for(org, actor):
        history = qry.astrolift_deployment_approval_history(
            fake_info,
            deployment_id=str(start.data.id),
        )

    actions = [entry.action for entry in history]
    assert "deployment.start" in actions
    assert "deployment.reject" in actions

    reject_entry = next(e for e in history if e.action == "deployment.reject")
    assert reject_entry.reason == "failed smoke test"


def test_approval_history_returns_empty_for_unknown_deployment(org, actor, fake_info, permission_resolver):
    _grant_all(permission_resolver)
    qry = LifecycleQuery()

    with _tenant_for(org, actor):
        out = qry.astrolift_deployment_approval_history(
            fake_info,
            deployment_id="00000000-0000-7000-8000-000000000000",
        )
    assert out == []


# ---------------------------------------------------------------------------
# Scope D — self-approval prevention + Constance override
# ---------------------------------------------------------------------------


def test_self_approval_blocked_by_default(
    org,
    app,
    env_requires_approval,
    actor,
    fake_info,
    permission_resolver,
    no_temporal,
):
    """Same user can't push and approve when the Constance flag is
    False (the default)."""
    _grant_all(permission_resolver)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        start = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env_requires_approval.name,
                image_tag="v1.0.0",
            ),
        )
        approve = mut.approve_deployment(fake_info, input=DeploymentByIdInput(id=start.data.id))

    assert not approve.ok
    assert approve.errors[0].code == "PRECONDITION"
    assert "another approver" in approve.errors[0].message


def test_self_approval_allowed_when_constance_flag_on(
    org,
    app,
    env_requires_approval,
    actor,
    fake_info,
    permission_resolver,
    temporal_recorder,
    monkeypatch,
):
    """When an operator flips ALLOW_SELF_APPROVE_DEPLOYS, the same
    user can push and approve. The workflow gets enqueued once the
    quorum is met."""
    _grant_all(permission_resolver)
    mut = LifecycleMutation()

    # Override the Constance lookup the resolver does. Patching the
    # helper directly is cheaper than mounting the Constance backend
    # with an override; the resolver only depends on the return value.
    monkeypatch.setattr(
        "astrolift_lifecycle.schema.mutations._self_approve_allowed",
        lambda: True,
    )

    with _tenant_for(org, actor):
        start = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env_requires_approval.name,
                image_tag="v1.0.0",
            ),
        )
        approve = mut.approve_deployment(fake_info, input=DeploymentByIdInput(id=start.data.id))

    assert approve.ok, approve.errors
    assert approve.data.status == Deployment.Status.PENDING.value
    assert len(temporal_recorder.starts) == 1


def test_deployment_type_triggered_by_me_when_viewer_matches(
    org, app, env, actor, fake_info, permission_resolver, no_temporal
):
    """``triggered_by_me`` is True when the viewer is the deployer,
    False otherwise. The FE uses this to hide the approve CTA."""
    _grant_all(permission_resolver)
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

    row = Deployment.objects.select_related("registered_app", "app_environment", "workload").get(
        guid=str(result.data.id)
    )

    # Viewer == deployer → True.
    self_view = deployment_to_type(row, viewer_user_id=actor.id)
    assert self_view.triggered_by_me is True
    # Other viewer → False.
    other_view = deployment_to_type(row, viewer_user_id=actor.id + 99999)
    assert other_view.triggered_by_me is False
    # No viewer at all → False (safe default for unauthenticated paths).
    no_view = deployment_to_type(row)
    assert no_view.triggered_by_me is False
