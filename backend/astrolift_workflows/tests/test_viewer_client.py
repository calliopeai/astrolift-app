"""Disabled-mode coverage for the workflow viewer helpers (#437).

The viewer's three reads + two writes added in #437 (``list_workflow_
instances``, ``describe_workflow_instance``, ``workflow_history``,
``cancel_workflow``, and the reused ``signal_workflow`` /
``terminate_workflow``) must degrade gracefully when Temporal is off
so the UI surface renders without a live cluster. Live-mode happy
path is exercised by the workflow integration tests that already
spin up the Temporal time-skipping environment.
"""

from __future__ import annotations

import pytest

from astrolift_workflows.client import (
    cancel_workflow,
    describe_workflow_instance,
    list_workflow_instances,
    workflow_history,
)


@pytest.fixture
def disabled(settings):
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    yield


def test_list_workflow_instances_disabled_returns_empty(disabled):
    assert list_workflow_instances() == []
    assert list_workflow_instances(workflow_type="DeployAppWorkflow") == []
    assert list_workflow_instances(status="RUNNING", limit=10) == []


def test_describe_workflow_instance_disabled_returns_none(disabled):
    assert describe_workflow_instance("any-id") is None


def test_workflow_history_disabled_returns_empty(disabled):
    assert workflow_history("any-id") == []


def test_cancel_workflow_disabled_returns_false(disabled):
    assert cancel_workflow("any-id") is False


def test_list_workflow_instances_clamps_limit(disabled):
    # Disabled returns [] regardless, but the clamp logic must not
    # raise on extreme inputs — keeps GraphQL-layer guards thin.
    assert list_workflow_instances(limit=0) == []
    assert list_workflow_instances(limit=99999) == []
    assert list_workflow_instances(limit=-1) == []
