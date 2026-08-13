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
