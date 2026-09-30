# Shared model UI — #2215

## Product flow and routes

`/models` is the organization-owned shared deployment catalogue. Hugging Face
selection at `/models/deploy` leads to an explicit CPU or GPU deployment to a
cluster, without an app/project owner. `/models/shared/[id]` shows deployment
facts, named app subscriptions, measurements, an explicit bounded model test and
management controls. Existing app/project/cloud endpoints remain accessible at
`/models/endpoints`, with their unchanged create flow at `/models/deploy/legacy`.

The Models rail consumes the server's dedicated `models` entitlement. Viewing
models does not require agent-read permission. Management controls read a fresh
`models.canManage` manifest; every actual server operation still checks current
ownership, bearer ceilings, grants, placement and version preconditions.

Screens are pure Storybook components. Route clients map actual typed GraphQL
reads and writes into their props. All eight locales translate presentation;
model repositories, immutable revisions, aliases, binding prefixes, resource
quantities, IDs, prompts/replies and actual server diagnostics remain literal.

## Hugging Face and deployment

`SearchHuggingFaceModels` sends search, publisher, task, library, license, gating,
ordering and bounded cursor pages to the server. There is no fabricated Mine
relation for public Hub metadata. CPU/GPU compatibility remains UNKNOWN: task,
library, popularity and repository metadata do not establish runtime fit.
Unavailable/rate-limited reads show failures; missing metadata stays unknown.
Search and detail results retain their actual source and observation time.

Selecting a repository performs `GetHuggingFaceModel`. A branch or tag must
resolve to a verified 40-character SHA before selection can be used. Changed
revision input blocks use immediately, including during debounce. Missing or
foreign identities cannot produce a pin. Tenant changes permanently discard
old selection and late responses, including changing away and back.

Deployment uses server-paged eligible clusters and an explicit CPU/GPU resource
draft through RHF and Zod. The review binds organization, cluster/provider,
immutable revision, CPU/memory/GPU requests, optional CPU KV cache and subscription
enablement. Configured operator runtime admission applies to the complete exact
request; it proves neither available hardware nor model fit. Replacing a provider
or changing the draft permanently invalidates an older review.

Only confirmation invokes the no-cache `ProvisionClusterModel` write. The
response must match the complete request and contain a deployment identity,
pending operation and unconfirmed readiness before the detail link is shown.
Refusals retain the review and draft; missing, mixed, foreign or premature-ready
replies cannot produce success. Late replies never replay a request.

## Catalogue and detail facts

`clusterModelDeploymentsPage` applies organization, search, cluster, explicit
compute, status, recorded-readiness and subscription filters before server
pagination. Mine uses the actual creator relation. Missing totals remain unknown;
foreign-organization responses cannot populate counts or rows.

The shared detail query binds organization and deployment ID. Denials remain
read diagnostics rather than a missing deployment claim. Failed refreshes retain
explicitly stale prior facts with retry. Inspection and refresh do not write.
Desired resources, last-applied resources, subscription revisions and operation
facts remain separate. Recorded reconciliation requires a timestamp and positive
generation and is labeled as historical evidence rather than live health.
Timestamps use explicit UTC across locales and hydration. Technical identifiers,
row versions and generation are available in a collapsed disclosure.

Legacy hosted endpoints require persisted compute facts; absent data never
implies CPU/GPU or a device count. Cloud compute is not applicable.

## App subscriptions and independent revocation

Destinations and subscriptions use server search and pagination through embedded
`ListPage` surfaces. New subscriptions require a lowercase named alias matching
`[a-z][a-z0-9_]{0,31}`. Legacy `MODEL_*` bindings remain readable. A reviewed target
must remain on the verified current page and in the deployment's cluster.

`SubscribeClusterModel` binds organization, model, environment, placement and
model/environment versions. `RevokeModelSubscription` binds the attachment version
and deployment version as well. Server eligibility and independent `canRevoke`
decisions control the actions. Disabling new subscriptions does not revoke old
access or prevent independently authorized revocation. Unsupported credential-list
runtimes block new attachment review.

Reviews warn that changes restart the shared model and can interrupt all
consumers. No credential values are exposed in UI props. Writes use no cache and
query the complete public refusal fields. Accepted results must match deployment,
subscription, alias, versions, revision and operation. Pending/revoking states
remain distinct from observed active/revoked states. Sibling aliases retain their
own identities and access state.

Accepted acknowledgments survive read failures without replay. They become
confirmed only when the matching revision is read as applied and active/revoked.
Already-revoked idempotent replies use separate confirmed copy. Model/target
replacement, pagination, tenant changes and unmounts permanently invalidate old
reviews and late responses, including changing away and back.

## Resource updates and retained-data cleanup

Management preserves repository, SHA, compute mode and placement. Resource
updates request admission for the exact current draft, then review and invoke
`UpdateClusterModel` with the deployment version and cluster/provider identities.
Accepted updates remain distinct from restart and readiness confirmation.

`DeprovisionClusterModel` is available for idle active/failed deployments even
when runtime admission is unavailable. The review requires subscription revocation
and explicitly sends `deleteData: false`. The backend refuses removal until
revocations are confirmed; a refusal keeps the review open and never renders
accepted removal. A valid accepted operation remains visibly pending. Stale facts,
unknown/denied management access and an existing operation block new writes.

## Honest observations and density

Owner-gated metrics and cluster inventory reads bind exact service, cluster and
provider identities. A 15-minute window starts after hydration and refresh uses
a new window. Responses with foreign identities, wrong scope or inconsistent
inventory are refused. Implicit tenant reads bypass shared cache and deduplication;
changed model/provider context discards late observations.

Cards preserve actual units, windows, samples, source, observation time and
availability. Measured zero remains zero. Missing, stale, unconfigured, unavailable
and unsupported states are explicit. KV-cache fraction is distinct from GPU/VRAM
utilization. Desired and applied requests are distinct from measured CPU/memory
usage and observed running/ready replicas.

The tenant inventory states exact count, returned count, its 20-row limit and
truncation, with model links and the full server-paged cluster catalogue. It has
no local search that hides later rows. Hardware capacity requires a verified
tenant node-pool mapping; it cannot fabricate zero or global saturation.

## Explicit bounded model test

The model test reads dedicated shared readiness and invokes
`testSharedModelEndpoint` only after user action. Both carry exact deployment
version, cluster and provider. Browser callers supply no URL, credential or
runtime configuration. The server checks actual cluster-owner admission.

Unknown, stale, unsupported, unconfigured, denied and malformed admission blocks
spend. Positive limits stay within the existing relay bounds. The UI states the
resource/cost impact. Mixed refusals, empty replies and timeouts never produce
success. Pending requests block duplicate sends; changed scopes and unmounts
permanently discard late results. No failure automatically retries inference.

## Verification boundaries

Apollo HTTP tests validate actual operation names/variables, SDL coercion,
server paging/search, complete refusals, stale contexts, no replay and all locale
callbacks. Portable stories cover loading, denied, unavailable, accepted-pending,
unknown-runtime cleanup, stale reads, long strings and 768-pixel layouts.

`e2e-routes/shared-models.spec.ts` walks the actual production Next UI against
controlled schema-backed HTTP fixtures: catalogue/metrics/model test, CPU/GPU
app-free deployment, independent subscription/revocation, resource update,
retained-data cleanup and subscription-dependent removal refusal. Inspection
requires zero writes; confirmed actions require one exact reviewed write. These
fixtures prove browser wiring and truthful state handling, not real model weights,
hardware fit, live tenant inference or production CNI enforcement.

Backend evidence is separate: real PostgreSQL ownership/lifecycle regressions,
real Temporal activities/history replay, native Kubernetes apply/binding/pod
checks and an enforcing Calico traffic test. The heavy model process is controlled
for local tests. Release requires generated contracts, full CI and separately
recorded immutable image/migration/control-plane deployment evidence.
