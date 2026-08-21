"""Singular detail resolvers for run entities + their tenancy (#1118).

Backs the list→detail views (#1106): a cold deep-link to a run outside the
100-row list window must resolve by id rather than showing not-found. Each
singular resolver is org-scoped — an unscoped by-id lookup is a cross-tenant
leak because these models reach the tenant only through
``workload``/``registered_app`` and their default manager is *not*
tenant-aware (``@tenant_scoped`` only asserts a tenant exists; it does not
filter).

Coverage per resolver (astroliftTaskRun / astroliftScheduledJobRun /
astroliftCommandRun):

* found-by-id happy path (same org)
* another org's row → ``None`` (tenancy — the load-bearing assertion)
* missing / unknown id → ``None``

Plus regressions for the scoping this ticket adds to the reference
``astroliftDeployment(id)`` resolver, the ``astroliftScheduledJobRuns`` /
``astroliftCommandRuns`` *list* resolvers, and the deployment-by-id
sub-resource resolvers (``astroliftDeploymentLog`` /
``astroliftDeploymentApprovalHistory``) — all shared the same latent leak
(they relied on manager-level scoping that no model actually opts into).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Team
from astrolift_lifecycle.models import (
    AppEnvironment,
    CommandRun,
    Deployment,
    ScheduledJobRun,
    TaskRun,
)
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_registry.models import RegisteredApp, Workload
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

User = get_user_model()


def _info(user=None):
    """`info`-shaped stub accepted by the resolvers (viewer + request)."""
    request = SimpleNamespace(user=user, auth=None)
    return SimpleNamespace(context=SimpleNamespace(request=request))


def _unknown_guid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def user():
    return User.objects.create_user(
        username="ops-1118",
        email="ops-1118@example.com",
        password="x",
    )


@pytest.fixture
def task_workload(app):
    return Workload.objects.create(registered_app=app, name="migrate", slug="migrate", kind="task")


@pytest.fixture
def cron_workload(app):
    return Workload.objects.create(registered_app=app, name="nightly", slug="nightly", kind="cronjob")


@pytest.fixture
def other_org():
    """A sibling tenant with its own app / env / workload and one run of
    each kind. Every by-id resolver must refuse to return these rows to a
    caller scoped to the primary ``org``.
    """
    org2 = Organization.objects.create(name="Other", slug="other-1118")
    team2 = Team.objects.create(organization=org2, name="other-team", slug="other-team-1118")
    app2 = RegisteredApp.objects.create(
        organization=org2,
        team=team2,
        name="other-app",
        slug="other-app",
        manifest_raw="name = 'other-app'\n",
    )
    # Seeded directly; the plugin row is scaffolding for this test.
    [plugin2] = ProviderPlugin.objects.bulk_create([ProviderPlugin(name="other-k8s", slug="other-k8s-1118")])
    cluster2 = TenantCluster.objects.create(
        organization=org2,
        name="other-cluster",
        slug="other-cluster-1118",
        provider_plugin=plugin2,
        provider_config={},
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
    )
    env2 = AppEnvironment.objects.create(registered_app=app2, name="prod", tenant_cluster=cluster2)
    wl2 = Workload.objects.create(registered_app=app2, name="other-wl", slug="other-wl", kind="task")
    return SimpleNamespace(
        org=org2,
        app=app2,
        env=env2,
        task_run=TaskRun.objects.create(workload=wl2, status=TaskRun.Status.SUCCEEDED),
        job_run=ScheduledJobRun.objects.create(
            workload=wl2, app_environment=env2, status=ScheduledJobRun.Status.SUCCEEDED
        ),
        command_run=CommandRun.objects.create(registered_app=app2, command=["whoami"]),
        deployment=Deployment.objects.create(
            registered_app=app2,
            app_environment=env2,
            trigger_kind="manual",
            status=Deployment.Status.RUNNING.value,
            image_tag="v9",
        ),
    )


# ---------------------------------------------------------------------------
# astroliftTaskRun(id)
# ---------------------------------------------------------------------------


def test_task_run_found_by_id(task_workload, org, user, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    run = TaskRun.objects.create(workload=task_workload, status=TaskRun.Status.SUCCEEDED)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = LifecycleQuery().astrolift_task_run(_info(user), id=str(run.guid))

    assert result is not None
    assert str(result.id) == str(run.guid)
    assert result.registered_app_slug == task_workload.registered_app.slug


def test_task_run_other_org_is_not_found(task_workload, org, user, other_org, permission_resolver):
    """A task run owned by another org must not resolve by id."""
    permission_resolver.grant(Permission.APP_READ_LOGS)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = LifecycleQuery().astrolift_task_run(_info(user), id=str(other_org.task_run.guid))

    assert result is None, "cross-tenant leak: other org's task run resolved by id"


def test_task_run_unknown_id_is_not_found(org, user, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = LifecycleQuery().astrolift_task_run(_info(user), id=_unknown_guid())

    assert result is None


# ---------------------------------------------------------------------------
# astroliftScheduledJobRun(id)
# ---------------------------------------------------------------------------


def test_scheduled_job_run_found_by_id(cron_workload, env, org, user, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    run = ScheduledJobRun.objects.create(
        workload=cron_workload, app_environment=env, status=ScheduledJobRun.Status.SUCCEEDED
    )

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = LifecycleQuery().astrolift_scheduled_job_run(_info(user), id=str(run.guid))

    assert result is not None
    assert str(result.id) == str(run.guid)
    assert result.workload_slug == cron_workload.slug


def test_scheduled_job_run_other_org_is_not_found(org, user, other_org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = LifecycleQuery().astrolift_scheduled_job_run(_info(user), id=str(other_org.job_run.guid))

    assert result is None, "cross-tenant leak: other org's scheduled job run resolved by id"


def test_scheduled_job_run_unknown_id_is_not_found(org, user, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = LifecycleQuery().astrolift_scheduled_job_run(_info(user), id=_unknown_guid())

    assert result is None


# ---------------------------------------------------------------------------
# astroliftCommandRun(id)
# ---------------------------------------------------------------------------


def test_command_run_found_by_id(app, org, user, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    run = CommandRun.objects.create(registered_app=app, command=["ls", "-la"])

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = LifecycleQuery().astrolift_command_run(_info(user), id=str(run.guid))

    assert result is not None
    assert str(result.id) == str(run.guid)
    assert result.registered_app_slug == app.slug
    assert result.command == ["ls", "-la"]


def test_command_run_other_org_is_not_found(org, user, other_org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = LifecycleQuery().astrolift_command_run(_info(user), id=str(other_org.command_run.guid))

    assert result is None, "cross-tenant leak: other org's command run resolved by id"


def test_command_run_unknown_id_is_not_found(org, user, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = LifecycleQuery().astrolift_command_run(_info(user), id=_unknown_guid())

    assert result is None


# ---------------------------------------------------------------------------
# Regressions for the reference resolver + list resolvers this ticket rescoped
# ---------------------------------------------------------------------------


def test_deployment_found_by_id_same_org(app, env, org, user, permission_resolver):
    permission_resolver.grant(Permission.APP_READ)
    deploy = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind="manual",
        status=Deployment.Status.RUNNING.value,
        image_tag="v1",
    )

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = LifecycleQuery().astrolift_deployment(_info(user), id=str(deploy.guid))

    assert result is not None
    assert str(result.id) == str(deploy.guid)


def test_deployment_other_org_is_not_found(org, user, other_org, permission_resolver):
    """Regression: astroliftDeployment(id) was scoped only by a manager that
    no model opts into, so it returned any org's deployment by guid."""
    permission_resolver.grant(Permission.APP_READ)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = LifecycleQuery().astrolift_deployment(_info(user), id=str(other_org.deployment.guid))

    assert result is None, "cross-tenant leak: other org's deployment resolved by id"


def test_scheduled_job_runs_list_tenant_isolated(
    cron_workload, env, org, user, other_org, permission_resolver
):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    ScheduledJobRun.objects.create(
        workload=cron_workload, app_environment=env, status=ScheduledJobRun.Status.SUCCEEDED
    )

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        rows = LifecycleQuery().astrolift_scheduled_job_runs(_info(user), limit=100)

    slugs = {r.registered_app_slug for r in rows}
    assert "hello-app" in slugs
    assert "other-app" not in slugs, "cross-tenant leak: other org's scheduled job run listed"


def test_command_runs_list_tenant_isolated(app, org, user, other_org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    CommandRun.objects.create(registered_app=app, command=["ls"])

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        rows = LifecycleQuery().astrolift_command_runs(_info(user), limit=100)

    slugs = {r.registered_app_slug for r in rows}
    assert "hello-app" in slugs
    assert "other-app" not in slugs, "cross-tenant leak: other org's command run listed"


# ---------------------------------------------------------------------------
# Deployment-by-id sub-resource resolvers — same latent leak, same fix.
# These are the sibling queries the deployment detail page fires alongside
# astroliftDeployment(id); scoping the header but not these would leave the
# logs / approver PII fetchable cross-org on a deep link.
# ---------------------------------------------------------------------------


def test_deployment_log_other_org_is_empty(org, user, other_org, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    from astrolift_lifecycle.models import DeploymentLog

    DeploymentLog.objects.create(deployment=other_org.deployment, status="running")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        rows = LifecycleQuery().astrolift_deployment_log(
            _info(user), deployment_id=str(other_org.deployment.guid)
        )

    assert rows == [], "cross-tenant leak: other org's deployment log returned by id"


def test_deployment_approval_history_other_org_is_empty(org, user, other_org, permission_resolver):
    """The approval trail carries approver display names + emails — a
    sibling-org deployment id must yield an empty trail, not that PII."""
    permission_resolver.grant(Permission.APP_READ)
    from astrolift_operations.models import AuditEvent

    AuditEvent.objects.create(
        actor_kind="user",
        actor_display="Sibling Approver",
        action="deployment.approve",
        target_id=str(other_org.deployment.guid),
    )

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        rows = LifecycleQuery().astrolift_deployment_approval_history(
            _info(user), deployment_id=str(other_org.deployment.guid)
        )

    assert rows == [], "cross-tenant leak: other org's approval history returned by id"
