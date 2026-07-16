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


def _activity_error(cause: BaseException | None) -> BaseException:
    """An ActivityError chained onto *cause* the way the Temporal runtime
    delivers it to the workflow's except path."""
    from temporalio.exceptions import ActivityError

    err = ActivityError(
        "Activity task failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="worker@test",
        activity_type="poll_rollout",
        activity_id="1",
        retry_state=None,
    )
    err.__cause__ = cause
    return err


def test_failure_reason_unwraps_activity_error_to_cause_message():
    """#1093 workflow path: str(ActivityError) is the generic 'Activity task
    failed' envelope — the persisted reason must be the innermost cause's
    own message (what the activity actually raised)."""
    from temporalio.exceptions import ApplicationError

    from astrolift_workflows.workflows.deploy_app import _failure_reason

    err = _activity_error(ApplicationError("rollout timed out after 600s: 0/2 ready"))
    assert _failure_reason(err) == "rollout timed out after 600s: 0/2 ready"


def test_failure_reason_is_one_line_and_capped():
    """No stack traces / multi-line dumps in aborted_reason — first line
    only, capped at 500 chars."""
    from temporalio.exceptions import ApplicationError

    from astrolift_workflows.workflows.deploy_app import _failure_reason

    multiline = _activity_error(
        ApplicationError("namespace gone\nTraceback (most recent call last):\n  File ...")
    )
    assert _failure_reason(multiline) == "namespace gone"

    long = _activity_error(ApplicationError("x" * 2000))
    assert len(_failure_reason(long)) == 500


def test_failure_reason_falls_back_to_error_itself_without_cause():
    """A bare ActivityError (no cause chain) still yields a usable reason."""
    from astrolift_workflows.workflows.deploy_app import _failure_reason

    err = _activity_error(None)
    assert _failure_reason(err) == "Activity task failed"


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


def test_mark_failed_records_reason_in_aborted_reason(app, env):
    """#1093: a pre-pipeline refusal (pre_flight) or exhausted-retry failure
    must leave WHY on the row — pending→failed with an empty aborted_reason
    gave operators nothing in the history sidebar."""
    from astrolift_workflows.activities.app_lifecycle import _mark_failed_sync

    d = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind="manual",
        status=Deployment.Status.PENDING.value,
        image_tag="v1.0.0",
    )
    _mark_failed_sync(d.pk, "manifest renders to zero Kubernetes resources")

    d.refresh_from_db()
    assert d.status == Deployment.Status.FAILED.value
    assert "zero Kubernetes resources" in d.aborted_reason


def test_mark_failed_keeps_existing_aborted_reason(app, env):
    """An operator-written reason (reject/abort) wins over the workflow's
    generic activity error."""
    from astrolift_workflows.activities.app_lifecycle import _mark_failed_sync

    d = _make_deploying(app, env)
    d.aborted_reason = "rejected by operator"
    d.save(update_fields=["aborted_reason", "updated_at", "version"])

    _mark_failed_sync(d.pk, "activity error after abort")

    d.refresh_from_db()
    assert d.aborted_reason == "rejected by operator"
