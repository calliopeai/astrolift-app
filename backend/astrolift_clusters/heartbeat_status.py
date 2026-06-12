"""
Cluster heartbeat liveness policy (#808).

Pure-Python policy. Translates ``(last_heartbeat_at, interval, now)``
into a closed live-status vocabulary so the GraphQL surface and the
ops dashboard can reliably switch on it. Pairs with the in-cluster
keep-alive agent that POSTs ``/api/clusters/v1/<guid>/heartbeat/`` on
a fixed interval (see ``views_heartbeat.py``).

Why a separate module: the existing Status-tab cards all do a live
driver / Prometheus / Temporal call, which is exactly why the tabs
hang when the apiserver is unreachable. The heartbeat is the *cheap*
signal — derived entirely from one timestamp + the configured cadence
— that the UI uses to short-circuit the expensive cards into a
targeted "cluster offline" empty-state instead of spinning forever.

The grace bands deliberately mirror what an operator expects from a
fixed-cadence agent:

* **NEVER_SEEN** — no heartbeat has ever landed. The cluster may be
  registered with no agent installed, or the agent has never reached
  the control plane.
* **CONNECTED** — last pulse is within one interval (plus a small
  jitter cushion). Green.
* **DEGRADED** — the agent missed at least one interval but fewer than
  the offline threshold. Amber — a transient network blip or a
  briefly-restarting agent looks like this; it usually self-heals on
  the next pulse.
* **OFFLINE** — the agent has missed ``OFFLINE_MISS_THRESHOLD``
  intervals. Red — the cluster is treated as disconnected and the
  dependent tabs render the offline empty-state.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

# Minimum cadence the policy will reason about. Mirrors the model
# default; a misconfigured zero/negative interval would make every
# elapsed band collapse to OFFLINE, so we floor it.
MIN_INTERVAL_SECONDS = 5

# Multiplicative jitter cushion on the CONNECTED band. A pulse that
# lands a hair after the nominal interval (scheduler jitter, request
# latency) shouldn't immediately flip the badge to amber.
CONNECTED_GRACE_FACTOR = 1.5

# Missed-interval count at which a cluster is considered OFFLINE. Three
# misses is the conventional "dead, not just slow" threshold (the same
# shape Prometheus alerting uses for `up == 0 for 3x scrape_interval`).
OFFLINE_MISS_THRESHOLD = 3


class HeartbeatStatus(StrEnum):
    """Closed live-status vocabulary driven by the last heartbeat."""

    NEVER_SEEN = "never_seen"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    OFFLINE = "offline"


def heartbeat_age_seconds(
    *,
    last_heartbeat_at: dt.datetime | None,
    now: dt.datetime,
) -> float | None:
    """Seconds since the last heartbeat, or ``None`` when never seen.

    Clamped at zero so a slightly-skewed agent clock (heartbeat stamped
    a moment in the future) reads as "just now" rather than a negative
    age.
    """
    if last_heartbeat_at is None:
        return None
    return max(0.0, (now - last_heartbeat_at).total_seconds())


def resolve(
    *,
    last_heartbeat_at: dt.datetime | None,
    interval_seconds: int,
    now: dt.datetime,
) -> HeartbeatStatus:
    """Derive the live status from the last heartbeat and the cadence.

    Bands (with ``i`` = effective interval after flooring):
      * no heartbeat ever            -> NEVER_SEEN
      * age <= i * CONNECTED_GRACE   -> CONNECTED
      * age <  i * OFFLINE_THRESHOLD -> DEGRADED
      * age >= i * OFFLINE_THRESHOLD -> OFFLINE
    """
    if last_heartbeat_at is None:
        return HeartbeatStatus.NEVER_SEEN

    interval = max(MIN_INTERVAL_SECONDS, int(interval_seconds or 0))
    age = heartbeat_age_seconds(last_heartbeat_at=last_heartbeat_at, now=now)
    # age is non-None here because last_heartbeat_at is non-None.
    assert age is not None

    if age <= interval * CONNECTED_GRACE_FACTOR:
        return HeartbeatStatus.CONNECTED
    if age < interval * OFFLINE_MISS_THRESHOLD:
        return HeartbeatStatus.DEGRADED
    return HeartbeatStatus.OFFLINE


def is_live(status: HeartbeatStatus) -> bool:
    """Whether the cluster is reachable enough to attempt a live pull.

    CONNECTED and DEGRADED both mean "the agent is (mostly) talking to
    us" — the dependent tabs should still try their driver/Prometheus
    calls. NEVER_SEEN and OFFLINE are the empty-state cases.
    """
    return status in (HeartbeatStatus.CONNECTED, HeartbeatStatus.DEGRADED)
