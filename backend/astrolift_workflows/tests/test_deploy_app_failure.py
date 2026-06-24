"""
DeployAppWorkflow terminal-failure handling (#1004).

Before this, every deploy activity ran under Temporal's default
(unlimited) retry policy. A rollout that timed out — a crashlooping
container, or an app deregistered mid-deploy so the target namespace is
gone — re-ran its 600s poll forever, leaving the workflow stuck
``running`` and saturating the worker's sync thread pool (starving
unrelated short activities).

Two guards pin the fix:
  * bounded retry policies so no deploy activity loops forever, and
  * a ``mark_failed`` transition so a terminal failure exits cleanly
    instead of leaving the deployment in ``deploying``.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_workflows.workflows.deploy_app import _ROLLOUT_RETRY, _STANDARD_RETRY


def test_rollout_retry_is_terminal():
    """A rollout poll that fails after its own 600s window won't succeed
    on retry — re-running just re-burns the worker. One attempt only."""
    assert _ROLLOUT_RETRY.maximum_attempts == 1


def test_standard_retry_is_bounded():
    """Transient deploy steps retry a few times then give up — never the
    unbounded default that leaked stuck workflows (#1004)."""
    assert _STANDARD_RETRY.maximum_attempts == 5
    assert _STANDARD_RETRY.maximum_attempts > 1  # still tolerates blips


pytestmark = pytest.mark.django_db


def _make_deploying(app, env):
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind="manual",
        status=Deployment.Status.DEPLOYING.value,
        image_tag="v1.0.0",
    )


def test_mark_failed_transitions_deploying_to_failed(app, env):
    from astrolift_workflows.activities.app_lifecycle import _mark_failed_sync

    d = _make_deploying(app, env)
    _mark_failed_sync(d.pk, "rollout timed out")

    d.refresh_from_db()
    assert d.status == Deployment.Status.FAILED.value
    assert d.failed_at is not None
    assert d.ended_at is not None


def test_mark_failed_is_idempotent(app, env):
    """A deploy already terminal (e.g. aborted-then-failed) must not raise
    on a redundant mark_failed — the workflow's except path may fire after
    an abort already moved it."""
    from astrolift_workflows.activities.app_lifecycle import _mark_failed_sync

    d = _make_deploying(app, env)
    _mark_failed_sync(d.pk, "first failure")
    # Second call is a no-op, not a transition error.
    _mark_failed_sync(d.pk, "second failure")

    d.refresh_from_db()
    assert d.status == Deployment.Status.FAILED.value
