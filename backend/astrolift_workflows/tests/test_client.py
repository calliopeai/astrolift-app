"""
Sync facade in front of the Temporal client.

The facade has two modes:

* Disabled (``ASTROLIFT_TEMPORAL_ENABLED=False`` settings kill
  switch OR constance ``TEMPORAL_ENABLED=False`` runtime toggle):
  start / signal / terminate are no-ops that return synthetic
  handles. This is what makes dev and test environments runnable
  without spinning up a real Temporal server.
* enabled: real client calls. Those are not exercised here because
  the client is async-only and lives behind ``async_to_sync``; the
  Temporal time-skipping test env covers that path in workflow tests.
"""

from __future__ import annotations

import pytest

from astrolift_workflows.client import (
    WorkflowHandle,
    signal_workflow,
    start_workflow,
    terminate_workflow,
)


@pytest.fixture
def disabled(settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    yield


def test_start_workflow_disabled_returns_synthetic_handle(disabled):
    handle = start_workflow("DeployAppWorkflow", args=[], workflow_id="DeployAppWorkflow-x-y")
    assert isinstance(handle, WorkflowHandle)
    assert handle.workflow_id == "DeployAppWorkflow-x-y"
    assert handle.run_id == ""
    assert handle.enqueued is False


def test_signal_workflow_disabled_returns_false(disabled):
    assert signal_workflow("any-id", "abort") is False


def test_terminate_workflow_disabled_returns_false(disabled):
    assert terminate_workflow("any-id", reason="test") is False


# Note: the DEBUG-derived default-disabled behaviour was removed
# from ``_temporal_enabled`` because DJANGO_CONFIGURATION=Dev (the
# default for the published image) leaves DEBUG truthy, which
# silently turned the entire workflow runtime into a no-op in prd.
# The kill switch lives on ``settings.ASTROLIFT_TEMPORAL_ENABLED``
# (or the constance ``TEMPORAL_ENABLED`` runtime toggle) — DEBUG
# no longer participates. See the ``disabled`` fixture above for
# the modern shape.
