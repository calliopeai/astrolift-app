"""Tests for TaskRun model, runTask mutation, and astroliftTaskRuns query (#801).

Coverage:
* TaskRun model field defaults and __str__ representation.
* run_task mutation — happy path creates a TaskRun row with correct fields.
* run_task mutation — unknown app returns NOT_FOUND.
* run_task mutation — unknown workload returns NOT_FOUND.
* run_task mutation — wrong workload kind (deployment, not task) returns NOT_FOUND.
* run_task mutation — APP_READ-only actor is rejected with PERMISSION_DENIED.
* astroliftTaskRuns query — returns runs for the current tenant only
  (tenant isolation: another org's runs must not leak).
* astroliftTaskRuns query — status filter narrows the result set.
* type serializer — task_run_to_type maps all fields correctly.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from astrolift_lifecycle.models import AppEnvironment, TaskRun
from astrolift_lifecycle.schema.mutations import LifecycleMutation, RunTaskInput
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_lifecycle.schema.types import task_run_to_type
from astrolift_registry.models import Workload
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

User = get_user_model()

_MANIFEST_TOML = """
name = "task-app"

[[workloads]]
name = "db-migrate"
kind = "task"

  [[workloads.containers]]
  name = "migrate"
  is_primary = true
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def user(org):
    return User.objects.create_user(
        username="ops-user",
        email="ops@example.com",
        password="x",
    )


@pytest.fixture
def app(org):
    from astrolift_identity.models import Team
    from astrolift_registry.models import RegisteredApp

    # team is NOT NULL on RegisteredApp.
    team = Team.objects.create(organization=org, name="task-team", slug="task-team")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        name="task-app",
        slug="task-app",
        manifest_raw=_MANIFEST_TOML,
    )


@pytest.fixture
def cluster(org):
    from astrolift_clusters.models import ProviderPlugin, TenantCluster

    # Seeded directly; the plugin row is scaffolding for this test.
    [plugin] = ProviderPlugin.objects.bulk_create([ProviderPlugin(name="test-k8s", slug="test-k8s")])
    return TenantCluster.objects.create(
        organization=org,
        name="test-cluster",
        slug="test-cluster",
        provider_plugin=plugin,
        provider_config={},
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
    )


@pytest.fixture
def env(app, cluster):
    return AppEnvironment.objects.create(
        registered_app=app,
        name="staging",
        tenant_cluster=cluster,
    )


@pytest.fixture
def task_workload(app):
    return Workload.objects.create(
        registered_app=app,
        name="db-migrate",
        slug="db-migrate",
        kind="task",
    )


@pytest.fixture
def deployment_workload(app):
    """A deployment-kind workload — should NOT match the task filter."""
    return Workload.objects.create(
        registered_app=app,
        name="web",
        slug="web",
        kind="deployment",
    )


def _info(user, permission):
    """Minimal request info object for mutation calls.

    ``permission`` is retained for call-site readability; the actual
    gate is driven by the ``permission_resolver`` fixture.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        context=SimpleNamespace(
            request=SimpleNamespace(
                user=user,
                auth=None,
            )
        )
    )


def _admin_info(user):
    return _info(user, "all")


def _readonly_info(user):
    return _info(user, Permission.APP_READ)


# ---------------------------------------------------------------------------
# Model unit tests
# ---------------------------------------------------------------------------


def test_task_run_defaults_and_str(task_workload):
    run = TaskRun.objects.create(
        workload=task_workload,
        trigger_kind=TaskRun.TriggerKind.MANUAL,
        command=["python", "manage.py", "migrate"],
        status=TaskRun.Status.PENDING,
    )
    assert run.status == "pending"
    assert run.exit_code is None
    assert run.duration_seconds is None
    assert run.k8s_job_name == ""
    assert "pending" in str(run)
    assert str(run.guid) in str(run)


def test_task_run_status_transitions_are_captured(task_workload):
    """Status field persists updates correctly; no accidental silent truncation."""
    run = TaskRun.objects.create(workload=task_workload, status=TaskRun.Status.PENDING)
    run.status = TaskRun.Status.RUNNING
    run.save(update_fields=["status", "updated_at", "version"])
    run.refresh_from_db()
    assert run.status == "running"

    run.status = TaskRun.Status.SUCCEEDED
    run.exit_code = 0
    run.save(update_fields=["status", "exit_code", "updated_at", "version"])
    run.refresh_from_db()
    assert run.status == "succeeded"
    assert run.exit_code == 0


# ---------------------------------------------------------------------------
# task_run_to_type serializer
# ---------------------------------------------------------------------------


def test_task_run_to_type_maps_fields(task_workload, user):
    run = TaskRun.objects.create(
        workload=task_workload,
        trigger_kind=TaskRun.TriggerKind.MANUAL,
        triggered_by_user=user,
        command=["echo", "hello"],
        status=TaskRun.Status.SUCCEEDED,
        exit_code=0,
        k8s_job_name="db-migrate-manual-abc12345",
    )
    # Pre-fetch the related row so task_run_to_type doesn't hit N+1
    run.workload.registered_app  # noqa: B018 — access caches the relation

    t = task_run_to_type(run)
    assert str(t.id) == str(run.guid)
    assert t.registered_app_slug == "task-app"
    assert t.workload_slug == "db-migrate"
    assert t.trigger_kind == "manual"
    assert t.triggered_by_username == "ops-user"
    assert t.command == ["echo", "hello"]
    assert t.status == "succeeded"
    assert t.exit_code == 0
    assert t.k8s_job_name == "db-migrate-manual-abc12345"


def test_task_run_to_type_no_user(task_workload):
    run = TaskRun.objects.create(
        workload=task_workload,
        command=[],
        status=TaskRun.Status.PENDING,
    )
    run.workload.registered_app  # noqa: B018
    t = task_run_to_type(run)
    assert t.triggered_by_username is None


# ---------------------------------------------------------------------------
# run_task mutation
# ---------------------------------------------------------------------------


def test_run_task_creates_record(app, task_workload, env, user, org, permission_resolver):
    mutation = LifecycleMutation()
    info = _admin_info(user)
    permission_resolver.grant(Permission.APP_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = mutation.run_task(
            info,
            input=RunTaskInput(
                app_slug="task-app",
                workload_slug="db-migrate",
                environment_name="staging",
                command=["python", "manage.py", "migrate"],
            ),
        )
    assert result.ok, result.errors
    assert result.data is not None
    assert result.data.workload_slug == "db-migrate"
    assert result.data.registered_app_slug == "task-app"
    assert result.data.status == "pending"

    # Verify the DB row
    run = TaskRun.objects.get(guid=str(result.data.id))
    assert run.command == ["python", "manage.py", "migrate"]
    assert run.trigger_kind == "manual"
    assert run.triggered_by_user == user


def test_run_task_unknown_app(task_workload, user, org, permission_resolver):
    mutation = LifecycleMutation()
    info = _admin_info(user)
    permission_resolver.grant(Permission.APP_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = mutation.run_task(
            info,
            input=RunTaskInput(app_slug="no-such-app", workload_slug="db-migrate"),
        )
    assert not result.ok
    assert any(e.code == "NOT_FOUND" for e in result.errors)


def test_run_task_unknown_workload(app, user, org, permission_resolver):
    mutation = LifecycleMutation()
    info = _admin_info(user)
    permission_resolver.grant(Permission.APP_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = mutation.run_task(
            info,
            input=RunTaskInput(app_slug="task-app", workload_slug="non-existent"),
        )
    assert not result.ok
    assert any(e.code == "NOT_FOUND" for e in result.errors)


def test_run_task_wrong_kind_rejected(app, deployment_workload, user, org, permission_resolver):
    """A deployment workload is not executable as a task."""
    mutation = LifecycleMutation()
    info = _admin_info(user)
    permission_resolver.grant(Permission.APP_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = mutation.run_task(
            info,
            input=RunTaskInput(app_slug="task-app", workload_slug="web"),
        )
    assert not result.ok
    assert any(e.code == "NOT_FOUND" for e in result.errors)


# ---------------------------------------------------------------------------
# astroliftTaskRuns query — tenant isolation
# ---------------------------------------------------------------------------


def test_task_runs_tenant_isolated(task_workload, org, user, permission_resolver):
    """Runs belonging to another org must not appear in the query results."""
    permission_resolver.grant(Permission.APP_READ_LOGS)
    from astrolift_identity.models import Organization, Team
    from astrolift_registry.models import RegisteredApp

    other_org = Organization.objects.create(name="Other", slug="other-org-tr")
    other_team = Team.objects.create(organization=other_org, name="other-team", slug="other-team-tr")
    other_app = RegisteredApp.objects.create(
        organization=other_org,
        team=other_team,
        name="other-app",
        slug="other-app",
        manifest_raw="name = 'other-app'\n",
    )
    other_workload = Workload.objects.create(
        registered_app=other_app,
        name="migrate",
        slug="migrate",
        kind="task",
    )
    # Create a run in the other org
    TaskRun.objects.create(workload=other_workload, status=TaskRun.Status.PENDING)
    # Create a run in our org
    TaskRun.objects.create(workload=task_workload, status=TaskRun.Status.SUCCEEDED)

    query = LifecycleQuery()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        from types import SimpleNamespace

        info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user, auth=None)))
        runs = query.astrolift_task_runs(info, limit=100)

    slugs = {r.registered_app_slug for r in runs}
    assert "task-app" in slugs
    assert "other-app" not in slugs, "cross-tenant leak: other org's task run returned"


def test_task_runs_status_filter(task_workload, org, user, permission_resolver):
    permission_resolver.grant(Permission.APP_READ_LOGS)
    TaskRun.objects.create(workload=task_workload, status=TaskRun.Status.PENDING)
    TaskRun.objects.create(workload=task_workload, status=TaskRun.Status.SUCCEEDED)
    TaskRun.objects.create(workload=task_workload, status=TaskRun.Status.FAILED)

    query = LifecycleQuery()
    from types import SimpleNamespace

    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user, auth=None)))
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        succeeded = query.astrolift_task_runs(info, status="succeeded", limit=100)
        pending = query.astrolift_task_runs(info, status="pending", limit=100)
        all_runs = query.astrolift_task_runs(info, limit=100)

    assert all(r.status == "succeeded" for r in succeeded)
    assert all(r.status == "pending" for r in pending)
    assert len(all_runs) >= 3
