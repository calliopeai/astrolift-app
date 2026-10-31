# Agent task completion callbacks

Register an HTTPS completion destination on `runAstroliftAgent` with
`callbackUrl`, `callbackSecretRef`, optional `correlationId` (at most 128
characters), and `callbackMode: FULL` or `NOTIFY` (default `FULL`). The URL is
distinct from the pod's `AGENT_CALLBACK_URL`, which reports results **into** the
control plane. The public [setup and verification guide](https://astrolift.dev/guides/agent-completion-callbacks/)
contains CLI commands, GraphQL examples and a Python signature verifier.

Preconditions: `org.update` on the organization to set the allowed hosts,
`secret.write` to store a signing key, `secret.read` plus `agent.dispatch` on
the agent's app to dispatch with a callback (`FULL` also needs `app.read`),
and task-scoped `app.read` plus `agent.dispatch` to redeliver; an organization
token or user session, not a team-scoped API token; the surfaces are the
`astro agent callbacks` and `astro agent dispatch` commands or the GraphQL
mutations named below; the production worker must reach the receiver. The
receiver host must be on the allow-list and present a certificate the worker
trusts. Placeholders: `<task-id>` is the task `id` from `astro agent ls`;
`<name>` is the signing key name; `<host>` is the receiver's exact host or
`*.example.org` pattern; `<url>` is the HTTPS callback URL.

```bash
astro agent callbacks show
astro agent callbacks configure --allow-host <host>
astro agent callbacks secret-set <name> --file <path>
astro agent dispatch <agent-slug> --callback-url <url> --callback-secret-ref <name> \
  --correlation-id <id> --callback-mode FULL
astro agent callbacks redeliver <task-id>
```

Organization administrators configure `configureAgentTaskCallbacks(allowedHosts)`
with `org.update` and write a signing key using
`setAgentTaskCallbackSecret(name, value)` with organization `secret.write`.
Names contain 1–128 characters, begin with an alphanumeric character and may
contain `_`, `.` and `-`. Keys contain 32–4096 UTF-8 bytes and use the existing
encrypted organization-secret backend. Dispatch with a callback also requires
organization `secret.read`; `FULL` also requires the agent app's `app.read`.
Team-scoped API tokens cannot configure or dispatch
organization callbacks. The dispatch request contains only the key's name.

Allowed hosts are exact names/IPs or `*.example.org` patterns (subdomains only,
not the apex). Credentials, fragments, HTTP URLs, loopback and metadata addresses
are rejected. DNS is checked on every delivery, then the connection is pinned to
the checked address and verifies TLS against the original hostname. Private VPC
addresses are supported. Redirects are never followed. The production worker
must have DNS, routing and a trusted certificate chain for the receiver.

The worker POSTs JSON with `X-Astrolift-Event: agent_task.finished`, a fresh UUID
in `X-Astrolift-Delivery`, Unix seconds in `X-Astrolift-Timestamp`, and
`X-Astrolift-Signature: v1=<hex HMAC-SHA256(key, timestamp + "." + raw_body)>`.
Receivers verify the **raw** body with constant-time comparison, reject a clock
skew greater than five minutes and deduplicate by `task_id`. Before replacing an
organization signing key, configure the receiver to accept both old and new
keys; retire the old key after in-flight requests and its timestamp window have
elapsed. Each attempt reads the current key rather than freezing it in the event.

The terminal transition (`completed`, `failed`, `cancelled`, `timed_out`) and its
single logical event commit in one PostgreSQL transaction. Intermediate states
and Temporal activity retries do not create completion events. The body contains
task identity, unchanged correlation ID, final execution attempt, timestamps and
reported token/model usage and duration. Unknown token/model/duration values are
null. Agents may report `usage: {input_tokens, output_tokens, model}` with their
final pod callback. `FULL` includes the task result and failure message;
`NOTIFY` omits `result` and makes `failure_message` null.

Monetary usage is retired (#2464). New normalization discards `total_cost_usd`.
For pending historical callbacks and manual redelivery, the worker removes only
known envelope `usage.total_cost_usd` in memory before signing the actual outgoing
bytes. Stored historical metadata and queued ciphertext are not backfilled or
rewritten; arbitrary user result JSON remains unchanged. Normal terminal and
retention purges still apply. Consumers must accept the missing optional field,
including on an existing logical event. Malformed envelopes and bodies above
8 MiB fail closed with a fixed metadata-only delivery error. Drain old workers
as described in [the retirement rollout](../operators/retire-costing.md); an old
worker can still send the earlier envelope until stopped.

Any 2xx response succeeds. 4xx responses other than 408 and 429 fail permanently.
Other responses and connection/time-out errors retry with ±20% jitter: 30 seconds,
2 minutes, 10 minutes, 30 minutes, then hourly, with a final attempt at the
24-hour boundary. Requests have a 10-second deadline. Delivery is at least once:
a worker interrupted after the receiver accepts a request can send it again.

`agentTask` exposes `callbackStatus` (`pending`, `delivered`, `failed`, or null
when unconfigured), `callbackAttempts` and sanitized `callbackLastError`.

- **Signature:** `callbackStatus: failed` with `callbackLastError` naming the
  final HTTP status or connection error, or `callbackStatus: pending` with a
  climbing `callbackAttempts` while the receiver answers 5xx, 408, 429 or
  times out.
- **Fix:** fix the receiver, its allow-list entry or its signing key, then
  replay the final event without rerunning the agent:
  `redeliverAgentTaskCallback(taskId:)` / `astro agent callbacks redeliver
  <task-id>`. A pending callback cannot be replayed; wait for it to settle.
- **Rollback:** no rollback needed; redelivery requeues the same final event
  under a new generation.
`redeliverAgentTaskCallback(taskId)` requires task-scoped `app.read` and
`agent.dispatch`. It requeues the same final event under a new delivery generation
and 24-hour window without running the agent again. Pending callbacks cannot be
replayed. Expired or deleted task results cannot be replayed; the default
retention is 72 hours, or the linked AgentRun's configured retention.

## Delivery recovery and sensitive data

`DeliverAgentTaskCallbackWorkflow` receives only callback row ID and generation.
Its persisted timers survive worker replacement. A 60-second
`AgentTaskCallbackReconcileWorkflow` schedule recovers queued events after a
Temporal outage; starting an existing delivery workflow joins it rather than
terminating it. The database lease prevents concurrent workers from POSTing the
same generation, while expired leases allow recovery after an interrupted worker.

Retry bodies are encrypted at rest. Delivery and exhaustion clear the ciphertext;
`FULL` also stops and clears its payload when the source result is erased,
expires, or its linked AgentRun is deleted. The default 72-hour result retention
covers the 24-hour retry window; shorter configured retention bounds `FULL`
delivery. Use `NOTIFY` to keep result data out of retries.
manual replay reconstructs from the still-accessible task result. Callback
request and response bodies, destination URLs and signing keys are excluded from
delivery logs and Temporal history. Logs contain only task/correlation identifiers,
final task status, HTTP status and delivery attempt, plus ordinary trace/request
context. Do not put sensitive data in correlation IDs. Receiver response bodies
are never read or saved.

Verify worker registration, the active reconciliation schedule and database
migrations before rolling out web/worker. Monitor pending/failed delivery state;
fix the receiver or its allow-list/key configuration before manual replay. Tests
cover real PostgreSQL migrations, actual HTTPS receivers, outages through the
24-hour boundary, rotation, payload markers absent from logs/history, and actual
Temporal worker replacement and history replay.

## Verify

```bash
astro api graphql -q 'query { agentTask(id: "<task-id>") { status callbackStatus callbackAttempts callbackLastError } }'
pytest astrolift_agents/tests/test_completion_callback_api_2234.py astrolift_agents/tests/test_completion_callback_outbox_2234.py astrolift_agents/tests/test_completion_callback_dispatch_api_2234.py
```

The terminal task reports `callbackStatus: delivered` with a null
`callbackLastError`, the receiver logged one `agent_task.finished` delivery
whose signature verified, and the test modules pass.
