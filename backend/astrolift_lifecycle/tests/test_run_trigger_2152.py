# ruff: noqa: F811  (pytest fixtures imported from test_task_run)
"""Who and what started a lifecycle run (#2152).

A ``runTask`` call made with an API token is an API run, not a manual one;
a deploy workflow's ``WorkflowRun`` mirror takes its trigger from the
deployment (and from the token, for a manual-shaped deploy); and job runs
expose their initiator on the GraphQL type.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_lifecycle.models import Deployment, ScheduledJobRun, TaskRun
from astrolift_lifecycle.schema.mutations import LifecycleMutation, RunTaskInput
from astrolift_lifecycle.schema.mutations.helpers import _start_deploy_workflow_on_commit
from astrolift_lifecycle.schema.types import scheduled_job_run_to_type
from astrolift_lifecycle.tests.test_task_run import (  # noqa: F401 - fixtures
    _admin_info,
    app,
    cluster,
    env,
    task_workload,
    user,
)
from astrolift_registry.models import Workload
from astrolift_workflows.client import WorkflowHandle
from astrolift_workflows.inputs import Actor
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def as_token(monkeypatch):
    """Make the request look token-authenticated, with a token that allows everything."""
    token = SimpleNamespace(pk=7, team_id=None, organization_id=None, scopes=["admin"])
    monkeypatch.setattr("astrolift_identity.api_tokens.get_current_api_token", lambda: token)
    monkeypatch.setattr("astrolift_identity.api_tokens.token_scope_allows_permission", lambda *_a: True)
    return token


def test_run_task_through_a_token_is_an_api_run(
    app, task_workload, env, user, org, permission_resolver, as_token
):
    permission_resolver.grant(Permission.APP_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = LifecycleMutation().run_task(
            _admin_info(user),
            input=RunTaskInput(app_slug="task-app", workload_slug="db-migrate", environment_name="staging"),
        )
    assert result.ok, result.errors
    run = TaskRun.objects.get(guid=str(result.data.id))
    assert (run.trigger_kind, run.triggered_by_user_id) == ("api", user.id)


@pytest.fixture
def started(monkeypatch):
    calls: list[str] = []

    def _start(name, args, *, workflow_id, task_queue=None):
        calls.append(workflow_id)
        return WorkflowHandle(workflow_id=workflow_id, run_id="r-1", enqueued=True)

    monkeypatch.setattr("astrolift_lifecycle.schema.mutations.helpers.start_workflow", _start)
    return calls


def _deploy_mirror(app, env, user, trigger_kind, django_capture_on_commit_callbacks):
    deployment = Deployment.objects.create(registered_app=app, app_environment=env, trigger_kind=trigger_kind)
    with django_capture_on_commit_callbacks(execute=True):
        _start_deploy_workflow_on_commit(
            deployment=deployment,
            workflow_kind="DeployAppWorkflow",
            workflow_id=f"DeployAppWorkflow-{deployment.guid}",
            args=[],
            organization_id=app.organization_id,
            registered_app_id=app.pk,
            app_environment_id=env.pk,
            actor=Actor(kind="user", user_id=user.pk, display=user.username),
        )
    deployment.refresh_from_db()
    return deployment.workflow_run


@pytest.mark.parametrize(
    ("deploy_trigger", "expected"),
    [
        ("manual", "manual"),
        ("rollback", "manual"),
        ("push", "webhook"),
        ("ci", "api"),
        ("scheduled", "schedule"),
    ],
)
def test_deploy_mirror_takes_the_deployments_trigger(
    app, env, user, started, django_capture_on_commit_callbacks, deploy_trigger, expected
):
    run = _deploy_mirror(app, env, user, deploy_trigger, django_capture_on_commit_callbacks)
    assert (run.trigger_kind, run.trigger_actor_user_id) == (expected, user.pk)


def test_token_started_manual_deploy_mirror_is_api(
    app, env, user, started, django_capture_on_commit_callbacks, as_token
):
    run = _deploy_mirror(app, env, user, "manual", django_capture_on_commit_callbacks)
    assert run.trigger_kind == "api"


def test_job_run_type_exposes_its_initiator(app, env, user):
    job = Workload.objects.create(
        registered_app=app, name="Nightly", slug="nightly", kind=Workload.Kind.CRONJOB
    )
    scheduled = ScheduledJobRun.objects.create(workload=job, app_environment=env)
    manual = ScheduledJobRun.objects.create(
        workload=job, app_environment=env, trigger_kind=ScheduledJobRun.TriggerKind.MANUAL, triggered_by=user
    )
    with tenant_context(TenantContext(organization_id=app.organization_id, actor_user_id=user.id)):
        fired, ran = scheduled_job_run_to_type(scheduled), scheduled_job_run_to_type(manual)
    assert (fired.trigger_kind, fired.triggered_by_user_id, fired.triggered_by_me) == (
        "scheduled",
        None,
        False,
    )
    assert (ran.trigger_kind, ran.triggered_by_user_id, ran.triggered_by_me) == ("manual", str(user.pk), True)
