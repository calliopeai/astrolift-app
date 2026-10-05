# Configuring a native workflow by identity

Keep the `definitionId` returned by `importWorkflowManifest`, or the exact
definition GUID reviewed through the workflow catalog. `createWorkflow` accepts
that `definitionId` directly. To prepare a configuration without activating it,
explicitly set `isEnabled: false`; legacy callers retain the default of true.

```graphql
mutation Prepare($definition: GUID!, $bindings: JSON!, $inputs: JSON!) {
  createWorkflow(
    name: "Weekly research"
    definitionId: $definition
    stageBindings: $bindings
    inputs: $inputs
    isEnabled: false
  ) {
    ok
    errors { field messages }
    workflow { guid definitionGuid isEnabled stageBindings inputs }
  }
}
```

Bindings use native stage-order keys, for example
`{"0":{"agent_workload_id":"<agent UUID>"}}`. The existing model validates
stage orders, binding shapes and concrete agent references. The configuration
retains the definition's owner and uses its live create permission. No run is
started by configuring it, and disabled creation does not activate a schedule.

Save the returned workflow `guid`. Read it through `workflow(workflowId: ...)`,
edit it through `updateWorkflow(workflowId: ...)`, or soft-delete it through
`deleteWorkflow(workflowId: ...)`. Configured results, including list results
over MCP, expose `definitionGuid` (`definition_guid` over MCP) so subsequent
reviews can resolve the exact definition without a slug lookup.

Legacy slug arguments remain supported. If an ID is supplied, it is
authoritative: a missing, malformed, foreign or deleted ID never substitutes a
same-slug row. Supplying both ID and slug asserts that they identify the same
live row. An exact global definition ID also stays global when the organization
has its own definition with the same slug; legacy definition-slug selection
continues to prefer the organization's own row.

Changing the definition through `updateWorkflow(definitionId: ...)` requires
update authority at the current owner and create authority at the destination.
Existing bindings must validate against the new stages, or no configuration is
saved. Every operation checks the current organization, project/team ownership
and bearer limits, independently of the selected UI context. Retired project or
team owners refuse these active configuration operations.

Schedule synchronization still uses the existing best-effort native behavior.
A saved enabled row is not proof that Temporal applied or removed its schedule.
Observable activation and recovery are tracked in
[issue #2308](https://github.com/calliopeai/astrolift-app/issues/2308); MCP
configuration adapters and reviewed configured starts remain under
[issue #2283](https://github.com/calliopeai/astrolift-app/issues/2283).
