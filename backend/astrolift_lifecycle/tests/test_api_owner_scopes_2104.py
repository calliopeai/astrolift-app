# ruff: noqa: F811
"""Real lifecycle API calls cannot reach sibling owners or create side effects."""

import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest

from astrolift_identity.models import ApiToken
from astrolift_lifecycle.models import (
    AppEnvironment,
    CommandRun,
    CustomDomain,
    Deployment,
    DeployToken,
    PreviewEnvironment,
    ScheduledJobRun,
    TaskRun,
)
from astrolift_lifecycle.schema.mutations import LifecycleMutation
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_lifecycle.tests.test_owner_scopes_2104 import no_search, subject, world  # noqa: F401
from core.permissions import Permission, PermissionDenied
from core.tests.utils.scope_world import bind_role, make_info

pytestmark = pytest.mark.django_db

ROUTES = (
    ("astrolift_deployment", "query", "deployment_app_scope", "id"),
    ("astrolift_deployment_approval_history", "query", "deployment_app_scope", "deployment_id"),
    ("astrolift_deployment_release_notes", "query", "deployment_app_scope", "deployment_id"),
    ("astrolift_deployment_log", "query", "deployment_app_scope", "deployment_id"),
    ("astrolift_scheduled_job_run", "query", "scheduled_job_run_app_scope", "id"),
    ("astrolift_command_runs", "query", "app_scope_by_slug", "app_slug"),
    ("astrolift_command_runs_page", "query", "app_scope_by_slug", "app_slug"),
    ("astrolift_command_run", "query", "command_run_app_scope", "id"),
    ("astrolift_app_domains", "query", "app_scope_by_slug", "app_slug"),
    ("astrolift_app_pods", "query", "app_scope_by_slug", "app_slug"),
    ("astrolift_workload_pod_status_breakdown", "query", "app_scope_by_slug", "app_slug"),
    ("astrolift_app_deploy_tokens", "query", "app_scope_by_slug", "app_slug"),
    ("astrolift_app_deploy_tokens_page", "query", "app_scope_by_slug", "app_slug"),
    ("astrolift_app_dns_records", "query", "app_scope_by_slug", "app_slug"),
    ("astrolift_app_certificates", "query", "app_scope_by_slug", "app_slug"),
    ("astrolift_app_identity_binding", "query", "app_scope_by_slug", "app_slug"),
    ("preview_astrolift_deregister", "query", "app_scope_by_slug", "app_slug"),
    ("preview_astrolift_force_redeploy", "query", "app_scope_by_slug", "app_slug"),
    ("astrolift_task_run", "query", "task_run_app_scope", "id"),
    ("create_deploy_token", "mutation", "app_scope_by_slug", "input.app_slug"),
    ("rotate_deploy_token", "mutation", "deploy_token_app_scope", "input.id"),
    ("revoke_deploy_token", "mutation", "deploy_token_app_scope", "input.id"),
    ("run_task", "mutation", "app_scope_by_slug", "input.app_slug"),
    ("force_astrolift_redeploy", "mutation", "app_scope_by_slug", "input.app_slug"),
    ("run_astrolift_job_once", "mutation", "app_scope_by_slug", "input.app_slug"),
    ("rerun_astrolift_onboarding", "mutation", "app_scope_by_slug", "input.app_slug"),
    ("set_environment_setting", "mutation", "environment_app_scope", "input.environment_id"),
    ("clear_environment_setting", "mutation", "environment_app_scope", "input.environment_id"),
    ("delete_app_dns_record", "mutation", "app_scope_by_guid", "input.app_id"),
    ("revoke_app_certificate", "mutation", "custom_domain_app_scope", "input.custom_domain_id"),
    ("delete_app_identity_role", "mutation", "app_scope_by_guid", "input.app_id"),
    ("archive_app_registry_repo", "mutation", "app_scope_by_guid", "input.app_id"),
    ("delete_app_ingress", "mutation", "app_scope_by_guid", "input.app_id"),
    ("tear_down_preview", "mutation", "preview_environment_app_scope", "input.id"),
    ("extend_preview_ttl", "mutation", "preview_environment_app_scope", "input.id"),
    ("set_preview_pinned", "mutation", "preview_environment_app_scope", "input.id"),
    ("create_preview_environment", "mutation", "app_scope_by_slug", "input.app_slug"),
    ("add_app_domain", "mutation", "app_scope_by_slug", "input.app_slug"),
    ("add_wildcard_domain", "mutation", "app_scope_by_slug", "input.app_slug"),
    ("remove_app_domain", "mutation", "custom_domain_app_scope", "input.id"),
    ("recheck_domain_validation", "mutation", "custom_domain_app_scope", "input.id"),
    ("upload_custom_domain_certificate", "mutation", "custom_domain_app_scope", "input.id"),
    ("set_domain_redirects", "mutation", "custom_domain_app_scope", "input.domain_id"),
    ("set_domain_path_routes", "mutation", "custom_domain_app_scope", "input.domain_id"),
    ("pause_environment", "mutation", "environment_app_scope", "input.id"),
    ("resume_environment", "mutation", "environment_app_scope", "input.id"),
    ("pause_app_ingress", "mutation", "environment_app_scope", "input.id"),
    ("resume_app_ingress", "mutation", "environment_app_scope", "input.id"),
    ("start_deployment", "mutation", "app_scope_by_slug", "input.app_slug"),
    ("approve_deployment", "mutation", "deployment_app_scope", "input.id"),
    ("reject_deployment", "mutation", "deployment_app_scope", "input.id"),
    ("abort_deployment", "mutation", "deployment_app_scope", "input.id"),
    ("delete_deployment", "mutation", "deployment_app_scope", "input.id"),
    ("rollback_deployment", "mutation", "deployment_app_scope", "input.id"),
    ("redeploy_app", "mutation", "deployment_app_scope", "input.id"),
    ("promote_deployment", "mutation", "app_scope_by_slug", "input.app_slug"),
    ("deregister_astrolift_app", "mutation", "app_scope_by_slug", "input.app_slug"),
    ("cancel_astrolift_deregister", "mutation", "deregister_workflow_scope", "input.workflow_id"),
    ("trigger_astrolift_deploy_workflow", "mutation", "app_scope_by_slug", "input.app_slug"),
    ("restart_astrolift_workload", "mutation", "app_scope_by_workload_guid", "input.workload_id"),
    ("scale_astrolift_workload", "mutation", "app_scope_by_workload_guid", "input.workload_id"),
    ("install_astrolift_source_webhook", "mutation", "app_scope_by_slug", "input.app_slug"),
    ("push_astrolift_ci_secrets_to_repo", "mutation", "app_scope_by_slug", "input.app_slug"),
    ("validate_astrolift_ci_secrets", "mutation", "app_scope_by_slug", "input.app_slug"),
    ("push_astrolift_ci_workflow_to_repo", "mutation", "app_scope_by_slug", "input.app_slug"),
    ("retry_astrolift_autowire", "mutation", "app_scope_by_slug", "input.app_slug"),
    ("migrate_app_to_cluster", "mutation", "environment_app_scope", "input.app_environment_id"),
)


def _invoke(world, route, *, target="platform", missing=False):
    name, kind, factory, field = route
    app = getattr(world, target + "_app")
    if factory in {"app_scope_by_slug", "app_scope_by_guid"}:
        value = (
            ("missing-app-2104" if missing else app.slug)
            if factory.endswith("slug")
            else (uuid4() if missing else app.guid)
        )
    elif factory == "app_scope_by_workload_guid":
        value = uuid4() if missing else world.rows[target]["command_run"].workload.guid
    elif factory == "deregister_workflow_scope":
        value = "DeregisterAppWorkflow-" + str(uuid4() if missing else app.guid)
    else:
        family = factory.removesuffix("_app_scope")
        value = uuid4() if missing else world.rows[target][family].guid
    cls = LifecycleQuery if kind == "query" else LifecycleMutation
    method = getattr(cls(), name)
    kwargs = {
        key: SimpleNamespace() if key == "input" else str(uuid4())
        for key, parameter in inspect.signature(method).parameters.items()
        if key != "info" and parameter.default is inspect.Parameter.empty
    }
    bits = field.split(".")
    if len(bits) == 2:
        kwargs.setdefault(bits[0], SimpleNamespace())
        setattr(kwargs[bits[0]], bits[1], value)
    else:
        kwargs[field] = value
    return method(make_info(world.user), **kwargs)


def _state():
    return [
        list(model.all_objects.order_by("pk").values())
        for model in (
            Deployment,
            AppEnvironment,
            CommandRun,
            TaskRun,
            ScheduledJobRun,
            CustomDomain,
            DeployToken,
            PreviewEnvironment,
        )
    ]


@pytest.mark.parametrize("route", ROUTES, ids=lambda row: row[0])
@pytest.mark.parametrize("authority", ["none", "sibling-app", "selected-team-miss", "team-token"])
def test_real_api_denies_before_any_write_driver_or_workflow(world, route, authority, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("a denied lifecycle operation reached a workflow or signal")

    for module in [
        "astrolift_lifecycle.schema.mutations.helpers",
        "astrolift_lifecycle.schema.mutations.deregister",
        "astrolift_workflows.client",
    ]:
        for name in ["start_workflow", "signal_workflow", "terminate_workflow"]:
            monkeypatch.setattr(f"{module}.{name}", forbidden, raising=False)
    token = None
    if authority != "none":
        kind, owner = (
            ("APP", world.medops_app)
            if authority == "sibling-app"
            else ("TEAM", world.platform)
            if authority == "selected-team-miss"
            else ("ORG", world.org)
        )
        bind_role(
            world.user, permissions=list(Permission), kind=kind, scope_id=owner.pk, slug="api-owner-2104"
        )
    if authority == "team-token":
        token = ApiToken.objects.create(
            user=world.user,
            organization=world.org,
            team=world.medops,
            name="Scoped",
            token_hash="api-owner-2104",
            scopes=["admin"],
        )
    before = _state()
    with subject(world, token):
        if route[1] == "query":
            with pytest.raises(PermissionDenied):
                _invoke(world, route, missing=authority == "selected-team-miss")
        else:
            result = _invoke(world, route, missing=authority == "selected-team-miss")
            assert not result.ok and result.errors[0].code == "PERMISSION_DENIED", result
    assert _state() == before


@pytest.mark.parametrize("level", ["viewer", "deployer", "owner"])
def test_actual_api_read_and_write_obey_bearer_share_level(world, level):
    from astrolift_lifecycle.schema.mutations import RevokeDeployTokenInput
    from astrolift_registry.models import AppTeamAccess

    bind_role(
        world.user, permissions=list(Permission), kind="ORG", scope_id=world.org.pk, slug="share-org-2104"
    )
    AppTeamAccess.objects.create(registered_app=world.platform_app, team=world.medops, access_level=level)
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=world.medops,
        name="Shared",
        token_hash="share-2104",
        scopes=["admin"],
    )
    row = world.rows["platform"]["deploy_token"]
    with subject(world, token):
        deployment = LifecycleQuery().astrolift_deployment(
            make_info(world.user), id=str(world.rows["platform"]["deployment"].guid)
        )
        assert str(deployment.id) == str(world.rows["platform"]["deployment"].guid)
        result = LifecycleMutation().revoke_deploy_token(
            make_info(world.user), input=RevokeDeployTokenInput(id=row.guid)
        )
    assert result.ok is (level != "viewer"), result
    row.refresh_from_db()
    assert row.is_revoked is (level != "viewer")


@pytest.mark.parametrize(
    "invalid", ["sibling-environment", "deleted-environment", "foreign-cluster", "sibling-workload"]
)
def test_persisted_deployment_mismatches_never_read_or_mutate_even_for_org_actor(world, invalid):
    from astrolift_lifecycle.schema.mutations import DeploymentByIdInput
    from core.tests.utils.scope_world import ScopeWorld, make_cluster

    bind_role(
        world.user, permissions=list(Permission), kind="ORG", scope_id=world.org.pk, slug="coherence-org-2104"
    )
    row = world.rows["medops"]["deployment"]
    if invalid == "sibling-environment":
        Deployment.objects.filter(pk=row.pk).update(app_environment=world.rows["platform"]["environment"])
    elif invalid == "deleted-environment":
        world.rows["medops"]["environment"].soft_delete()
    elif invalid == "sibling-workload":
        Deployment.objects.filter(pk=row.pk).update(workload=world.rows["platform"]["command_run"].workload)
    else:
        foreign = ScopeWorld("foreign-cluster-2104")
        cluster = make_cluster(foreign, "foreign-cluster-2104")
        AppEnvironment.objects.filter(pk=row.app_environment_id).update(tenant_cluster=cluster)
    before = _state()
    with subject(world):
        result = LifecycleMutation().delete_deployment(
            make_info(world.user), input=DeploymentByIdInput(id=row.guid)
        )
        assert not result.ok and result.errors[0].code == "NOT_FOUND", result
        page = LifecycleQuery().astrolift_deployments_page(
            make_info(world.user), app_slug=world.medops_app.slug
        )
    assert page.items == [] and page.total_count == 0
    assert _state() == before


@pytest.mark.parametrize("credential", ["org-session", "team-token"])
@pytest.mark.parametrize(
    "name",
    [
        "astrolift_deployments_page",
        "astrolift_environments_page",
        "astrolift_preview_environments_page",
        "astrolift_task_runs_page",
        "astrolift_scheduled_job_runs_page",
    ],
)
def test_lifecycle_collection_filters_bearer_before_rows_and_counts(world, credential, name):
    bind_role(
        world.user,
        permissions=list(Permission),
        kind="ORG",
        scope_id=world.org.pk,
        slug="collection-org-2104",
    )
    token = (
        ApiToken.objects.create(
            user=world.user,
            organization=world.org,
            team=world.medops,
            name="Rows",
            token_hash="collection-2104",
            scopes=["admin"],
        )
        if credential == "team-token"
        else None
    )
    with subject(world, token):
        page = getattr(LifecycleQuery(), name)(make_info(world.user))
    family = {
        "astrolift_deployments_page": "deployment",
        "astrolift_environments_page": "environment",
        "astrolift_preview_environments_page": "preview_environment",
        "astrolift_task_runs_page": "task_run",
        "astrolift_scheduled_job_runs_page": "scheduled_job_run",
    }[name]
    expected = {str(world.rows["medops"][family].guid)}
    if credential == "org-session":
        expected.add(str(world.rows["platform"][family].guid))
    assert {str(row.id) for row in page.items} == expected
    assert page.total_count == len(expected)


def test_deregister_cancel_resolves_actual_app_before_signalling(world, monkeypatch):
    from astrolift_lifecycle.schema.mutations import CancelDeregisterInput

    bind_role(
        world.user,
        permissions=[Permission.APP_DELETE],
        kind="APP",
        scope_id=world.medops_app.pk,
        slug="cancel-app-2104",
    )
    calls = []
    monkeypatch.setattr(
        "astrolift_lifecycle.schema.mutations.deregister.signal_workflow",
        lambda *args: calls.append(args) or True,
    )
    with subject(world):
        result = LifecycleMutation().cancel_astrolift_deregister(
            make_info(world.user),
            input=CancelDeregisterInput(workflow_id="DeregisterAppWorkflow-" + str(world.medops_app.guid)),
        )
    assert result.ok, result.errors
    assert calls[0][0] == "DeregisterAppWorkflow-" + str(world.medops_app.guid)


@pytest.mark.parametrize("invalid", ["deleted", "inactive", "foreign"])
@pytest.mark.parametrize("target", ["environment", "default"])
def test_actual_pod_read_never_falls_back_from_invalid_target_or_calls_driver(
    world, invalid, target, monkeypatch
):
    from astrolift_clusters.models import TenantCluster
    from core.tests.utils.scope_world import ScopeWorld, make_cluster

    bind_role(
        world.user, permissions=[Permission.APP_READ_LOGS], kind="ORG", scope_id=world.org.pk, slug="pods"
    )
    env = world.rows["medops"]["environment"]
    cluster = env.tenant_cluster
    if invalid == "foreign":
        cluster = make_cluster(ScopeWorld("foreign-provider-2104"), "foreign-provider-2104")
    elif invalid == "deleted":
        cluster.soft_delete()
    else:
        TenantCluster.objects.filter(pk=cluster.pk).update(is_active=False)
    if target == "environment":
        AppEnvironment.objects.filter(pk=env.pk).update(tenant_cluster=cluster)
    else:
        type(world.medops_app).objects.filter(pk=world.medops_app.pk).update(default_tenant_cluster=cluster)

    def forbidden(*args, **kwargs):
        pytest.fail("an invalid cluster reached the observability driver")

    monkeypatch.setattr("astrolift_lifecycle.schema.queries.list_app_pods", forbidden)
    with subject(world):
        result = LifecycleQuery().astrolift_app_pods(
            make_info(world.user),
            app_slug=world.medops_app.slug,
            environment_name=env.name if target == "environment" else None,
        )
    assert result == []


@pytest.mark.parametrize("invalid", ["deleted", "inactive", "foreign"])
@pytest.mark.parametrize("operation", ["restart", "scale"])
def test_actual_runtime_write_rejects_invalid_cluster_before_driver(world, invalid, operation, monkeypatch):
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.schema.mutations import RestartWorkloadInput, ScaleWorkloadInput
    from core.tests.utils.scope_world import ScopeWorld, make_cluster

    bind_role(
        world.user, permissions=[Permission.APP_DEPLOY], kind="ORG", scope_id=world.org.pk, slug="runtime"
    )
    env = world.rows["medops"]["environment"]
    cluster = env.tenant_cluster
    if invalid == "foreign":
        cluster = make_cluster(ScopeWorld("foreign-runtime-2104"), "foreign-runtime-2104")
        AppEnvironment.objects.filter(pk=env.pk).update(tenant_cluster=cluster)
    elif invalid == "deleted":
        cluster.soft_delete()
    else:
        TenantCluster.objects.filter(pk=cluster.pk).update(is_active=False)
    workload = world.rows["medops"]["command_run"].workload
    workload.kind = "service"
    workload.save()
    before = _state()

    def forbidden(*args, **kwargs):
        pytest.fail("an invalid cluster reached the runtime driver")

    monkeypatch.setattr("core.cluster_management._driver_for_cluster", forbidden)
    with subject(world):
        if operation == "restart":
            result = LifecycleMutation().restart_astrolift_workload(
                make_info(world.user), input=RestartWorkloadInput(workload_id=str(workload.guid))
            )
        else:
            result = LifecycleMutation().scale_astrolift_workload(
                make_info(world.user), input=ScaleWorkloadInput(workload_id=str(workload.guid), replicas=1)
            )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert _state() == before


@pytest.mark.parametrize("authority", ["none", "team", "org", "org-team-token"])
def test_actual_agent_box_pod_query_uses_box_owner_and_bearer_ceiling(world, authority, monkeypatch):
    from astrolift_agents.models import AgentBox

    own = AgentBox.objects.create(organization=world.org, team=world.medops, slug="own-pods-2104")
    sibling = AgentBox.objects.create(organization=world.org, team=world.platform, slug="sibling-pods-2104")
    if authority != "none":
        kind = "TEAM" if authority == "team" else "ORG"
        bind_role(
            world.user,
            permissions=[Permission.AGENT_BOX_ATTACH],
            kind=kind,
            scope_id=world.medops.pk if kind == "TEAM" else world.org.pk,
            slug="box",
        )
    token = (
        ApiToken.objects.create(
            user=world.user,
            organization=world.org,
            team=world.medops,
            name="Box",
            token_hash="box-2104",
            scopes=["admin"],
        )
        if authority == "org-team-token"
        else None
    )
    calls = []
    monkeypatch.setattr(
        "astrolift_lifecycle.schema.queries._list_pods_for_box",
        lambda slug, **kwargs: calls.append(slug) or [],
    )
    with subject(world, token):
        if authority == "none":
            with pytest.raises(PermissionDenied):
                LifecycleQuery().agent_box_pods(make_info(world.user), slug=own.slug)
        else:
            assert LifecycleQuery().agent_box_pods(make_info(world.user), slug=own.slug) == []
            if authority == "team":
                with pytest.raises(PermissionDenied):
                    LifecycleQuery().agent_box_pods(make_info(world.user), slug=sibling.slug)
            else:
                assert LifecycleQuery().agent_box_pods(make_info(world.user), slug=sibling.slug) == []
    assert calls == (
        [] if authority == "none" else [own.slug, sibling.slug] if authority == "org" else [own.slug]
    )


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("level", [None, "viewer", "deployer", "owner"])
def test_bulk_votes_check_actual_bearer_share_before_vote_signal_or_status(world, action, level, monkeypatch):
    from astrolift_lifecycle.models import DeploymentApproval
    from astrolift_lifecycle.schema.mutations import BulkApproveDeploymentsInput, BulkRejectDeploymentsInput
    from astrolift_registry.models import AppTeamAccess

    bind_role(
        world.user,
        permissions=[Permission.APP_APPROVE_DEPLOY],
        kind="ORG",
        scope_id=world.org.pk,
        slug="bulk",
    )
    row = world.rows["platform"]["deployment"]
    Deployment.objects.filter(pk=row.pk).update(status="pending_approval")
    if level is not None:
        AppTeamAccess.objects.create(registered_app=world.platform_app, team=world.medops, access_level=level)
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=world.medops,
        name="Vote",
        token_hash="vote-2104",
        scopes=["admin"],
    )
    calls = []
    monkeypatch.setattr(
        "astrolift_workflows.client.signal_workflow", lambda *args, **kwargs: calls.append(args) or True
    )
    before = _state()
    with subject(world, token):
        if action == "approve":
            result = LifecycleMutation().bulk_approve_deployments(
                make_info(world.user), input=BulkApproveDeploymentsInput(deployment_ids=[str(row.guid)])
            )
        else:
            result = LifecycleMutation().bulk_reject_deployments(
                make_info(world.user),
                input=BulkRejectDeploymentsInput(deployment_ids=[str(row.guid)], reason="Stop"),
            )
    assert result.ok
    item = result.data.results[0]
    if level in {None, "viewer"}:
        assert not item.ok
        assert item.errors[0].code == "PERMISSION_DENIED"
        assert _state() == before
        assert not DeploymentApproval.objects.exists()
        assert calls == []
    else:
        assert item.ok, item.errors
        row.refresh_from_db()
        assert row.status != "pending_approval"
        if action == "approve":
            assert DeploymentApproval.objects.filter(deployment=row, voter_user_id=world.user.pk).count() == 1


@pytest.mark.parametrize("invalid", ["deleted", "inactive", "foreign"])
@pytest.mark.parametrize(
    "name",
    [
        "delete_app_dns_record",
        "revoke_app_certificate",
        "delete_app_identity_role",
        "archive_app_registry_repo",
        "delete_app_ingress",
    ],
)
def test_actual_capability_deprovision_refuses_invalid_cluster_before_driver_or_write(
    world, invalid, name, monkeypatch
):
    from astrolift_clusters.models import TenantCluster
    from astrolift_lifecycle.schema import mutations
    from core.tests.utils.scope_world import ScopeWorld, make_cluster

    bind_role(
        world.user, permissions=[Permission.APP_DELETE], kind="ORG", scope_id=world.org.pk, slug="deprovision"
    )
    cluster = world.rows["medops"]["environment"].tenant_cluster
    if invalid == "foreign":
        cluster = make_cluster(ScopeWorld("foreign-deprovision-2104"), "foreign-deprovision-2104")
    elif invalid == "deleted":
        cluster.soft_delete()
    else:
        TenantCluster.objects.filter(pk=cluster.pk).update(is_active=False)
    type(world.medops_app).objects.filter(pk=world.medops_app.pk).update(default_tenant_cluster=cluster)
    input = {
        "delete_app_dns_record": lambda: mutations.DeleteAppDnsRecordInput(
            app_id=str(world.medops_app.guid), hostname="api.example.test"
        ),
        "revoke_app_certificate": lambda: mutations.RevokeAppCertificateInput(
            custom_domain_id=str(world.rows["medops"]["custom_domain"].guid)
        ),
        "delete_app_identity_role": lambda: mutations.DeleteAppIdentityRoleInput(
            app_id=str(world.medops_app.guid)
        ),
        "archive_app_registry_repo": lambda: mutations.ArchiveAppRegistryRepoInput(
            app_id=str(world.medops_app.guid)
        ),
        "delete_app_ingress": lambda: mutations.DeleteAppIngressInput(app_id=str(world.medops_app.guid)),
    }[name]()
    before = _state()

    def forbidden(*args, **kwargs):
        pytest.fail("invalid persisted target reached capability driver")

    monkeypatch.setattr(
        "astrolift_workflows.activities.capability_deprovision._resolve_capability_driver", forbidden
    )
    with subject(world):
        result = getattr(LifecycleMutation(), name)(make_info(world.user), input=input)
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert _state() == before


@pytest.mark.parametrize("invalid", ["deleted", "inactive", "foreign"])
def test_migration_checks_actual_destination_cluster_before_workflow_or_binding_changes(
    world, invalid, monkeypatch
):
    from astrolift_lifecycle.schema.mutations import MigrateAppInputGql
    from core.tests.utils.scope_world import ScopeWorld, make_cluster

    bind_role(
        world.user, permissions=[Permission.APP_DEPLOY], kind="ORG", scope_id=world.org.pk, slug="migration"
    )
    target = make_cluster(
        ScopeWorld("migration-destination-2104") if invalid == "foreign" else world, "migration-target-2104"
    )
    if invalid == "deleted":
        target.soft_delete()
    elif invalid == "inactive":
        target.is_active = False
        target.save()
    before = _state()

    def forbidden(*args, **kwargs):
        pytest.fail("invalid migration destination reached a workflow")

    monkeypatch.setattr("astrolift_lifecycle.schema.mutations.migration.start_workflow", forbidden)
    with subject(world):
        result = LifecycleMutation().migrate_app_to_cluster(
            make_info(world.user),
            input=MigrateAppInputGql(
                app_environment_id=str(world.rows["medops"]["environment"].guid),
                target_cluster_id=str(target.guid),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert _state() == before
