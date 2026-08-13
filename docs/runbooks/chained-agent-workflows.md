# Chained agent workflows

Astrolift agent workflows are ordered pipelines. `WorkflowDefinition.states`,
`transitions`, and `model_label` belong to the older generic Django object
state machine; they do not select an LLM and do not control an agent pipeline.
An agent's model is selected by its environment/runtime package.

The canonical pipeline is a `WorkflowDefinition` with ordered
`WorkflowStage` rows. The TOML example in
[`../examples/chained-agent-workflow.toml`](../examples/chained-agent-workflow.toml)
maps one `[[stage]]` table to one row:

For an organization-owned agent repository, commit definitions under
`workflows/*.toml` (nested paths under `workflows/` are also accepted). Agent
repo registration, manual resync, and source push reconciliation parse and
upsert those definitions after the repo's agents are reconciled. The
`backend/workflows/catalogue/*.toml` directory is reserved for platform-owned
built-in templates and is not the tenant configuration path.

| TOML | Persisted field | Runtime meaning |
|---|---|---|
| `agent` | `agent_ref` and, when available, `agent_definition` | Organization-local agent workload. |
| `environment_spec_slug` | `environment_spec_slug` | Environment recipe frozen onto the `AgentTask`. |
| `skills` | `skill_refs` | Ordered org/global skill overlays added to a task-specific immutable Brief. |
| `prompt` | `prompt` | Agent instruction overlay, or the approval question on a human gate. |
| `output_key` | `output_key` | Key used in the run's `named_outputs` map. Defaults to `stage_<order>`. |
| `workflow` | `workflow_ref` | Visible child definition invoked by a `kind = "workflow"` stage. |

A configured `Workflow.stage_bindings` may override a definition without
editing it. Bindings are keyed by stage order:

```json
{
  "0": {
    "agent_workload_id": "<workload-guid>",
    "skill_refs": ["emr-evidence", "customer-runbook"],
    "params": {
      "environment_spec_slug": "emr-triage-prod",
      "prompt": "Investigate this tenant using the production runbook.",
      "output_key": "tenant_evidence"
    }
  }
}
```

Resolution precedence is binding override, then stage default. The executor
freezes the resolved packet before dispatch. A local `agent` slug can therefore
be imported before its workload exists and resolves when a matching workload is
later registered in the run's organization.

Each agent receives its immediate predecessor at the top level for backward
compatibility and an explicit `_astrolift_workflow` object:

```json
{
  "_astrolift_workflow": {
    "input": {"issue": "EMR-123"},
    "previous": {"classification": "product_bug"},
    "outputs": {
      "evidence": {"reproduced": true},
      "classification": {"classification": "product_bug"}
    },
    "stage": {"order": 2, "output_key": "report"}
  }
}
```

The workflow result retains the ordered `outputs` list and also exposes
`named_outputs` plus `final_output`. Agent-stage output is the callback's real
structured result; terminal status and execution IDs remain metadata on the
ordered output record.

## Nested workflows

Use a workflow stage to package a reusable series of steps inside another
pipeline:

```toml
[[stage]]
kind = "workflow"
workflow = "emr-triage-patch"
output_key = "patch_review"
on_failure = "retry"
timeout = 1800
```

The parent passes the same composed stage packet shown above as the child's
trigger input. The child's `final_output` becomes the value of the parent's
`output_key`; its credentials, environment specs, skills, and stage bindings
are resolved inside the child and are never inherited from the parent.

Nested invocations are first-class Temporal child workflows and first-class
`WorkflowRun` rows. The run stores its parent run, invoking stage execution,
and nesting depth, so the Observe graph links directly to the child run.
Cancelling or terminating a parent propagates to its active children.

Repository reconciliation is atomic and order-independent: a parent manifest
may sort before its child manifest in the same repository. Reconciliation and
interactive edits reject missing/invisible children, cross-project references,
cycles, and nesting deeper than eight levels. A project definition may invoke
another definition in the same project or a reusable org/global definition
with no project; it cannot reach into a different project packet.
