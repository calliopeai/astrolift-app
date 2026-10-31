# Workload action permissions

`AstroliftWorkload.viewerCan` exposes advisory restart and scale permissions for
the current primary environment. Both actions use `app.deploy`, the same live
owner, credential scope ceiling, RoleBinding/share grants and ABAC decision as
their mutations. The page resolver loads these inputs in a batch, including
project slugs used by policies; it does not issue one permission query per row.

Preconditions: `app.read` on the app to read the page and its `viewerCan`
fields; an allowed decision needs `app.deploy` on the app's live owner scope
(`backend/core/permissions.py`); run the query through `astro api graphql`, or
read the decisions as the enabled or disabled restart and scale controls at
`/apps/<app-slug>/workloads` and `/apps/<app-slug>/workloads/<workload-slug>`.
Placeholders: `<app-slug>` is `slug` from `astro app list`; `<workload-slug>` is
`slug` from `astroliftWorkloadsPage(appSlug: "<app-slug>")`.

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

CLI main `618d120` adds verified `whoami --permissions` and repairs account
permission diagnosis. Those informational reports do not establish the current
credential's target authority or mutation environment and approval requirements.
They do not preflight another operation; the tagged CLI distribution remains a
separate release step.

This is a partial foundation for APP #1867. Other object actions, web navigation,
remaining action consumption, operation-specific early refusal, common denial
envelopes across transports and complete stock-role web/CLI acceptance remain
outstanding. Existing union-based capability lists are not object authority and
should not substitute for `viewerCan`.

## Verify

```sh
astro api graphql -q 'query($app: String!) {
  astroliftWorkloadsPage(appSlug: $app) {
    items { id slug version viewerCan { restart { allowed code reason } scale { allowed code reason } } }
  }
}' --var app=<app-slug>
```

Every item must carry both decisions: an allowed action shows `allowed: true`
with empty `code` and `reason`; a denial shows `PERMISSION_DENIED` or
`PRECONDITION` with the checker's reason.
