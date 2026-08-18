"""Agent-box reap tick activity (#128).

Thin Temporal activity around the synchronous box reaper so the sweep gets
durable retries and its own run history. The ORM and cluster reads are
blocking, so it runs in a worker thread (``thread_sensitive=False``). Mirrors
``activities/run_status_reconcile.py``.

This is the control-plane half of the idle timeout: the pod's own keep-alive
loop is what frees the node, and this is what notices, settles the row, and
removes the per-box Secret the pod no longer needs.
"""

from __future__ import annotations

import dataclasses

from temporalio import activity


@dataclasses.dataclass
class AgentBoxReapSummary:
    evaluated: int
    running: int
    expired: int
    failed: int
    unchanged: int
    errors: int


@activity.defn(name="astrolift.agents.reap_boxes_tick")
async def reap_agent_boxes_tick() -> AgentBoxReapSummary:
    """One sweep: settle every live AgentBox against its k8s Job."""
    from asgiref.sync import sync_to_async

    return await sync_to_async(_reap_agent_boxes_tick_sync, thread_sensitive=False)()


def _reap_agent_boxes_tick_sync() -> AgentBoxReapSummary:
    from astrolift_agents.services.agent_box import reap_agent_boxes

    summary = reap_agent_boxes()
    return AgentBoxReapSummary(
        evaluated=summary["evaluated"],
        running=summary["running"],
        expired=summary["expired"],
        failed=summary["failed"],
        unchanged=summary["unchanged"],
        errors=summary["errors"],
    )
