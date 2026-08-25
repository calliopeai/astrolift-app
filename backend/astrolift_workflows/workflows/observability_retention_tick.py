"""ObservabilityRetentionTickWorkflow — one eviction pass (#1602 step 6).

Thin Temporal entry point around
:func:`astrolift_workflows.activities.observability_retention.
prune_observability_data`, so the sweep has durable retries and each pass
is its own run in the Temporal UI.

Coalescing tick model, as the uptime / cert / alert ticks: the activity
timeout is bounded well under the 24h interval so a stuck sweep cannot
overlap the next. Generous rather than tight because the sweep makes one
HTTP call per (cluster, stream, window) and a large install has many.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.observability_retention import (
        ObservabilityRetentionSummary,
        prune_observability_data,
    )


_TICK_TIMEOUT = timedelta(minutes=30)
"""Well under the 24h interval. A sweep that overran this would be retried
rather than run twice concurrently, which matters more here than for the
read-only ticks: two concurrent passes would issue the same deletes twice."""


@workflow.defn(name="ObservabilityRetentionTickWorkflow")
class ObservabilityRetentionTickWorkflow:
    @workflow.run
    async def run(self) -> ObservabilityRetentionSummary:
        return await workflow.execute_activity(
            prune_observability_data,
            start_to_close_timeout=_TICK_TIMEOUT,
        )
