# Workload action permissions

`AstroliftWorkload.viewerCan` exposes advisory restart and scale permissions for
the current primary environment. Both actions use `app.deploy`, the same live
owner, credential scope ceiling, RoleBinding/share grants and ABAC decision as
their mutations. The page resolver loads these inputs in a batch, including
project slugs used by policies; it does not issue one permission query per row.

```graphql
query WorkloadActions {
  astroliftWorkloadsPage {
    items {
      id
      version
      viewerCan {
        restart { allowed code reason }
        scale { allowed code reason }
      }
    }
  }
}
```

An allowed action has empty `code` and `reason`. Permission denials return
`PERMISSION_DENIED` and the actual checker's reason, including bearer scope,
credential team, missing grants and ABAC denials. Missing or incoherent live
primary environments return `PRECONDITION`. Deleted or incoherent workload
owners cannot receive an allowed action. Current primary environment selection
matches the mutations; a permissive second environment cannot authorize the
first. No selected-environment argument is implied by these fields.

These decisions describe permissions at read time. They do not guarantee that
an operation will succeed: workload kind, replica bounds, HPA ownership, expected
version and driver availability still apply. A grant or policy can change after
the read. Mutations lock their targets and recheck authority before side effects;
clients must handle the mutation result even when an action was enabled.

The web workload list and detail scaling controls consume the corresponding
object decision. Settings render restart and scale once under **Primary
environment**, separately from each environment's deploy/pause controls. These
mutations do not accept a selected environment; a named-environment scaling
control refuses to write even when the primary decision allows it. Denied and
unavailable decisions disable controls and retain their reason on readable pages.
The client sends the displayed workload version as the top-level
`ifMatchVersion` mutation argument.

Mutation envelopes remain authoritative. A structured permission denial shows
its reason and refreshes active workload authority queries; failed refreshes
keep a stale allowed snapshot locally blocked. Version conflicts and missing
targets likewise require a refreshed snapshot. Input validation and transport
errors retain their normal error handling. This does not promise exactly-once
execution after transport uncertainty.

This is a partial foundation for APP #1867. Other object actions, web navigation
and remaining action consumption, CLI `whoami --permissions` and early permission
diagnosis, common denial envelopes across transports, and stock-role web/CLI
acceptance remain outstanding. Existing union-based capability lists are not
object authority and should not substitute for `viewerCan`. The pinned CLI
release does not yet implement the requested `whoami` contract.
