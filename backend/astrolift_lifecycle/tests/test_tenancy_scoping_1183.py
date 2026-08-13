"""Cross-tenant scoping regression tests for astrolift_lifecycle (#1183).

Every by-guid / by-slug resolver in this app must constrain the lookup to
the caller's org. ``@tenant_scoped`` only *asserts* a tenant exists — it
does not filter — and ``@require_permission`` checks the caller's role in
their OWN org, not row ownership. So a by-guid/slug fetch without an
explicit org clause returns another org's row (guids are globally unique;
slugs are unique only within an org).

These tests pin, for the distinct object shapes and the highest-blast-radius
mutations:

* the fail-closed ``NOT_FOUND`` (and *no side effect*) for a sibling-org
  caller, and
* the happy path for the owning org.

Coverage: approve/delete deployment (Deployment by-id write), deregister
(RegisteredApp destroy + no teardown workflow cross-org), create/rotate
deploy token (secret — none minted / rotated cross-org), the caller-org-only
metrics + health aggregates, and the ``ci_deploy`` slug-collision path
(a deploy token drives *its own* app, never a sibling org's identically
slugged app).
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.deploy_tokens import issue_token
from astrolift_lifecycle.models import AppEnvironment, Deployment, DeployToken
from astrolift_lifecycle.schema.mutations import (
    CreateDeployTokenInput,
    DeploymentByIdInput,
    DeregisterAppInput,
    LifecycleMutation,
    RotateDeployTokenInput,
)
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_registry.models import RegisteredApp, Workload
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

User = get_user_model()

_MANIFEST_WITH_WEB = """
name = "shared-slug"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "web"
  is_primary = true
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(org, actor=None):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=(actor.id if actor else None)))


def _sibling_org():
    """A second, unrelated org with its own team/project. The caller lives
    here in the cross-org cases; the resource under test lives in the
    conftest ``org``."""
    org = Organization.objects.create(name="Globex", slug="globex-1183")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-1183")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-1183")
    return org, team, project


def _pending_approval_deployment(app, env, triggered_by):
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user=triggered_by,
        trigger_kind="manual",
        status=Deployment.Status.PENDING_APPROVAL.value,
        image_tag="v1",
        approvals_required=1,
        approvals_received=0,
    )


# ---------------------------------------------------------------------------
# approve_deployment — Deployment by-id write
# ---------------------------------------------------------------------------


def test_approve_deployment_cross_org_not_found(app, env, actor, other_actor, permission_resolver):
    """A caller in another org must not approve this org's deployment, and
    the deployment's state must be untouched."""
    deployment = _pending_approval_deployment(app, env, triggered_by=other_actor)
    sibling_org, _, _ = _sibling_org()
    permission_resolver.grant(Permission.APP_APPROVE_DEPLOY)

    from types import SimpleNamespace

    caller = User.objects.create(username="sib-approver", email="sib@x")
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=caller, auth=None)))
    with _ctx(sibling_org, caller):
        result = LifecycleMutation().approve_deployment(
            info, input=DeploymentByIdInput(id=str(deployment.guid))
        )

    assert not result.ok
    assert any(e.code == "NOT_FOUND" for e in result.errors)
    deployment.refresh_from_db()
    assert deployment.status == Deployment.Status.PENDING_APPROVAL.value
    assert deployment.approvals_received == 0


def test_approve_deployment_same_org_works(
    app, env, actor, other_actor, fake_info, permission_resolver, temporal_recorder
):
    """The owning org's approver clears the gate — sanity check the org
    filter isn't over-broad."""
    deployment = _pending_approval_deployment(app, env, triggered_by=other_actor)
    permission_resolver.grant(Permission.APP_APPROVE_DEPLOY)

    with _ctx(app.organization, actor):
        result = LifecycleMutation().approve_deployment(
            fake_info, input=DeploymentByIdInput(id=str(deployment.guid))
        )

    # The owning-org approver clears the gate: the mutation succeeds and,
    # with a quorum of 1, dispatches the deploy workflow. We assert the
    # observable dispatch rather than an internal counter so the test stays
    # robust to the vote-accounting internals.
    assert result.ok, result.errors
    assert any(name == "DeployAppWorkflow" for name, _args, _wf in temporal_recorder.starts)


# ---------------------------------------------------------------------------
# delete_deployment — Deployment by-id write
# ---------------------------------------------------------------------------


def test_delete_deployment_cross_org_not_found(app, env, actor, permission_resolver):
    deployment = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind="manual",
        status=Deployment.Status.FAILED.value,
        image_tag="v1",
    )
    sibling_org, _, _ = _sibling_org()
    permission_resolver.grant(Permission.APP_DEPLOY)

    from types import SimpleNamespace

    caller = User.objects.create(username="sib-deleter", email="sibd@x")
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=caller, auth=None)))
    with _ctx(sibling_org, caller):
        result = LifecycleMutation().delete_deployment(
            info, input=DeploymentByIdInput(id=str(deployment.guid))
        )

    assert not result.ok
    assert any(e.code == "NOT_FOUND" for e in result.errors)
    deployment.refresh_from_db()
    assert deployment.deleted_at is None, "cross-org caller soft-deleted another org's deployment"


def test_delete_deployment_same_org_works(app, env, actor, fake_info, permission_resolver):
    deployment = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind="manual",
        status=Deployment.Status.FAILED.value,
        image_tag="v1",
    )
    permission_resolver.grant(Permission.APP_DEPLOY)

    with _ctx(app.organization, actor):
        result = LifecycleMutation().delete_deployment(
            fake_info, input=DeploymentByIdInput(id=str(deployment.guid))
        )

    assert result.ok, result.errors
    deployment.refresh_from_db()
    assert deployment.deleted_at is not None


# ---------------------------------------------------------------------------
# deregister_astrolift_app — RegisteredApp destroy (workflow side effect)
# ---------------------------------------------------------------------------


def test_deregister_cross_org_not_found_and_no_workflow(app, permission_resolver, temporal_recorder):
    """A sibling-org caller who knows the slug + name must not be able to
    kick the teardown workflow. The org gate has to precede the side
    effect, so ``start_workflow`` must never fire."""
    sibling_org, _, _ = _sibling_org()
    permission_resolver.grant(Permission.APP_DELETE)

    from types import SimpleNamespace

    caller = User.objects.create(username="sib-destroyer", email="sibx@x")
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=caller, auth=None)))
    with _ctx(sibling_org, caller):
        result = LifecycleMutation().deregister_astrolift_app(
            info, input=DeregisterAppInput(app_slug=app.slug, confirm_name=app.name)
        )

    assert not result.ok
    assert any(e.code == "NOT_FOUND" for e in result.errors)
    assert temporal_recorder.starts == [], "teardown workflow started for a cross-org caller"


def test_deregister_same_org_starts_workflow(app, actor, fake_info, permission_resolver, temporal_recorder):
    permission_resolver.grant(Permission.APP_DELETE)

    with _ctx(app.organization, actor):
        result = LifecycleMutation().deregister_astrolift_app(
            fake_info, input=DeregisterAppInput(app_slug=app.slug, confirm_name=app.name)
        )

    assert result.ok, result.errors
    assert any(name == "DeregisterAppWorkflow" for name, _args, _wf in temporal_recorder.starts)


# ---------------------------------------------------------------------------
# create_deploy_token — secret mint
# ---------------------------------------------------------------------------


def test_create_deploy_token_cross_org_mints_nothing(app, permission_resolver):
    """A sibling-org caller must not mint a working deploy token against
    this org's app — the secret is never created."""
    sibling_org, _, _ = _sibling_org()
    permission_resolver.grant(Permission.APP_UPDATE)

    from types import SimpleNamespace

    caller = User.objects.create(username="sib-minter", email="sibm@x")
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=caller, auth=None)))
    with _ctx(sibling_org, caller):
        result = LifecycleMutation().create_deploy_token(
            info, input=CreateDeployTokenInput(app_slug=app.slug, name="evil-ci")
        )

    assert not result.ok
    assert any(e.code == "NOT_FOUND" for e in result.errors)
    assert DeployToken.objects.filter(registered_app=app).count() == 0


def test_create_deploy_token_same_org_works(app, actor, fake_info, permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(app.organization, actor):
        result = LifecycleMutation().create_deploy_token(
            fake_info, input=CreateDeployTokenInput(app_slug=app.slug, name="ci")
        )
    assert result.ok, result.errors
    assert DeployToken.objects.filter(registered_app=app).count() == 1


# ---------------------------------------------------------------------------
# rotate_deploy_token — secret rotation
# ---------------------------------------------------------------------------


def test_rotate_deploy_token_cross_org_leaves_secret_untouched(app, permission_resolver):
    token_row, _ = issue_token(app=app, name="ci", scopes=["app.deploy"])
    original_hash = token_row.token_hash
    sibling_org, _, _ = _sibling_org()
    permission_resolver.grant(Permission.APP_UPDATE)

    from types import SimpleNamespace

    caller = User.objects.create(username="sib-rotator", email="sibr@x")
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=caller, auth=None)))
    with _ctx(sibling_org, caller):
        result = LifecycleMutation().rotate_deploy_token(
            info, input=RotateDeployTokenInput(id=str(token_row.guid))
        )

    assert not result.ok
    assert any(e.code == "NOT_FOUND" for e in result.errors)
    token_row.refresh_from_db()
    assert token_row.token_hash == original_hash, "cross-org caller rotated another org's token"
    assert token_row.previous_token_hash == ""


def test_rotate_deploy_token_same_org_works(app, actor, fake_info, permission_resolver):
    token_row, _ = issue_token(app=app, name="ci", scopes=["app.deploy"])
    original_hash = token_row.token_hash
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(app.organization, actor):
        result = LifecycleMutation().rotate_deploy_token(
            fake_info, input=RotateDeployTokenInput(id=str(token_row.guid))
        )

    assert result.ok, result.errors
    token_row.refresh_from_db()
    assert token_row.token_hash != original_hash


# ---------------------------------------------------------------------------
# astrolift_deployment_metrics — caller-org aggregate only
# ---------------------------------------------------------------------------


def test_deployment_metrics_counts_caller_org_only(
    app, env, actor, fake_info, permission_resolver, provider_plugin
):
    """The rollout-health aggregate must not pool other orgs' deployments."""
    # Our org: two deployments.
    Deployment.objects.create(
        registered_app=app, app_environment=env, trigger_kind="manual", status="running", image_tag="v1"
    )
    Deployment.objects.create(
        registered_app=app, app_environment=env, trigger_kind="manual", status="failed", image_tag="v2"
    )

    # Sibling org: its own app + env + three deployments that must be excluded.
    sibling_org, sib_team, _ = _sibling_org()
    sib_app = RegisteredApp.objects.create(
        organization=sibling_org, team=sib_team, name="Sib", slug="sib-app", provisioning_status="ready"
    )
    sib_cluster = TenantCluster.objects.create(
        organization=sibling_org,
        name="sib-cluster",
        slug="sib-cluster",
        provider_plugin=provider_plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    sib_env = AppEnvironment.objects.create(
        registered_app=sib_app, tenant_cluster=sib_cluster, name="prod", url="https://sib.example.com"
    )
    for tag in ("a", "b", "c"):
        Deployment.objects.create(
            registered_app=sib_app,
            app_environment=sib_env,
            trigger_kind="manual",
            status="running",
            image_tag=tag,
        )

    permission_resolver.grant(Permission.APP_READ)
    with _ctx(app.organization, actor):
        metrics = LifecycleQuery().astrolift_deployment_metrics(fake_info, window_days=30)

    assert metrics.total == 2, "metrics leaked another org's deployments into the aggregate"
    assert metrics.succeeded == 1
    assert metrics.failed == 1


# ---------------------------------------------------------------------------
# astrolift_app_health_summary — caller-org apps only
# ---------------------------------------------------------------------------


def test_app_health_summary_lists_caller_org_only(app, actor, fake_info, permission_resolver):
    sibling_org, sib_team, _ = _sibling_org()
    RegisteredApp.objects.create(
        organization=sibling_org, team=sib_team, name="Sib", slug="sib-app", provisioning_status="ready"
    )

    permission_resolver.grant(Permission.APP_READ)
    with _ctx(app.organization, actor):
        rows = LifecycleQuery().astrolift_app_health_summary(fake_info)

    slugs = {r.app_slug for r in rows}
    assert app.slug in slugs
    assert "sib-app" not in slugs, "health summary leaked another org's app"


def test_app_health_summary_marks_agent_registered_apps(app, actor, fake_info, permission_resolver):
    Workload.objects.create(
        registered_app=app,
        name="triage agent",
        slug="triage-agent",
        kind=Workload.Kind.AGENT,
    )

    permission_resolver.grant(Permission.APP_READ)
    with _ctx(app.organization, actor):
        rows = LifecycleQuery().astrolift_app_health_summary(fake_info)

    row = next(row for row in rows if row.app_slug == app.slug)
    assert row.primitive_kind == "agent"


def test_app_health_summary_keeps_mixed_registered_apps_as_apps(app, actor, fake_info, permission_resolver):
    Workload.objects.create(
        registered_app=app,
        name="web",
        slug="web",
        kind=Workload.Kind.DEPLOYMENT,
    )
    Workload.objects.create(
        registered_app=app,
        name="helper agent",
        slug="helper-agent",
        kind=Workload.Kind.AGENT,
    )

    permission_resolver.grant(Permission.APP_READ)
    with _ctx(app.organization, actor):
        rows = LifecycleQuery().astrolift_app_health_summary(fake_info)

    row = next(row for row in rows if row.app_slug == app.slug)
    assert row.primitive_kind == "app"


# ---------------------------------------------------------------------------
# ci_deploy — token drives its own app on a cross-org slug collision
# ---------------------------------------------------------------------------


def _deployable_app(org_name, org_slug, *, app_slug):
    org = Organization.objects.create(name=org_name, slug=org_slug)
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{org_slug}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-{org_slug}")
    [plugin] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name=f"pp-{org_slug}", slug=f"pp-{org_slug}", capabilities_manifest={}, config_schema={}
            )
        ]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        name=f"cl-{org_slug}",
        slug=f"cl-{org_slug}",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    app = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name=app_slug,
        slug=app_slug,
        provisioning_status="ready",
        manifest_raw=_MANIFEST_WITH_WEB,
    )
    AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url=f"https://{org_slug}.example.com",
        required_approvals=0,
    )
    return org, app


def test_ci_deploy_slug_collision_binds_to_token_app(monkeypatch):
    """Two orgs own an app with the *same slug*. A deploy token issued for
    one org's app must drive that app — never the sibling's identically
    slugged row. The decoy app is created first (lower pk), so a slug-only
    re-fetch (the #1183 bug) would resolve it via ``.first()``."""
    starts: list[dict] = []

    def _fake_start(name, args, *, workflow_id, task_queue=None):
        starts.append({"name": name, "workflow_id": workflow_id})
        from astrolift_workflows.client import WorkflowHandle

        return WorkflowHandle(workflow_id=workflow_id, run_id="run-1", enqueued=True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", _fake_start)

    # Decoy first → lower pk → the row an unscoped ``filter(slug=).first()``
    # would return.
    _decoy_org, decoy_app = _deployable_app("Decoy", "decoy-1183", app_slug="shared-slug")
    token_org, token_app = _deployable_app("Owner", "owner-1183", app_slug="shared-slug")

    _row, plaintext = issue_token(app=token_app, name="ci", scopes=["app.deploy"])

    client = Client()
    resp = client.post(
        "/api/cli/v1/apps/shared-slug/deploy/",
        data=json.dumps(
            {
                "image_tags": {"web": "abc1234567890"},
                "commit_sha": "a" * 40,
                "branch": "main",
                "environment": "prod",
                "trigger_kind": "ci",
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {plaintext}",
    )

    assert resp.status_code == 201, resp.content
    deployment = Deployment.objects.get(guid=resp.json()["deployment_id"])
    assert deployment.registered_app_id == token_app.id
    assert deployment.registered_app_id != decoy_app.id
