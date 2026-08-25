"""Observability eviction tick activity (#1602 step 6).

Thin Temporal wrapper around the synchronous sweep so it gets durable
retries and each pass is its own run history. Mirrors
``activities/cert_expiry.py`` -- the ORM work and the backend HTTP calls
are blocking, so it runs in a worker thread
(``thread_sensitive=False``).

Distinct from ``scheduled.apply_observability_retention``, which is easy to
confuse it with: that one **sets** a log group's retention window on the
cloud provider, this one **evicts** data past the window the org
configured. Two halves of the same policy, and only one of them issues
deletes.

`dry_run=False` here, and that is the whole reason this activity ships
behind a HELD schedule rather than an active one: a real tick deletes
tenant data. The sweep's own default is dry-run so nothing else that
happens to call it deletes by accident; the deliberate call site is the one
that opts in.
"""

from __future__ import annotations

import dataclasses

from temporalio import activity


@dataclasses.dataclass
class ObservabilityRetentionSummary:
    orgs: int
    windows: int
    evicted: int
    held: int
    skipped_unsupported: int
    errors: int


@activity.defn(name="astrolift.observability.retention_tick")
async def prune_observability_data() -> ObservabilityRetentionSummary:
    """One eviction pass over every org and observability stream.

    Reads the four `Organization` retention columns, resolves the effective
    window per stream, subtracts any operator-placed holds, and asks each
    cluster's backend to delete what is left.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_prune_observability_data_sync, thread_sensitive=False)()


def _prune_observability_data_sync() -> ObservabilityRetentionSummary:
    from astrolift_operations.observability_retention_sweep import (
        run_observability_retention_sweep,
    )

    counts = run_observability_retention_sweep(dry_run=False)
    return ObservabilityRetentionSummary(
        orgs=counts["orgs"],
        windows=counts["windows"],
        evicted=counts["evicted"],
        held=counts["held"],
        skipped_unsupported=counts["skipped_unsupported"],
        errors=counts["errors"],
    )
