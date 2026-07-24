"""Alert-evaluation tick activity.

Thin Temporal activity wrapping the synchronous alert sweep so the
evaluation loop gets durable retries + shows up as its own run history.
The ORM work is blocking, so it runs in a worker thread
(``thread_sensitive=False``). Mirrors ``activities/uptime.py``.
"""

from __future__ import annotations

import dataclasses

from temporalio import activity


@dataclasses.dataclass
class AlertEvalSummary:
    evaluated: int
    fired: int
    resolved: int


@activity.defn(name="astrolift.alerts.evaluate_tick")
async def evaluate_alerts_tick() -> AlertEvalSummary:
    """One sweep: evaluate every active AlertRule, create AlertEvent rows on
    clear->firing transitions and resolve on firing->clear, emitting
    alert.fired / alert.resolved through the notification dispatch path."""
    from asgiref.sync import sync_to_async

    return await sync_to_async(_evaluate_alerts_tick_sync, thread_sensitive=False)()


def _evaluate_alerts_tick_sync() -> AlertEvalSummary:
    from astrolift_operations.alert_engine import run_alert_sweep

    summary = run_alert_sweep()
    return AlertEvalSummary(
        evaluated=summary["evaluated"],
        fired=summary["fired"],
        resolved=summary["resolved"],
    )
