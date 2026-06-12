# Runbook: Agent dispatch

Procedures for dispatching agent tasks, monitoring execution, and
handling failures or stuck agents.

---

## What agents do

Agents are operator-defined units of autonomous work — database
migrations, canary analysis, smoke tests, infrastructure provisioning,
or any Celery-backed task that runs on a schedule or in response to a
platform event. An agent task is dispatched from a trigger (manual,
schedule, webhook, or deploy lifecycle hook) and runs to completion
in a worker process.

---

## Dispatch an agent task manually

### From the UI

1. Go to **Agents** in the left sidebar.
2. Find the agent in the gallery (or search by name / skill).
3. Click **Dispatch**.
4. Fill in any input parameters the agent requires.
5. Click **Run**. The task appears in the **Tasks** list immediately.

### From the CLI

```bash
astro agent dispatch <agent-slug> --input key=value --input key2=value2
```

Add `--wait` to block until the task completes or fails, and `--tail`
to stream the task log to stdout while waiting.

### Via the API

```graphql
mutation {
  dispatchAgent(agentSlug: "<slug>", input: {key: "value"}) {
    ok
    task {
      id
      status
    }
    errors { field messages }
  }
}
```

---

## Monitor a running task

The task detail page (reachable from **Tasks → [id]**) shows:

- **Status**: Queued → Running → Completed | Failed | Cancelled
- **Log stream**: real-time stdout from the agent worker.
- **Input / output**: the input parameters and any structured output
  the agent emits on success.
- **Duration**: wall-clock time in the current or final status.

From the CLI:

```bash
astro tasks list --agent <agent-slug> --limit 20
astro tasks logs <task-id>
```

---

## Cancel a running task

### From the UI

1. Open the task detail page.
2. Click **Cancel task**. Confirm.

Astrolift sends a soft cancel signal to the worker. The worker has up
to 30 seconds to handle the signal and clean up before a hard
`SIGKILL` is sent.

### From the CLI

```bash
astro tasks cancel <task-id>
```

---

## Stuck tasks

A task is considered stuck if it stays in **Running** status for
longer than its configured timeout (default: 1 hour for most agents).

**Check the worker health first.**

```bash
# From the Flower dashboard at :5555 (self-hosted)
# or from the platform admin: Administration → Workers

astro workers status
```

If all workers show as active, the task may be consuming no output.
Check the task log for the last emitted line and compare with the
task timeout setting.

**Manually expire a stuck task.**

If the worker is healthy and the task is genuinely stuck (no progress
for an extended period):

1. Cancel the task from the UI or CLI (see above).
2. Investigate the agent code for an operation that blocks indefinitely
   (database query without timeout, external API call without deadline,
   blocking on a resource lock).
3. Fix the underlying issue and re-dispatch.

**Worker is dead or unreachable.**

If the Celery worker holding the task lease crashes, the task stays
in Running status until the visibility timeout expires (default: 1h).
After expiry, it re-queues automatically if `max_retries > 0`. To
force immediate re-queue:

1. In the Django admin, navigate to the task record.
2. Set status to `PENDING` and clear the `started_at` field.
3. Save. The task re-queues on the next Celery beat tick.

---

## Scheduled agents

Agents can run on a cron schedule configured in the agent definition.
To view or modify the schedule:

1. Go to **Agents → [agent name] → Settings**.
2. Find **Schedule** — shows the cron expression and next run time.
3. Click **Edit schedule** to change the expression or disable the
   schedule entirely.

To debug why a scheduled agent did not run:

1. Go to **Tasks** and filter by the agent. Check if a task was
   created at the expected time.
2. If no task was created: the scheduler (Celery beat) may have been
   down at the scheduled time. Check the beat worker health in
   **Administration → Workers**.
3. If a task was created but stayed Queued: no workers were available.
   Check worker capacity and queue depth.

---

## Agent failure modes

| Failure | Likely cause | Resolution |
|---------|-------------|------------|
| Task fails immediately with `ImportError` | Agent code references a module not installed in the worker | Add the dependency, rebuild the worker image, redeploy |
| Task fails with `Permission denied` | Agent needs a cloud credential or API token not configured | Add the required secret under **Administration → Secrets** and map it to the agent |
| Task completes but output is wrong | Logic error in agent code | Review agent logs, fix code, redeploy |
| Task fails after N retries | Transient external dependency unreachable | Check external service health; agent will stop retrying after exhausting `max_retries` — dispatch manually once the dependency recovers |
| Task never starts (Queued indefinitely) | No workers registered for the queue | Confirm worker containers are running and subscribed to the correct queue (`default` unless configured otherwise) |

---

## Related

- [Deploy runbook](deploy.md)
- [Incident response runbook](incident-response.md)
