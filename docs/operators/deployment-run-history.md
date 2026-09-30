# Deployment phase history and persisted run logs

Each deployment can expose `phases { name startedAt completedAt failedAt healthyAt }`.
The runtime records build, apply, rollout, and health activity boundaries in PostgreSQL,
and records healthy or failed with the deployment's durable status transition. A
status transition and its log row commit together; lifecycle notifications follow
that commit. A log storage failure prevents a transition from committing.

Only work observed by the runtime gets a timestamp. Historical deployments, external
CI builds, disabled platform builds, and unavailable build infrastructure have no
platform build/push timing. An image tag or a running status does not supply missing
phase completion. Kaniko builds and pushes in one Job: its successful terminal result
confirms both completed, but does not report a separate push start. The completed
timestamps describe when the worker confirmed the result, not an internal Kaniko
stage boundary. No successful completion is recorded for a failed or stub build.

Temporal retries append new observations. `startedAt` is the first observed start;
completion, failure, and healthy timestamps are the latest corresponding observations.
A later successful retry can follow a recorded failed attempt. The complete event
sequence stays in the run log. A worker crash or activity timeout can leave a start
without a completion; the deployment's final failure is recorded by the existing
workflow failure path when it runs.

## Read and download

`astroliftDeploymentRunLogPage(deploymentId: String!, cursor: String, limit: Int = 100)`
returns `items`, `nextCursor`, `hasMore`, and `pageSize`. Items include the existing
log fields plus `phase` and `event`. The first page is the newest persisted window,
ordered chronologically within the page. Continuations fetch earlier windows, so a
client prepends them. Limits are clamped to 1–200 entries; an entry may contain a
chunk of multiline build output. Signed cursors bind the organization, deployment,
and maximum observed row id and expire after 24 hours. Entries appended after the
first page are visible on refresh rather than joining its earlier-page walk. Row id
order is observation insertion order, not a guarantee about concurrent transaction
commit order.

`astroliftDeploymentRunLogDownload(deploymentId: String!)` returns `filename`,
`content`, and `contentType`. It includes all persisted entries through the maximum
row id observed when the request starts, including entries outside the current UI
page. This is an authenticated GraphQL export materialized as text in the response;
it is not an object-storage archive or a streaming endpoint. Large exports consume
server/client memory and remain subject to the deployment's HTTP response limits.
The deployment detail page downloads this artifact and supports older-page loading.

Both APIs require `app.read_logs` on the actual deployment owner, apply token team
and explicit share permission ceilings, and evaluate policies using the persisted
environment. Selected team/project headers do not change the deployment's owner.
Organization-confined coherent history remains available after app/environment soft
teardown; foreign or mismatched app, environment, workload, or cluster ancestry does
not. Each continuation and export rechecks current authorization. The legacy
`astroliftDeploymentLog` keeps its status-only list and 1,000-entry cap.

## Capture and retention

Platform Kaniko Jobs are polled at the driver's existing interval. Each poll reads
up to the last 1,000 lines per build pod through the existing cluster log reader and
persists newly observed output before the next poll. Successful output is captured
as well as failed output. Clone credentials and configured build-argument values are
redacted from captured output. The capture cannot discover every secret an application
might print; operators should keep secrets out of build output.

The reader can return diagnostic notes for absent or inaccessible pods. Capture
warnings mark unavailable reads, a full initial tail, or a tail with no overlap.
Polling is not a complete byte stream: fast output can exceed the tail between polls;
pod retries, worker restarts, or pod cleanup can produce gaps or repeated output.
Already persisted entries survive pod/Job deletion and Redis loss. Application stdout,
external CI logs, and static-site build/sync Jobs are not archived by this change.

DeploymentLog retains its existing append-only database trigger and has no automated
age-based purge. This adds no pruning or archive policy. Capacity planning and any
privileged database retention procedure must account for captured build output as
well as lifecycle entries. Migration `0045_deployment_run_history` adds two metadata
columns and creates the cursor index concurrently; deploy it through the normal
migration process before workers or API code that writes/reads the new columns.
