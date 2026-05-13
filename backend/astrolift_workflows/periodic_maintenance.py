"""
Periodic maintenance workflow policy (#144, spec 06 §5 + 03 §11).

Pure-Python policy module that the three scheduled Temporal
workflows consult:

  * RehealWebhookSubscriptionsWorkflow — daily re-test of
    disabled webhook subscriptions (spec 06 §5).
  * CapturePlatformCostSnapshotWorkflow — daily cost rollup
    (spec 06 §5; pairs with the cost ledger from #30).
  * PruneAuditLogWorkflow — retention-window enforcement
    with cold-storage archive (spec 03 §11; pairs with
    #161 audit-log streaming export and #160 obs retention).

Each workflow's policy bits live here; the workflow definitions
are thin orchestrators on top.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta
from enum import StrEnum

# ---- webhook reheal ------------------------------------------------


class RehealOutcome(StrEnum):
    """Per-subscription test-delivery outcome."""

    REENABLED = "reenabled"
    """Test 2xx — workflow re-enables the subscription."""

    STILL_FAILING = "still_failing"
    """Test failed — leave disabled; on N-th consecutive failure,
    notify the operator (spec 06 §5)."""

    NOT_DUE = "not_due"
    """Subscription disabled too recently to retest. Reheal
    backs off so we don't hammer dead endpoints right after
    the initial 50-failure auto-disable from #161."""


# Spec 06 §5 — minimum gap between reheal attempts. Without it,
# an endpoint that just hit the auto-disable threshold gets
# tested again immediately, which is pointless.
MIN_REHEAL_INTERVAL_HOURS = 6


def reheal_due(
    *,
    disabled_at: datetime,
    last_reheal_attempt_at: datetime | None,
    now: datetime,
) -> bool:
    """Is this subscription due for a reheal test?

    Rules:
      * Never tested before AND disabled >= MIN_REHEAL_INTERVAL_HOURS
        ago → due.
      * Previously tested AND last attempt >=
        MIN_REHEAL_INTERVAL_HOURS ago → due.
      * Otherwise not due (back off).
    """
    if disabled_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")

    interval = timedelta(hours=MIN_REHEAL_INTERVAL_HOURS)
    if last_reheal_attempt_at is None:
        return now - disabled_at >= interval
    if last_reheal_attempt_at.tzinfo is None:
        raise ValueError("last_reheal_attempt_at must be tz-aware")
    return now - last_reheal_attempt_at >= interval


@dataclasses.dataclass(frozen=True, slots=True)
class RehealResult:
    subscription_id: int
    outcome: RehealOutcome
    consecutive_failed_reheals: int
    """Tracks how many reheals in a row have failed; spec 06 §5
    notifies operator after a configurable threshold."""

    notify_operator: bool


def classify_test_delivery(
    *,
    subscription_id: int,
    test_status_code: int | None,
    prior_consecutive_failed_reheals: int,
    notify_threshold: int = 5,
) -> RehealResult:
    """Decide what to do with a test-delivery outcome.

    2xx → REENABLED, counter reset to 0.
    Anything else → STILL_FAILING, counter incremented.
    Operator notified on the N-th consecutive failure (and
    only then — once notified, don't spam every cycle)."""
    if test_status_code is not None and 200 <= test_status_code < 300:
        return RehealResult(
            subscription_id=subscription_id,
            outcome=RehealOutcome.REENABLED,
            consecutive_failed_reheals=0,
            notify_operator=False,
        )
    new_count = prior_consecutive_failed_reheals + 1
    return RehealResult(
        subscription_id=subscription_id,
        outcome=RehealOutcome.STILL_FAILING,
        consecutive_failed_reheals=new_count,
        # Notify exactly when crossing the threshold; subsequent
        # cycles set this back to False.
        notify_operator=(new_count == notify_threshold),
    )


# ---- platform cost snapshot ----------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class CostSnapshotPlan:
    """Plan emitted by the daily cost-snapshot workflow. The
    workflow then iterates orgs and writes a CostSnapshot row
    per (org, app, binding, category) per #30."""

    snapshot_date: date
    organizations_to_capture: tuple[int, ...]
    """Active org ids — workflow filters out suspended /
    deleted orgs since their cost is zero by definition."""


def plan_cost_snapshot(
    *,
    snapshot_date: date,
    active_org_ids: tuple[int, ...],
) -> CostSnapshotPlan:
    """Build the per-day capture plan. Active orgs only —
    suspended/deleted orgs aren't billed; running their
    aggregations would just emit zero rows."""
    if not active_org_ids:
        return CostSnapshotPlan(
            snapshot_date=snapshot_date,
            organizations_to_capture=(),
        )
    return CostSnapshotPlan(
        snapshot_date=snapshot_date,
        organizations_to_capture=tuple(sorted(set(active_org_ids))),
    )


# ---- audit log prune -----------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class PrunePlan:
    """Plan the prune workflow consumes per org."""

    org_id: int
    cutoff: datetime
    """Audit rows older than this are eligible for archive +
    delete."""

    archive_destination: str
    """Per-org archive sink (S3/GCS/Azure Blob URI). Empty
    string means 'archive disabled' — workflow then skips
    delete to avoid losing data."""


def plan_prune(
    *,
    org_id: int,
    retention_days: int,
    archive_destination: str,
    now: datetime,
) -> PrunePlan:
    """Compute the cutoff for an org's prune run.

    Pairs with #161 (hash-chained audit JSONL exports — the
    archive contains those, not raw rows) and #160
    (obs_retention's per-org override).
    """
    if retention_days <= 0:
        raise ValueError("retention_days must be positive")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return PrunePlan(
        org_id=org_id,
        cutoff=now - timedelta(days=retention_days),
        archive_destination=archive_destination,
    )


def safe_to_delete(plan: PrunePlan) -> bool:
    """The workflow checks this before issuing the DELETE.

    Without an archive destination configured, deletion would
    silently lose audit data — so we require archive_destination
    to be non-empty before allowing delete. Operators get a
    'configure archive first' surface.
    """
    return bool(plan.archive_destination.strip())
