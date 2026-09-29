"""Why a deploy failed, or what a pending one waits on, in one line (#2123).

On CONFLICT four deploys sat pending for over twenty minutes and nothing on
the page said why; a failure's reason was readable only over GraphQL.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.types import deployment_status_reason

pytestmark = pytest.mark.django_db


def _deploy(app, env, status, *, minutes_ago=0, **fields):
    d = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=status,
        **fields,
    )
    if minutes_ago:
        Deployment.objects.filter(pk=d.pk).update(
            created_at=timezone.now() - dt.timedelta(minutes=minutes_ago)
        )
        d.refresh_from_db()
    return d


def test_a_failure_leads_with_the_abort_reason(app, env):
    d = _deploy(app, env, "failed", aborted_reason="superseded by a newer deploy", build_error="exit 1")

    assert deployment_status_reason(d) == "superseded by a newer deploy"


def test_a_build_failure_gives_the_first_line_of_the_build_error(app, env):
    d = _deploy(app, env, "failed", build_error="\n  error: failed to solve: dockerfile not found\nmore")

    assert deployment_status_reason(d) == "error: failed to solve: dockerfile not found"


def test_a_failure_with_nothing_recorded_says_so(app, env):
    assert "no recorded reason" in deployment_status_reason(_deploy(app, env, "failed"))


def test_a_pending_deploy_names_the_deploy_it_is_queued_behind(app, env):
    _deploy(app, env, "deploying", minutes_ago=5, image_tag="main-abc1234")
    d = _deploy(app, env, "pending")

    assert deployment_status_reason(d) == "Queued behind deploy main-abc1234, still deploying."


def test_a_pending_deploy_with_nothing_ahead_that_never_started_reads_as_stuck(app, env):
    d = _deploy(app, env, "pending", minutes_ago=25)

    reason = deployment_status_reason(d)
    assert reason.startswith("Not started after 25 minutes")
    assert "worker" in reason


def test_a_fresh_pending_deploy_is_waiting_for_its_workflow(app, env):
    assert (
        deployment_status_reason(_deploy(app, env, "pending")) == "Waiting for the deploy workflow to start."
    )


def test_an_approval_gate_says_how_far_along_it_is(app, env):
    d = _deploy(app, env, "pending_approval", approvals_required=2, approvals_received=1)

    assert deployment_status_reason(d) == "Waiting for approval: 1 of 2."


def test_a_running_deploy_has_no_reason(app, env):
    assert deployment_status_reason(_deploy(app, env, "running")) == ""
