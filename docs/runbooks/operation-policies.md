# Environment, region and approval policies

ABAC policies restrict an existing role grant. Deployment and run controls,
environment operations and secret access bind operation facts before their
permission check (#2164). The facts come from records in the active organization:

- App operations use the resolved `AppEnvironment.name` and its cluster's region.
  A promotion uses the target environment. A migration checks the source and
  destination regions. Preview creation uses the environment name and cluster
  that the creation handler will persist.
- App-wide secret changes check every active environment before staging a change.
  A literal secret proposal affects every environment even if its input names one.
  A reveal uses the environment encoded in the secret's identifier. Shared bundle
  writes check each attached app environment. Managed services use their persisted
  app environment or project-owned cluster and environment.
- Agent dispatch and agent secret storage use the same managed cluster resolver
  as the spawner. Existing task controls use their frozen dispatch cluster.
- Workflow execution controls use the exact execution record. Workflow ID controls
  use the latest owned execution, matching the existing control surface.

For example, a DENY policy with `resourcePattern: {"env": ["production"]}`
restricts production operations while staging remains usable. A region selector
such as `{"region": ["us-east-1"]}` targets the operation's resolved cluster.
Names and approval totals supplied in a trigger payload cannot override these facts.

`approval_required` reads a deployment's durable `approvals_received`, a secret
proposal's distinct approved voters, or a workflow's completed approved human-gate
votes. Workflow votes are distinct by deciding user; retries, rejected decisions
and unfinished stages do not add approvals. A new operation with no gate has zero
recorded approvals. Gate creation/decision permissions must remain available to
the designated approvers; attaching an approval requirement to the permission
needed to record that first approval would prevent it.

An unresolved, malformed or foreign target supplies unknown facts and retains
fail-closed policy evaluation. Missing region values never become an unrestricted
region. Context ends when the resolver returns, including on errors, so one
operation cannot lend its facts to the next request.

These facts are bound at user/API permission entry points, including MCP calls.
Temporal activities execute the already authorized operation using its persisted
target; they do not acquire a new user grant by trusting workflow inputs.

Collection filters evaluate narrower app/project/team policies against concrete
tenant-owned scopes. An allowed parent row does not restore a denied descendant.
App navigation remains available when an app has an allowed environment; its
operation controls still check their actual environment. A capability held only
at denied scopes disappears from the capability manifest. Scope trees and policy
facts are loaded in batches and cached only for the current request.

Policy create and update validate condition JSON using the evaluator's catalog:
known kinds and fields, nonempty string lists, valid IP ranges and time windows,
IANA time zones, and positive integer approval/freshness limits. Invalid updates
return a validation error before modifying any field or the policy version.
