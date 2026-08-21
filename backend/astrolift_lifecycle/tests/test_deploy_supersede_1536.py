"""
supersede_in_flight_deploys (#1536) — new deploys cancel stacked old ones.

Pins the entry-point contract: a live workflow gets the abort signal and
its (newest) row is left for its own bookkeeping; rows whose workflow is
dead are failed out directly with a reason; terminal rows are untouched;
and a signal failure never blocks the caller.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.supersede import supersede_in_flight_deploys

pytestmark = pytest.mark.django_db


def _deploy(app, env, *, status, created_at) -> Deployment:
    dep = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=status,
        image_tag="v1",
    )
    Deployment.objects.filter(pk=dep.pk).update(created_at=created_at)
    dep.refresh_from_db()
    return dep


def test_dead_workflow_rows_fail_out_with_reason(app, env, monkeypatch):
    import astrolift_workflows.client as wf_client

    monkeypatch.setattr(wf_client, "signal_workflow", lambda *a, **k: False)
    now = timezone.now()
    stuck = _deploy(app, env, status=Deployment.Status.DEPLOYING.value, created_at=now - timedelta(hours=3))
    pending = _deploy(app, env, status=Deployment.Status.PENDING.value, created_at=now - timedelta(hours=2))

    assert supersede_in_flight_deploys(app, env) == 2

    stuck.refresh_from_db()
    pending.refresh_from_db()
    assert stuck.status == Deployment.Status.FAILED.value
    assert pending.status == Deployment.Status.FAILED.value
    assert "superseded" in stuck.aborted_reason


def test_live_workflow_keeps_newest_row_for_self_marking(app, env, monkeypatch):
    import astrolift_workflows.client as wf_client

    signalled: list[tuple] = []

    def _signal(wf_id, name, *args):
        signalled.append((wf_id, name))
        return True

    monkeypatch.setattr(wf_client, "signal_workflow", _signal)
    now = timezone.now()
    dead = _deploy(app, env, status=Deployment.Status.DEPLOYING.value, created_at=now - timedelta(hours=2))
    live = _deploy(app, env, status=Deployment.Status.DEPLOYING.value, created_at=now - timedelta(minutes=5))

    assert supersede_in_flight_deploys(app, env) == 1

    dead.refresh_from_db()
    live.refresh_from_db()
    assert dead.status == Deployment.Status.FAILED.value
    # The live workflow observed the abort signal and owns its own
    # FAILED transition — superseding it here would race it.
    assert live.status == Deployment.Status.DEPLOYING.value
    assert signalled == [(f"DeployAppWorkflow-{app.guid}-{env.guid}", "abort")]


def test_terminal_rows_untouched_and_signal_error_is_swallowed(app, env, monkeypatch):
    import astrolift_workflows.client as wf_client

    def _boom(*a, **k):
        raise RuntimeError("temporal down")

    monkeypatch.setattr(wf_client, "signal_workflow", _boom)
    now = timezone.now()
    done = _deploy(app, env, status=Deployment.Status.RUNNING.value, created_at=now - timedelta(hours=1))

    assert supersede_in_flight_deploys(app, env) == 0
    done.refresh_from_db()
    assert done.status == Deployment.Status.RUNNING.value
