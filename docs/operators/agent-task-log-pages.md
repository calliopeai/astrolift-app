# Agent task log pages

`agentTaskLogsPage(id, cursor, limit)` adds structured, server-paged logs to
the agent Observe view. `agentTaskLogs(id, tail): [String!]!` remains available
for existing run-detail, CLI and other callers.

```graphql
query TaskLogPage($id: ID!, $cursor: String, $limit: Int) {
  agentTaskLogsPage(id: $id, cursor: $cursor, limit: $limit) {
    items { id timestamp level stream message podName container }
    nextCursor hasMore pageSize liveOnly windowLimited expiresAt
  }
}
```

The first request returns the newest page, in the pod's chronological output
order. Pass `nextCursor` unchanged to read an earlier page; prepend its items.
`limit` defaults to 100 and clamps to 1–200. `pageSize` reports the effective
limit. Duplicate messages, including duplicates with the same timestamp, are
separate lines with distinct IDs within the snapshot.

The source is a bounded, one-shot kubelet read of at most 2,000 recent lines.
The server caches that immutable snapshot for five minutes, so appends cannot
shift subsequent page boundaries. `expiresAt` reports that snapshot's expiry.
`windowLimited` is true when the read reached the 2,000-line ceiling; it does
not claim that all earlier output has been retained. The Observe view pauses
polling while loading earlier pages. Refresh log starts a new latest snapshot
and resumes polling.

`liveOnly` is always true for this source. The cache is temporary, shared across
API workers, and is not a durable crash-history archive. An explicitly removed
pod or an unavailable cluster can produce an empty page. Completed pods that
remain on the cluster may still provide output; availability after removal is
not promised. A provider read failure or timeout may return an empty or partial
snapshot, matching the existing diagnostic log-read behavior.

Timestamps, streams, pod names and containers come from the provider's log
records. Messages retain their original text. The optional level is inferred
from recognized leading level tokens, including application timestamp prefixes,
or a JSON `level`/`severity`; unrecognized lines have a null level. Kubernetes'
combined log API usually reports stdout for all lines, so the page does not
invent stderr classification. A provider that supplies stderr keeps that value.

Every page rechecks `agent.read` against the task's actual project, team or app
owner, or the explicit organization for an unowned task. Role grants, bearer
permission/team/share ceilings and operation policies apply before both cache
and driver reads. A recorded dispatch target supplies the cluster and namespace;
an unavailable, changed or foreign target cannot fall through to a replacement
dispatcher. Legacy tasks without frozen placement use the existing dispatcher
or managed-cluster resolution, with the actual cluster region rechecked.

Cursors are signed and bound to the organization, task, cluster and namespace.
A malformed or mismatched cursor produces `LOG_CURSOR_INVALID`. An expired or
evicted snapshot produces `LOG_CURSOR_EXPIRED`. Both are GraphQL query errors;
refresh the log rather than replaying the cursor. Authorization denial retains
the normal permission error and does not reveal cached output.
