"""Actual authenticated public transport and committed private origin handoff."""

from dataclasses import asdict
from uuid import uuid4

import pytest
from django.db import DatabaseError, transaction

from astrolift_identity.models import Member, RoleBinding
from astrolift_lifecycle.deployment_identity_origin import deployment_origin
from astrolift_lifecycle.models import AppEnvironment, Deployment, DeploymentIdentityOrigin
from astrolift_services.models import ManagedService
from astrolift_services.tests.test_cluster_model_foundation_2213 import world as foundation_world
from astrolift_services.tests.test_cluster_model_queries_2213 import grant
from astrolift_services.tests.test_model_connection_2270 import graphql_http, http_token
from astrolift_workflows.client import WorkflowHandle
from core.permissions import Permission
from core.tests.utils.scope_world import bind_role, make_user

pytestmark = pytest.mark.django_db(transaction=True)
START = "mutation($input:StartDeploymentInput!){startDeployment(input:$input){ok errors{code message} data{id status}}}"
APPROVE = "mutation($input:DeploymentByIdInput!){approveDeployment(input:$input){ok errors{code message} data{id status}}}"


@pytest.fixture
def world(monkeypatch):
    w = foundation_world.__wrapped__(monkeypatch)
    w.cluster.provider_plugin.slug = "gcp"
    w.cluster.provider_plugin.save()
    w.medops_app.build_mode = "ci_pushed"
    w.medops_app.save()
    w.endpoint = ManagedService.objects.create(
        registered_app=w.medops_app,
        app_environment=w.env,
        kind="model_endpoint",
        variant="vertex_ai",
        name="origin-endpoint",
    )
    grant(w, Permission.APP_DEPLOY, "APP", w.medops_app.pk)
    grant(w, Permission.APP_APPROVE_DEPLOY, "APP", w.medops_app.pk)
    w.token, w.headers = http_token(w, scopes=("read:apps", "write:apps"))
    w.starts = []

    def start(name, args, *, workflow_id, **kwargs):
        if args[0].identity_authority is not None:
            row = DeploymentIdentityOrigin.objects.get(deployment_id=args[0].deployment_id)
            assert row.authority_sha256 and row.deployment_binding
        w.starts.append((name, args[0]))
        return WorkflowHandle(workflow_id, "native-origin-test-run", True)

    monkeypatch.setattr("astrolift_lifecycle.schema.mutations.helpers.start_workflow", start)
    monkeypatch.setattr("astrolift_workflows.client.signal_workflow", lambda *a, **k: False)
    monkeypatch.setattr("core.pubsub.publish_sync", lambda *a, **k: None)
    return w


def start_public(w, client, **changes):
    return graphql_http(
        client,
        w.headers,
        START,
        {
            "input": {
                "appSlug": w.medops_app.slug,
                "environmentName": w.env.name,
                "imageTag": "origin-v1",
                **changes,
            }
        },
    )["data"]["startDeployment"]


def test_public_start_persists_original_bearer_separate_from_config(world, client):
    result = start_public(world, client, triggerKind="ci")
    assert result["ok"], result["errors"]
    row = Deployment.objects.get(guid=result["data"]["id"])
    ref = deployment_origin(row)
    assert ref.actor_user_id == world.user.pk and ref.credential_kind == "api_token"
    assert ref.credential_guid == str(world.token.guid)
    assert world.starts[0][1].identity_authority == ref
    assert world.starts[0][1].actor.user_id == world.user.pk
    assert "identity_authority" not in row.config_snapshot
    assert world.headers["HTTP_AUTHORIZATION"] not in str(asdict(ref))


@pytest.mark.parametrize("operation", ["redeploy", "promote"])
def test_public_repeat_and_target_promotion_capture_new_exact_target(world, client, operation):
    source = Deployment.objects.create(
        registered_app=world.medops_app,
        app_environment=world.env,
        status="running",
        image_tag="origin-v1",
        image_digest="sha256:" + "1" * 64,
    )
    if operation == "redeploy":
        query = "mutation($input:DeploymentByIdInput!){redeployApp(input:$input){ok errors{code message} data{id}}}"
        variables = {"input": {"id": str(source.guid)}}
        field = "redeployApp"
        target = world.env
    else:
        target = AppEnvironment.objects.create(
            registered_app=world.medops_app, tenant_cluster=world.cluster, name="target"
        )
        query = "mutation($input:PromoteDeploymentInput!){promoteDeployment(input:$input){ok errors{code message} data{id}}}"
        variables = {
            "input": {
                "appSlug": world.medops_app.slug,
                "sourceEnvironmentName": world.env.name,
                "targetEnvironmentName": target.name,
            }
        }
        field = "promoteDeployment"
    result = graphql_http(client, world.headers, query, variables)["data"][field]
    assert result["ok"], result["errors"]
    row = Deployment.objects.get(guid=result["data"]["id"])
    ref = deployment_origin(row)
    assert ref.environment_guid == str(target.guid) and ref.actor_user_id == world.user.pk
    assert world.starts[0][1].identity_authority == ref


@pytest.mark.parametrize("withdrawal", ["token", "membership", "grant", "scopes", "team"])
def test_withdrawn_original_refuses_before_any_other_person_vote(world, client, withdrawal):
    world.env.required_approvals = 1
    world.env.save()
    result = start_public(world, client)
    assert result["ok"], result["errors"]
    row = Deployment.objects.get(guid=result["data"]["id"])
    voter = make_user("origin-voter-" + uuid4().hex)
    Member.objects.create(user=voter, scope_kind="ORG", scope_id=world.org.pk)
    bind_role(
        voter,
        permissions=[Permission.APP_APPROVE_DEPLOY],
        kind="APP",
        scope_id=world.medops_app.pk,
        slug="origin-approver-" + uuid4().hex,
    )
    _, headers = http_token(world, actor=voter)
    if withdrawal == "token":
        world.token.is_revoked = True
        world.token.save()
    elif withdrawal == "membership":
        Member.objects.filter(user=world.user, scope_kind="ORG", scope_id=world.org.pk).update(
            is_active=False
        )
    elif withdrawal == "scopes":
        world.token.scopes = ["read:apps"]
        world.token.save()
    elif withdrawal == "team":
        world.token.team = world.platform
        world.token.save()
    else:
        RoleBinding.objects.filter(user=world.user).update(deleted_at=world.env.updated_at)
    outcome = graphql_http(client, headers, APPROVE, {"input": {"id": str(row.guid)}})["data"][
        "approveDeployment"
    ]
    assert not outcome["ok"]
    row.refresh_from_db()
    assert row.status == "pending_approval" and row.approvals_received == 0
    assert not row.approval_votes.exists() and not world.starts


def test_origin_is_append_only_in_actual_postgresql(world, client):
    result = start_public(world, client)
    assert result["ok"], result["errors"]
    origin = DeploymentIdentityOrigin.objects.get()
    for mutate in (
        lambda: DeploymentIdentityOrigin.all_objects.filter(pk=origin.pk).update(authority_sha256="0" * 64),
        lambda: DeploymentIdentityOrigin.all_objects.filter(pk=origin.pk).delete(),
    ):
        with pytest.raises(DatabaseError), transaction.atomic():
            mutate()
    origin.refresh_from_db()
    assert origin.authority_sha256 != "0" * 64 and origin.deleted_at is None


def reviewer(w, label):
    voter = make_user(label + uuid4().hex)
    Member.objects.create(user=voter, scope_kind="ORG", scope_id=w.org.pk)
    bind_role(
        voter,
        permissions=[Permission.APP_APPROVE_DEPLOY],
        kind="APP",
        scope_id=w.medops_app.pk,
        slug=label + uuid4().hex,
    )
    return voter, http_token(w, actor=voter)[1]


@pytest.mark.parametrize("minimum", [1, 2])
def test_policy_changed_during_wait_uses_original_and_actual_distinct_votes(world, client, minimum):
    from astrolift_identity.models import Policy

    world.env.required_approvals = 1
    world.env.save()
    result = start_public(world, client)
    assert result["ok"], result["errors"]
    row = Deployment.objects.get(guid=result["data"]["id"])
    original = deployment_origin(row)
    voter, headers = reviewer(world, "origin-policy-voter-")
    Policy.objects.create(
        organization=world.org,
        slug="origin-approval-condition",
        effect="ALLOW",
        action_pattern=Permission.APP_DEPLOY.value,
        scope_level="APP",
        scope_id=world.medops_app.pk,
        conditions=[{"kind": "approval_required", "min_approvers": minimum}],
    )
    outcome = graphql_http(client, headers, APPROVE, {"input": {"id": str(row.guid)}})["data"][
        "approveDeployment"
    ]
    row.refresh_from_db()
    if minimum == 1:
        assert outcome["ok"], outcome["errors"]
        assert row.approvals_received == 1 and row.status == "pending"
        assert row.approval_votes.get().voter_user_id == voter.pk
        assert world.starts[0][1].identity_authority == original
        assert world.starts[0][1].actor.user_id == world.user.pk != voter.pk
        assert row.workflow_run.trigger_actor_user_id == world.user.pk
    else:
        assert not outcome["ok"] and not world.starts
        assert row.status == "pending_approval" and not row.approval_votes.exists()


@pytest.mark.parametrize("condition", ["malformed", "nondeferrable"])
def test_pending_origin_never_defers_other_or_malformed_policy(world, client, condition):
    from astrolift_identity.models import Policy

    world.env.required_approvals = 2
    world.env.save()
    result = start_public(world, client)
    row = Deployment.objects.get(guid=result["data"]["id"])
    _, headers = reviewer(world, "origin-policy-refusal-")
    Policy.objects.create(
        organization=world.org,
        slug="origin-condition-refusal",
        effect="ALLOW",
        action_pattern=Permission.APP_DEPLOY.value,
        scope_level="APP",
        scope_id=world.medops_app.pk,
        conditions=[{"kind": "approval_required", "min_approvers": 0}]
        if condition == "malformed"
        else [{"kind": "environment", "allowed": ["other"]}],
    )
    outcome = graphql_http(client, headers, APPROVE, {"input": {"id": str(row.guid)}})["data"][
        "approveDeployment"
    ]
    assert not outcome["ok"] and not row.approval_votes.exists() and not world.starts


def test_browser_origin_is_real_session_and_survives_other_bearer_approval(world, client):
    client.force_login(world.user)
    world.headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid), "HTTP_X_PLATFORM": "web"}
    graphql_http(client, world.headers, "query{__typename}", {})
    world.env.required_approvals = 1
    world.env.save()
    result = start_public(world, client)
    assert result["ok"], result["errors"]
    row = Deployment.objects.get(guid=result["data"]["id"])
    ref = deployment_origin(row)
    assert ref.credential_kind == "browser_session"
    _, headers = reviewer(world, "origin-browser-reviewer-")
    outcome = graphql_http(client, headers, APPROVE, {"input": {"id": str(row.guid)}})["data"][
        "approveDeployment"
    ]
    assert outcome["ok"], outcome["errors"]
    assert world.starts[0][1].identity_authority == ref


def test_deploy_token_refuses_before_create_or_supersede(world, client):
    import json

    from astrolift_lifecycle.deploy_tokens import issue_token

    _, plaintext = issue_token(app=world.medops_app, name="origin-unsupported", scopes=["app.deploy"])
    before = Deployment.objects.count()
    result = client.post(
        f"/api/cli/v1/apps/{world.medops_app.slug}/deploy/",
        json.dumps(
            {
                "environment": world.env.name,
                "image_tags": {"web": "origin-v1"},
                "commit_sha": "1" * 40,
                "branch": "main",
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer " + plaintext,
    )
    assert result.status_code == 409
    assert result.json()["detail"] == "NATIVE_HUMAN_ORIGIN_REQUIRED"
    assert Deployment.objects.count() == before and not world.starts


def test_webhook_and_scheduled_origins_have_explicit_pre_effect_refusals(world):
    from astrolift_lifecycle.deployment_identity_origin import DeploymentOriginError
    from astrolift_workflows.activities.cron_deploy import _dispatch_cron_deploys_sync
    from auth1.scm_webhook import _fire_deploy

    with pytest.raises(DeploymentOriginError, match="NATIVE_HUMAN_ORIGIN_REQUIRED"):
        _fire_deploy(world.medops_app, "main", "1" * 40)
    world.medops_app.trigger_mode = "cron"
    world.medops_app.cron_expression = "* * * * *"
    world.medops_app.save()
    result = _dispatch_cron_deploys_sync()
    assert result.fired_count == 0 and result.refused_native_count == 1
    assert not Deployment.objects.exists() and not world.starts


def test_retained_journal_requires_origin_after_endpoint_graph_is_gone(monkeypatch):
    from astrolift_lifecycle.deployment_identity_origin import (
        DeploymentOriginError,
        native_origin_required,
        refuse_unsupported_origin,
    )
    from astrolift_services.gcp_workload_identity_journal import journal_mutex
    from astrolift_services.tests.test_gcp_workload_identity_journal_2278 import admitted
    from astrolift_services.tests.test_gcp_workload_identity_journal_2278 import world as journal_world

    w = journal_world.__wrapped__()
    assert not native_origin_required(w.medops_app)
    with journal_mutex(w.target) as store:
        store.reserve(w.operation, checkpoint=admitted)
    assert native_origin_required(w.medops_app)
    with pytest.raises(DeploymentOriginError, match="NATIVE_HUMAN_ORIGIN_REQUIRED"):
        refuse_unsupported_origin(w.medops_app)


def test_actual_migration_roundtrip_is_empty_only_and_retained_history_refuses(world, client):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    before = ("astrolift_lifecycle", "0045_deployment_run_history")
    after = ("astrolift_lifecycle", "0046_deploymentidentityorigin")
    executor = MigrationExecutor(connection)
    original = executor.loader.graph.leaf_nodes()
    try:
        assert not DeploymentIdentityOrigin.objects.exists()
        executor.migrate([after])
        MigrationExecutor(connection).migrate([before])
        MigrationExecutor(connection).migrate([after])
        result = start_public(world, client)
        assert result["ok"], result["errors"]
        origin = DeploymentIdentityOrigin.objects.get()
        snapshot = (origin.authority_reference, origin.authority_sha256, origin.deployment_binding)
        with pytest.raises(RuntimeError, match="DEPLOYMENT_ORIGIN_ROLLBACK_REQUIRES_EMPTY_HISTORY"):
            MigrationExecutor(connection).migrate([before])
        origin.refresh_from_db()
        assert (origin.authority_reference, origin.authority_sha256, origin.deployment_binding) == snapshot
    finally:
        MigrationExecutor(connection).migrate(original)


@pytest.mark.parametrize("withdrawal", ["expired", "revoked"])
def test_browser_origin_sidecar_withdrawal_prevents_vote(world, client, withdrawal):
    from datetime import timedelta

    from django.utils import timezone

    from astrolift_identity.models import AstroliftSession

    client.force_login(world.user)
    world.headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid), "HTTP_X_PLATFORM": "web"}
    graphql_http(client, world.headers, "query{__typename}", {})
    world.env.required_approvals = 1
    world.env.save()
    result = start_public(world, client)
    row = Deployment.objects.get(guid=result["data"]["id"])
    ref = deployment_origin(row)
    sidecar = AstroliftSession.objects.get(guid=ref.credential_guid)
    if withdrawal == "expired":
        sidecar.expires_at = timezone.now() - timedelta(seconds=1)
    else:
        sidecar.revoked_at = timezone.now()
    sidecar.save()
    _, headers = reviewer(world, "origin-sidecar-reviewer-")
    outcome = graphql_http(client, headers, APPROVE, {"input": {"id": str(row.guid)}})["data"][
        "approveDeployment"
    ]
    assert not outcome["ok"] and not row.approval_votes.exists() and not world.starts
    sidecar.refresh_from_db()
    if withdrawal == "expired":
        assert sidecar.expires_at < timezone.now()
    else:
        assert sidecar.revoked_at is not None


def test_withdrawal_after_durable_acceptance_before_start_cannot_enqueue(world, client, monkeypatch):
    from core.permissions import PermissionDenied

    callbacks = []
    monkeypatch.setattr(transaction, "on_commit", lambda fn, **kwargs: callbacks.append(fn))
    result = start_public(world, client)
    assert result["ok"], result["errors"]
    row = Deployment.objects.get(guid=result["data"]["id"])
    assert deployment_origin(row) is not None and not world.starts
    world.token.is_revoked = True
    world.token.save()
    with pytest.raises(PermissionDenied):
        callbacks[0]()
    assert not world.starts


def test_mixed_source_union_refuses_before_deployment_creation(world, client):
    ManagedService.objects.create(
        registered_app=world.medops_app,
        app_environment=world.env,
        kind="postgres",
        variant="cloudsql",
        name="unsupported-mixed-union",
    )
    outcome = start_public(world, client)
    assert not outcome["ok"] and outcome["errors"][0]["code"] == "PRECONDITION"
    assert (
        not Deployment.objects.exists() and not DeploymentIdentityOrigin.objects.exists() and not world.starts
    )


@pytest.mark.parametrize("provider", ["aws", "local"])
def test_public_other_cluster_environment_keeps_ordinary_flow(world, client, provider):
    from core.tests.utils.scope_world import make_cluster

    cluster = make_cluster(world, "origin-other-" + uuid4().hex)
    cluster.provider_plugin.slug = provider
    cluster.provider_plugin.save()
    env = AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=cluster, name="other-target"
    )
    ManagedService.objects.create(
        registered_app=world.medops_app,
        app_environment=env,
        kind="postgres",
        variant="ordinary",
        name="ordinary-other-cluster",
    )
    outcome = start_public(world, client, environmentName=env.name)
    assert outcome["ok"], outcome["errors"]
    row = Deployment.objects.get(guid=outcome["data"]["id"])
    assert row.app_environment_id == env.pk and deployment_origin(row) is None
    assert not DeploymentIdentityOrigin.objects.exists()
    assert world.starts[0][1].identity_authority is None


def test_deploy_token_other_cluster_is_not_refused_by_separate_gcp_source(world, client, monkeypatch):
    import json

    from astrolift_lifecycle.deploy_tokens import issue_token
    from core.tests.utils.scope_world import make_cluster

    cluster = make_cluster(world, "origin-rest-other-" + uuid4().hex)
    cluster.provider_plugin.slug = "aws"
    cluster.provider_plugin.save()
    env = AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=cluster, name="other-ci-target"
    )
    calls = []

    def start(name, args, *, workflow_id, **kwargs):
        calls.append(args[0])
        return WorkflowHandle(workflow_id, "ordinary-rest-test", True)

    monkeypatch.setattr("astrolift_workflows.client.start_workflow", start)
    _, plaintext = issue_token(app=world.medops_app, name="ordinary-ci", scopes=["app.deploy"])
    result = client.post(
        f"/api/cli/v1/apps/{world.medops_app.slug}/deploy/",
        json.dumps(
            {
                "environment": env.name,
                "image_tags": {"web": "ordinary-v1"},
                "commit_sha": "1" * 40,
                "branch": "main",
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer " + plaintext,
    )
    assert result.status_code == 201
    assert len(calls) == 1 and calls[0].actor.kind == "deploy_token"
    assert calls[0].app_environment_id == env.pk and calls[0].identity_authority is None
    assert not DeploymentIdentityOrigin.objects.exists()


def test_gcp_selected_cluster_checks_sibling_alias_union(world, client):
    alias = AppEnvironment.objects.create(
        registered_app=world.medops_app, tenant_cluster=world.cluster, name="same-cluster-alias"
    )
    ManagedService.objects.create(
        registered_app=world.medops_app,
        app_environment=alias,
        kind="postgres",
        variant="cloudsql",
        name="unsupported-alias-source",
    )
    outcome = start_public(world, client)
    assert not outcome["ok"] and outcome["errors"][0]["code"] == "PRECONDITION"
    assert not Deployment.objects.exists() and not DeploymentIdentityOrigin.objects.exists()


def test_same_physical_cluster_provider_replacement_refuses_native_origin(world, client):
    world.cluster.provider_plugin.slug = "aws"
    world.cluster.provider_plugin.save()
    outcome = start_public(world, client)
    assert not outcome["ok"] and outcome["errors"][0]["code"] == "PRECONDITION"
    assert not Deployment.objects.exists() and not world.starts


def test_protected_approval_votes_apply_only_to_exact_original_environment(world, client):
    from astrolift_identity.models import Policy
    from astrolift_lifecycle.deployment_identity_origin import (
        DeploymentOriginError,
        approval_context,
        deployment_app_identity_authority,
    )
    from astrolift_services.native_identity_authority import current_app_identity_authority
    from astrolift_workflows.native_identity_inputs import DeploymentAuthorityContext
    from core.permissions import PermissionDenied

    alias = AppEnvironment.objects.create(
        registered_app=world.medops_app,
        tenant_cluster=world.cluster,
        name="approval-alias",
        required_approvals=1,
    )
    world.env.required_approvals = 1
    world.env.save()
    first = start_public(world, client)
    sibling = start_public(world, client, environmentName=alias.name)
    assert first["ok"] and sibling["ok"]
    selected = Deployment.objects.get(guid=first["data"]["id"])
    other = Deployment.objects.get(guid=sibling["data"]["id"])
    selected_ref, other_ref = deployment_origin(selected), deployment_origin(other)
    Policy.objects.create(
        organization=world.org,
        slug="exact-environment-approval",
        effect="ALLOW",
        action_pattern=Permission.APP_DEPLOY.value,
        scope_level="APP",
        scope_id=world.medops_app.pk,
        conditions=[{"kind": "approval_required", "min_approvers": 1}],
    )
    _, headers = reviewer(world, "exact-env-voter-")
    outcome = graphql_http(client, headers, APPROVE, {"input": {"id": str(selected.guid)}})["data"][
        "approveDeployment"
    ]
    assert outcome["ok"], outcome["errors"]
    assert approval_context(selected_ref, selected.guid) == (1, False)
    assert approval_context(other_ref, other.guid) == (0, True)
    with deployment_app_identity_authority(selected_ref, DeploymentAuthorityContext(str(selected.guid))):
        pass
    with pytest.raises(PermissionDenied):
        with current_app_identity_authority(selected_ref):
            pass
    with pytest.raises(DeploymentOriginError, match="DEPLOYMENT_ORIGIN_INVALID"):
        with deployment_app_identity_authority(other_ref, DeploymentAuthorityContext(str(selected.guid))):
            pass
    assert not other.approval_votes.exists()


@pytest.mark.parametrize("provider_replaced", [False, True])
def test_preparation_only_retained_history_requires_exact_selected_cluster_origin(
    monkeypatch, client, request, provider_replaced
):
    from astrolift_lifecycle.deployment_identity_origin import native_origin_required
    from astrolift_services.gcp_gke_preparation_journal import preparation_journal_mutex
    from astrolift_services.models import (
        GCPGKEPreparationJournal,
        GCPGKEPreparationOperation,
        GCPWorkloadIdentityJournal,
    )
    from astrolift_services.tests.test_gcp_gke_preparation_journal_2278 import admitted
    from astrolift_services.tests.test_gcp_gke_preparation_journal_2278 import world as prep_world
    from core.tests.utils.scope_world import make_cluster

    w = prep_world.__wrapped__(monkeypatch, client, request)
    # Capture fixture initially connects vLLM only to obtain a real HTTP ref.
    # Retire that fixture attachment before proving preparation-only ownership.
    from django.utils import timezone

    from astrolift_services.models import ManagedServiceAttachment

    ManagedServiceAttachment.objects.filter(app_environment=w.env).update(deleted_at=timezone.now())
    assert not ManagedServiceAttachment.objects.exists()
    assert not native_origin_required(w.medops_app, w.env)
    with preparation_journal_mutex(w.target) as store:
        store.reserve(w.operation, checkpoint=admitted)
    assert GCPGKEPreparationJournal.objects.count() == GCPGKEPreparationOperation.objects.count() == 1
    assert not GCPWorkloadIdentityJournal.objects.exists()
    assert native_origin_required(w.medops_app, w.env)
    cluster = make_cluster(w, "prep-other-" + uuid4().hex)
    other = AppEnvironment.objects.create(
        registered_app=w.medops_app, tenant_cluster=cluster, name="unrelated-preparation-target"
    )
    assert not native_origin_required(w.medops_app, other)
    grant(w, Permission.APP_DEPLOY, "APP", w.medops_app.pk)
    w.medops_app.build_mode = "ci_pushed"
    w.medops_app.save()
    w.token, w.headers = http_token(w, scopes=("read:apps", "write:apps"))
    starts = []

    def start(name, args, *, workflow_id, **kwargs):
        starts.append(args[0])
        return WorkflowHandle(workflow_id, "prep-origin-test", True)

    monkeypatch.setattr("astrolift_lifecycle.schema.mutations.helpers.start_workflow", start)
    monkeypatch.setattr("core.pubsub.publish_sync", lambda *a, **k: None)
    if provider_replaced:
        w.cluster.provider_plugin.slug = "aws"
        w.cluster.provider_plugin.save()
    outcome = start_public(w, client)
    if provider_replaced:
        assert not outcome["ok"] and outcome["errors"][0]["code"] == "PRECONDITION"
        assert not starts and not Deployment.objects.exists()
    else:
        assert outcome["ok"], outcome["errors"]
        row = Deployment.objects.get(guid=outcome["data"]["id"])
        assert deployment_origin(row) == starts[0].identity_authority
