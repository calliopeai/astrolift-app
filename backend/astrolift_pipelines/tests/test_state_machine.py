"""Tests for the pipeline state machine (#87)."""

from __future__ import annotations

import pytest

from astrolift_pipelines.state_machine import (
    InvalidTransition,
    validate_job_run_transition,
    validate_pipeline_run_transition,
)


# ---------------------------------------------------------------------------
# PipelineRun transitions
# ---------------------------------------------------------------------------


def test_pipeline_run_pending_to_running_valid():
    validate_pipeline_run_transition("pending", "running")  # no exception


def test_pipeline_run_pending_to_cancelled_valid():
    validate_pipeline_run_transition("pending", "cancelled")  # no exception


def test_pipeline_run_running_to_success_valid():
    validate_pipeline_run_transition("running", "success")  # no exception


def test_pipeline_run_running_to_failure_valid():
    validate_pipeline_run_transition("running", "failure")  # no exception


def test_pipeline_run_pending_to_success_invalid():
    with pytest.raises(InvalidTransition, match="cannot transition from 'pending' to 'success'"):
        validate_pipeline_run_transition("pending", "success")


def test_pipeline_run_success_to_running_invalid():
    with pytest.raises(InvalidTransition, match="terminal"):
        validate_pipeline_run_transition("success", "running")


def test_pipeline_run_failure_to_running_invalid():
    with pytest.raises(InvalidTransition, match="terminal"):
        validate_pipeline_run_transition("failure", "running")


def test_pipeline_run_cancelled_is_terminal():
    with pytest.raises(InvalidTransition):
        validate_pipeline_run_transition("cancelled", "running")


# ---------------------------------------------------------------------------
# JobRun transitions
# ---------------------------------------------------------------------------


def test_job_run_pending_to_running_valid():
    validate_job_run_transition("pending", "running")


def test_job_run_pending_to_skipped_valid():
    validate_job_run_transition("pending", "skipped")


def test_job_run_running_to_success_valid():
    validate_job_run_transition("running", "success")


def test_job_run_running_to_failure_valid():
    validate_job_run_transition("running", "failure")


def test_job_run_pending_to_failure_invalid():
    with pytest.raises(InvalidTransition, match="cannot transition from 'pending' to 'failure'"):
        validate_job_run_transition("pending", "failure")


def test_job_run_success_is_terminal():
    with pytest.raises(InvalidTransition, match="terminal"):
        validate_job_run_transition("success", "running")


def test_job_run_skipped_is_terminal():
    with pytest.raises(InvalidTransition, match="terminal"):
        validate_job_run_transition("skipped", "running")
