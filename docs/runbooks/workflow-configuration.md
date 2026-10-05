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

Configured results also expose `version`. Pass the reviewed value as
`expectedVersion` to update/delete to reject stale edits under the row lock.
The argument is optional for legacy GraphQL callers and required by the native
MCP update/delete tools. Re-read and reassess on mismatch; a failed assertion
does not save configuration or apply a schedule change.

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

## Schedule activation and recovery

Create, update and delete return `configurationSaved` plus a `schedule`
observation. `ok: false, configurationSaved: true` means the configuration was
saved but the schedule was not confirmed. Keep `schedule.workflowId`; inspect
and reconcile that ID instead of creating another workflow. `not_requested`
means an inactive draft never requested activation or cleanup; it is not an
engine observation.

```graphql
query Inspect($id: GUID!) {
  workflowSchedule(workflowId: $id) {
    workflowId scheduleId configurationVersion desiredRevision desiredActive
    observedState confirmed observedAt errorCode message
    engineCreatedAt engineUpdatedAt actionCount
  }
}

mutation Recover($id: GUID!, $version: Int!, $active: Boolean!) {
  reconcileWorkflowSchedule(
    workflowId: $id expectedVersion: $version expectedActive: $active
  ) {
    ok errors { field messages }
    schedule {
      workflowId scheduleId configurationVersion desiredActive
      observedState confirmed errorCode message
    }
  }
}
```

Use the version and desired activation state from the reviewed observation.
A stale version or activation assertion refuses engine changes. An active
observation is confirmed only when the engine action, normalized schedule spec
and stored receipt agree. An inactive configuration is confirmed when the engine
reports the schedule missing. `paused` and `drifted` require reconciliation;
`unknown` means the engine could not be inspected. Disabled Temporal, unavailable
service, invalid cron and timeout after acceptance return explicit error codes.
Operations have a 20-second engine deadline with bounded RPCs. Errors omit raw
engine messages and credentials.

Updates preserve the existing Temporal schedule and its history. Configuration
writes and reconciliation serialize on the configured workflow row; observations
carry their configuration version. Each schedule fire still creates its own run,
loads the current inputs and bindings, and skips a replaced schedule revision.
Previously written actions without revision markers remain compatible until an
operator reconciles them.

Native creation and updates of active schedules require `workflow.trigger` in
addition to create/update authority. Inspection requires read authority; recovery
requires update authority, plus trigger authority for activation or delete
authority for cleanup of a soft-deleted workflow. Retired owners require explicit
organization authority for inspection/cleanup. Bearer organization and team limits
still apply. Existing run controls and human-gate decisions keep their own authority.

MCP exposes `astrolift_get_workflow_schedule` and
`astrolift_reconcile_workflow_schedule`, with the same fields in snake_case.
Inspection requires `mcp:read`; recovery requires `mcp:write` and `workflow:write`.
Active recovery additionally requires `mcp:dispatch` and `workflow:trigger`.
MCP failures retain the native result and exact workflow/schedule IDs in both
structured and text content. Event activation is not offered by these tools.

After upgrading the backend and worker together, report legacy schedules, then
reconcile them with the install-wide operator command:

```sh
python manage.py resync_workflow_schedules
python manage.py resync_workflow_schedules --workflow-id <configured-workflow-guid> --apply
```

The migration conservatively tracks every existing configuration for possible
cleanup, including soft-deleted rows. The command fails if any activation or
cleanup remains unconfirmed. The exact-ID filter permits targeted retries.
Native Astrolift CLI commands and rollout qualification remain tracked in
[issue #2308](https://github.com/calliopeai/astrolift-app/issues/2308). MCP
configuration adapters and reviewed configured starts remain under
[issue #2283](https://github.com/calliopeai/astrolift-app/issues/2283).
