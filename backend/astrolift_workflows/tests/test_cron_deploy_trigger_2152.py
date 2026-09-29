# ruff: noqa: F811  (pytest fixtures imported from test_cron_deploy)
"""A cron-fired deploy's ``WorkflowRun`` mirror says the schedule started it (#2152)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from astrolift_workflows.tests.test_cron_deploy import (  # noqa: F401 - fixtures
    _no_opensearch_profile_index,
    cron_app_stack,
)


@pytest.mark.django_db
def test_cron_deploy_mirror_is_a_schedule_run(cron_app_stack, settings):
    from astrolift_lifecycle.models import Deployment
    from astrolift_workflows.activities.cron_deploy import _dispatch_cron_deploys_sync
    from astrolift_workflows.client import WorkflowHandle

    settings.ASTROLIFT_TEMPORAL_ENABLED = False

    def _start(name, args, *, workflow_id, task_queue=None):
        return WorkflowHandle(workflow_id=workflow_id, run_id="r-1", enqueued=True)

    with patch("astrolift_workflows.client.start_workflow", _start):
        assert _dispatch_cron_deploys_sync().fired_count == 1

    run = Deployment.objects.get(registered_app=cron_app_stack["app"]).workflow_run
    assert (run.trigger_kind, run.trigger_actor_user_id, run.trigger_actor_token_kind) == (
        "schedule",
        None,
        "cron",
    )
