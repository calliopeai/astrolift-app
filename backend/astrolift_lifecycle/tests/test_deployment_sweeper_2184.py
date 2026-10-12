"""Deployments whose deploy workflow is gone are failed by the reconcile tick (#2184).

Temporal is faked at ``workflow_run_state``; the bound run id at
``_bound_run_id``. A deployment is failed only when its workflow is
closed, never ran, or is running a run this deployment did not bind to.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

import astrolift_workflows.client as wf_client
from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.services import deployment_sweeper
from astrolift_lifecycle.services.deployment_sweeper import REASON, sweep_stuck_deployments
from astrolift_workflows.client import WorkflowRunState

pytestmark = pytest.mark.django_db


def _deploy(app, env, *, status=Deployment.Status.DEPLOYING.value, age=timedelta(hours=1)) -> Deployment:
    dep = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=status,
        image_tag="v1",
    )
    Deployment.objects.filter(pk=dep.pk).update(updated_at=timezone.now() - age)
    dep.refresh_from_db()
    return dep


@pytest.fixture
def temporal(monkeypatch):
    answers: dict[str, WorkflowRunState | None] = {}
    asked: list[str] = []

    def _state(workflow_id):
        asked.append(workflow_id)
        return answers.get(workflow_id)

    monkeypatch.setattr(wf_client, "workflow_run_state", _state)
    return answers, asked


def _bound(monkeypatch, run_id):
    monkeypatch.setattr(deployment_sweeper, "_bound_run_id", lambda deployment: run_id)


@pytest.mark.parametrize(
    "state",
    [WorkflowRunState(running=False, run_id="r1"), WorkflowRunState(running=False, run_id="")],
    ids=["closed", "never-ran"],
)
def test_a_deployment_whose_workflow_is_closed_or_missing_is_failed(app, env, temporal, state):
    answers, _asked = temporal
    dep = _deploy(app, env)
    answers[f"DeployAppWorkflow-{app.guid}-{env.guid}"] = state

    assert sweep_stuck_deployments() == 1

    dep.refresh_from_db()
    assert dep.status == Deployment.Status.FAILED.value
    assert dep.aborted_reason == REASON


def test_a_running_workflow_on_another_run_fails_the_deployment(app, env, temporal, monkeypatch):
    answers, _asked = temporal
    _bound(monkeypatch, "old-run")
    dep = _deploy(app, env, status=Deployment.Status.PENDING.value)
    answers[f"DeployAppWorkflow-{app.guid}-{env.guid}"] = WorkflowRunState(running=True, run_id="new-run")

    assert sweep_stuck_deployments() == 1
    dep.refresh_from_db()
    assert dep.status == Deployment.Status.FAILED.value


@pytest.mark.parametrize("bound", ["the-run", ""], ids=["its-own-run", "not-bound-yet"])
def test_a_running_workflow_on_its_own_run_or_not_yet_bound_is_left(app, env, temporal, monkeypatch, bound):
    answers, _asked = temporal
    _bound(monkeypatch, bound)
    dep = _deploy(app, env, status=Deployment.Status.REDEPLOYING.value)
    answers[f"DeployAppWorkflow-{app.guid}-{env.guid}"] = WorkflowRunState(running=True, run_id="the-run")

    assert sweep_stuck_deployments() == 0
    dep.refresh_from_db()
    assert dep.status == Deployment.Status.REDEPLOYING.value


def test_an_engine_that_does_not_answer_leaves_the_deployment(app, env, temporal):
    dep = _deploy(app, env)  # no answer: workflow_run_state returns None

    assert sweep_stuck_deployments() == 0
    dep.refresh_from_db()
    assert dep.status == Deployment.Status.DEPLOYING.value


def test_recent_and_settled_deployments_are_not_asked_about(app, env, temporal):
    answers, asked = temporal
    _deploy(app, env, age=timedelta(minutes=5))
    _deploy(app, env, status=Deployment.Status.RUNNING.value)
    _deploy(app, env, status=Deployment.Status.PENDING_APPROVAL.value)
    answers[f"DeployAppWorkflow-{app.guid}-{env.guid}"] = WorkflowRunState(running=False, run_id="")

    assert sweep_stuck_deployments() == 0
    assert asked == []


def test_a_failing_row_does_not_stop_the_sweep(app, env, temporal, monkeypatch):
    answers, _asked = temporal
    first = _deploy(app, env, age=timedelta(hours=2))
    second = _deploy(app, env, age=timedelta(hours=1))
    answers[f"DeployAppWorkflow-{app.guid}-{env.guid}"] = WorkflowRunState(running=False, run_id="")
    real = Deployment.transition_to

    def _transition(self, new_status):
        if self.pk == first.pk:
            raise RuntimeError("boom")
        return real(self, new_status)

    monkeypatch.setattr(Deployment, "transition_to", _transition)

    assert sweep_stuck_deployments() == 1
    second.refresh_from_db()
    assert second.status == Deployment.Status.FAILED.value
