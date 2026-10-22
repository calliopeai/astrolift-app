# Retiring Astrolift monetary surfaces (#2464)

This release removes application/task/model money transport, budgets, forecasts,
price estimates and product cost queries. Zentinelle remains the canonical home
for per-model token tallies. Resource quotas, GPU admission, token/time limits,
callback status/duration/tokens and lifecycle evidence remain.

No migration drops or rewrites historical budgets, snapshots, task usage,
events, encrypted callback bodies or Temporal histories. Historical monetary
models remain installed; their Django admin is read-only. Private native cloud
actual adapters remain unused. Project attribution is unproven, so this release
provides no project infrastructure-cost API or replacement ledger.

## Coordinate clients and backend

Publish the backend and clients as one reviewed release. The removed GraphQL
queries are `astroliftBudgets`, `astroliftCostSnapshots`, `astroliftCostTrend`,
`astroliftCostForecast`, `astroliftCostByBinding` and
`astroliftManagedServiceCostPreview`. Zentinelle usage no longer exposes
`costUsd`. Health no longer exposes `budgets` or the `AstroliftZentinelleBudget`
type; availability, block/approval counts and gateway/server observations remain.
Observability retention also drops the duplicate `billableWindowDays` field;
effective `days`, policy `source` and `warnThresholdDays` remain available.
Only the typed operational health projection is cached under a new cache key;
earlier raw-response entries are ignored and expire normally. Preview detail no
longer exposes monetary estimate fields.
`includeRuntimeResources` replaces `includeRuntimeCost` on the existing exact
preview read, with capability `previews.explicit_runtime_resources`. Its
CPU/memory/pod observations and explicit runtime status remain; list reads do
not request runtime enrichment. MCP no longer advertises or accepts
`astrolift_preview_project_resource_cost`.

Completion callbacks omit envelope `usage.total_cost_usd`, including retries
and manual redelivery of historical events. Historical ciphertext and frozen
metadata are not rewritten: the worker projects this one known field in memory
before signing the exact outgoing bytes. Arbitrary user result JSON is retained.
Bodies above 8 MiB or malformed/invalid envelopes fail closed with a fixed
metadata-only delivery error. Consumers must accept the missing optional field;
event identity, current signing secret, retry window and retention checks remain.
Previously delivered bodies cannot be recalled.

## Drain old workers and retire operator schedules

These are operator runtime steps requiring the normal reviewed release process;
the source change does not perform them or prove a successful rollout.

1. Inventory running worker revisions, existing Temporal schedules and in-flight
   delivery/cost histories. Prepare a compatible worker image and paired client/API
   images. Do not reset histories or alter saved workflow inputs.
2. Pause the exact existing `astro-capture_platform_cost_snapshot` schedule if it
   exists, and remove `capture_platform_cost_snapshot` from the operator's
   `ASTROLIFT_ACTIVE_SCHEDULES` configuration. Retain its definition/history for
   audit; do not delete tenant schedules or database rows.
3. Drain and stop every old worker capable of monetary collection/delivery, then
   start the compatible workers before exposing the new API/clients. Old worker
   processes may continue sending money or collecting costs until stopped; a
   mixed worker fleet does not meet retirement acceptance.
4. Keep `CapturePlatformCostSnapshotWorkflow` and activity
   `astrolift.scheduled.capture_platform_cost_snapshot` registered in the new
   worker. Existing histories can complete through the now-side-effect-free
   activity. The registrar never creates or updates the retired cost schedule,
   even if an old allowlist still names it. An already-existing schedule is only
   reported as an orphan; the registrar does not pause/delete it automatically.
5. Historical `AUDIT.agent.model_spend` event vocabulary remains readable, but
   new emission/default subscriptions are retired. Its exact trusted webhook
   routing type returns terminal `dropped` / `event retired` before subscription,
   secret, HTTP or attempt-history writes. Old queued histories finish without
   modifying their saved body or subscription counters. Other event types and
   arbitrary nested user payloads retain their existing delivery behavior.
6. Verify the exact running revisions, reduced contracts, callback signature and
   token/resource paths using nonmonetary canaries. No provider financial query
   is needed to establish retirement. Existing read-only deployment observations
   and synthetic source tests do not establish a working live token pipeline.

The quota snapshot schedule is separate and remains available. Legacy internal
`billing.read` permission identifiers still protect quota APIs; renaming them
would change existing grants. Do not disable the Django billing app or revoke
those grants as part of monetary retirement.

## Rollback

Historical models and migrations remain present. Rolling back to an old worker
or API can re-enable money collection, transport and old schedule creation, so
keep the retired schedule paused/configuration removed and require a separate
review before any rollback that restores old behavior. Preserve old histories
and ciphertext; do not backfill, scrub or reconstruct monetary records.
