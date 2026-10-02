"""Everything defined as durable must be registered with a worker.

`PipelineRunWorkflow` was defined, started by name from
`pipeline_webhook_views`, and absent from `WORKFLOWS`. All eight of its
activities were absent from `ACTIVITIES`. Temporal accepts a start request
for an unregistered type, so nothing errored at the call site — the
workflow task simply sat with no worker polling for it.

That is the same shape as every other finding in this area: the thing was
built, and nothing was wired to run it. The difference is that this one
sits *above* the pipeline fixes in #1501, #1531 and #1529, so all of them
were unreachable regardless of being correct.

A count-based assertion would rot. These walk the decorators.
"""

from __future__ import annotations

import ast
import pathlib

from astrolift_workflows.worker import ACTIVITIES, WORKFLOWS

ROOT = pathlib.Path("astrolift_workflows")


def _decorated(kind: str) -> dict[str, str]:
    """Map name -> file, for every `@workflow.defn` / `@activity.defn`."""
    found: dict[str, str] = {}
    for path in ROOT.rglob("*.py"):
        if "/tests/" in path.as_posix():
            continue
        try:
            tree = ast.parse(path.read_text(errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for deco in node.decorator_list:
                target = deco.func if isinstance(deco, ast.Call) else deco
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "defn"
                    and isinstance(target.value, ast.Name)
                    and target.value.id == kind
                ):
                    found[node.name] = path.as_posix()
    return found


def test_every_workflow_definition_is_registered():
    defined = _decorated("workflow")
    registered = {w.__name__ for w in WORKFLOWS}

    missing = {n: f for n, f in defined.items() if n not in registered}

    assert missing == {}, (
        "these are decorated @workflow.defn but no worker runs them; a caller "
        f"starting one by name gets a task nothing ever picks up: {missing}"
    )


def test_every_activity_a_registered_workflow_calls_is_served():
    """The invariant that actually bites.

    A registered workflow invoking an unregistered activity fails at
    runtime, and only once that branch is reached. `BuildPreviewWorkflow`
    was in WORKFLOWS while four of the activities it calls
    (`provision_preview_namespace`, `mark_preview_building`,
    `mark_preview_running`, `mark_preview_failed`) were absent from
    ACTIVITIES, so building a preview environment could not work.

    Deliberately not "every defined activity": an activity nothing calls
    is dead code, a different and lesser problem than a live workflow
    reaching for something no worker serves.
    """
    defined = _decorated("activity")
    served = {getattr(a, "__name__", "") for a in ACTIVITIES}

    wanted: dict[str, str] = {}
    for path in (ROOT / "workflows").rglob("*.py"):
        if "/tests/" in path.as_posix():
            continue
        body = path.read_text(errors="ignore")
        for name in defined:
            if name in body:
                wanted[name] = path.as_posix()

    missing = {n: w for n, w in wanted.items() if n not in served}

    assert missing == {}, (
        "a registered workflow calls these and no worker serves them, so the "
        f"run fails when it reaches that step: {missing}"
    )


def test_activities_nothing_calls_are_pinned_not_ignored():
    """A ledger, not a failure.

    Some activities are defined and referenced by no workflow at all.
    That is the build-it-and-wire-nothing shape again, but harmless at
    runtime, so this pins the current set. Shrinking it is good; growing
    it should require a deliberate edit here.
    """
    defined = _decorated("activity")

    workflow_bodies = [
        p.read_text(errors="ignore")
        for p in (ROOT / "workflows").rglob("*.py")
        if "/tests/" not in p.as_posix()
    ]
    uncalled = {n for n in defined if not any(n in b for b in workflow_bodies)}

    known = {
        # Agent-stage cancellation: reached through the agent dispatch path
        # rather than from a workflow body.
        "cancel_agent_stage",
        # Capability teardown, built for #1365 ownership work and not yet
        # called from the teardown workflow.
        "deprovision_app_certificate",
        "deprovision_app_dns_record",
        "deprovision_app_ingress",
        "force_redeploy_app",
    }

    assert uncalled == known, (
        "the set of activities no workflow calls changed; new entries mean "
        f"something was built and never wired: added={uncalled - known}, "
        f"removed={known - uncalled}"
    )


def test_the_pipeline_workflow_specifically_is_registered():
    """Named on purpose. It is the one that was missing, and the whole
    pipeline subsystem — job persistence, secret mounting, log capture —
    runs behind it."""
    assert "PipelineRunWorkflow" in {w.__name__ for w in WORKFLOWS}

    served = {getattr(a, "__name__", "") for a in ACTIVITIES}
    for activity in (
        "mark_pipeline_run_running",
        "spawn_pipeline_job",
        "poll_pipeline_job",
        "mark_pipeline_run_success",
        "mark_pipeline_run_failed",
        "cancel_pipeline_job",
        "mark_job_run_cancelled",
        "mark_job_run_failed",
    ):
        assert activity in served, f"{activity} is not served by any worker"


def test_the_name_a_caller_starts_matches_the_definition():
    """The webhook delegates to the durable service, whose engine name must
    match the actual registered Temporal definition for starts and recovery."""
    from astrolift_workflows.workflows.pipeline_run import PipelineRunWorkflow

    definition_name = getattr(PipelineRunWorkflow, "__temporal_workflow_definition").name
    assert PipelineRunWorkflow in WORKFLOWS

    def function(path, name):
        module = ast.parse(pathlib.Path(path).read_text())
        return next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == name)

    webhook = function("astrolift_pipelines/webhook_views.py", "_dispatch_pipeline_run")
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "astrolift_pipelines.run_contracts"
        and any(alias.name == "dispatch_pipeline_run" for alias in node.names)
        for node in ast.walk(webhook)
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "dispatch_pipeline_run"
        for node in ast.walk(webhook)
    )

    dispatch = function("astrolift_pipelines/run_contracts.py", "dispatch_pipeline_run")
    calls = {
        node.func.id: node
        for node in ast.walk(dispatch)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"start_workflow_once", "recover_workflow_once"}
    }
    assert set(calls) == {"start_workflow_once", "recover_workflow_once"}
    for call in calls.values():
        assert isinstance(call.args[0], ast.Constant)
        assert call.args[0].value == definition_name
