# NVKE operator agent through Astro

Astro submits native tooling to a dedicated, site-bound operator agent. The web
server and Temporal worker never execute the native toolchain themselves. The
agent protocol and installation instructions live in
[`astrolift-opscode/kubernetes/nvke/OPERATOR-AGENT.md`](https://github.com/calliopeai/astrolift-opscode/blob/feature/nvke-2656-full-surface/kubernetes/nvke/OPERATOR-AGENT.md).

This is a local integration under development. Agent connection management is
wired into the site screen and locally qualified in Storybook and Chromium
against real GraphQL/Postgres/mTLS. The native command workbench and original-receipt recovery are also wired into
the site screen and locally browser-qualified for one actual native utility.
Original-job receipt inspection, private browser artifact downloads and reviewed
read-only reconciliation are implemented locally. Original-job stop/fencing is
implemented in the agent, SDK, reviewed GraphQL recovery API, CLI and operation
screen. Real Chromium and compiled CLI qualification cover durable fencing of
explicitly never-dispatched uncertain fixtures, including a deliberately lost
reply and delayed-submission refusal. Agent-driven Linux bootstrap/install,
production supply-chain checks and deployed acceptance remain separate work.

## Native tooling in the site screen

The Native tooling section exposes all 17 source-pinned commands and their
declared positional arguments, options and choices. Choose a command, edit its
complete private JSON envelope or import a UTF-8 JSON file, then review the site
and agent versions, persistent instance, bundle digest, command, exact request
SHA-256 and observation deadline. Infrastructure-changing requests require an
explicit acknowledgement; a declared `dry-run: true` is reviewed as a dry-run.
Native option validation still runs at the API and agent boundaries.

Supply file contents as UTF-8 strings in `files` and refer to them as
`input:NAME`. Outputs use `output:NAME`; retained artifacts use
`job:UUID:NAME`; directory inputs accept the declared pinned `source:` references.
Host file paths are refused. User-edited/imported text is submitted unchanged.
Private text is cleared when review opens and is excluded from browser recovery
storage and Apollo cache. Cancelling review discards that private snapshot.

After an unconfirmed submission, refresh and use **Look up original receipt**.
This reads the existing request identity without submitting another job, even
when the configured agent was subsequently removed or changed. If no receipt is
visible, the identity remains unresolved; any explicit resubmission must retain
the exact original text, versions, deadline and acknowledgement. The local
browser qualification deliberately drops the admission response, recovers the
original receipt, and observes exactly one native site-spec generation process
through real PostgreSQL/Temporal/mTLS. Other native command-family effects need
separate qualification.

## Inspect and export original jobs

The operation detail screen has an **Original native job** section. **Read original
receipt** explicitly observes the same site-bound agent and original job UUID.
Artifact names, decimal byte sizes and SHA-256 digests are server-paginated;
searches and cursors bind to the caller, operation/site/authority versions,
physical agent pins and receipt metadata. No native request body or opaque intent
is returned by this metadata projection.

**Download private file** reviews those source pins, the original artifact name,
size and full SHA-256. The default browser file limit is 64 MiB, adjustable from
1 to 256 MiB. Every returned chunk must match the original job/name/size/hash and
contiguous byte range; the complete file hash must match before a Blob download
is offered. Cancelling the transfer or changing callers/sources discards the
partial bytes. Use the CLI streaming export for larger files. Artifact bytes
never enter UI state, browser storage, URLs or Apollo cache.

For an uncertain operation, **Reconcile original job** acquires the original
operation lease and observes that same agent/job/intent without launching a
process. A completed native receipt can update the recorded outcome; completion
of the native process alone does not establish infrastructure readiness.
A `not_found` receipt is only an observation of absence: a delayed original PUT
can still arrive. Reconciliation therefore keeps the operation uncertain and
preserves its infrastructure intent fence. Only a confirmed durable native-agent
fence can settle such an absent admission safely.
The existing generic cancellation request is consumed by an active observer;
the reviewed recovery API below can stop/fence after that observer has closed.

After reading the original receipt of an uncertain job, **Stop or fence original
job** reviews that same operation, site, authority, instance and bundle. A missing
receipt can be durably fenced only where the agent can prove it was never
admitted. An admitted process can be stopped without establishing that remote
effects were cancelled. An unconfirmed reply disables further actions until an
explicit refresh; refresh can recover the original operation's confirmed fence
without another stop or native admission. A stale review is refused before any
agent closure. The local browser stale-review test deliberately alters the
expected version to exercise this refusal; it does not claim a naturally occurring
concurrent operator change.

The local browser qualification generates a native site spec, deliberately loses
its admission response, recovers the original request receipt, downloads and
independently verifies the real private stdout artifact, and reconciles that same
job. Reconciliation starts from an explicitly marked uncertain fixture after the
native job completed; no natural infrastructure uncertainty is claimed. Exactly
one native process remains recorded across admission recovery, download and
reconciliation.

## Connect an agent

The site screen’s operator-agent section exposes configuration, verification,
enable/disable and removal with reviewed site/authority versions and agent pins.
Replacement credentials are cleared from visible fields when review opens and
are never displayed in confirmation. Cancelling or changing caller/site scope
discards the draft. An unconfirmed write blocks further writes until refresh;
refresh reads the existing authority instead of repeating configuration. Private
NVKE HTTP requests refuse redirects and bypass the browser HTTP cache.


1. Start one agent for the existing Astro site GUID with the pinned native bundle.
   Keep its state directory and instance identity across restarts.
2. Issue its HTTPS server certificate and a dedicated mTLS client certificate.
   Configure the trusted client CA and exact client leaf fingerprint on the agent.
   Astro requires hostname and server-chain verification.
3. Call `configureNvkeOperatorAuthority` with the reviewed caller, site version,
   authority version (`0` for creation), HTTPS base URL, persistent instance GUID,
   bundle manifest SHA-256 and write-only CA/client certificate/key fields.
4. Refresh the returned site version, then call `verifyNvkeOperatorAuthority`.
   Verification compares the site, instance, bundle, native revision, job format
   and complete command contract. It launches no process and proves no
   infrastructure readiness.

Configuration, credential rotation, enable/disable and removal advance both the
site and authority versions and clear verification. Omitted credentials preserve
the sealed existing pair. Removal soft-deletes the authority. A replacement has a
new identity. Controller, datastore, metrics and native Temporal credentials are
never inferred for this connection.

## Submit a native command

`astroliftNvkeOperatorContract` exposes every declared command, positional input,
option type, fixed choice and required option. `requestNvkeOperatorJob` accepts
one bounded JSON string, preserving native numbers and file contents without
browser floating-point conversion. For example:

```json
{
  "format": 1,
  "revision": "92c39b8ad563ebe5ef705f781b3038b901aba10f",
  "siteId": "REVIEWED_SITE_GUID",
  "command": "generate.sitespec",
  "positionals": ["my-site"],
  "options": {"nodes": "input:nodes.csv"},
  "files": {"nodes.csv": "name,ip\ncp-1,192.0.2.10\n"}
}
```

Use declared `input:`, `output:`, retained `job:` or pinned `source:` artifact
references. Host paths, shell commands, arbitrary environment variables and
unbounded Helm options are refused. Native kubeconfigs must use inline
credentials; executable credential plugins, host-file references and TLS bypasses
are refused by the agent.

Admission binds the actor, API token, request UUID, exact request, reviewed site
and authority versions, acknowledgement and observation deadline. Preserve the
request UUID and these reviewed inputs when recovering an uncertain admission
reply. Changed inputs or a different token cannot adopt the original intent.
Requests and peer receipts are encrypted at rest. Temporal receives operation IDs
and generations only. Command documents, endpoint credentials and client keys are
redacted according to their GraphQL schema positions, including aliased variables.

Infrastructure-changing bootstrap, install and access-configuration commands
require explicit `acknowledgeInfrastructureChange`. Another unresolved effectful
intent blocks a new effectful job. An already successful bootstrap cannot be
submitted again through another request UUID. Process completion requires a
separate check of the resulting infrastructure.

## Observe, cancel and recover

Use existing operation metadata, private result and cancellation APIs. Original
caller, token, site, agent identity and credential authority are checked before
and after each agent exchange. After a submission may have reached the agent,
repeated attempts only read that original job UUID. A lost response never
justifies another native invocation. The agent compares an opaque keyed intent
digest on submission, observation, cancellation and artifact access.

Cancellation before dispatch prevents submission. After dispatch, the agent can
stop only its owned local process group; remote effects may survive and require
review. An unresolved result remains uncertain and fences further infrastructure
changes. Observation deadlines do not promise remote rollback.

An authorized current administrator can call `reconcileNvkeOperatorJob` with
reviewed current site/authority/operation versions after the original caller loses
access. This acquires the same operation lease as normal execution and reads the
original site, persistent instance, bundle and intent. It never submits a new job.
A matching finished receipt can settle the operation; absence on that exact
agent records `OPERATOR_RECEIPT_NOT_FOUND` and retains uncertainty. A running or
conflicting receipt remains unresolved. Credential rotation is permitted for this explicitly reviewed
read path; changing the original agent identity is refused.

`stopNvkeOperatorJob` uses the same reviewed input, current-admin permission and
original operation lease for an uncertain job. The SDK first checks the agent's
explicit version-1 stop/fence capability. A complete admission journal can certify
that an absent original intent never ran and durably reject its delayed PUT;
only that confirmed `ORIGINAL_JOB_FENCED` receipt settles absence as cancelled.
Legacy or incomplete history remains unresolved. For admitted jobs the agent
records cancellation and terminates only a currently owned process group;
infrastructure effects remain uncertain where completion is unproven. A lost or
unconfirmed stop reply leaves the Astro row uncertain. Refresh and reconcile the
same original receipt before further action; never submit a replacement intent to
resolve uncertainty. Caller/source versions and authority are rechecked around
the capability handshake and stop exchange. Agent state backups must preserve
identity, admission journal, digest key and private receipts together; pruning or
rolling back admission history is unsupported.

The CLI exposes this as `nvke agent stop OPERATION_GUID --site GUID
--site-version N --authority-version N --version N`. It checks the original
operation identity and exact reviewed versions before writing and refuses a
reply for another operation. The compiled command is locally qualified against
real Astro/Postgres/mTLS for confirmed and deliberately lost stop replies,
original-operation recovery and delayed original PUT refusal. These fixtures
never execute bootstrap and leave the one earlier native generation run intact;
they do not qualify stopping live infrastructure effects.

`astroliftNvkeOperatorArtifactText` returns a bounded, base64-encoded chunk from a
retained private artifact. Review the operation and current authority versions for
each request. Offsets and sizes are decimal strings. Verify the complete assembled
file against the receipt SHA-256, save credentials privately, and keep private
diagnostics out of issue comments and workflow history.


## Remote CLI

`astro nvke agent catalog` shows the full connected command contract. Use
`agent configure/get/verify/enable/disable/remove` for agent authority lifecycle.
`agent request --file PRIVATE.json --request-file NEW-RECOVERY.json` takes the
complete envelope with reviewed `--site`, `--site-version` and
`--authority-version`. Acknowledged infrastructure changes use
`--acknowledge-infrastructure-change`. `agent recover --request-file FILE` reads
the original receipt without another admission and needs no input file.

Use `nvke operation get/wait/cancel/result` for durable operation state and private
artifact metadata. `nvke agent reconcile OPERATION_GUID` and
`nvke agent artifact OPERATION_GUID --name ARTIFACT --output NEW-PRIVATE-FILE`
require current reviewed site/authority versions and operation `--version`.
Artifact downloads stream to an exclusive protected local file, check original
size/hash and retain private partial files on failure. `--max-bytes` defaults to
64 MiB and can be raised explicitly. The remote agent API refuses redirects.

The compiled local qualification covers CLI site creation, actual TLS agent
verification, one native generation run, deliberately lost admission reply,
original recovery without the input file, private output, read-only settlement
from an explicit uncertain fixture and revoked-token refusal. Other native
command families and infrastructure effects remain separate qualification work.

## Install plan preparation SDK checkpoint

The agent now exposes read-only `POST /v1/jobs/ORIGINAL/install-plan` and
`GET /v1/jobs/ORIGINAL/install-plan?planId=UUID`. The SDK provides
`prepare_install_plan`, `install_plan` and `install_plan_page`. Preparation requires
the original unsuccessful management-install receipt and intent, a new plan UUID,
an explicit decimal `maximumInputBytes` budget, and either `request: null` to inherit
the authenticated original request or a complete proposed install request. Optional
`overrides.options` and `overrides.files` merge into that request; null deletes a field.

Use the same plan UUID and exact preparation body to recover an unconfirmed reply.
An incomplete plan is refused explicitly. Reading a saved summary or page does not
repeat Kubernetes observations or render a new deployment. Item and workload pages
are separately bound to the immutable plan digest, search, limit and signed cursor.
SDK workload counters are decimal strings or null. Source or content changes refuse
recovery; they do not authorize another install.

The SDK has 80 focused cases, including an actual agent mutual-TLS
preparation/recovery/page/tamper roundtrip with controlled Kubernetes reads. The
retained-artifact update's run had 79 passes and one 45-second subprocess timeout;
that case passed a targeted rerun with a 120-second limit. The original failed
receipt, native guard and run inventory remain unchanged. Plan UI and a continuation executor remain pending. Actual Linux agent/application RPC
qualification is recorded below. Frozen dependency closure and skipped resource
verification, durable linked execution and deployed acceptance remain pending.
The paged SDK validates the source-bound header and each page; it does not claim
to recompute a full-plan digest from a single page.

The application service now retains a separate `NvkeInstallPlan` before agent I/O.
It encrypts the private preparation body and binds recovery to the reviewed
operation/site/authority versions. A read can recover the sealed result after a
lost reply without preparing again. Repeating the retained private body requires
the original preparation actor and API token; another current administrator can
inspect an existing plan after the original caller loses access. Every operation
rechecks current admin/read authority and source versions. Public page cursors bind
the current caller/token/source as a hash, view, search and limit. The original
operation's status and effect fence remain intact. This service/model checkpoint
is exposed through typed GraphQL; CLI commands are available; operation-screen plan integration remains pending.

`prepareNvkeOperatorInstallPlan` accepts the reviewed original versions plus a
private `requestText` JSON body. `reviewNvkeOperatorInstallPlan` reads the saved
agent plan and can recover its result after a lost reply.
`recoverNvkeOperatorInstallPlanPreparation` repeats only the retained original
preparation intent; it does not admit an install. Item and workload queries are
`astroliftNvkeOperatorInstallPlanItemPage` and
`astroliftNvkeOperatorInstallPlanWorkloadPage`. Each requires the reviewed source
versions and exact plan digest. Responses expose typed metadata and bounded rows;
private input text is excluded from audit variables even through aliased inputs.

The backend checkpoint passed 21 new PostgreSQL plan/schema cases, with real
mTLS authority configuration and controlled preparation/page responses. These establish service checks alongside the separately tested SDK wire
roundtrip; the actual Linux application path is now qualified below.
Continuation remains pending.

The [actual Linux plan qualification](https://github.com/calliopeai/astrolift-opscode/blob/0a70cf3fe245c67554501cb09662063d3c60e0c8/kubernetes/nvke/qualification/operator-install-plan-api-local-2026-11-09.json)
prepared and reviewed a 12-item plan and 24 workloads through the current-admin
GraphQL/SDK/mTLS path. Complete pages and NKTP search, stale version/cursor
refusal, exact private-intent retry, a deliberately discarded same-identity
response body and cold agent restart preserved the same plan digest/version.
The private input is encrypted and audit variables are redacted. Eight native
source files, five job/run identities and the original operation's entire row,
UNCERTAIN status and version 23 remain unchanged. The initial staging refusal and
local test timeout/rerun are recorded explicitly. No linked reservation or native
continuation was admitted. Reconstructed provenance, closure/live skipped-resource
verification, plan UI review, execution and deployed acceptance remain open.

The remote CLI now provides `agent install-plan prepare`, `review`,
`retry-preparation`, `items` and `workloads`. Preparation takes private `--file`
text and flushes a metadata-only `--request-file` before the API mutation. Every
action requires the original operation and current site/authority versions plus
`--plan`. Pages additionally require the reviewed `--plan-digest`; search, limit
and cursor scope cannot change silently. Explicit retry uses only the server's
retained private intent, and requires its original actor/API token.

The compiled CLI qualification walked 12 items and 24 workloads and refused
changed-cursor and stale-version requests while preserving the original source
and uncertain operation. A later shared Lima network-service outage is tracked
in #98: both VMs are preserved without reboot or native replay, but current host
guest forwarding is unavailable. Completed CLI checks do not claim post-incident
environment availability. The full integration and plan UI/execution remain open.


The remaining-install plan panel is also available in Storybook under
`Screens/NVKE/Install plan`. It shows retained versus reconstructed inventory,
original failure and log-order evidence, proposed skip/execute actions, content
digests, and independent server-paged item/workload tables. Its private request
form preserves exact text, clears it after submission, and retains the plan UUID
for explicit review/retry after an unconfirmed response. Source or permission
changes hide prior observations and clear the private form. All eight locale
catalogs include this panel. The operation screen now wires the scoped Apollo hook and independent item and
workload page queries into this panel. Each request bypasses Apollo/browser
caches, refuses redirects, redacts diagnostics, and pins the expected organization,
actor, original operation, site and agent versions. Actor/source/visit changes
discard late replies and hide prior pages. Connected API/agent browser
qualification is still pending while the host-network incident in #98 remains
unresolved. There is no continuation execution action.

Local verification of this component checkpoint: 97 focused protocol/panel/story
checks passed; the Chromium runner
`frontend/e2e/nvke-install-plan-storybook-runner.mjs` passed all 14 panel states
without page errors and checked page overflow at 768px. The initial runner used
an incorrect responsive-story ID; it was corrected to the Storybook index's
`width-768` ID before the passing run. These fixtures do not qualify the connected
API/agent path.

Browser preparation uses an exclusive operation-scoped Web Lock and verifies a
metadata-only local-storage journal before submitting the private text. Journals
are immutable per plan UUID and contain its exact-text SHA-256 plus caller/source
identities; native request text, credentials and tokens never enter them. The
selected plan UUID survives reload without an implicit API call. Explicit review
reads the existing result; explicit retry uses the server-retained intent and
still requires the original actor/token at the backend.

If an explicit review cannot recover a result, the original caller may paste and
submit the original private text again under the same UUID. Its exact-text hash
must match the journal before any API call. This covers a connection loss before
the backend retained an intent, without silently retrying or accepting changed
input. After an explicit review attempt, choosing another proposal clears only the
selection and preserves earlier immutable journals. Read-only inspection works
without browser locks or writable browser storage; private preparation does not.

The wired recovery checkpoint has 134 focused protocol/journal/hook/panel/story
checks and 16 pure-panel Chromium states passing. Two additional Chromium
scenarios use controlled in-browser Apollo fixtures: losing a reply before
retention requires exactly one explicit original-text resubmission; losing it
after retention recovers by review alone. Changed text makes no preparation API
call, the journal exists before preparation, private text stays out of browser
storage, and both scenarios complete without page errors. These controlled
fixtures are separate from the pending real backend/Linux-agent qualification.

The dependency-inventory runtime is pinned in the SDK transport fixture at
`166aaafa9448bf9b62260104061200c55dc54d65`, including the dependency and chart-artifact helpers. All 48 client/transport
cases pass against that source and the explicit native bundle, including the
actual mTLS SDK plan prepare/recover/page path with controlled Kubernetes reads.
The regenerated coverage ledger and its 22 checks also pass. The application’s
full frontend typecheck passed after separating each generated plan mutation
input; its 15 focused hook cases and lint passed as well. These checks do not
replace the pending retained Linux-site qualification and native continuation.


The SDK also exposes a separate chart-artifact stage. After preparing and reviewing
a remaining-install plan, call `prepare_install_plan_charts(original_job_id,
intent=original_intent, body=reviewed_body)` with a new `artifactId`, the exact
`planId`/`planDigest`, `format: 1`, and positive int64-string `maximumBytes` and
`maximumExpandedBytes`. Optional `timeoutSeconds` is 1–3600 (default 300);
`allowPlainHttp` defaults to false. Select the SDK HTTP timeout for the reviewed
agent/tool deadline; a lost response remains ambiguous and is never replayed
automatically.

Recover through `install_plan_charts(original_job_id, intent=original_intent,
plan_id=plan_id, plan_digest=plan_digest, artifact_id=artifact_id)`. This GET never
downloads charts. An unpublished index returns `OPERATOR_CHARTS_INCOMPLETE`; an
explicit same-ID preparation retry must retain the exact immutable artifact
intent. Changing byte budgets or chart source requires a new artifact UUID.
The operational timeout is not part of artifact intent.

Both SDK methods require the chart-stage capability and validate the retained
parent before and after the call. They return only source IDs/digest, artifact
digest, chart count, exact byte strings, the remaining-verification state and
`continuationAuthorized: false`. Unexpected private fields, foreign identities,
invalid types, budget overruns, authorizing state, changed source/capability and
revoked client checkpoints are refused. Native install is never dispatched by
these methods.

The controlled SDK qualification uses an actual pinned agent and real Helm
download over an owned local HTTP repository. A transport deliberately discards
the successful preparation reply; a new SDK client recovers the retained index,
and an explicit exact-ID re-prepare returns it without a second fetch. Original
operation bytes, native guard, run inventory and linked reservation directory
remain unchanged. Its dependency inventory is a controlled single-chart fixture;
it does not qualify every chart in the retained Linux install. The chart stage
now has a durable current-admin API qualified through real PostgreSQL/GraphQL/
SDK/mTLS/Helm recovery. The CLI has controlled transport checks and the UI has
controlled browser recovery checks. Combined API/CLI and actual browser/API
qualification, OCI/private registry/TLS policy, full frozen execution-tree/render/
image closure, fresh skipped-resource/credential verification, linked once-only
continuation and deployed acceptance remain open.

The chart API adds `NvkeInstallPlanCharts` and migration
`0039_nvke_install_plan_charts`. It reserves an encrypted request before agent I/O,
binds the original operation/source and prepared plan version/digest/summary hash,
and freezes a successful public summary. The plan/chart API suite passed 46
PostgreSQL cases, including actual GraphQL/SDK/mTLS/Helm lost-result recovery from
an owned single-chart repository. It does not authorize native continuation.

`prepareNvkeOperatorInstallPlanCharts` accepts
`PrepareNvkeOperatorInstallPlanChartsInput`: the original operation review fields,
`planId`, `expectedPlanVersion`, `planDigest` and exact JSON `requestText` for the
SDK chart request. `reviewNvkeOperatorInstallPlanCharts` and
`recoverNvkeOperatorInstallPlanChartsPreparation` accept
`NvkeOperatorInstallPlanChartsReviewInput` with the same review fields and
`artifactId`. The read recovers a lost result without downloading again; only the
original caller and token may retry the stored private preparation intent.
Application identity reuse requires the exact original request, including
operational options, even though the agent's artifact intent omits the timeout.

Every route checks current admin authority and source versions before and after
agent I/O. A replacement current administrator/token may inspect the retained
result after original-caller revocation. Private request retries authenticate the
retained encrypted body against its keyed digest and exact artifact/plan IDs.
Responses use typed hashes/counts and decimal byte strings; request text is masked
in audit records, including rejected requests and renamed GraphQL variables.
Prepared chart IDs are also reserved against new plan preparation. The original
uncertain operation, parent plan, native guard and native admission remain intact.

GraphQL schema files and frontend generated types/documents include the three
chart operations. The operation screen now wires the chart panel through a
separate hook and browser recovery journal; live API/browser qualification remains
open.


The compiled CLI now exposes `agent install-plan charts prepare`, `review` and
`retry-preparation`. They bind `--plan`, `--plan-version`, `--plan-digest`,
`--artifact` and the original site/authority/operation versions. Preparation
requires a private body and metadata-only `--request-file` journal; review/retry
accept no private text. Exact byte strings survive output, and source changes,
case-variant/duplicate/unknown/null fields, changed journals and over-budget or
authorizing replies are refused. A lost response never silently retries.

CLI source `bbd9522e43810eeb2ac677a0ce4ee825b11bb432` passed the full local Go suite
(1,040 top-level passes; two existing native opt-in skips), targeted new boundary
and journal cases, formatting, vet, lint and a clean build. All three chart
documents validate against the exported application schema. These controlled
transport/source checks do not qualify the combined current-admin API, CLI,
PostgreSQL, agent and retained Linux install. The plan/chart API suite now passes
all 46 cases against the migrated isolated PostgreSQL database. Native
continuation, merge and deployed acceptance remain unverified.


The chart UI checkpoint adds a pure Storybook panel, strict request/result
validation and copy in all eight locales. It exposes the artifact UUID, archive
and expanded byte budgets, timeout and plain-HTTP policy, preserving decimal
precision. It hides stale results, retains the UUID after an ambiguous response
and exposes explicit review/retry callbacks. It does not fetch data or authorize
continuation. Local qualification passed 72 focused protocol/panel/story tests
and all 15 Chromium story scenarios, with desktop and 768px layouts visually
reviewed. The eight locale rendering cases also passed. The panel checkpoint
type/lint gate passed before the later hook and screen integration changes.


Chart recovery now journals the complete current actor/source and parent
plan ID/version/digest, the artifact UUID and the exact deterministic request
hash. It stores no request options or private body. Preparation verifies durable
readback under the same operation-scoped browser lock used by parent plan
preparation. An explicit failed review permits re-entering the exact original
settings; changed settings cannot reach the API under the retained UUID. Read-only
inspection remains available without browser storage or lock support. Selecting
a different artifact preserves earlier per-ID intent journals.

The hook discards late replies after source, actor, parent or visit changes and
keeps the new visit busy until an earlier request settles. The operation screen
includes the panel and gates its controls with current permission and operation
busy/error state. Two controlled Chromium recovery scenarios passed for reply
loss before and after server retention, checking journal-before-call, identical
request hashes, explicit read recovery and no request options in local storage.
Local checks passed 48 hook/parent-regression/story cases, four journal cases
and 17 operation-screen interaction cases. Targeted ESLint and fixture Ruff
checks passed. These controlled Apollo peers do not qualify the real browser/API or deployed retained Linux path. The
PostgreSQL/real Helm/mTLS and full current-tree TypeScript gates passed.

The initial database run's single setup failure was a test-agent startup race:
`identity.json` existed before JSON was completely written. The owned-agent test
fixture now waits for parseable identity data within the existing startup
deadline, then still requires successful mTLS identity verification. The final
API suite passed all 46 cases against the migrated isolated PostgreSQL database.
The formerly failing setup case and actual Helm/mTLS recovery case both passed
without skips.


The paired Helm 4 checkpoint now selects Opscode
`5200851616150efb6d121923f9e5dd2185f23474`. All 47 PostgreSQL plan/chart cases
and 128 SDK plan/chart/transport cases passed. An owned local parent/child chart
survives a deliberately lost preparation result and an agent/TLS-listener restart
at the same endpoint; explicit GraphQL recovery and private retry leave the
original operation, parent, native state, guard, runs and linked reservations
unchanged. This fixture controls the dependency inventory and Kubernetes reads.

CLI test revision `f1c1247f77fbb13ac36b5d5e832ff858b408e1ac` also passed one
compiled CLI/actual API/PostgreSQL/SDK/mTLS/Helm 4 local-chart scenario, with a lost
HTTP preparation response, durable exact-input-hash journal, explicit review and
private retry without another attempt. Its binary is the earlier `bbd9522` build;
the Go sources/modules/Makefile are identical at both revisions. Formatting, vet,
lint and all 1,040 top-level Go tests passed. Exact binary/log identities and
corrections are recorded in
`backend/providers/nvke/qualification/install-plan-chart-cli-local.json`.

This scenario explicitly configures `--connection-timeout 300`. The default
30-second agent connection expired while preparation was still running; that
failure did not prove native work stopped. Preserve the artifact/plan IDs and
journal after an ambiguous response, then use explicit review. The test harness
also needed a process deadline longer than the product's private preparation
window. These changes do not qualify actual browser/API behavior, all upstream
charts, private registries, the retained Linux installation or native continuation.


Execution-tree SDK support now selects Opscode
`b0f9d26dd5467d97eef92d6d728dca33e0bf5777`. `prepare_install_plan_tree` and
`install_plan_tree` bind the original intent, exact plan ID/digest and chart
publication ID/digest. The SDK checks both publications and the optional tree
capability before and after the request. Replies contain counts, decimal byte
strings and hashes; private manifests/arguments and continuation authorization
are refused. A lost preparation response requires explicit read recovery.

All 193 focused SDK plan/chart/tree/transport cases passed without skips. The
owned real mTLS/Helm 4 fixture discards a successful tree response, restarts its
agent/listener at the same endpoint, then recovers and explicitly retries without
another attempt. Original job/run files, guard, frozen plan, chart publication
and links remain unchanged. The fixture uses a controlled deployment and
Kubernetes GETs. Source/log identities are recorded in
`backend/providers/nvke/qualification/install-plan-tree-sdk-local.json`.
At that SDK checkpoint the durable application tree API, compiled CLI and actual
browser/API qualification were still pending. The API and CLI evidence below
extends that checkpoint; native rendering/image closure, fresh skipped-resource
proof and linked native execution remain required.


The durable tree API now reserves `NvkeInstallPlanTree` with encrypted immutable
intent before agent I/O. Its prepare, review and explicit recovery mutations bind
the exact prepared chart UUID/version/digest in addition to the plan and original
source. Public recovery rechecks current administrative authority; private retry
requires the original caller/token. Fresh locked chart/tree rows must still decrypt
and match their retained intent and creator before a reply can publish metadata.

The local PostgreSQL suite passed 89 cases without skips, including eight cases
that corrupt a retained intent during adapter I/O. Schema/migration drift checks
passed. Browser contract/storage utilities passed 91 cases, ESLint and full
TypeScript checking. They preserve decimal byte strings and metadata-only journals
bound to both publications. These are utility checks; the tree panel and actual
browser/API behavior remain pending. Source/log hashes and scope are recorded in
`backend/providers/nvke/qualification/install-plan-tree-api-local.json`.

CLI `c641f4c010d1cc5783b49d9590d572b9aaddf93c` adds
`agent install-plan tree prepare|review|retry-preparation`. Supply the exact
`--chart-artifact`, `--chart-artifact-version` and `--chart-artifact-digest` as well
as the plan/source flags and distinct `--artifact` tree UUID. Preparation also
requires a private `--file` and metadata-only `--request-file` journal. The body
contains format, the exact source identifiers/digests and `maximumBytes`; see
the CLI README for its exact seven fields. Local CLI tests, vet, lint and build
passed. Combined CLI/API/agent tree recovery also passed against application
`532e6e1073c46b5dd6cdb30f3c76e750d32b57ce`, the compiled CLI above and Opscode
`b0f9d26dd5467d97eef92d6d728dca33e0bf5777`. It deliberately discards a successful
application reply, restarts the owned operator at the same endpoint/instance,
and recovers the identical tree through CLI review and original-token private
retry without a new attempt. Original operation/plan/chart rows, receipt, install
guard, run membership and linked reservations remain unchanged. Its fixture
explicitly selects the supported 300-second agent timeout. The test and scoped
evidence live in the CLI repository under
`scripts/integration/test_nvke_install_plan_tree.py` and
`scripts/integration/nvke-install-plan-tree-local.json`. The actual browser/API
tree controls and retained Linux installation remain unqualified. The tree still
cannot authorize or dispatch native continuation.


The operation screen now includes execution-tree preparation, review and explicit
retained-intent retry, built first as a pure Storybook panel. Review both the plan
and chart publication before selecting a new tree UUID and positive decimal byte
budget. A metadata-only browser journal binds the exact input hash and full
source/publication tuple before submission. Lost replies retain that identity;
read recovery does not rebuild, and original settings cannot be changed when
recovering preparation. Source/actor/publication changes hide earlier results.
A mismatched journal hides its old selection while preserving its recovery data.

All 945 NVKE frontend cases, changed-file ESLint, full TypeScript checking and
formatting passed. The complete production Storybook build passed. Chromium
against that static build exercised 16 tree-panel states, two controlled lost-reply
recovery paths and three operation-screen states with zero page errors, including
768px overflow checks. Tree copy is present in all eight locale catalogs. Exact
source/log/screenshot hashes and limits are recorded in
`backend/providers/nvke/qualification/install-plan-tree-ui-local.json`.

These browser cases use a controlled Apollo peer. Actual authenticated route and
browser/API/agent tree qualification remain separate from the already qualified
compiled CLI/API/mTLS path. Native rendering, image closure, fresh resource and
credential proof, remaining-only selection and linked execution remain open;
no tree control authorizes continuation. The retained Linux lab remains
unreachable on its SSH, Kubernetes and operator endpoints.


The tree panel/hook now also has actual browser/API/agent recovery evidence.
Chromium runs the production-built static Storybook catalog against the owned
ASGI/PostgreSQL API and mTLS native agent. A private forwarding driver supplies
its scoped bearer token without injecting it into the page. Deliberately dropping
a successful preparation response leaves one prepared tree; UI review and
original-token private retry recover identical digest/decimal bytes without a
new attempt. The browser journal is persisted before the mutation and stores
neither raw request options nor authentication. Original operation/plan/chart
rows, native receipt, install guard, runs and linked reservations remain unchanged.
The selected CLI binary is hash-checked before and after the qualified case.

The actual static-browser case passed without skips/page errors, alongside 43
targeted story/hook cases, full typechecking, lint/format checks and the production
Storybook build. Evidence and exact source/catalog hashes are recorded in
`backend/providers/nvke/qualification/install-plan-tree-browser-api-local.json`;
the cross-repository driver is published at CLI
`4de57628c32d92952ea4b2d587cfd30fb02bd45b`. This proves the panel’s real API
mutations with owned metadata and private bearer forwarding, not authenticated
Next.js route/login or automatic source/access discovery. The retained Linux lab
and complete native closure/continuation remain unqualified.

Opscode `7a087fded94bce88026afb18f66ba4a085a7dd03` separately adds a bounded
pinned native BootstrapConfig template-rendering primitive. Ten cases include
direct native parity, UTF-8/static-copy output budgets and changed-and-restored
input refusal. It is not yet wired into sealed tree publication; runtime tree
capabilities/state remain unchanged. Kustomize/Helm manifest freezing, verified
images/private registry policy, fresh resource/credential proof, remaining-only
selection and linked native execution are still required.


SDK fixtures now select Opscode `0d17f53ec998698df88b2ab86f3d0359fbedbbfc`, which
integrates native BootstrapConfig template rendering into sealed trees and rejects
copied inventory changes before canonicalizing item types. All 193 focused SDK
plan/chart/tree/transport cases pass without skips. The real mTLS fixture verifies
rendered NICo credentials and retained templates before losing a response,
restarting the owned agent and recovering without rerendering. Private output and
original receipts/guards remain protected; source/log hashes and scope are in
`backend/providers/nvke/qualification/install-plan-tree-templates-sdk-local.json`.
Paired compiled CLI/API/browser qualification is checked separately. Kustomize/Helm
manifest freezing, verified images and fresh resource/credential proof remain
required before remaining-only selection and linked execution.


The native-template tree qualification now includes the compiled CLI and actual
Chromium panel/hook through the production-built Storybook, real ASGI/PostgreSQL
and mTLS agent. A controlled BootstrapConfig renders the pinned NICo credential
templates; sealed output and retained template sources are checked before cold
recovery. Deliberately lost CLI and browser preparation replies recover through
review and explicit private retry without a second tree attempt. Full original
rows, native receipt, guard, run membership and linked reservations are unchanged.
Evidence: `backend/providers/nvke/qualification/install-plan-tree-templates-browser-api-local.json`.
The selected browser variant also runs the common compiled CLI journey; the
standalone CLI-only variant was deselected. Authentication remains private driver
forwarding with owned Storybook metadata. Actual Next.js login/routes/access
discovery and retained Linux continuation are not qualified by this test. Native
Kustomize/Helm freezing integration, image closure and fresh resource/credential
proof remain required before linked execution.


SDK operator qualification is now pinned to Opscode
`3fa6f4fb71bbfed4f4fcb216b53d5e456b2eb1b0`, which checks the native bundle once
per chart/tree request and validates the same filesystem frame at every source
checkpoint. All 194 selected operator tests pass, including real TLS/native cold
recovery and the unchanged template-output/original-source seals. The SDK result
schema and continuation authorization remain unchanged. Evidence is recorded in
`backend/providers/nvke/qualification/install-plan-bundle-verification-sdk-local.json`.
Compiled CLI/API/browser repinning is qualified separately; agent snapshot
acquisition/freshness, rendering integration and linked continuation remain open.

The compiled CLI/API/browser journey also passes at the scoped-verification
checkpoint. It checks actual native template output/source seals, deliberately
lost preparation replies, same-endpoint cold recovery and explicit private retry
while preserving complete original rows, receipt, guard, runs and linked
reservations. Chromium uses the unchanged production-built Storybook catalog,
owned metadata and private bearer forwarding, so this does not qualify actual
Next.js login/access discovery. Current evidence is in
`backend/providers/nvke/qualification/install-plan-bundle-verification-browser-api-local.json`.


The SDK is now pinned to Opscode `870d1400c0c9986e5787b7b45635baae88785c0a`.
All 194 operator tests pass with the native render-input helper included in source
guards and tree intent. The private tree manager can capture ordered values and
setFile bytes against authenticated plan/chart/tree/helper seals; no public agent
or SDK capture endpoint is exposed. SDK qualification covers the existing tree
preparation/cold-recovery/private-retry contract, not public Helm-render routing.
Evidence: `backend/providers/nvke/qualification/install-plan-native-render-inputs-sdk-local.json`.
Live phase-sensitive context acquisition/freshness is tracked in Opscode #100;
frozen-chart publication, Kustomize, image closure and linked admission remain open.
