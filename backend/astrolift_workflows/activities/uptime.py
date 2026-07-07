"""Uptime-probe tick activity.

Thin Temporal activity wrapping the synchronous prober so the sweep gets
durable retries + shows up as its own run history. The HTTP probing is
blocking, so it runs in a worker thread (``thread_sensitive=False``).
"""

from __future__ import annotations

import dataclasses

from temporalio import activity


@dataclasses.dataclass
class UptimeProbeSummary:
    checked: int
    down: int


@activity.defn(name="astrolift.uptime.probe_tick")
async def probe_uptime_tick() -> UptimeProbeSummary:
    """One sweep: probe every READY app's health URL, record results, emit
    app.down / app.recovered on transitions."""
    from asgiref.sync import sync_to_async

    return await sync_to_async(_probe_uptime_tick_sync, thread_sensitive=False)()


def _probe_uptime_tick_sync() -> UptimeProbeSummary:
    from astrolift_operations.uptime_probe import probe_all_deployed_apps

    summary = probe_all_deployed_apps()
    return UptimeProbeSummary(checked=summary["checked"], down=summary["down"])
