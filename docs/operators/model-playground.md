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
