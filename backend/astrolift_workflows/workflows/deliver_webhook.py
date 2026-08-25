"""DeliverWebhookWorkflow — retries one webhook until it lands or gives up.

Named in four docstrings across `utility_workflows`, `webhook_delivery`,
`webhooks` and `schema.mutations.helpers`, and defined nowhere, since before
outbound webhooks shipped (#1598). This is it.

The retry loop is here rather than in Temporal's ``RetryPolicy`` because the
schedule is a product decision that was already written down:
``webhook_delivery.RETRY_SCHEDULE_SECONDS`` with jitter, and ``classify``
deciding which outcomes are worth retrying. A 410 Gone means the endpoint is
deliberately unsubscribing; a 503 means try again in five seconds. Temporal's
policy cannot tell those apart, and treating them the same is either
hammering an endpoint that asked us to stop or dropping one that was briefly
down.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from astrolift_workflows.activities.webhook_deliver import deliver_webhook

_ATTEMPT_TIMEOUT = timedelta(seconds=30)


@workflow.defn(name="DeliverWebhookWorkflow")
class DeliverWebhookWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        from astrolift_operations.webhook_delivery import next_retry_delay_seconds

        attempt = 1
        last: dict[str, Any] = {}

        while True:
            last = await workflow.execute_activity(
                deliver_webhook,
                {**payload, "attempt": attempt},
                start_to_close_timeout=_ATTEMPT_TIMEOUT,
            )
            classification = str(last.get("classification", ""))

            if classification != "retry":
                # success, permanent_failure, immediate_disable and dropped
                # are all terminal. Only "retry" means try again -- an
                # `if classification == "success": break` would loop forever
                # on a permanent failure.
                return {**last, "attempts": attempt}

            delay = next_retry_delay_seconds(attempt=attempt)
            if delay is None:
                return {**last, "attempts": attempt, "exhausted": True}

            # workflow.sleep, not asyncio.sleep: a webhook retry can be
            # minutes out, and this has to survive a worker restart.
            await workflow.sleep(timedelta(seconds=delay))
            attempt += 1
