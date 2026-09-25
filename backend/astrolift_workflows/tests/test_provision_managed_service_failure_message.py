"""ProvisionManagedServiceWorkflow's failure-message unwrap (#1916).

Before this, a driver that returned ``ok=False`` (or raised) surfaced as
Temporal's generic ``str(ActivityError)`` -- "Activity task failed" -- on
both the ``ManagedService`` row's ``status_error`` and the workflow's own
result message. Operators had no route to the driver's actual reason short
of the worker's logs. ``DeployPromotedAppWorkflow`` (``dev_environment.py``)
already unwraps this with a ``_cause()`` helper; this pins the identical
unwrap here, plus a redaction pass over the unwrapped message -- config a
driver echoes back can carry a credential-shaped value the platform's own
secret-ref convention doesn't catch.

``test_provision_managed_service_temporal.py`` proves the same thing through
the real Temporal pipeline; these pin the pure unwrap/redact logic directly,
the way ``test_deploy_app_failure.py`` pins ``deploy_app._failure_reason``.
"""

from __future__ import annotations

from astrolift_workflows.workflows.provision_managed_service import _cause, _redact


def _activity_error(cause: BaseException | None) -> BaseException:
    """An ActivityError chained onto *cause* the way the Temporal runtime
    delivers it to the workflow's except path."""
    from temporalio.exceptions import ActivityError

    err = ActivityError(
        "Activity task failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="worker@test",
        activity_type="provision_managed_service",
        activity_id="1",
        retry_state=None,
    )
    err.__cause__ = cause
    return err


def test_cause_unwraps_activity_error_to_the_drivers_message():
    from temporalio.exceptions import ApplicationError

    err = _activity_error(ApplicationError("capacity exhausted"))
    assert _cause(err) == "capacity exhausted"


def test_cause_falls_back_to_the_error_itself_without_a_chained_cause():
    """A bare ActivityError (no cause chain) still yields a usable reason
    rather than raising on the unwrap."""
    err = _activity_error(None)
    assert _cause(err) == "Activity task failed"


def test_cause_unwraps_a_bare_exceptions_type_prefixed_reconstruction():
    """The activity's ``ok=False`` path raises a bare ``RuntimeError``;
    Temporal auto-wraps an un-typed exception as an ``ApplicationError``
    whose string form is ``"<TypeName>: <message>"`` -- the same shape
    ``DeployPromotedAppWorkflow``'s own test pins for its bare
    ``RuntimeError`` path."""
    from temporalio.exceptions import ApplicationError

    err = _activity_error(ApplicationError("driver.provision returned ok=False", type="RuntimeError"))
    assert _cause(err) == "RuntimeError: driver.provision returned ok=False"


def test_redact_masks_a_key_value_credential():
    assert _redact("config rejected: password=hunter2 for host db.internal") == (
        "config rejected: password=***redacted*** for host db.internal"
    )


def test_redact_masks_a_quoted_json_style_credential():
    assert _redact('config rejected: {"password": "hunter2", "host": "db.internal"}') == (
        'config rejected: {"password": "***redacted***", "host": "db.internal"}'
    )


def test_redact_masks_a_credential_embedded_in_a_connection_uri():
    assert _redact("connect failed: postgres://dbadmin:Sup3rSecret!@db.internal:5432/mydb") == (
        "connect failed: postgres://dbadmin:***redacted***@db.internal:5432/mydb"
    )


def test_redact_leaves_ordinary_driver_text_untouched():
    """No false positives on prose that merely contains a sensitive word,
    or on an AWS action name that happens to carry a colon."""
    denial = (
        "AccessDeniedException: User: arn:aws:sts::790347818717:assumed-role/"
        "conflictastro-astrolift-task/x is not authorized to perform: "
        "ecr:CreateRepository"
    )
    assert _redact(denial) == denial
    assert _redact("capacity exhausted") == "capacity exhausted"
    assert _redact("a message about a token bucket algorithm") == "a message about a token bucket algorithm"
