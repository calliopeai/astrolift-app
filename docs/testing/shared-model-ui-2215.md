# Shared model UI — #2215

The subscription view is a pure Storybook surface, pending the #2213 typed
GraphQL adapter and production route integration. Its props are presentation
facts, not a new server API or a source of permission authority.

New subscriptions require a named lowercase alias matching
`[a-z][a-z0-9_]{0,31}`. Existing legacy `MODEL_*` bindings remain readable.
The target picker and subscription list use embedded `ListPage` surfaces with
server search and pagination supplied by their adapters. A reviewed target must
still be on the verified current page. Changing pages requires a fresh review;
no browser filtering or cap hides later eligible environments.

The review captures organization, model and environment identities and versions;
revocation captures the attachment identity/version as well. Identity changes
clear the form/review state. Target, model or attachment replacement permanently
invalidates an old review, including changing away and back. Late accepted or
failed replies cannot supply completion for a replaced context. The backend must
still enforce its live ownership and version preconditions.

Adding or revoking a subscription requests a shared model restart. The view
warns that all consumers may temporarily lose access and distinguishes accepted
requests from applied access. Only server-provided reconciliation status can show
active or revoked. Unknown/unsupported credential-list runtimes block review.
Credentials are references on the server; the view has no credential-value props.

All eight locales supply translated presentation text. Immutable aliases, IDs,
binding prefixes and server diagnostics remain literal. The view's existing
subscription list may include legacy aliases; the new-subscription form cannot
create one.

Validation for the view layer includes React interactions and portable stories:
exact request identities, required aliases, explicit refusal/retry, model and
target version changes, organization A→B→A, late subscribe/revoke replies,
unverified runtime admission, server search/paging callbacks and rendered locale
copy. This is not yet proof of #2215 production HF/cluster/subscription journeys,
shared playground dispatch or metrics availability. Those acceptance items remain
open until their actual API adapters and browser checks land.

## Shared cluster placement review

The placement screen is a pure Storybook surface until the #2213 runtime
admission and provision documents are exported. It requires a verified immutable
Hugging Face revision, an eligible current-page cluster and an explicit CPU or
GPU request. The complete review binds organization, cluster and provider IDs,
resource requests, CPU KV-cache allocation and subscription enablement. Replacing
the provider under the same cluster ID invalidates the review permanently.

Only admission for the identical complete request enables deployment review.
Configured runtime admission does not establish node capacity, model fit or live
readiness. CPU is never inferred from zero GPU devices. Subscription enablement
warns about later Recreate restarts and availability impact for all consumers.
The server must recheck permission, placement and runtime admission during create.

Handled refusals keep the confirmation and draft open. Missing deployment identity
cannot render success. Late replies for changed contexts are ignored; an accepted
request has a detail link but no readiness claim, and changing the request clears
that completion permanently. React tests exercise exact payloads, CPU/GPU
admission, provider replacement, changed-back requests, late replies, refusal and
all eight locales. Portable stories cover admission/loading/error states,
translated reviews and long content at 768 pixels. Actual create and route
journeys remain separate acceptance work.

## Hugging Face catalogue view and adapter

The catalogue uses the real `SearchHuggingFaceModels` and
`GetHuggingFaceModel` documents from #2214. Search, publisher, task, library,
license, gating, ordering and bounded cursor pages are sent to the server.
The catalogue has one All view: public repository metadata has no viewer-owned
relation for a truthful Mine view. CPU/GPU compatibility is UNKNOWN; neither
model popularity nor library/task metadata establishes runtime support or fit.

Unavailable and rate-limited reads render failures rather than an empty catalogue
or substitute rows. Nullable metadata remains unknown. Search and revision reads
each retain their actual source and observation time; timestamps are formatted
with an explicit UTC zone to keep cold render and hydration consistent.

Selecting a repository performs a separate detail read. A moving branch or tag
must resolve to a verified 40-character immutable SHA before the deployment flow
can receive it. Changing revision blocks use immediately, including the debounce
interval. Missing or foreign detail identities cannot produce a selected pin.
Changing organization clears a selection and does not revive it on returning.
No deployment mutation or credential write is part of catalogue inspection.

Apollo HTTP regressions exercise actual operation names and variables, opaque
cursor walking, debounced search and revision reads, all supported server filters,
rate limits, unavailable responses, missing revisions and eight-locale selection.
Both operation documents validate against the backend-produced SDL. Portable
stories cover loading, empty, unavailable, rate-limited, resolving, pinned,
translated and long/narrow frames. Production route wiring and the CPU/GPU
placement/subscription browser journeys still remain open.

## Primary shared deployment catalogue

`/models` now reads `clusterModelDeploymentsPage` for the active organization.
Search, cluster ID, explicit compute mode, status, recorded readiness and
subscription enablement are server filters applied before numbered pagination.
Mine uses the server's actual creator relation. Invalid Boolean filters refuse
the read rather than silently widening it. Missing totals remain unknown;
foreign-organization response identities cannot supply rows or counts.

The Models rail entry consumes the server's dedicated `models` entitlement;
agent-read permission is not inferred as model-read authority. Backend module
ownership and credential-ceiling proof lives with #2213/#2214. Frontend tests
exercise the exact row contract and rendered Models-only rail.

`/models/endpoints` preserves the existing app/project/cloud endpoint contract,
owner links and server paging. Its existing create flow remains reachable at
`/models/deploy/legacy`. Hosted variants no longer imply GPU mode or capacity;
legacy CPU/GPU mode requires explicit persisted `compute_mode`. Cloud compute is
not applicable and missing hosted compute is unknown. Device summaries no longer
invent a GPU count or interpret zero devices as CPU runtime support.

All six new read documents validate against the combined exported SDL. Apollo
HTTP tests prove exact organization/filter/page variables, later-page access,
transport failure/retry, no inspection mutations and foreign-identity refusal.
The list and translated filter callbacks render in all eight locales. Pure
stories retain long/narrow, loading, stale, empty, failed and unknown states.
The primary read route is wired; shared detail/create/subscription/playground
adapters and complete production browser journeys remain separate acceptance.

## Shared deployment detail read

`/models/shared/[id]` uses the actual organization-and-ID detail query. A returned
foreign organization or deployment identity cannot populate the screen. Read
denials remain diagnostic frames and do not claim that the deployment is missing.
Failed refreshes retain explicitly stale prior facts with an enabled retry;
inspection and refresh do not write.

The detail separates desired resources, last-applied resources, subscription
revisions and reconciliation operation facts. Stored quantities remain literal;
missing resources stay unknown. Reconciliation confirmation requires the returned
recorded time and positive generation and is labeled as a historical fact rather
than live health or capacity. Display timestamps use explicit UTC in every locale.

Actual route-client Apollo HTTP tests cover exact identities, mismatched replies,
permission errors, refresh/retry and tenant changes. Eight locales preserve model
IDs, SHA and resource units; French/Japanese SSR hydration regressions check
timestamp stability. Portable stories cover missing/failed/loading/stale reads,
CPU, unsupported runtime and long/narrow frames. Raw organization/provider IDs, internal row version, operation identifiers and
generation remain available in a collapsed native technical-details disclosure.
The primary view leads with model/revision, cluster/compute/resources and recorded
readiness; opening the disclosure is covered by an actual interaction test.
The metadata route is wired;
subscription, observation and playground panels and production browser journeys
remain the following integration work.

## Actual model observations and bounded cluster inventory

The shared detail mounts the existing owner-gated deployment metrics and cluster
model density queries with exact service, cluster and provider identities. The
15-minute window is initialized after hydration and refresh requests a new window.
Neither operation writes. Responses with foreign identities, wrong scope or an
inconsistent/broader inventory are refused. Implicit tenant responses use neither
Apollo's shared cache nor request deduplication, including on a physical cluster
shared by organizations. Changed provider/model context remounts the read scope;
late replies cannot replace the current observation.

Cards show actual series values, units, aggregation windows, samples, source,
observation time and availability. Recorded zero is preserved; absent,
unconfigured, unavailable and unsupported data stays unknown. Stale measured
values and failed refreshes are marked. KV cache is a fraction and is explicitly
separate from unsupported GPU/VRAM utilization. Requests and applied resources are
separate from measured CPU/memory usage and running/ready replicas.

The tenant-only density snapshot shows exact count, returned count, the 20-row
limit and truncation, with real model links and a link to the full cluster-filtered
server-paged catalogue. It has no misleading local search. Capacity requires
verified tenant node-pool mapping and never fabricates zero or global saturation.
All eight locales translate presentation while preserving IDs, raw units and
source labels. Focused Apollo/locale/hydration tests and portable stories cover
these states; production browser journeys remain final composed acceptance.
