# Runbook: Agent dispatch

Procedures for dispatching agent tasks, monitoring execution, and
handling failures or stuck agents.

Preconditions: `agent.dispatch` on the agent's app to dispatch, cancel or
redeliver, `agent.read` to list tasks and read their logs,
`agent_task.send_input` to steer a running task or read a send receipt,
`secret.write` on the organization for `astro agent secret set`; the console
(`/agents`, `/agents/[agentSlug]`, `/tasks`, `/tasks/runs/[id]`), an `astro`
login (`astro auth status`), or GraphQL (`runAstroliftAgent`, `cancelTask`,
`agentTasksPage`, `agentTask`); a shell in the backend container for `python
manage.py temporal_ops`; the agent's dispatch cluster must report
`heartbeatStatus: connected`. Placeholders: `<agent-slug>` is `slug` from
`astro agent workloads ls` or `agentWorkloads`; `<task-id>` is `id` from
`astro agent ls` or `agentTasksPage`; `<workflow-id>` is
`DispatchAgentTaskWorkflow-<task-id>`; `<env-spec-slug>` from `astro agent
env-spec ls`; `<ENV_VAR>` is the variable name the agent reads.

---

## What agents do

Agents are operator-defined units of autonomous work: database
migrations, canary analysis, smoke tests, infrastructure provisioning,
or any containerised task that runs once, on a cron, in a loop or on a
trigger. An `AgentTask` is dispatched by `DispatchAgentTaskWorkflow`
(Temporal) as a Kubernetes Job or Docker container on the agent's
dispatch cluster and runs to completion there.

---

## Dispatch an agent task manually

### From the UI

1. Go to **Agents** in the left sidebar.
2. Open the agent (or search by name / skill).
3. Choose **Once** and fill in the prompt or JSON inputs; **Advanced**
   pins an environment spec and a timeout in seconds.
4. Click **Dispatch run**. The task appears under **Tasks** immediately.

### From the CLI

```bash
astro agent dispatch <agent-slug> --input '{"key": "value"}'
astro agent dispatch <agent-slug> --input @payload.json --env-spec <slug> --timeout 900
```

Add `--wait` to block until the task reaches a terminal state, and
`--tail` to stream the task log while waiting (implies `--wait`).
`--callback-url`, `--callback-secret-ref`, `--correlation-id` and
`--callback-mode` register a signed completion callback; `--brain` adds a
brain the run may read (repeatable, at most 10).

### Via the API

```graphql
mutation {
  runAstroliftAgent(input: {agentSlug: "<slug>", triggerPayload: {key: "value"}}) {
    ok
    errors { code message }
    data { id status }
  }
}
```

---

## Monitor a running task

The task detail page (reachable from **Tasks → [id]**, route
`/tasks/runs/<id>`) shows:

- **Status**: `draft` → `queued` → `provisioning` → `running` →
  `completed` | `failed` | `timed_out` | `cancelled`.
- **Log stream**: real-time stdout from the agent container.
- **Input / output**: the trigger payload and any structured result
  the agent reports with its terminal callback.
- **Duration**: wall-clock time in the current or final status.

From the CLI:

```bash
astro agent ls --status running --limit 20
astro agent logs <task-id> --follow
astro agent inspect <task-id> --json
```

---

## Cancel a running task

### From the UI

1. Open the task detail page.
2. Cancel the task. Confirm.

`cancelTask(id)` stops a spawned task through its dispatcher backend
(Kubernetes Job or Docker container) before the row transitions to
`cancelled`, so the same control is the hard stop for a runaway pod. A
task that is already terminal is refused with `PRECONDITION`.

- **Signature:** `PRECONDITION` with `task is already terminal (<status>)`
  for a `completed`, `failed`, `timed_out` or `cancelled` task; `task could
  not be cancelled: <error> (current status: <status>)` when the dispatcher
  backend did not confirm the stop.
- **Fix:** nothing to do for a terminal task; for an unconfirmed stop, check
  the dispatch cluster is reachable and cancel again.
- **Rollback:** no rollback needed; a cancelled task is terminal and is
  re-run by a fresh dispatch.

### From the CLI

```bash
astro agent cancel <task-id> --yes
```

---

## Stuck tasks

A task's `timeout_seconds` (default 300; set per dispatch with
`--timeout` or the **Timeout** field) counts provisioning and execution
time. A typed question or approval pauses the clock (see
[agent task events](agent-task-events.md)). When the budget is exhausted
the reconciler stops the container, keeps retrying until the deletion is
confirmed, then publishes `timed_out`.

- **Signature:** `status: timed_out` with `failureMessage` `agent exceeded
  its <timeout>s timeout` or `agent exceeded its <budget>s input-wait
  budget`; when the dispatch activity itself ran out, `status: failed` with
  `failure.code` `AGENT_DISPATCH_TIMEOUT` and the message `Agent dispatch
  could not finish.`
- **Fix:** re-dispatch with a larger `--timeout`, or fix the blocking
  operation (below).
- **Rollback:** no rollback needed; the stopped container is deleted and the
  row is terminal.

**Check the Temporal worker first.**

```bash
python manage.py temporal_ops list --status running
python manage.py temporal_ops describe <workflow-id>
```

If the dispatch workflow is running and polling, the task may simply be
producing no output. Check the task log for the last emitted line and
compare with the task timeout.

**Stop a stuck task.**

If the worker is healthy and the task is genuinely stuck (no progress
for an extended period):

1. Cancel the task from the UI or CLI (see above).
2. Investigate the agent code for an operation that blocks indefinitely
   (database query without timeout, external API call without deadline,
   blocking on a resource lock).
3. Fix the underlying issue and re-dispatch.

**Worker is dead or unreachable.**

Dispatch is a Temporal workflow: if the control-plane worker dies, the
workflow resumes on the next worker and the Kubernetes Job keeps its own
deadline (`timeout_seconds`, plus one day when a human input wait was
reserved). Nothing in the database needs editing. A task that was never
spawned stays `queued` until a worker picks the workflow up; terminating
the workflow followed by a fresh dispatch is the manual reset:

```bash
python manage.py temporal_ops terminate DispatchAgentTaskWorkflow-<task-id> --reason "<why>"
astro agent dispatch <agent-slug> --input '{"key": "value"}'
```

---

## Scheduled agents

Task-family agents have a run mode: **Once**, **Loop**, **Schedule** or
**Trigger** (Service agents run continuously). Schedule and Loop are
persistent cadences configured on the agent's **Control** tab, not in the
dispatch dialog; the dialog shows the current schedule and points there.

To debug why a scheduled agent did not run:

1. Go to **Tasks** and filter by the agent. Check if a task was
   created at the expected time.
2. If no task was created: `AgentCronTickWorkflow` (every 60 s) matches
   due crons and dispatches tasks; `AgentLoopTickWorkflow` (every 60 s)
   re-dispatches loop agents up to their concurrency cap. Check that the
   Temporal worker was up and the tick ran:
   `python manage.py temporal_ops list --status all --limit 50`.
3. If a task was created but stayed `queued` or `provisioning`: the
   dispatch cluster could not schedule the Job. Check the cluster's
   capacity and the task's `failure` message.

---

## Agent failure modes

A failed container ends the task as `status: failed`; `failureMessage` is
the dispatcher's error message for the container (`container failed` when
the backend reports none) plus a startup diagnostic suffix, and `astro agent
logs <task-id>` holds the container output.

**Task fails immediately with `ImportError`**

- **Signature:** `status: failed`; the task log ends in a Python traceback
  whose last line is `ImportError` or `ModuleNotFoundError`.
- **Fix:** the agent code references a module not installed in the agent
  image. Add the dependency to the environment spec image, rebuild,
  re-dispatch.
- **Rollback:** pin the env spec back to the previous image:

  ```bash
  astro agent env-spec upsert <env-spec-slug> --image-tag <previous-image>
  ```

**Task fails with `Permission denied`**

- **Signature:** `status: failed`; the task log shows the cloud or API
  client's `Permission denied` / 401 / 403 response.
- **Fix:** the agent needs a cloud credential or API token not configured.
  Add the secret to the agent's env spec and re-dispatch:

  ```bash
  astro agent secret set <env-spec-slug> <ENV_VAR>
  astro agent dispatch <agent-slug> --input @payload.json --env-spec <env-spec-slug>
  ```
- **Rollback:** remove the secret again:

  ```bash
  astro agent secret rm <env-spec-slug> <ENV_VAR>
  ```

**Task completes but output is wrong**

- **Signature:** `status: completed`; the structured result on the task
  detail page (or `astro agent inspect <task-id> --json`) is not what the
  agent should have reported.
- **Fix:** logic error in agent code. Review agent logs, fix code, redeploy.
- **Rollback:** redeploy the previous agent image.

**Task fails after N retries**

- **Signature:** repeated `status: failed` tasks for the same agent, each
  with the dependency's error in `failureMessage` and the task log; the
  agent stops retrying after exhausting its workload `max_retries`.
- **Fix:** transient external dependency unreachable. Check external service
  health; dispatch manually once the dependency recovers.
- **Rollback:** no rollback needed.

**Task never starts (`queued` indefinitely)**

- **Signature:** `status: queued` with no log lines; `python manage.py
  temporal_ops list --status running` does not show
  `DispatchAgentTaskWorkflow-<task-id>` being worked (worker down), or the
  dispatch cluster reports `heartbeatStatus: offline`. A Job whose pod
  never starts is failed instead, with `failureMessage` `container never
  started: <reason>` (`ImagePullBackOff`, `InvalidImageName`,
  `CreateContainerConfigError`).
- **Fix:** confirm the control-plane worker is running and the dispatch
  cluster heartbeat is Connected, then re-dispatch; for a `container never
  started` failure fix the env spec image or missing secret first.
- **Rollback:** no rollback needed.

**Dispatch refused: runtime retired**

- **Signature:** `runAstroliftAgent` or the env-spec upsert answers
  `VALIDATION` with `Agent runtime '<name>' is retired because it depends on
  LiteLLM; select an active runtime.`
- **Fix:** the agent's runtime was retired with #2546. Select an active
  runtime on the env spec:

  ```bash
  astro agent env-spec upsert <env-spec-slug> --runtime <active-runtime>
  ```
- **Rollback:** no rollback needed; the retired runtime cannot be selected
  again.

---

## Verify

```bash
astro agent inspect <task-id> --json
astro agent ls --status running --limit 20
```

The dispatched task reaches `status: completed` with its structured result
(`failed`, `timed_out` or `cancelled` carry `failureMessage`), and a cancelled
or stuck task no longer appears in the `running` list.

## Related

- [Deploy runbook](deploy.md)
- [Incident response runbook](incident-response.md)

## Recovering a queued steering message

`sendAgentTaskInput(taskId, message, clientRequestId)` accepts an optional
client-generated UUID. Persist the task, UUID and original message before sending.
A retry with the same task, UUID and edge-trimmed message returns the original
receipt, including after delivery or task completion. Reusing that UUID with a
different message is a `PRECONDITION` failure. Omitted/null UUIDs retain the legacy
behavior: every successful call enqueues a new message.

```bash
astro agent send <task-id> "<message>" --request-id <uuid>
astro agent input-receipt <task-id> --request-id <uuid> --json
```

After a lost response, query
`agentTaskInputMessage(taskId, clientRequestId)` for the exact receipt. This query
requires `agent_task.send_input` in the task's scope and the active organization;
it neither enqueues nor consumes input. A missing receipt is not proof that an
in-flight send failed. If needed, retry the original mutation with the **same**
UUID and message; never generate a replacement UUID merely because the response
was lost. A soft-deleted receipt keeps its UUID reserved and cannot be re-enqueued.

`ok: true` confirms queue admission. `deliveredAt` means the control plane claimed
the message for runner delivery, not that the harness received or executed it.
The existing runner batch callback remains at-most-once; losing that callback
response can still lose the claimed batch.

Rollout: apply migration `astrolift_agents.0029_agent_input_request_id`, deploy the
API and generated schema, then enable keyed sends in clients after checking that
the server supports them. Rolling back application code can leave the additive
nullable column and uniqueness constraint in place. Dropping the column discards
recovery identities and must not occur while clients can retry outstanding sends.
