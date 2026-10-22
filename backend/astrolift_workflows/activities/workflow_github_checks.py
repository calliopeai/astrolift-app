"""Poll a ``github_checks`` workflow stage's required GitHub check runs (#2489).

One evaluation per call. The executor waits between polls on a durable
timer and owns the deadline: its last poll, at the stage timeout, passes
``final`` and a still-pending gate is decided ``timed_out``.

Each poll resolves the run organization's connection through
``connection_resolver`` (``ORG_REPO_WRITE``: the org GitHub App
installation first, as the other autonomous repository reads do), reads
the pull request's current head and that commit's check runs, and records
the evaluation on the stage execution: progress while the gate waits, the
terminal status once it is decided. A retried poll returns the decision
already recorded rather than deciding again.

The GitHub reads block, so the work runs in a worker thread
(``thread_sensitive=False``), like ``activities/uptime.py``.
"""

from __future__ import annotations

import logging

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.workflow_github_checks")

# A transient read failure keeps the gate waiting with the error recorded;
# any other provider error (refused credential, unknown pull request,
# unsupported connection) decides it.
_TRANSIENT_CODES = frozenset({"RATE_LIMITED", "NETWORK", "API_ERROR", "UNEXPECTED_SHAPE"})


@activity.defn(name="astrolift.workflow_stage.poll_github_checks")
async def poll_github_checks(
    workflow_run_id: str,
    execution_id: str,
    config: dict,
    pull_request: int,
    final: bool,
) -> dict:
    """Evaluate the gate once: ``{"outcome": "pending"}`` or its recorded decision."""
    from asgiref.sync import sync_to_async

    return await sync_to_async(_poll_github_checks_sync, thread_sensitive=False)(
        workflow_run_id, execution_id, config, pull_request, final
    )


def _poll_github_checks_sync(
    workflow_run_id: str,
    execution_id: str,
    config: dict,
    pull_request: int,
    final: bool,
) -> dict:
    from astrolift_scm.providers import ProviderError
    from astrolift_scm.providers.github_checks import read_pull_request_checks
    from astrolift_scm.services.connection_resolver import (
        ORG_REPO_WRITE,
        ConnectionResolutionError,
        resolve_connection,
    )
    from astrolift_workflows.activities.workflow_stage_activities import (
        _parent_run_pk,
        _update_stage_execution_sync,
    )
    from workflows import github_checks as gate
    from workflows.models import WorkflowStage, WorkflowStageExecution

    execution = WorkflowStageExecution.objects.select_related("workflow_run", "stage").get(
        pk=int(execution_id),
        workflow_run_id=_parent_run_pk(workflow_run_id),
        deleted_at__isnull=True,
    )
    if execution.effective_stage.kind != WorkflowStage.StageKind.GITHUB_CHECKS:
        raise RuntimeError("GitHub check polling requires a github_checks stage execution")
    recorded = execution.output.get("github_checks") if isinstance(execution.output, dict) else None
    if execution.is_terminal:
        if isinstance(recorded, dict) and recorded.get("outcome") in gate.TERMINAL_OUTCOMES:
            return recorded
        return {"outcome": gate.ERROR, "error": "the stage execution closed before its checks were decided"}

    result: dict = {
        "outcome": gate.ERROR,
        "repo": None,
        "pull_request": pull_request,
        "sha": None,
        "required": [],
        "checks": [],
    }
    try:
        config = gate.validate_github_checks(config)
        result.update(repo=config["repo"], required=config["checks"])
        organization_id = execution.workflow_run.organization_id
        if organization_id is None:
            raise ConnectionResolutionError(
                "PRECONDITION", "a GitHub checks gate needs a workflow run that belongs to an organization"
            )
        connection = resolve_connection(
            organization_id, purpose=ORG_REPO_WRITE, source_kind="github", repo=config["repo"]
        )
        sha, runs = read_pull_request_checks(connection, repo_full_name=config["repo"], number=pull_request)
        result.update(sha=sha, **gate.evaluate_check_runs(config["checks"], runs))
    except gate.GithubChecksContractError as exc:
        result["error"] = str(exc)
    except ConnectionResolutionError as exc:
        result["error"] = exc.message
    except ProviderError as exc:
        result["error"] = exc.message
        if exc.code in _TRANSIENT_CODES:
            last = recorded if isinstance(recorded, dict) else {}
            result.update(outcome=gate.PENDING, sha=last.get("sha"), checks=last.get("checks") or [])
    if result["outcome"] == gate.PENDING and final:
        result["outcome"] = gate.TIMED_OUT

    output = {"github_checks": result}
    if result["outcome"] == gate.PENDING:
        if output != execution.output:
            _update_stage_execution_sync(execution_id, WorkflowStageExecution.Status.RUNNING, output, None)
        return {"outcome": gate.PENDING}
    if result["outcome"] == gate.PASSED:
        _update_stage_execution_sync(execution_id, WorkflowStageExecution.Status.COMPLETED, output, None)
    else:
        _update_stage_execution_sync(
            execution_id, WorkflowStageExecution.Status.FAILED, output, gate.outcome_message(result)
        )
    log.info(
        "github_checks gate decided execution=%s outcome=%s repo=%s pull_request=%s sha=%s",
        execution_id,
        result["outcome"],
        result["repo"],
        pull_request,
        result["sha"],
    )
    return result
