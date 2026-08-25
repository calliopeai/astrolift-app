"""The caller `observability_retention.py` never had (#1602 step 5).

This is the step the issue is actually about. The policy module has been
written, tested and green since it landed, and nothing called it: five
public functions with no production call site, and four `Organization`
columns an operator could set while the data lived forever.

The chain, mirroring `cert_expiry` -> `cert_expiry_monitor`:

    four Organization columns
      -> effective_for()      per (org, stream)
      -> cutoff_at()          the eviction boundary
      -> active_holds_for()   the rows, projected onto policy inputs
      -> is_held()            validates each window edge
      -> resolve_eviction_driver() per cluster
      -> evict_before()

**`dry_run` defaults True.** A sweep that deletes tenant data by default is
one accidental call away from an unrecoverable mistake, and the operator
path in the issue is explicitly "run in dry run, read the window counts,
then activate".

The hold arithmetic is here, but the *match rules* stay in the policy
module: `_delete_windows` subtracts hold intervals and then validates both
edges of every surviving window through `is_held`. Re-implementing the
match rules to do the subtraction would give two answers to "is this
held?", and the one that deletes data would be the new one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from astrolift_operations.observability_retention import (
    ALL_STREAMS,
    RetentionHold,
    cutoff_at,
    effective_for,
    is_held,
)
from astrolift_operations.retention_holds import active_holds_for

log = logging.getLogger(__name__)

# Which Organization column overrides each stream. The join that did not
# exist anywhere in the repo before this module: the columns were writable
# and the policy took an `org_override_days`, and nothing connected them.
STREAM_COLUMNS: dict[str, str] = {
    "log": "log_retention_days_default",
    "metric_raw": "metrics_retention_days_default",
    "metric_rollup": "metrics_rollup_retention_days_default",
    "trace": "trace_retention_days_default",
}

# How far back a sweep will look for data to evict. Bounded because the
# eviction window needs a lower edge and "the beginning of time" would ask
# a backend to scan its whole history every night. Ten years is past any
# platform retention ceiling, so nothing evictable falls outside it.
OLDEST_HORIZON = timedelta(days=3650)


def _delete_windows(
    *,
    stream: str,
    oldest: datetime,
    cutoff: datetime,
    holds: list[RetentionHold],
) -> list[tuple[datetime, datetime]]:
    """`(oldest, cutoff)` minus every hold interval that overlaps it.

    Both edges of every surviving window are then checked through
    `is_held`, which is the authority on the match rules. Belt and braces
    on purpose: the subtraction here only looks at time, while `is_held`
    also applies the stream and resource scoping, so a window that survives
    the arithmetic can still be held.
    """
    if cutoff <= oldest:
        return []

    spans = [(oldest, cutoff)]
    for hold in holds:
        if hold.stream not in (stream, "*"):
            continue
        # A hold scoped to a resource cannot be subtracted from a
        # whole-stream window: it holds some rows in that window and not
        # others, and time arithmetic cannot express that. `is_held` still
        # sees it below, so the window is refused rather than narrowed.
        if hold.resource_kind or hold.resource_id:
            continue
        remaining: list[tuple[datetime, datetime]] = []
        for start, end in spans:
            if hold.ends_at <= start or hold.starts_at >= end:
                remaining.append((start, end))
                continue
            if start < hold.starts_at:
                remaining.append((start, hold.starts_at))
            if hold.ends_at < end:
                remaining.append((hold.ends_at, end))
        spans = remaining

    kept: list[tuple[datetime, datetime]] = []
    for start, end in spans:
        if end <= start:
            continue
        # Probe the **interior**, not the edges.
        #
        # Probing the edges is the obvious thing and it is wrong: the
        # subtraction above produces boundaries that sit exactly on a hold's
        # `starts_at` / `ends_at`, and `is_held` treats its window as
        # inclusive on both ends -- so the edge check re-detects the very
        # hold it just subtracted and discards both surviving spans. A hold
        # over any part of the range then suspended retention for the whole
        # range, which is the opposite of carving.
        #
        # The midpoint is inside a span that survived subtraction, so it
        # cannot fall inside a time-subtracted hold. What it still catches
        # is the holds the subtraction deliberately skipped -- a different
        # stream's, or one whose scoping the arithmetic could not express --
        # which is the reason for the second check at all.
        midpoint = start + (end - start) / 2
        if is_held(
            stream=stream,
            timestamp=midpoint,
            resource_kind="",
            resource_id="",
            holds=holds,
        ):
            continue
        kept.append((start, end))
    return kept


def _clusters_for(organization: Any) -> list[Any]:
    from astrolift_clusters.models import TenantCluster

    return list(
        TenantCluster.objects.filter(deleted_at__isnull=True).filter(organization=organization).order_by("pk")
    )


def run_observability_retention_sweep(*, dry_run: bool = True) -> dict[str, int]:
    """One pass over every org and stream. Returns the counts.

    Per-org and per-stream `try/except`: one misconfigured org must not
    abort a sweep that is the only thing enforcing retention for every
    other org. The same reason `run_cert_expiry_sweep` guards per domain.

    `skipped_unsupported` is counted separately from `evicted` and from
    `errors` because it is none of them. A Tempo backend cannot delete;
    that is a fact about the backend, not a failure of the sweep, and
    folding it into either of the other two would tell the operator
    something false.
    """
    from django.utils import timezone

    from astrolift_identity.models import Organization
    from core.cluster_observability_eviction import resolve_eviction_driver

    now = timezone.now()
    counts = {
        "orgs": 0,
        "windows": 0,
        "evicted": 0,
        "held": 0,
        "skipped_unsupported": 0,
        "errors": 0,
    }

    for org in Organization.objects.filter(deleted_at__isnull=True).order_by("pk"):
        try:
            holds = active_holds_for(org)
            clusters = _clusters_for(org)
        except Exception:
            log.exception("retention sweep: could not load org %s", getattr(org, "slug", org.pk))
            counts["errors"] += 1
            continue

        counts["orgs"] += 1
        if not clusters:
            continue

        for stream in ALL_STREAMS:
            try:
                override = getattr(org, STREAM_COLUMNS[stream], None)
                retention = effective_for(stream=stream, org_override_days=override)
                cutoff = cutoff_at(retention, now=now)
                windows = _delete_windows(
                    stream=stream,
                    oldest=now - OLDEST_HORIZON,
                    cutoff=cutoff,
                    holds=holds,
                )
                if not windows:
                    # No window means everything older than the cutoff is
                    # held. Counted as held rather than silently skipped:
                    # "nothing to do" and "an operator is protecting this"
                    # are different states and the operator needs the second.
                    counts["held"] += 1
                    continue

                for cluster in clusters:
                    driver = resolve_eviction_driver(cluster, stream=stream)
                    if driver is None:
                        continue
                    for start, end in windows:
                        counts["windows"] += 1
                        outcome = _evict(
                            driver=driver,
                            stream=stream,
                            start=start,
                            end=end,
                            dry_run=dry_run,
                        )
                        if not outcome.supported:
                            counts["skipped_unsupported"] += 1
                            log.info(
                                "retention sweep: %s/%s not evictable: %s",
                                getattr(cluster, "slug", cluster.pk),
                                stream,
                                outcome.detail,
                            )
                            continue
                        counts["evicted"] += outcome.requested
                        if outcome.requested and not dry_run:
                            _emit_evicted(org, stream, start, end)
            except Exception:
                log.exception(
                    "retention sweep: stream %s failed for org %s",
                    stream,
                    getattr(org, "slug", org.pk),
                )
                counts["errors"] += 1
                continue

    return counts


def _evict(*, driver: Any, stream: str, start: datetime, end: datetime, dry_run: bool):
    from _sdk.observability.eviction import EvictionRequest

    return driver.evict_before(EvictionRequest(stream=stream, start=start, end=end, dry_run=dry_run))


def _emit_evicted(org: Any, stream: str, start: datetime, end: datetime) -> None:
    """Fan out one event per (org, stream) window actually evicted.

    Only on a real eviction: emitting for a dry run would put "we deleted
    your data" in an org's event feed on a run that deleted nothing.
    """
    from core.events import Event

    try:
        Event.emit(
            "observability.retention_evicted",
            organization_id=org.pk,
            payload={
                "stream": stream,
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
            },
        )
    except Exception:
        # An event that fails to emit must not turn a completed eviction
        # into a counted error; the data is already gone either way.
        log.exception("retention sweep: could not emit evicted event for org %s", org.pk)
