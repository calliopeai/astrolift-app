# Agent policy delivery and quarantine

Astrolift receives Zentinelle policy actions through the authenticated install
channel. The durable `AUDIT.agent.session.event` outbox carries recorded turn,
tool and approval events, with task/turn/tool identity, registered cluster,
team/project and declared agent/spec/tool-preset intent. Prompt and tool-result
content is not copied to the governance stream. A stable event ID makes retry
delivery idempotent. This stream is independent of attached AHP clients.

Task reconciliation, accepted runner callbacks and the box reaper poll the
install-scoped enforcement feed. Each action binds tenant, cluster, minted agent
identity and saved task/box target. Both the authenticated polling channel and
`POST /app/zentinelle/agent-enforcement/` use the encrypted enrollment credential.
A delivery requires HMAC-SHA256 over sorted compact JSON, using SHA256 of the
credential as its key, a fresh UUID nonce and an expiry within 120 seconds.
The header for a POST is `X-Zentinelle-Signature: sha256=<hex>`.

A stable action ID reserves one exact intent. A nonce replay is refused; the
same intent with a new delivery nonce returns its recorded outcome. An uncertain
external effect is not automatically repeated. Received actions, final outcomes,
policy IDs and evidence links are audited. Task interactions and AHP chat
annotations show the outcome, including to CLI followers.

| Requested action | Applied control |
|---|---|
| log / warn | Recorded policy outcome and session/timeline annotation |
| alert | Same evidence, plus inbox notifications to active authorized owners/controllers |
| steer | Durable follow-up queue, labelled Zentinelle and policy name |
| require_approval | Preserve a reported live approval hold; otherwise escalate |
| block tool_call | Deny a reported, unanswered approval gate; otherwise escalate |
| block turn / redact after observed execution | Report unavailable harness control and revoke model access |
| block revoke_key | Confirm per-run gateway key revocation |
| block stop | Existing task cancellation or box teardown |
| block quarantine | Refuse future agent/spec dispatch, and stop the current target |

If key revocation is unavailable, delivery attempts the existing stop control
and reports that fallback. Boxes have a stop control, not a suspend control.
Observed events do not establish that a tool was held before execution; the
pre-approval policy path remains responsible for that guarantee. Gateway content
policies retain their own in-flight redaction behavior.

The org module `agent_policy_enforcement` is off by default. The install switch
`AGENT_POLICY_ENFORCEMENT_ALLOWED` can force it off. A disabled organization or
audit-mode policy records observation without interruption; audit-mode alerts
may notify. The normal per-run gateway enrollment and cluster guards still apply.

`agentQuarantines` lists only quarantines the caller can manage.
`clearAgentQuarantine(id: UUID)` requires `agent.dispatch` at the agent's app/team
scope, or org scope for an environment spec. A controller in another team or org
cannot clear it. Clearing permits future dispatch and does not restart a stopped
run. The clearing mutation has its own authorization audit.
