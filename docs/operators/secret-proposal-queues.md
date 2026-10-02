# Secret proposal queues

The additive proposal page API returns queue metadata without secret material.
The legacy list remains available for compatible clients and retains its 200-row
cap. Clients needing a complete queue use `astroliftSecretChangeProposalsPage`.

```graphql
query ProposalPage($appSlug: String, $status: String, $limit: Int!, $after: String) {
  astroliftSecretChangeProposalsPage(
    appSlug: $appSlug, status: $status, limit: $limit, after: $after
  ) {
    items {
      id registeredAppSlug environmentName op status proposerDisplayName
      requiredApproverCount approvalsCount createdAt expiresAt decidedAt appliedAt
    }
    nextCursor totalCount complete
  }
}
```

Start with `status: "pending"`, `limit: 25`, and no `after`. Limits must be 1–200.
Use `nextCursor` unchanged as the next request's `after` with the same app/status
filters. Ordering is descending creation timestamp and GUID, including ties.
`totalCount` describes the caller-visible filtered queue; `complete` is true when
there is no subsequent page in that unchanged queue. Count and continuation never
include unreadable apps or foreign organizations.

Cursors are signed, bound to the current tenant, actor, credential and filters,
and expire after 15 minutes. The server uses scoped database aggregates to detect
proposal creation, update, deletion and approval changes before and after reading
a page. Fixed-size SHA-256 identity fingerprints also detect changed visible
proposal cohorts and vote identities when counts, versions and timestamps stay
equal. The database sums hash limbs from deduplicated source rows; it does not
load all GUIDs into the application or build whole-queue arrays or strings.
Changed queues return `STALE_CURSOR`; malformed or mismatched tokens
return `INVALID_CURSOR`, rather than silently restarting or producing an empty
continuation. Discard the entire cursor chain and request the first page. Counts
and ordering are observations of an unchanged walk, not a retained historical
snapshot or a lock on concurrent decisions.

The approvals overview displays a top-five metadata summary linking to
`/approvals/secret`. That full queue follows server cursors through the shared
ListPage and DataTable controller. Refresh, successful proposal decisions and retry after a refused
continuation restart at page one. It does not fetch a capped list and paginate it
locally. A failed continuation remains an error with explicit recovery.

Exact selection uses the proposal GUID, independently of its position in a page:

```graphql
query ProposalMetadata($id: GUID!) {
  astroliftSecretChangeProposalMetadata(id: $id) {
    id registeredAppSlug environmentName op status proposerDisplayName
    requiredApproverCount approvalsCount createdAt expiresAt decidedAt appliedAt
  }
}
```

Page and exact metadata reads require present `app.read` authority and retain
organization, app/team, bearer-ceiling and operation-policy checks. The metadata
type has no payload, diff, value hint, apply error or approval reason fields.
Opening the existing proposal detail remains a separately authorized read; its
payload redaction, `secret.read` and step-up checks remain unchanged. Paging never
grants reveal or decision authority. Decisions still target the exact GUID and
use the existing mutation envelopes and permissions.

Source contract: [issue #2233](https://github.com/calliopeai/astrolift-app/issues/2233).
This guide describes the additive API; availability depends on the installed
server. It does not claim a deployment or mobile client update.
