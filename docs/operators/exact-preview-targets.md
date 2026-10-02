# Exact preview identity and explicit runtime reads

Preview catalog browsing and preview detail have separate APIs. Use the immutable
preview GUID for detail; a branch, pull request number, retained hostname, or
conventional environment name does not identify a workload target.

```graphql
query Preview($id: GUID!, $includeRuntimeCost: Boolean = false) {
  astroliftPreviewEnvironment(id: $id, includeRuntimeCost: $includeRuntimeCost) {
    id version registeredAppSlug prNumber isManual branch status hostname
    tornDownAt ttlUntil isPinned pinnedAt pinnedByEmail pinReason
    environmentStatus runtimeStatus
    environment {
      previewId previewVersion appId appVersion appSlug
      environmentId environmentVersion environmentName
      clusterId clusterVersion namespace
    }
    aggregateResources { cpuCores memoryBytes podCount }
    estimatedDailyCostUsd estimatedCostApproximate estimatedCostNotes
  }
}
```

The exact read requires `APP_READ`, tenant visibility, and token scope. A missing,
soft-deleted, foreign, or inaccessible preview returns null. The API never
substitutes a different preview sharing a PR, branch, or hostname. It reads the
persisted `PreviewEnvironment.app_environment` foreign key and validates its app,
organization, cluster and namespace ownership. The current database requires this
foreign key; defensive projection also handles unavailable historical bindings.

`environmentStatus` is `available`, `retired`, or `unavailable`. An owned retired
environment remains identifiable for historical metadata. Its hostname can remain
in the record, but it is not an action target. An unavailable binding exposes no
target. Manual preview identity, lifecycle timestamps, pin and TTL remain metadata.

Basic exact reads, paged browsing, and the deprecated unpaged catalog never list
pods or look up pricing. They report `runtimeStatus: not_requested`, empty resource
aggregates, and null estimated cost. Set `includeRuntimeCost: true` to request one
bounded runtime read for the exact available target. A provider failure reports
`unavailable`; a missing price remains null, not a zero-cost claim. Retired or
unavailable targets do not trigger provider reads even with that option enabled.

## Bound logs and deployment history

Retain the exact target's environment GUID, preview version, and environment
version when requesting logs or deployment history:

```graphql
query PreviewDeployments(
  $id: GUID!, $environmentId: GUID!, $previewVersion: Int!,
  $environmentVersion: Int!, $after: String
) {
  astroliftPreviewDeploymentsPage(
    id: $id, expectedEnvironmentId: $environmentId,
    ifMatchPreviewVersion: $previewVersion,
    ifMatchEnvironmentVersion: $environmentVersion, limit: 20, after: $after
  ) {
    items { id version appId environmentId status triggerKind commitSha imageTag createdAt startedAt endedAt }
    nextCursor totalCount
  }
}
```

`astroliftAppLogs` accepts four additive nullable arguments: `previewId`,
`expectedEnvironmentId`, `ifMatchPreviewVersion`, and `ifMatchEnvironmentVersion`.
A preview request supplies all four together and retains them on every log page
and follow request. It requires `APP_READ_LOGS`. If `environmentName` is also sent,
it must equal the canonical target name; it does not select a replacement target.
Partial proof, changed bindings or versions, retirement, and namespace mismatch
produce `PRECONDITION` before the provider is called. These reads never infer a
namespace or cluster from a name or URL. Ordinary app logs without preview proof
retain their existing behavior.

Deployment history requires `APP_READ`, filters the exact app and environment
foreign keys, and reads stored deployment snapshots without live pricing or
computed deployment-status provider calls. Its cursor is scoped to that preview
and environment. A stale or retired target is refused rather than substituted.

## Web and CLI

The web detail route reads one GUID rather than finding it in a capped catalog.
Resource/cost, logs, and deployment history each have explicit load controls. A
new actor, organization, preview, or target hides the prior scope's data, and late
responses cannot populate the new detail.

The matching CLI supports:

```sh
astro app previews show example-app --id PREVIEW_GUID --json
astro app previews show example-app --id PREVIEW_GUID --cost --json
astro app previews logs example-app --id PREVIEW_GUID --since 30m --tail 200
astro app previews open example-app --id PREVIEW_GUID --url
```

`--id` reads directly, including previews older than the recent 200-entry catalog.
`--pr` and `--branch` remain bounded discovery selectors and are followed by an
exact GUID reread. Logs carry the stored environment GUID and reviewed versions
on every page. Open and logs refuse retired or unavailable bindings. Upgrade the
server and CLI together: the matching CLI requires the additive exact-preview
schema and reports an explicit error against an older server.

Public capability metadata exposes `previews.exact_identity`,
`previews.reviewed_routes`, and `previews.explicit_runtime_cost`. Discovery is
informational and never grants permission. Mobile clients can consume this
additive canonical contract; native mobile integration and consent UI require
separate acceptance evidence.
