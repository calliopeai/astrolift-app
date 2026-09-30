# Real model prompt relay

The additive `astroliftModelPromptReadiness(id: GUID!)` read returns only advisory
state and bounded prompt limits for one persisted endpoint. It uses the same
`APP_UPDATE` permission, actual app/team/project ancestry, environment policy,
and credential ceilings as `testModelEndpoint`. Retired or incoherent service,
app, environment, project, team, or cluster ownership is refused before reading
heartbeat/configuration facts or accessing rate/job caches. An inactive cluster
cannot relay a prompt. The mutation repeats these checks on each invocation.

Only active, app-owned vLLM endpoints with a live cluster heartbeat, configured
model and opt-in `vllm_agent_test.namespace` can attempt a prompt. Other managed
model variants have provisioning/binding drivers, but no supported playground
invocation contract. No endpoint URL, Kubernetes secret reference, API key, or
raw configuration is returned by the readiness read.

Each invocation sends one user prompt, at most 4,000 characters and 128 output
tokens. Previous displayed turns are not sent. Existing server limits remain six
prompts per minute per caller and one pending/dispatched job per cluster. The
wait is derived from the real heartbeat interval, capped at 60 seconds. Failure,
timeout, null data, or a refused mutation is never an assistant success.

The current heartbeat payload advertises `agent_version`, but no prompt-relay
capability. A READY snapshot permits an attempt; it does not prove the running
image contains a relay. Reported version 0.3.0 is not feature proof: the relay
addition and a later safety fix both used that version. A model/agent can still
fail or time out, and readiness/configuration/authorization can change before
submission. Upgrade/roll out the agent through the ordinary operator workflow;
this feature does not change an agent repository or deploy an image.

## Job admission and delivery

The v.next shared-owner API adds `astroliftSharedModelPromptReadiness` and
`testSharedModelEndpoint`. Both require organization-level `CLUSTER_UPDATE`,
the current credential ceiling and actual cluster region policy. Every call
captures the model GUID, cluster GUID, provider GUID and expected model version;
changed targets refuse before rate/job cache access. This is an operator test,
and an app subscription does not grant that permission or the operator key.

Shared prompts require an active generation model with a recorded readiness
observation, positive Deployment generation, matching applied/pod authentication
revision and applied positive replicas. They also require a managed active
cluster, enabled provider, live heartbeat and configured agent relay. The internal
target and operator Secret are derived from the saved
organization, cluster and model UUIDs, using the provider's canonical naming.
The mutation locks model, cluster and provider through admission, rechecks the
current policy, then releases those locks before waiting for the agent. The
same existing prompt, output, rate, cluster-job and wait bounds apply. The read
contains only readiness and limits; browser requests contain no URL or credential.
These additive fields are under implementation for #2213, with route integration
tracked by #2215; this documentation does not establish deployment or live
tenant inference.

The configured Django Redis cache commits job admission and the cluster's slot
together. Dispatch atomically changes the current pending job to dispatched and
renews both entries' 180-second TTL. Concurrent heartbeats dispatch at most once;
if the heartbeat response is lost, the model may execute zero times. There is no
exactly-once network-delivery guarantee and no automatic re-dispatch.

A result can finish only the current dispatched job. The first terminal outcome
releases its matching slot atomically. Replayed terminal results acknowledge that
original outcome unchanged, without refreshing its TTL or clearing a newer job's
slot. Pending, orphaned, expired and foreign-cluster results are refused. Prompt
jobs and their results remain transient cache data, not persisted crash history.

Redis transitions use bounded WATCH/MULTI retries against the configured primary,
watching both job and slot. Redis 6.0.9 or newer aborts a watched transaction when
an entry expires; the local regression proof uses Redis 7. See the
[Redis transaction contract](https://redis.io/docs/latest/interact/transactions/).
The supported production backend is Django's core `RedisCache`; this does not
add Redis Cluster support. `LocMemCache` provides process-local transitions for
ordinary tests, not coordination across server processes. Other cache backends,
including `DummyCache`, refuse relay operations.

Cache connection failure or exhausted transition retries return a generic
`PRECONDITION` mutation failure. Healthy authenticated heartbeats still persist
their observations and return without a test job; unavailable result callbacks
return HTTP 503. Result-polling failure does not cancel an already admitted job:
it may still finish, so transport uncertainty is not evidence that retrying the
prompt will have no additional effect. Existing permission and token checks run
before cache admission, and rate limits remain independent of job transitions.

Regression checks use actual PostgreSQL ownership and HTTP credentials plus
isolated Redis key prefixes. They cover independent processes, concurrent
admission/dispatch/results, expiry during watched transitions, immutable replay,
and cache failure at rate checking, admission, polling and agent HTTP boundaries.
Redis tests use `ASTROLIFT_TEST_REDIS_URL`, then `DJANGO_CACHE_URL`, then the
configured cache location; unavailable Redis is a failure, not a skipped proof.

Source verification: published `astrolift-agents` main at
`ce47cfb152b0ca7bc362833640b0cfc2ed1ec48d`,
`images/keepalive/keepalive.py` (blob
`ba279e8aebffbd55e975c470b9753bf25a882655`) implements the existing heartbeat
`test_job` relay. It resolves credentials inside the cluster and validates the
Service hostname against the job's secret namespace. The control plane derives
that target through `resolve_agent_test_target`, never caller-supplied URLs.
Tests use controlled HTTP/agent transport replies and make no tenant model calls.

## Browser behavior

`/playground` selects persisted GUIDs from the existing authorized model catalog,
using server search and numbered pages of ten rows. The picker does not request
raw model configuration. Selection never invokes a model. Each explicitly
submitted prompt uses the existing mutation; no conversation roles, previous
turns, caller URL, or browser API key enter that request.

Chat and batch share one local admission/rate budget. A batch accepts up to six
plain-text prompt lines and sends them sequentially. Failure stops remaining
lines; cancellation stops future submissions but an already admitted server job
can still finish. There are no automatic retries. The server independently
rechecks permission, state, configuration, and its rate/cluster job limits.
Late metadata/results and saved-session state cannot cross identity or endpoint
changes, including A → B → A transitions. Readiness refresh also closes local
admission until that new read finishes.

History and starred sessions are browser-local records scoped to the active
organization GUID and current user id; they are not server audit/history. Only
validated version-2 records are loaded, capped at 30 sessions and 40 messages per
session, with 4,000-character user prompts and 8,000-character endpoint replies.
Start a new session after reaching the local message limit. Dates and observed
metrics are validated; absent metrics remain unknown. Legacy global demo records
and URL-hash conversations are ignored. Opening a saved session does not invoke
its stored endpoint: that persisted GUID must pass a fresh readiness read and
every later mutation authorization check. Clipboard JSON and CSV/JSONL exports
contain only the locally observed records explicitly selected by the user.

The real prompt, local history, and starred routes are reachable from Models.
Unrelated `/playground/topology` and `/playground/observability` showcases, and
`/forms`, remain parked. All new playground notices have all eight UI locales.

## Shared cluster-owned endpoints

The additive `astroliftSharedModelPromptReadiness` query and
`testSharedModelEndpoint` mutation bind the deployment GUID, expected cluster
GUID, expected provider GUID and current deployment version. Their operator
admission is exact-organization `CLUSTER_UPDATE`, with current credential and
region-policy checks. App subscription rights do not grant operator playground
or aggregate telemetry access. No browser key, endpoint URL or raw config is
accepted or returned.

Shared readiness requires the current available cluster/provider and the
persisted confirmed generation, auth revision, observation time and recorded
canonical backend target. It is advisory last-confirmed state; admission is
rechecked under locks before the existing atomic bounded relay enqueue. The
agent uses the private operator key in the model namespace, not a subscriber's
app binding. Pending, failed, retargeted or unobserved deployments refuse a
prompt. The existing bounded limits, transient-cache delivery semantics and
at-most-once dispatch conditions above also apply to shared requests.

Subscription creation/revocation can restart the entire shared model; pending
reconciliation does not promise uninterrupted playground availability. See
[vLLM hosting](vllm-model-hosting.md#organization-owned-shared-deployments) for
certified CPU/GPU declarations, named bindings, monitoring and agent prerequisites.
