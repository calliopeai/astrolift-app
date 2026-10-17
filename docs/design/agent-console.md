# Agent Console (design, #1766)

Status: **design for review, 2026-10-17.** Nothing here is approved, built or
released. No product code ships with this document. PR #1764 stays as merged:
the dispatcher-token controller-input route and its opt-in setting remain
removed, and the read-only Client Cove integration works without the console.

Inputs: #1766 (gates), Client Cove `docs/agent-console-actions.md` at 5da1dd92
(proposed, unreviewed), #1971, #2432, #2434, calliope-chat-studio#686 rules,
`docs/agent-host.md`, `docs/agent-enforcement.md`.

## 1. What it is

An **optional Astrolift module** (`agent_console`, per organization) that gives
people one place to act on running agents: steer a run, answer its gates, stop
or retry it, open a box terminal, and manage a conversation that moved in.
Every action is a typed, durable **console action** with its own permission,
receipt and audit trail.

It is not:

- a new authority source. It wraps permissions Astrolift already checks; turning
  the module on or reading a task grants nothing;
- a Client Cove feature. Cove stays read-only; dispatcher service tokens never
  authorize actions;
- the inbox. Agent-to-person messages (#2434) have their own model; the console
  links to a run's thread and reuses its reply path for approvals;
- a way to widen a run. A follow-up cannot add tools, models, secrets, brains
  or runtime authority.

## 2. Who uses it

| Person | Uses it to | Notes |
|---|---|---|
| Org admin / operator | Watch the fleet's live runs, stop a misbehaving run, answer gates | Holds `agent.dispatch`, `agent_task.send_input` at org scope |
| Team or project member who launched a run | Steer their run, answer its questions, retry | Scope is the run's team/project |
| On-call responder (mobile) | Stop a run, answer an approval, open a box shell | Mobile #85 terminal UI, #109 effective RBAC; same backend rules |
| Chat Studio / CLI user | Same actions from their client with their own Astrolift token | Intersection of both sides' rights (chat-studio#686) |
| Zentinelle | Already steers, blocks, stops through the enforcement feed | Not a console user; its actions show in the console timeline |

Not users: impersonating sessions, generic staff, read-only partners (the
90-day grant), commercial relationships, other agents on MCP.

## 3. What exists today (the console builds on it)

| Capability | Today | Gap the console closes |
|---|---|---|
| Steer | `sendAgentTaskInput` (`agent_task.send_input`), `AgentTaskInputMessage`, `clientRequestId` | `claim_pending_input` is at-most-once; `deliveredAt` means claimed, never applied; no expiry; no runner ack |
| Answer a gate | `replyAgentTaskInput`; Zentinelle `guard_approval` on allow | Fine as is; console only presents it |
| Stop | `cancelTask` (`agent.dispatch`), `cancel_requested_at` | Receipt state, honest "cancel requested" vs cancelled |
| Retry | `retryAgentTask` (`agent.dispatch`), carries brain slugs (#2432) | None |
| Live view | AHP `/app/ahp`, `agent_live_attach` module | Console reads the same projection |
| Box shell | AHP terminal, `agent_box.attach` | Step-up and audit per session (§4.4) |
| Step-up, approval | `astrolift_identity.action_safety` (`exec.attach`, approvals, 24h TTL) | Register console action keys |
| Policy | Zentinelle pre-approval and enforcement feed | Pre-action check for console actions (§4.5) |

## 4. Action authority model

### 4.1 Opt-in

- New org module key `agent_console` (`ORG_MODULE_KEYS`), off by default, set by
  `setOrganizationModule` (`org.update`). The install can force it off
  (`AGENT_CONSOLE_ALLOWED`) and the licence can withhold it, as for the
  existing modules. `me.modules.agent_console` reports `enabled` and per-kind
  capability, never a bare role name.
- Each action kind is also switched on per organization
  (`agentConsoleSettings.kinds`). Module on with no kinds on is a read-only console.
- When the module is off, the existing `sendAgentTaskInput`, `replyAgentTaskInput`,
  `cancelTask` and AHP paths keep working exactly as today (open question Q1).

### 4.2 Action kinds

Each kind is defined separately; nothing is bundled.

| Kind | Permission (run's scope) | Runner capability required | Step-up default | Approval eligible |
|---|---|---|---|---|
| `follow_up` | `agent_task.send_input` | `actions.follow_up` + ack | off | no |
| `gate_answer` | `agent_task.send_input` | existing request/response | off | no (it is the approval) |
| `stop` | `agent.dispatch` | none (pod delete) | on for production env | yes |
| `retry` | `agent.dispatch` | none | off | no |
| `box_shell` | `agent_box.attach` | AHP terminal | on (as `exec.attach`) | yes |
| `move_undo` (#1971) | `agent_task.send_input` | `transfer.undo` | on | no |
| `move_out` (#1971) | `agent.dispatch` | `transfer.capture` | on | no |

Not console kinds: spawn a new agent (that is `runAstroliftAgent`), model or
backend change, secret change, brain binding change, arbitrary command
execution in a task pod. Each would need its own reviewed kind.

### 4.3 Actor and target

- **Actor** is the real, active user, resolved fresh at submit and again right
  before delivery: active membership, role bindings, token ceiling and ABAC
  attributes. Impersonation is refused. Staff or superuser status adds nothing.
  An API token may submit within its ceiling (open question Q2).
- **Target** is frozen at submit: organization, team/project, agent workload,
  task, and **execution attempt**. Astrolift has no attempt id today; the
  runner's callback session (pod name plus a runner-generated attempt UUID
  sent at checkin) becomes one. A restart, retry, re-spawn, move, team change
  or brain revocation that changes the attempt makes queued actions for the
  old attempt `expired` with reason `target_changed`; they never transfer.
- Unknown or foreign targets read as `NOT_FOUND`, never as "exists elsewhere".

### 4.4 Step-up and approval

Console kinds register in `action_safety.ACTIONS` (`agent.console.stop`,
`agent.console.box_shell`, `agent.console.move_undo`, `agent.console.move_out`)
and follow its rules: on by default, the strictest scope wins, the org may turn
a gate off. `box_shell` reuses the WebSocket 4428 step-up path. Approval for
`stop` and `box_shell` uses `ActionApprovalRequest` (bound to action, target,
requester; expires). API tokens skip step-up but never approval, as today.

### 4.5 Zentinelle policy

When the org has a Zentinelle connection, each submit is evaluated over the
install channel with the kind, actor, agent, task and attempt (no payload text):
`allow`, `deny` (refused, audited) or `ask` (becomes an approval). Delivery
re-evaluates. **Policy unavailable means hold until expiry, never allow.**
Zentinelle's own `steer` and `stop` enforcement actions appear in the console
timeline with the policy name; they are not console actions and need no
console permission.

### 4.6 Credentials

- In the web console and mobile: the user's session (or bearer token on mobile).
- Chat Studio and CLI: the user's own Astrolift token; no service credential.
- Client Cove: no action credential. Cove links an authorized person to the
  Astrolift console (open question Q3). Dispatcher tokens stay read-only.
- Runner side: the run's callback key, bound to task and attempt, may only
  claim and acknowledge actions for that exact attempt.

## 5. Action lifecycle and receipts

One table, `AgentConsoleAction` (org-scoped): `guid` (client-supplied UUID),
actor, frozen target tuple, kind, payload reference, payload digest (internal
only), `expires_at`, state, state timestamps, receipt version, reason code.
Payload text for `follow_up` lives in the existing `AgentTaskInputMessage`
row (encrypted at rest, retention per org policy), linked by action guid.

| State | Meaning |
|---|---|
| `queued` | Durable in Astrolift for the exact attempt; not yet at the runner |
| `held` | Waiting on approval or on Zentinelle being reachable |
| `claimed` | Handed to the runner's response; not proof of receipt |
| `received` | Runner acknowledged the action id for the exact attempt |
| `applied` | Runner reports it entered the harness at turn N. Not proof the model complied |
| `rejected` / `expired` / `cancelled` | Definitive, before application, with reason |
| `outcome_unknown` | Claimed or received, then the runner crashed or went silent |

Rules:

- Same UUID, same content and target returns the same receipt (after fresh
  authorization), including after expiry. Same UUID with a different digest
  or target is `IDEMPOTENCY_CONFLICT`. The runner deduplicates by action id too.
- Default expiry 5 minutes from submit; no retry ever extends it (Q4).
- Cancel, expiry and claim serialize on the row lock. Cancel before claim is
  final; after claim it is refused with the current state. Browser abort or
  navigating away is not cancel.
- A claim is acked by the runner on its next callback
  (`POST /api/dispatch/v1/agents/<task>/actions/ack/`, callback key, attempt
  id, `[{id, state: received|applied|rejected, turn}]`). Claimed with no ack by
  the next heartbeat after a restart becomes `outcome_unknown`. The existing
  steer path keeps at-most-once semantics; the console never shows `deliveredAt`
  as applied.
- Audit rows: `agent.console.submit`, `.deliver`, `.ack`, `.expire`,
  `.cancel`, `.deny`, with actor, capability source, target tuple, action id,
  state and reason. Never prompt text, payload, digest, tokens or tool output.

## 6. How it relates

- **#1971 conversation Move.** A Move into Astrolift lands as a paused
  `ConversationTransfer` and, after commit, a running task. The console shows
  arrival progress (prepare, validate, commit, abort) read-only, and owns the
  two missing actions as console kinds: `move_undo` (revert a checkpoint
  conditional on later edits) and `move_out` (paused capture). The IDE keeps
  driving prepare and commit through the transfer API; the console does not.
- **#2434 agent messages.** Messages are agent to person; console actions are
  person to agent. The run page shows the run's message thread. An
  `approval` message is answered through the existing gate (`gate_answer`), a
  `question` reply goes through `replyToAgentMessage`. A console `follow_up`
  to a run that has a thread is posted into that thread as the person's turn,
  so both views stay one conversation. Inbox fan-out and quiet hours stay in #2434.
- **#2432 semantic layers.** The console shows the run's bound brains
  (`AstroliftAgentTask.brains`, revoked reason). Console actions cannot add or
  remove brains; every `follow_up` and `gate_answer` already rechecks brain
  access (`brain_runs.recheck`), and a revocation is shown in the timeline.
- **Chat Studio agent runs.** Chat Studio stays a mirror: it launches with
  `runAstroliftAgent`, steers, answers gates and cancels with the user's token.
  When `agent_console` is on, those calls become console actions with receipts
  (same mutations, see §8), so Chat Studio can show queued vs applied honestly.
  No terminal from Chat Studio (chat-studio#686 rule 3).

## 7. Screens (Storybook first)

Each component gets stories for loading, empty, error, forbidden, module off,
and each action state before any page is assembled. Pages fit about two
screens and fetch only what is visible (paged lists, detail on open).

| Component | States / notes |
|---|---|
| `ConsoleRunList` | Live runs the viewer can act on; status, agent, launcher, pending actions count; paged 25 |
| `ConsoleRunHeader` | Target tuple (org, project, agent, task, attempt), harness capabilities, brains, module/kind availability |
| `ConsoleTimeline` | Turns, gates, Zentinelle outcomes, console actions with receipt state; windowed |
| `FollowUpComposer` | Disabled with reason (no capability, kind off, not running, policy held); shows target before send |
| `GateCard` | Question/approval with options; existing reply semantics |
| `ActionReceipt` | One per action: each state above, expiry countdown, same-ID retry, cancel when allowed |
| `StopRunDialog` | Step-up and approval branches, "cancel requested" vs cancelled |
| `BoxShellPanel` | Step-up prompt, approval pending, attached, revoked mid-session |
| `MoveArrivalCard` | Transfer phases, abort, `move_undo` checkpoint picker, `move_out` |
| `ConsoleModuleSettings` | Org module and per-kind toggles, install-forced-off and unlicensed states |

Pages: `/agents/console` (run list), `/agents/runs/[task]/console` (header,
timeline, composer, receipts). Translation keys for states and reason codes come
from fixed sets; unknown server codes fall back to a generic key via `t.has`.

## 8. API

GraphQL (all tenant-scoped, `NOT_FOUND` for foreign ids):

```graphql
type Query {
  agentConsoleRuns(first: Int = 25, after: String, status: [AgentTaskStatus!]): AgentConsoleRunConnection!
  agentConsoleActions(taskId: ID!, first: Int = 25, after: String): AgentConsoleActionConnection!
  agentConsoleAction(id: ID!): AgentConsoleAction        # receipt lookup, own authorization
  agentConsoleSettings: AgentConsoleSettings!
}
type Mutation {
  submitAgentConsoleAction(input: SubmitAgentConsoleActionInput!): AgentConsoleActionMutationResult!
  cancelAgentConsoleAction(id: ID!): AgentConsoleActionMutationResult!
  setAgentConsoleSettings(input: AgentConsoleSettingsInput!): AgentConsoleSettingsMutationResult!
}
type Subscription {
  agentConsoleActionChanged(taskId: ID!): AgentConsoleAction!
}
input SubmitAgentConsoleActionInput {
  id: UUID!                 # client-generated, kept across retries
  kind: AgentConsoleActionKind!
  taskId: ID!
  attemptId: UUID!          # from agentConsoleRuns; stale attempt is PRECONDITION
  message: String           # follow_up only
  requestSequence: Int      # gate_answer only
  response: JSON            # gate_answer only
  checkpointId: String      # move_undo only
}
```

`sendAgentTaskInput`, `replyAgentTaskInput` and `cancelTask` stay; with the
module on they create a console action internally and return it in
`consoleAction`, so existing clients keep working.

Runner (callback key, attempt-bound): the state callback response carries
`actions: [{id, kind, payload}]`; `POST /api/dispatch/v1/agents/<task>/actions/ack/`
records `received | applied | rejected`. Checkin advertises
`capabilities.actions` (`follow_up`, `ack`, `transfer.undo`, `transfer.capture`).
astrolift-agents persists seen action ids in the workspace so a restart does
not reapply.

MCP: no console actions on the agent MCP surface (agents cannot step up; same
reasoning as `AGENT_GATED`).

## 9. Tests and release gates

From #1766, unchanged in substance: two orgs and two teams, read-only partner,
revoked actor mid-queue, impersonation, foreign task and attempt ids, task
restart and retry, UUID conflict, expiry at the boundary, cancel vs claim race,
Zentinelle down (held then expired), approval expiry. Before release: one
harmless `follow_up` through the real web UI, backend, runner and harness with
correlated queue, claim and ack rows; duplicate submits return one receipt; a
killed runner shows `outcome_unknown`, never `applied`. Mocks and queue inserts
do not count. A kill switch (module off at install level) blocks new submits
and unclaimed delivery.

## 10. Open questions for Leo

1. **Q1, existing steer path.** With the module off, should `sendAgentTaskInput`
   and AHP steering keep working as today (proposed), or move behind the
   console module so every write has a receipt?
2. **Q2, API tokens.** May a personal API token submit console actions (CLI,
   Chat Studio need it), or session only for `stop` and `box_shell`?
3. **Q3, Client Cove.** Is "Cove links to the Astrolift console" enough, or do
   you want Cove to embed it later with the user's own Astrolift token (never a
   dispatcher token)?
4. **Q4, expiry.** 5 minutes for `follow_up`, 24 hours for held approvals.
   Right numbers?
5. **Q5, step-up defaults.** Proposed on for `box_shell`, `move_*` and `stop`
   in production, off for `follow_up` and `gate_answer`. Agree?
6. **Q6, Zentinelle on actions.** Should console actions be evaluated by
   Zentinelle policy (proposed), or only gate answers as today?
7. **Q7, first slice.** Proposed order: `follow_up` with ack and receipts,
   then `stop`, then `move_undo` / `move_out` with #1971, then `box_shell`
   with mobile #85.
