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
