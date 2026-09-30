# Mobile quick-action preconditions

The four quick actions accept an optional top-level `ifMatchVersion: Int`.
The existing mutation inputs are unchanged. Omitting the argument, or passing
null, retains the existing web and CLI behavior.

| Mutation | Read this version | Target identified by input |
| --- | --- | --- |
| `rollbackDeployment` | `AstroliftDeployment.version` | The current **RUNNING** deployment in the selected environment |
| `redeployApp` | `AstroliftDeployment.version` | The deployment snapshot whose image/config will be copied |
| `restartAstroliftWorkload` | `AstroliftWorkload.version` | The workload being restarted |
| `scaleAstroliftWorkload` | `AstroliftWorkload.version` | The workload being scaled |

Use the version returned alongside the target's ID. An app's `version`, an
environment's version, or the version of the prior deployment is not the
precondition value for these actions. Deployment detail and list queries now
return `version`; workload detail and list queries return their own `version`.
Read the field explicitly before enabling the action.

```graphql
mutation Rollback($input: DeploymentByIdInput!, $ifMatchVersion: Int) {
  rollbackDeployment(input: $input, ifMatchVersion: $ifMatchVersion) {
    ok
    errors { code message currentVersion requestedVersion }
    data { id status version }
  }
}
```

Rollback receives the current running deployment's ID and version. The server
chooses the latest older **SUPERSEDED** deployment belonging to that same app
and environment, then creates a rollback from that snapshot. Passing the
superseded deployment's ID is refused. A newer running deployment in another
environment does not change the selected target, and a superseded deployment
in another environment cannot supply the rollback image. The client should
select the current running row for its chosen environment, rather than choose
an array position across all environments.

Redeploy remains a snapshot action: the identified deployment supplies the
image and config, and its persisted environment supplies the destination.
A newer deployment in another environment cannot redirect it. The server
also rechecks the destination's current pause and approval settings.

All four actions lock the live app owner and persisted environment and target
row in one database transaction, compare the version before business writes,
workflow scheduling or Kubernetes calls, and recheck permissions using the
locked environment's operation facts. Restart and scale retain the existing
primary-environment contract: the workload's first registered live environment.
Successful runtime actions advance the workload version; two concurrent calls
with the same old version cannot both reach the driver.

A mismatch returns `ok: false`, `data: null`, and an error with
`code: VERSION_MISMATCH`, `currentVersion`, and `requestedVersion`. Refresh the
target, show its current state, and require a new user decision before retrying.
Permission, not-found, non-running rollback, and paused-environment failures
retain their existing structured errors.

Version checking does not promise exactly-once execution after transport or
driver uncertainty. A remote operation can complete before its response is
lost; a database rollback cannot undo a Kubernetes call. Refresh the target's
state before deciding whether another action is appropriate.

The four current mobile operation documents at
`calliopeai/astrolift-mobile@3dc2222aa759d16040be80f642637d69decdd0d8`
validate unchanged against this schema, including their top-level
`ifMatchVersion` arguments. The checked-in backend contract fixture validates
those exact documents offline. Mobile hooks must consume the target row's
version and rollback target described here; this backend change does not
replace the separate mobile hook work.
