"""Agent-trigger workflow types must be registered on the worker (#1025).

The agent webhook/schedule trigger path enqueues Temporal workflows by their
type *name*. If that name doesn't match a ``@workflow.defn(name=...)`` the
worker registers, Temporal accepts the start but no worker ever claims it — the
run sits unexecuted forever. This bit ``AGENT_WORKFLOW_TYPE``, which pointed at
``"AgentWorkflowDefinitionRun"`` while the worker only registers
``WorkflowDefinitionRunWorkflow``.

This guard pins every workflow-type string the trigger path dispatches to the
set of names the worker registers, so a future rename of either side fails in
CI instead of silently dropping triggered runs.
"""

from __future__ import annotations

from astrolift_agents.services.workflow_triggers import AGENT_WORKFLOW_TYPE


def _registered_workflow_names() -> set[str]:
    import astrolift_workflows.worker as worker

    return {
        cls.__temporal_workflow_definition.name
        for cls in worker.WORKFLOWS
        if getattr(cls, "__temporal_workflow_definition", None) is not None
    }


def test_agent_workflow_type_constant_is_registered():
    registered = _registered_workflow_names()
    assert AGENT_WORKFLOW_TYPE in registered, (
        f"{AGENT_WORKFLOW_TYPE!r} is dispatched by the agent trigger path but no "
        f"worker registers it; the triggered run would never execute"
    )


def test_all_agent_trigger_workflow_types_are_registered():
    """Every workflow-type string the agent trigger path can enqueue."""
    registered = _registered_workflow_names()
    # Type names dispatched from astrolift_agents.services.workflow_triggers:
    #   * AGENT_WORKFLOW_TYPE — definition runs (webhook / schedule)
    #   * "DispatchAgentTaskWorkflow" — per-task agent dispatch
    dispatched = {AGENT_WORKFLOW_TYPE, "DispatchAgentTaskWorkflow"}
    missing = dispatched - registered
    assert not missing, f"agent trigger dispatches unregistered workflow types: {sorted(missing)}"
