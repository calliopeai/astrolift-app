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

`approval_required` reads a deployment's distinct durable human voter identities, a secret
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

Owner scope factories resolve inside the operation context. Each context starts
with a fresh permission-scope memo, including collections that admit any owned
scope. Multi-target operations authorize every target independently; facts from
one permitted region cannot authorize another target.

Log subscriptions authorize on first iteration using the actual persisted app,
cluster and namespace. An explicitly named environment must exist. Legacy pod
streams bind an environment only when a unique persisted cluster/namespace pair
proves it; otherwise they bind only the cluster's region. Facts and scope memos
are bound while advancing or closing the inner stream and released before each
outward event. Interleaved subscriptions retain their own contexts, and a stream
can close in another task without leaving operation facts in its caller.

Deployment approval requests record one `DeploymentApproval` per authenticated
user. Repeated and concurrent requests from the same user are idempotent. The
emailed bearer link records a separate single credential vote for a one-approval
quorum. A multi-person quorum requires distinct authenticated human identities;
the bearer link supplies no identified human to an ABAC `min_approvers` requirement.
Historical `approvals_received` counters are retained for display but do not
establish policy approval facts. The next vote on a pending historical deployment
rebuilds its quorum total from the durable ledger; rollout statuses are preserved.
Bulk approve/reject checks each deployment's own app scope and facts before its
vote or rejection. A denied item remains unchanged while permitted items proceed.

Apply additive lifecycle migration `0044_deployment_approval_identities` before
upgrading workers. It creates the ledger without changing existing deployment
columns, so the previous application can still read its schema. Upgrade all
approval writers before enabling approval-count policies; older writers do not
record identity evidence and their counters cannot satisfy those policies.

These facts are bound at user/API permission entry points, including MCP calls.
Temporal activities execute the already authorized operation using its persisted
target; they do not acquire a new user grant by trusting workflow inputs.

Collection filters evaluate narrower app/project/team policies against concrete
tenant-owned scopes. An allowed parent row does not restore a denied descendant.
App navigation remains available when an app has an allowed environment; its
operation controls still check their actual environment. A capability held only
at denied scopes disappears from the capability manifest. Scope trees and policy
facts are loaded in batches and cached only for the current request.

Environment, deployment and run collections check each row's persisted facts
before applying page limits, counts, comparisons or health aggregates. An app
with both production and staging can remain in navigation while its denied
production operations stay absent. Approved executions keep their read capability,
but their votes never make an unapproved execution visible. Agent fleet entries
use the current dispatch cluster; task history uses each task's saved cluster,
including after the default cluster changes. Unknown or foreign saved clusters
remain denied. Named-app collection requests still require access to that app.
Secret-reference, bundle-attachment and managed-service lists likewise filter
their actual environments. The proposal queue checks each proposal's own votes;
an app-wide literal proposal must be readable in every affected environment,
even when its display environment names an allowed one.

Policy create and update validate condition JSON using the evaluator's catalog:
known kinds and fields, nonempty string lists, valid IP ranges and time windows,
IANA time zones, and positive integer approval/freshness limits. Invalid updates
return a validation error before modifying any field or the policy version.
