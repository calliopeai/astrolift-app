# Task usage and cost provenance

Probe the public `astroliftServerInfo` handshake for `agents.task_usage_snapshot` before querying an older server. The authenticated read is `agentTaskUsageSnapshot(id: ID!)`. It requires the existing task read (`app.read`) and `agent.read` at the task's recorded project, team, agent app, or explicit organization scope. Current tenant, operation policy, bearer organization/team/scope ceilings, live organization membership and role grants, token revocation, expiry and deletion apply at every read. A prior response does not retain permission to read usage. This exact-task root field is intentionally absent from task-list items, so listing tasks cannot fan out into per-task usage reads.

```graphql
query TaskUsage($id: ID!) {
  agentTaskUsageSnapshot(id: $id) {
    taskId taskVersion taskStatus asOf aggregationScope
    linkedRunId linkedRunRetryCount
    inputTokens { value unit source sourceRecordedAt completeness reason }
    outputTokens { value unit source sourceRecordedAt completeness reason }
    duration { value unit source sourceRecordedAt completeness reason }
    reportedCost { amount currency source sourceRecordedAt completeness reconciliation reason }
    actualCost { amount currency source sourceRecordedAt completeness reconciliation reason }
    model modelSource modelSourceRecordedAt
  }
}
```

The CLI can submit this operation through its existing `astro api graphql --file task-usage.graphql --var id=<task-guid> --json` command using the person's configured API credential. No elevated credential or second billing lookup is needed. The same operation is generated as `AgentTaskUsage` for Apollo consumers.

`asOf` is the server read time. `taskVersion` and `taskStatus` describe the same task row used for the snapshot; clients should use these rather than a previously cached task status. `sourceRecordedAt` is when the platform stored a source record, when known; it is not provider billing time. Completion reports have no independently recorded report timestamp, so their timestamp stays null. An unrelated task `updatedAt` is never substituted. Platform duration uses the terminal `ended_at` as its observation time. A meter's timestamp is its ingestion time.

`Decimal` values are decimal **strings**, including integer token quantities. Keep them as strings or use a decimal/big-integer library: JavaScript `Number` cannot represent every supported token count. This transport preserves the stored value; it cannot recover precision already lost when an incoming JSON cost was normalized to a float. Units are `TOKENS` or `MILLISECONDS`. A duration is only available for coherent recorded start/end timestamps on a terminal task; it is not a live clock or billed runtime.

Each value carries its source, completeness and a nullable reason. `REPORTED` means an authoritative source reported the value, not independently verified billing or guaranteed provider completeness. `OBSERVED` identifies platform timestamp duration. `PARTIAL` marks a linked-run meter that cannot be established as a post-terminal observation with coherent task and run periods (including a historical, future or reversed run period); terminal status alone does not make it final. `UNKNOWN` carries no value. Missing, malformed, negative, overflowed or inconsistent observations remain null; an explicit reported zero remains zero. Nonterminal completion JSON is ignored. The model comes only from a valid terminal completion report, not event JSON or a later incidental task update.

Aggregation is `EXACT_TASK_PREFER_COMPLETION_THEN_LINKED_RUN_NO_SUM`: prefer each valid normalized `completion_usage` token field, then fall back to that task's exact `agent_run` foreign key and its one metering record. Completion and meter counts are never added together. A run must still belong to the exact live agent definition and organization; an invalid binding exposes neither its GUID, retry count nor meter. A linked run's retry count is metadata, not a count of independently measured attempts. Retrying a task creates a new task GUID with no recorded lineage; this snapshot does not find or sum those other tasks. Meter readings cover the linked run, with no inferred per-attempt allocation.

`reportedCost` is the runner's normalized `total_cost_usd`, explicitly `USD`, with `NOT_RECONCILED`. Token meter records contain no monetary amount. `actualCost` currently has null amount, currency, source and timestamp, `UNKNOWN` completeness, `NO_SOURCE` reconciliation and `NO_TASK_BILLING_SOURCE`. Organization/project/app cost rollups are estimates and cannot supply an exact task invoice. No token rate, currency conversion, projection, event payload, or zero amount fills that gap. This contract does not complete the actual-billing acceptance in #2458 or the Studio cost requirement in #907.
