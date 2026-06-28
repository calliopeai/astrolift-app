"""Deploy-before-commit race guard (#1025).

The deploy mutations create the ``Deployment`` row and start its Temporal
workflow inside the same ``transaction.atomic()`` block. A worker could then
claim the run and read the deployment before that row was committed, leaving a
freshly-registered app's first deploy stuck ``pending`` (the worker saw nothing
to act on) until something re-triggered it.

The fix registers the workflow start via ``transaction.on_commit`` so it only
fires once the row is durable. These tests assert that contract directly: with
``on_commit`` captured (not executed), no ``start_workflow`` happens during the
mutation; the start only appears once the post-commit callbacks run.
"""

from __future__ import annotations

import pytest
from django.db import transaction

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.mutations import (
    DeploymentByIdInput,
    LifecycleMutation,
    StartDeploymentInput,
)
from astrolift_workflows.client import WorkflowHandle
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _grant_all(resolver):
    for p in (Permission.APP_DEPLOY, Permission.APP_ROLLBACK):
        resolver.grant(p)


def _tenant_for(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


@pytest.fixture
def deferred_temporal(monkeypatch):
    """Capture ``start_workflow`` calls and ``transaction.on_commit``
    callbacks *without* running the callbacks, so a test can assert the
    deploy start is deferred past commit and then fire the hooks itself."""
    starts: list[tuple[str, list, str]] = []
    callbacks: list = []

    def _start(name, args, *, workflow_id, task_queue=None):
        starts.append((name, list(args), workflow_id))
        return WorkflowHandle(workflow_id=workflow_id, run_id=f"run-{len(starts)}", enqueued=True)

    monkeypatch.setattr("astrolift_lifecycle.schema.mutations.start_workflow", _start)
    monkeypatch.setattr(transaction, "on_commit", lambda fn, using=None: callbacks.append(fn))
    return starts, callbacks


def test_start_deployment_defers_workflow_until_commit(
    org, app, env, actor, fake_info, permission_resolver, deferred_temporal
):
    starts, callbacks = deferred_temporal
    _grant_all(permission_resolver)
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

    assert result.ok, result.errors
    assert result.data.status == Deployment.Status.PENDING.value

    # The defect: starting inside the atomic block races the row commit.
    # The start must be deferred, so nothing has fired yet — only a
    # post-commit callback should be queued.
    assert starts == [], "workflow started before the deployment row committed"
    assert len(callbacks) == 1

    for fn in callbacks:
        fn()

    assert len(starts) == 1
    name, _args, _wf_id = starts[0]
    assert name == "DeployAppWorkflow"

    # The WorkflowRun mirror link is written by the post-commit callback.
    deploy = Deployment.objects.get(guid=str(result.data.id))
    assert deploy.workflow_run_id is not None


def test_rollback_defers_workflow_until_commit(
    org, app, env, actor, fake_info, permission_resolver, deferred_temporal
):
    starts, callbacks = deferred_temporal
    _grant_all(permission_resolver)
    mut = LifecycleMutation()

    Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user=actor,
        trigger_kind="manual",
        status=Deployment.Status.SUPERSEDED.value,
        image_tag="v0.9.0",
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
    assert starts == [], "rollback workflow started before the new deploy row committed"
    assert len(callbacks) == 1

    for fn in callbacks:
        fn()

    assert [s[0] for s in starts] == ["RollbackDeploymentWorkflow"]
