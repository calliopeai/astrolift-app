"""Where a provisioning attempt resumes, from the recorded failed step (#2606).

Pure: the rule is in ``managed_service_resume`` and needs no Temporal.
"""

from __future__ import annotations

from astrolift_workflows.managed_service_resume import FINALIZE, PROVISION, WAIT_READY, resume_step


def test_no_recorded_failure_starts_at_the_beginning():
    assert resume_step("", "") == PROVISION


def test_an_unknown_step_starts_at_the_beginning():
    assert resume_step("bounce_typo", "postgres/records") == PROVISION


def test_a_provision_failure_restarts_the_driver_call():
    assert resume_step(PROVISION, "") == PROVISION


def test_a_wait_failure_resumes_at_the_wait_when_a_handle_is_recorded():
    assert resume_step(WAIT_READY, "postgres/records") == WAIT_READY


def test_a_wait_failure_without_a_handle_reruns_the_driver_call():
    assert resume_step(WAIT_READY, "") == PROVISION


def test_a_finalize_failure_resumes_at_finalize_when_a_handle_is_recorded():
    assert resume_step(FINALIZE, "postgres/records") == FINALIZE


def test_a_finalize_failure_without_a_handle_reruns_the_driver_call():
    assert resume_step(FINALIZE, "") == PROVISION
