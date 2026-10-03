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

## Exact live streams and log exports

The additive #2257 contract extends the four historical-log selectors to
`astroliftOnAppLog`, `astroliftOnAppLogs`, and `ExportAppLogsInput`. Require a
server advertising `previews.reviewed_live_logs` and `previews.reviewed_log_exports`
before using these fields. Capability metadata is informational; live logs still
require `APP_READ_LOGS`, and exports require `APP_LOG_EXPORT`, at the actual app
and preview environment operation context. Neither capability grants access.

```graphql
subscription PreviewTail(
  $app: String!, $preview: GUID!, $environment: GUID!,
  $previewVersion: Int!, $environmentVersion: Int!
) {
  astroliftOnAppLogs(
    appSlug: $app, previewId: $preview, expectedEnvironmentId: $environment,
    ifMatchPreviewVersion: $previewVersion,
    ifMatchEnvironmentVersion: $environmentVersion, follow: true, tailLines: 100
  ) { podName container timestamp message stream }
}

mutation PreviewExport($input: ExportAppLogsInput!) {
  exportAstroliftAppLogs(input: $input) {
    ok errors { code message field }
    data { id status downloadUrl sha256 byteCount rowCount truncated expiresAt }
  }
}
```

Supply the export input with `appSlug`, `podName`, `format` (`CSV`, `NDJSON` or
`TXT`) and all four selectors from the exact preview read. An optional
`environmentName` must equal that read's canonical name. Single-pod live logs
also require `podName`; plural live logs discover replicas only inside the
reviewed namespace. Explicit pod, workload and container selections must match
current discovery inside that namespace before log bytes open. These checks use
existing provider pod metadata; they do not provide an atomic Kubernetes pod-UID
lease or prove future pods by name alone.

All four selectors must be present together. Missing proof for a name or route
that identifies an actual preview is refused. Partial proof, changed versions,
foreign ownership, deleted/replaced bindings, retirement, and invalid placement
return a static `PRECONDITION` without trying the production/default cluster.
Ordinary app log requests retain their existing behavior. Refresh exact metadata
and start an explicitly reviewed new request after a refusal; never silently
replace the target or resume an old stream onto a replacement environment.

Exact streams recheck active requester membership, live credential expiry and
revocation, current grants and operation policies, and the entire captured
app/environment/cluster binding before provider setup, before each emitted line,
and while idle at one-second intervals. Refusal closes underlying streams and
returns a static GraphQL error (`PRECONDITION` or `PERMISSION_DENIED`). This is
periodic authorization, not a permanent lease or an atomic provider-write fence.
Exact exports cap the existing line count and add a 30-second source-read deadline;
they perform the same checks during collection and before publishing a receipt.
Failed or refused reads do not produce a successful artifact receipt.

Preview artifacts retain only reviewed identity/version and original authority
metadata in `source_snapshot`, alongside the existing log artifact storage.
They retain no token value or copied log body in the receipt. Their opaque download
URL and TTL are necessary but insufficient:

- A same-origin browser must use the original requester's authenticated session.
- A bearer client must send the same original API credential; a different token,
  even owned by the same user, cannot widen access to the old artifact.
- Both paths recheck the requester, present grants, original credential scopes and
  team ceiling, live token validity when one created the export, exact source
  lifecycle and versions, artifact status and TTL. Browser use never bypasses the
  original token's revocation or scope ceiling.

Preview downloads use `Cache-Control: private, no-store` so shared caches do not
serve a previous authenticated response as fresh authorization. The download
view returns opaque `404` on link, source or access refusal. An
invalid or expired bearer can instead be rejected by the shared authentication
middleware with generic `401` before the view runs. Each 64-KiB artifact chunk is
checked before reading under ASGI; mid-transfer refusal closes the file and
truncates the response. A client must verify `Content-Length` and
`X-App-Log-Export-Sha256` rather than treat a partial transfer as success. The link
never reruns a provider query. Old receipts remain empty and gain no inferred
preview proof; a legacy artifact whose recorded environment currently identifies
a preview is refused. Ordinary legacy capability-link exports retain their
existing token-and-TTL behavior.

These server contracts do not add a live WebSocket or export command to the CLI.
The current `astro app previews logs` command uses guarded historical GraphQL
polling, carrying all four selectors on every page and follow request. The web
preview detail also uses the existing historical query. API clients can consume
the new live/export fields explicitly; native mobile acceptance remains separate.
Local PostgreSQL/provider-boundary tests establish source and authorization
contracts, not successful log delivery from a live production preview.

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
