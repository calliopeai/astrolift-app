"""
DeployAppWorkflow activity timeout (#359).

Each activity step on DeployAppWorkflow shares a single
``start_to_close_timeout``. Fargate cold-starts + image-pull on the
first rollout of a new env routinely exceed 15 minutes, so the gate
moved to 20 minutes. This is a visual / regression guard: bumping it
back down by accident would silently fail real first deploys, and a
test failure here is the cheapest signal.
"""

from __future__ import annotations

from datetime import timedelta

from astrolift_workflows.workflows.deploy_app import _TIMEOUT


def test_deploy_app_activity_timeout_is_20_minutes():
    assert _TIMEOUT == timedelta(minutes=20)
