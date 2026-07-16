"""
GraphQL deployment control plane mutations.

These tests exercise ``LifecycleMutation`` resolver methods directly
(bypassing the Strawberry runtime). The control-plane logic is what
matters here: state-machine transitions, single-flight workflow ids,
approval gates, and the WorkflowRun mirror row. The Temporal facade
is replaced by ``temporal_recorder`` so we can assert *what* would
have been enqueued without spinning up a server.

What we don't test here:
- The Strawberry execution path (covered separately by integration
  tests that hit /graphql).
- Real Temporal worker behavior (covered by the Temporal time-skipping
  test env in workflow tests).
"""

from __future__ import annotations

import pytest

from astrolift_clusters.models import TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_lifecycle.schema.mutations import (
    AbortDeploymentInput,
    DeploymentByIdInput,
    LifecycleMutation,
    PromoteDeploymentInput,
    StartDeploymentInput,
)
from astrolift_operations.models import WorkflowRun
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _grant_all(resolver, org_id):
    for p in [
        Permission.APP_DEPLOY,
        Permission.APP_APPROVE_DEPLOY,
        Permission.APP_ROLLBACK,
    ]:
        resolver.grant(p)


def _tenant_for(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


# ---------------------------------------------------------------------------
# start_deployment
# ---------------------------------------------------------------------------


def test_start_deployment_creates_pending_and_enqueues(
    org, app, env, actor, fake_info, permission_resolver, temporal_recorder
):
    _grant_all(permission_resolver, org.id)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="v1.0.0",
                trigger_kind="manual",
            ),
        )

    assert result.ok, result.errors
    assert result.data.status == Deployment.Status.PENDING.value

    deploy = Deployment.objects.get(guid=str(result.data.id))
    assert deploy.image_tag == "v1.0.0"
    assert deploy.workflow_run_id is not None

    # Single-flight id shape per spec
    (name, args, workflow_id) = temporal_recorder.starts[0]
    assert name == "DeployAppWorkflow"
    assert workflow_id == f"DeployAppWorkflow-{app.guid}-{env.guid}"


def test_start_deployment_pending_approval_when_required(
    org,
    app,
    env_requires_approval,
    actor,
    fake_info,
    permission_resolver,
    temporal_recorder,
):
    _grant_all(permission_resolver, org.id)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env_requires_approval.name,
                image_tag="v1.0.0",
            ),
        )

    assert result.ok
    assert result.data.status == Deployment.Status.PENDING_APPROVAL.value
    # Workflow should NOT be enqueued until approved.
    assert temporal_recorder.starts == []


def test_start_deployment_refuses_when_paused(
    org, app, env, actor, fake_info, permission_resolver, no_temporal
):
    _grant_all(permission_resolver, org.id)
    env.deploys_paused = True
    env.save(update_fields=["deploys_paused"])

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

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_start_deployment_refuses_job_family_agent_only_app(
    org, app, env, actor, fake_info, permission_resolver, no_temporal
):
    """#1093: an app whose manifest declares ONLY Job-family (task) agent
    workloads renders zero deployable resources — refuse synchronously with
    a pointer at registerAgentRepo instead of async-failing in pre-flight
    (the live repro: pending→failed with an empty aborted_reason). Covers
    both the implicit default (run_family omitted → task) and the explicit
    ``run_family = "task"`` spelling."""
    _grant_all(permission_resolver, org.id)
    app.manifest_raw = (
        'name = "hello"\n\n'
        '[[workloads]]\nname = "helper-agent"\nkind = "agent"\n\n'
        '[[workloads]]\nname = "batch-agent"\nkind = "agent"\nrun_family = "task"\n'
    )
    app.save(update_fields=["manifest_raw", "updated_at", "version"])

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

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert "registerAgentRepo" in result.errors[0].message
    # No Deployment row minted — the refusal is fully synchronous.
    assert Deployment.objects.filter(registered_app=app).count() == 0


def test_start_deployment_allows_service_family_agent_only_app(
    org, app, env, actor, fake_info, permission_resolver, temporal_recorder
):
    """A service-family agent IS deployable — the manifest renderer emits a
    standing Deployment (+Service/HPA) for ``kind=agent`` with
    ``run_family = "service"`` (#1012/#1027) — so an app whose workloads are
    all service-family agents must deploy, not be refused (#1093)."""
    _grant_all(permission_resolver, org.id)
    app.manifest_raw = (
        'name = "hello"\n\n'
        '[[workloads]]\nname = "agent-svc"\nkind = "agent"\nrun_family = "service"\n'
    )
    app.save(update_fields=["manifest_raw", "updated_at", "version"])

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

    assert result.ok, result.errors
    assert result.data.status == Deployment.Status.PENDING.value


def test_start_deployment_allows_mixed_manifest_with_agent(
    org, app, env, actor, fake_info, permission_resolver, temporal_recorder
):
    """A manifest that carries an agent NEXT TO a deployable workload still
    deploys — only the agent-ONLY shape is refused (#1093)."""
    _grant_all(permission_resolver, org.id)
    app.manifest_raw = (
        'name = "hello"\n\n'
        '[[workloads]]\nname = "web"\nkind = "deployment"\n\n'
        '[[workloads]]\nname = "helper-agent"\nkind = "agent"\n'
    )
    app.save(update_fields=["manifest_raw", "updated_at", "version"])

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

    assert result.ok, result.errors


def test_start_deployment_unknown_trigger_kind(
    org, app, env, actor, fake_info, permission_resolver, no_temporal
):
    _grant_all(permission_resolver, org.id)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="v1.0.0",
                trigger_kind="bogus",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "triggerKind"


def test_start_deployment_unknown_app(org, env, actor, fake_info, permission_resolver, no_temporal):
    _grant_all(permission_resolver, org.id)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug="does-not-exist",
                environment_name=env.name,
                image_tag="v1.0.0",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# approve_deployment
# ---------------------------------------------------------------------------


def test_approve_deployment_starts_workflow_when_quorum_met(
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
    _grant_all(permission_resolver, org.id)
    mut = LifecycleMutation()

    # Original deployer creates a pending_approval deployment.
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
    assert temporal_recorder.starts == []  # not enqueued yet

    # A different actor approves.
    with _tenant_for(org, other_actor):
        approve = mut.approve_deployment(fake_info_other, input=DeploymentByIdInput(id=start.data.id))
    assert approve.ok, approve.errors
    assert approve.data.status == Deployment.Status.PENDING.value

    # Workflow now enqueued exactly once.
    assert len(temporal_recorder.starts) == 1


def test_approve_deployment_refuses_self_approval(
    org,
    app,
    env_requires_approval,
    actor,
    fake_info,
    permission_resolver,
    no_temporal,
):
    _grant_all(permission_resolver, org.id)
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


# ---------------------------------------------------------------------------
# abort_deployment
# ---------------------------------------------------------------------------


def test_abort_signals_workflow_and_marks_failed(
    org, app, env, actor, fake_info, permission_resolver, temporal_recorder
):
    _grant_all(permission_resolver, org.id)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        start = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="v1.0.0",
            ),
        )
        abort = mut.abort_deployment(
            fake_info,
            input=AbortDeploymentInput(id=start.data.id, reason="bad image, halting"),
        )

    assert abort.ok, abort.errors
    assert abort.data.status == Deployment.Status.FAILED.value
    # Reason persisted on the row so the history sidebar can render it.
    assert abort.data.aborted_reason == "bad image, halting"

    # Should have signalled the in-flight workflow with 'abort'.
    assert any(s[1] == "abort" for s in temporal_recorder.signals)


def test_abort_requires_reason(org, app, env, actor, fake_info, permission_resolver, temporal_recorder):
    """#419 — empty / whitespace reason is rejected at the boundary."""
    _grant_all(permission_resolver, org.id)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        start = mut.start_deployment(
            fake_info,
            input=StartDeploymentInput(
                app_slug=app.slug,
                environment_name=env.name,
                image_tag="v1.0.0",
            ),
        )
        abort = mut.abort_deployment(
            fake_info,
            input=AbortDeploymentInput(id=start.data.id, reason="   "),
        )

    assert not abort.ok
    assert abort.errors[0].code == "VALIDATION"
    assert abort.errors[0].field == "reason"
    # No signal fired because validation short-circuited before the
    # workflow call site.
    assert not any(s[1] == "abort" for s in temporal_recorder.signals)


def test_abort_refuses_when_not_in_flight(
    org, app, env, actor, fake_info, permission_resolver, temporal_recorder
):
    _grant_all(permission_resolver, org.id)
    mut = LifecycleMutation()

    deploy = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user=actor,
        trigger_kind="manual",
        status=Deployment.Status.RUNNING.value,
        image_tag="v1.0.0",
    )

    with _tenant_for(org, actor):
        abort = mut.abort_deployment(
            fake_info,
            input=AbortDeploymentInput(id=deploy.guid, reason="operator override"),
        )

    assert not abort.ok
    assert abort.errors[0].code == "PRECONDITION"


def test_abort_failed_deployment_dismisses_and_skips_signal(
    org, app, env, actor, fake_info, permission_resolver, temporal_recorder
):
    """A failed deploy is dismissable: there is no workflow left to
    stop, so abort soft-deletes the record (drops it off the lists)
    and never touches Temporal."""
    _grant_all(permission_resolver, org.id)
    mut = LifecycleMutation()

    deploy = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user=actor,
        trigger_kind="manual",
        status=Deployment.Status.FAILED.value,
        image_tag="v1.0.0",
    )

    with _tenant_for(org, actor):
        abort = mut.abort_deployment(
            fake_info,
            input=AbortDeploymentInput(id=deploy.guid, reason="dismissing failed run"),
        )

    assert abort.ok, abort.errors
    # Reason persisted; record soft-deleted so it leaves every list.
    assert abort.data.aborted_reason == "dismissing failed run"
    assert not Deployment.objects.filter(guid=deploy.guid).exists()
    assert Deployment.all_objects.get(guid=deploy.guid).deleted_at is not None
    # No Temporal interaction — nothing was running.
    assert not temporal_recorder.signals
    assert not temporal_recorder.terminates


# ---------------------------------------------------------------------------
# delete_deployment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        Deployment.Status.FAILED.value,
        Deployment.Status.SUPERSEDED.value,
        Deployment.Status.ROLLED_BACK.value,
    ],
)
def test_delete_terminal_deployment_soft_deletes(
    status, org, app, env, actor, fake_info, permission_resolver, no_temporal
):
    """Terminal deploys (failed / superseded / rolled_back) soft-delete
    so they disappear from the deployments lists."""
    _grant_all(permission_resolver, org.id)
    mut = LifecycleMutation()

    deploy = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user=actor,
        trigger_kind="manual",
        status=status,
        image_tag="v1.0.0",
    )

    with _tenant_for(org, actor):
        result = mut.delete_deployment(
            fake_info,
            input=DeploymentByIdInput(id=deploy.guid),
        )

    assert result.ok, result.errors
    assert not Deployment.objects.filter(guid=deploy.guid).exists()
    assert Deployment.all_objects.get(guid=deploy.guid).deleted_at is not None


def test_delete_running_deployment_supersedes_and_logs(
    org, app, env, actor, fake_info, permission_resolver, no_temporal
):
    """A running deploy is retired by superseding it (a legal state
    transition) and writing an operator-attributed log note. The row is
    NOT soft-deleted — the superseded revision stays a rollback target —
    and no k8s teardown is attempted here."""
    _grant_all(permission_resolver, org.id)
    mut = LifecycleMutation()

    deploy = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user=actor,
        trigger_kind="manual",
        status=Deployment.Status.RUNNING.value,
        image_tag="v1.0.0",
    )

    with _tenant_for(org, actor):
        result = mut.delete_deployment(
            fake_info,
            input=DeploymentByIdInput(id=deploy.guid),
        )

    assert result.ok, result.errors
    deploy.refresh_from_db()
    assert deploy.status == Deployment.Status.SUPERSEDED.value
    assert deploy.deleted_at is None
    # An operator-attributed note was written explaining the supersede.
    note = deploy.logs.filter(message="superseded via delete_deployment").first()
    assert note is not None
    assert note.by_user_id == actor.id


def test_delete_refuses_in_flight_deployment(
    org, app, env, actor, fake_info, permission_resolver, no_temporal
):
    """In-flight deploys can't be deleted — abort them first."""
    _grant_all(permission_resolver, org.id)
    mut = LifecycleMutation()

    deploy = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user=actor,
        trigger_kind="manual",
        status=Deployment.Status.PENDING.value,
        image_tag="v1.0.0",
    )

    with _tenant_for(org, actor):
        result = mut.delete_deployment(
            fake_info,
            input=DeploymentByIdInput(id=deploy.guid),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    # Untouched.
    deploy.refresh_from_db()
    assert deploy.status == Deployment.Status.PENDING.value
    assert deploy.deleted_at is None


def test_delete_unknown_deployment_not_found(
    org, app, env, actor, fake_info, permission_resolver, no_temporal
):
    _grant_all(permission_resolver, org.id)
    mut = LifecycleMutation()

    import uuid

    with _tenant_for(org, actor):
        result = mut.delete_deployment(
            fake_info,
            input=DeploymentByIdInput(id=str(uuid.uuid4())),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# rollback_deployment
# ---------------------------------------------------------------------------


def test_rollback_creates_new_deploy_from_prior_revision(
    org, app, env, actor, fake_info, permission_resolver, temporal_recorder
):
    _grant_all(permission_resolver, org.id)
    mut = LifecycleMutation()

    # Prior superseded deploy: this is what we should roll back to.
    Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user=actor,
        trigger_kind="manual",
        status=Deployment.Status.SUPERSEDED.value,
        image_tag="v0.9.0",
        config_snapshot={"replicas": 3},
    )
    running = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user=actor,
        trigger_kind="manual",
        status=Deployment.Status.RUNNING.value,
        image_tag="v1.0.0",
    )

    with _tenant_for(org, actor):
        result = mut.rollback_deployment(fake_info, input=DeploymentByIdInput(id=running.guid))

    assert result.ok, result.errors
    assert result.data.image_tag == "v0.9.0"
    assert result.data.trigger_kind == "rollback"

    running.refresh_from_db()
    assert running.status == Deployment.Status.ROLLED_BACK.value

    # RollbackDeploymentWorkflow id includes the new deploy's guid.
    assert any(s[0] == "RollbackDeploymentWorkflow" for s in temporal_recorder.starts)


def test_rollback_refuses_without_prior(org, app, env, actor, fake_info, permission_resolver, no_temporal):
    _grant_all(permission_resolver, org.id)
    mut = LifecycleMutation()

    running = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user=actor,
        trigger_kind="manual",
        status=Deployment.Status.RUNNING.value,
        image_tag="v1.0.0",
    )

    with _tenant_for(org, actor):
        result = mut.rollback_deployment(fake_info, input=DeploymentByIdInput(id=running.guid))

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


# ---------------------------------------------------------------------------
# redeploy_app
# ---------------------------------------------------------------------------


def test_redeploy_clones_image_and_starts_workflow(
    org, app, env, actor, fake_info, permission_resolver, temporal_recorder
):
    _grant_all(permission_resolver, org.id)
    mut = LifecycleMutation()

    source = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user=actor,
        trigger_kind="manual",
        status=Deployment.Status.RUNNING.value,
        image_tag="v1.0.0",
        image_digest="sha256:abc",
        config_snapshot={"replicas": 2},
    )

    with _tenant_for(org, actor):
        result = mut.redeploy_app(fake_info, input=DeploymentByIdInput(id=source.guid))

    assert result.ok, result.errors
    assert result.data.image_tag == "v1.0.0"
    assert result.data.image_digest == "sha256:abc"
    assert result.data.status == Deployment.Status.PENDING.value
    assert WorkflowRun.objects.filter(workflow_kind="DeployAppWorkflow").exists()


# ---------------------------------------------------------------------------
# promote_deployment (#1041, astrolift-cli#40)
# ---------------------------------------------------------------------------


def _running_in(env, app, actor, *, image_tag="v1.0.0", image_digest="sha256:abc"):
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user=actor,
        trigger_kind="manual",
        status=Deployment.Status.RUNNING.value,
        image_tag=image_tag,
        image_digest=image_digest,
        config_snapshot={"replicas": 2},
    )


def test_promote_clones_running_image_and_starts_workflow(
    org, app, env, env_requires_approval, actor, fake_info, permission_resolver, temporal_recorder
):
    """staging (source, running) → prod (target, no approvals): the target
    gets a PROMOTION deployment carrying the source's exact image+config,
    ``promoted_from`` lineage, and a DeployAppWorkflow keyed on the target."""
    _grant_all(permission_resolver, org.id)
    source = _running_in(env_requires_approval, app, actor)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.promote_deployment(
            fake_info,
            input=PromoteDeploymentInput(
                app_slug=app.slug,
                source_environment_name=env_requires_approval.name,
                target_environment_name=env.name,
            ),
        )

    assert result.ok, result.errors
    assert result.data.trigger_kind == "promotion"
    assert result.data.image_tag == "v1.0.0"
    assert result.data.image_digest == "sha256:abc"
    assert result.data.status == Deployment.Status.PENDING.value

    new_deploy = Deployment.objects.get(guid=str(result.data.id))
    assert new_deploy.app_environment_id == env.id
    assert new_deploy.promoted_from_id == source.id
    assert new_deploy.config_snapshot == {"replicas": 2}

    (name, _args, workflow_id) = temporal_recorder.starts[0]
    assert name == "DeployAppWorkflow"
    assert workflow_id == f"DeployAppWorkflow-{app.guid}-{env.guid}"


def test_promote_into_approval_gated_target_waits_for_approval(
    org, app, env, env_requires_approval, actor, fake_info, permission_resolver, temporal_recorder
):
    """prod (source) → staging (target, requires 1 approval): lands
    pending_approval and does NOT enqueue the apply workflow."""
    _grant_all(permission_resolver, org.id)
    _running_in(env, app, actor)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.promote_deployment(
            fake_info,
            input=PromoteDeploymentInput(
                app_slug=app.slug,
                source_environment_name=env.name,
                target_environment_name=env_requires_approval.name,
            ),
        )

    assert result.ok, result.errors
    assert result.data.status == Deployment.Status.PENDING_APPROVAL.value
    assert temporal_recorder.starts == []


def test_promote_rejected_to_same_environment(
    org, app, env, actor, fake_info, permission_resolver, no_temporal
):
    """validate_promotion guards the no-op misclick: source == target env."""
    _grant_all(permission_resolver, org.id)
    _running_in(env, app, actor)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.promote_deployment(
            fake_info,
            input=PromoteDeploymentInput(
                app_slug=app.slug,
                source_environment_name=env.name,
                target_environment_name=env.name,
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"


def test_promote_refuses_paused_target(
    org, app, env, env_requires_approval, actor, fake_info, permission_resolver, no_temporal
):
    _grant_all(permission_resolver, org.id)
    _running_in(env_requires_approval, app, actor)
    env.deploys_paused = True
    env.save(update_fields=["deploys_paused"])
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.promote_deployment(
            fake_info,
            input=PromoteDeploymentInput(
                app_slug=app.slug,
                source_environment_name=env_requires_approval.name,
                target_environment_name=env.name,
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_promote_refuses_when_source_has_no_running_deployment(
    org, app, env, env_requires_approval, actor, fake_info, permission_resolver, no_temporal
):
    _grant_all(permission_resolver, org.id)
    mut = LifecycleMutation()

    with _tenant_for(org, actor):
        result = mut.promote_deployment(
            fake_info,
            input=PromoteDeploymentInput(
                app_slug=app.slug,
                source_environment_name=env_requires_approval.name,
                target_environment_name=env.name,
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_promote_denies_cross_org(
    org, app, env, actor, provider_plugin, fake_info, permission_resolver, no_temporal
):
    """Caller is in org A; the app + envs live in org B. Org-scoped
    resolution must treat the cross-org slug as not-found — never promote
    another tenant's app (#1042)."""
    _grant_all(permission_resolver, org.id)

    other_org = Organization.objects.create(name="Globex", slug="globex-test")
    other_team = Team.objects.create(organization=other_org, name="Ops", slug="ops")
    other_project = Project.objects.create(organization=other_org, team=other_team, name="P", slug="p")
    other_cluster = TenantCluster.objects.create(
        organization=other_org,
        name="b-cluster",
        slug="b-cluster",
        provider_plugin=provider_plugin,
        provider_config={},
        endpoint="https://b.cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    other_app = RegisteredApp.objects.create(
        organization=other_org,
        project=other_project,
        team=other_team,
        name="Secret",
        slug="secret-app",
        provisioning_status="ready",
    )
    other_src = AppEnvironment.objects.create(
        registered_app=other_app, tenant_cluster=other_cluster, name="stg", required_approvals=0
    )
    AppEnvironment.objects.create(
        registered_app=other_app, tenant_cluster=other_cluster, name="prd", required_approvals=0
    )
    _running_in(other_src, other_app, actor)

    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.promote_deployment(
            fake_info,
            input=PromoteDeploymentInput(
                app_slug=other_app.slug,
                source_environment_name="stg",
                target_environment_name="prd",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    # Nothing was created in the other org.
    assert Deployment.objects.filter(app_environment__name="prd").count() == 0
