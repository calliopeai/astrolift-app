"""A cron trigger fires a workflow that exists (#1614).

`astrolift_pipelines/schedule_sync.py` created Temporal schedules targeting
`"PipelineScheduleWorkflow"` since pipeline triggers shipped, and the
workflow was defined nowhere. Both of the import ratchet's last two entries
were that module failing earlier still, on
`astrolift_workflows.client.get_temporal_client`, which does not exist -- so
the schedule was never created either, and the missing workflow was never
reached. The bare `except` logged "Temporal not available" on every
schedule-trigger save, which is what a healthy Temporal-less dev install
logs.

Fixing only the import would have produced schedules whose every fire failed
to start. Three faults in one path, and the outermost hid the other two.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def test_the_workflow_exists_and_is_registered():
    """The one that would have caught the original absence, and the cheap
    half. A schedule pointing at an unregistered workflow fails on every
    fire, and the schedule itself looks healthy."""
    from astrolift_workflows.worker import WORKFLOWS
    from astrolift_workflows.workflows import PipelineScheduleWorkflow

    assert PipelineScheduleWorkflow in WORKFLOWS


def test_its_activity_is_registered():
    from astrolift_workflows.activities.pipeline_schedule_fire import (
        create_scheduled_pipeline_run,
    )
    from astrolift_workflows.worker import ACTIVITIES

    assert create_scheduled_pipeline_run in ACTIVITIES


def test_the_import_ratchet_is_empty():
    """`KNOWN_BROKEN` started this sweep at nine entries. These two were the
    last, and the list can only shrink -- the anti-rot test forces an entry
    out the moment its site resolves."""
    from core.tests.test_import_resolvability import KNOWN_BROKEN

    assert KNOWN_BROKEN == frozenset()


# ---- the fire itself ----------------------------------------------------


@pytest.fixture
def trigger(db, settings):
    """Temporal off: saving a Trigger fires the post_save signal into
    `create_or_update_schedule`, which now really tries to reach Temporal --
    it used to die on an ImportError first (#1614)."""
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    from astrolift_identity.models import Organization, Project, Team
    from astrolift_pipelines.models import Pipeline, Trigger
    from astrolift_registry.models import RegisteredApp

    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Core", slug="core")
    project = Project.objects.create(organization=org, team=team, name="P", slug="p")
    app = RegisteredApp.objects.create(
        organization=org, team=team, project=project, name="hello", slug="hello"
    )
    pipeline = Pipeline.objects.create(organization=org, registered_app=app, name="ci", default_branch="main")
    return Trigger.objects.create(pipeline=pipeline, kind="schedule", config={"cron": "0 2 * * *"})


def test_the_writer_respects_the_temporal_kill_switch(settings, monkeypatch):
    """New in #1614 and worth pinning: before the fix this path died on an
    ImportError long before it tried to connect, so a Temporal-less install
    paid nothing. Now it would pay a connection timeout on every
    schedule-trigger save without this guard."""
    from astrolift_pipelines import schedule_sync

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    called = []
    monkeypatch.setattr(schedule_sync, "_write_schedule", lambda **kw: called.append(kw))

    schedule_sync.create_or_update_schedule(_FakeTrigger())

    assert called == []


class _FakeTrigger:
    guid = "11111111-1111-1111-1111-111111111111"

    class pipeline:
        guid = "22222222-2222-2222-2222-222222222222"
        default_branch = "main"

    config = {"cron": "0 2 * * *"}


def test_a_fire_creates_a_pipeline_run(trigger):
    """`PipelineRunWorkflow` takes an integer pk and a schedule cannot supply
    one, because the row does not exist until the fire happens. That is the
    whole reason this wrapper exists."""
    from astrolift_pipelines.models import PipelineRun
    from astrolift_workflows.activities.pipeline_schedule_fire import _create_sync

    result = _create_sync(
        {"trigger_id": str(trigger.guid), "pipeline_id": str(trigger.pipeline.guid), "branch": "main"}
    )

    assert "pipeline_run_id" in result
    run = PipelineRun.objects.get(pk=result["pipeline_run_id"])
    assert run.status == "pending"
    assert run.trigger_kind == "schedule"


def test_run_numbers_are_monotonic(trigger):
    from astrolift_workflows.activities.pipeline_schedule_fire import _create_sync

    action = {"trigger_id": str(trigger.guid), "pipeline_id": str(trigger.pipeline.guid)}

    first = _create_sync(action)
    second = _create_sync(action)

    assert second["run_number"] == first["run_number"] + 1


def test_a_deleted_trigger_is_skipped_not_failed(trigger):
    """Temporal keeps firing until something deletes the schedule, so a
    schedule outliving its trigger by a tick is ordinary. A red workflow for
    it would train operators to ignore red workflows."""
    from astrolift_workflows.activities.pipeline_schedule_fire import _create_sync

    trigger.deleted_at = trigger.created_at
    trigger.save(update_fields=["deleted_at"])

    result = _create_sync({"trigger_id": str(trigger.guid), "pipeline_id": ""})

    assert result["skipped"]
    assert "pipeline_run_id" not in result


def test_an_unknown_trigger_is_skipped(trigger):
    from astrolift_workflows.activities.pipeline_schedule_fire import _create_sync

    result = _create_sync({"trigger_id": "00000000-0000-0000-0000-000000000000", "pipeline_id": ""})

    assert result["skipped"]


# ---- the schedule writer ------------------------------------------------


def test_the_writer_does_not_name_a_queue_nobody_polls():
    """The literal "pipelines" that stood here names no registered queue --
    the same defect as the webhook dispatch fixed in #1623. It reads from
    settings now."""
    import ast
    import inspect
    import textwrap

    from astrolift_pipelines import schedule_sync

    # `.awaitable` is asgiref's handle on the wrapped coroutine function;
    # `__wrapped__` is not set by AsyncToSync.
    #
    # Over the AST with the docstring dropped, not the raw source: the
    # docstring names the old literal on purpose, so a substring check fails
    # on the explanation of the bug rather than on the bug. That trap has now
    # bitten this sweep three times.
    tree = ast.parse(textwrap.dedent(inspect.getsource(schedule_sync._write_schedule.awaitable)))
    fn = tree.body[0]
    if fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant):
        fn.body = fn.body[1:]

    literals = {n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    names = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)} | {
        a.name for n in ast.walk(fn) if isinstance(n, ast.ImportFrom) for a in n.names
    }

    assert "pipelines" not in literals, "the writer still hard-codes a queue nobody polls"
    assert "TEMPORAL_TASK_QUEUE" in literals or "TEMPORAL_TASK_QUEUE" in names


def test_the_writer_awaits_the_client():
    """The second fault behind the missing import: `create_schedule` and
    `get_schedule_handle(...).delete()` are async on the Temporal client, and
    were being called synchronously -- returning coroutines nobody awaited,
    so neither ran."""
    import inspect

    from astrolift_pipelines import schedule_sync

    assert inspect.iscoroutinefunction(schedule_sync._write_schedule.awaitable)
    assert inspect.iscoroutinefunction(schedule_sync._drop_schedule.awaitable)
