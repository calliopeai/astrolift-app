"""
Scheduled workflow registration policy (#129, spec 06 §5).

Pure-Python policy. The control-plane boot routine consults this
module to register the platform's scheduled workflows in Temporal.

* **Schedule catalog** — locked-down list of (workflow_name,
  interval) tuples. Adding a schedule requires a deliberate code
  review (boot-time fan-out has cost implications).
* **Schedule ID conventions** — deterministic so re-registration
  at boot is idempotent (Temporal de-dupes by ID).
* **Interval validation** — refuses < 1 minute (would be a hot
  loop) and > 24 hours (probably operator typo).

This module owns the boot-registration *contract*; the actual
Temporal client calls live in ``workers/schedules.py``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from enum import StrEnum


class ScheduleRegistryError(ValueError):
    pass


# Per spec 06 §5: minimum 60s, maximum 24h. Lower would be a hot
# loop; higher is almost always operator typo (use cron expr if
# you really mean 'once a week').
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 24 * 60 * 60


# ---- schedule definition -------------------------------------------


class ScheduleKind(StrEnum):
    """Spec 06 §5: locked vocabulary of recurring workflows."""

    PREVIEW_GC = "preview_gc"
    """Every 15 min — preview environment teardown by TTL +
    max-active eviction (#88)."""

    POLL_SCHEDULED_JOB_RUNS = "poll_scheduled_job_runs"
    """Every 1 min — discover k8s Jobs created by tenant
    CronJobs (spec §4.16)."""

    RECONCILE_CLUSTER_CAPABILITIES = "reconcile_cluster_capabilities"
    """Every 10 min — refresh TenantCluster.capabilities snapshot;
    emit CLUSTER_CAPABILITY_CHANGED on drift (spec §4.18)."""

    DRIFT_DETECTION = "drift_detection"
    """Every 1 hr — compare platform DB to live cluster state for
    each active deployment; emit DRIFT_DETECTED on material diff
    (spec §4.19)."""

    REHEAL_WEBHOOK_SUBSCRIPTIONS = "reheal_webhook_subscriptions"
    """Every 5 min — re-enable backed-off webhook subs."""

    PRUNE_AUDIT_LOG = "prune_audit_log"
    """Daily 02:00 UTC — delete audit log past retention
    (per ObservabilityProfile retention bounds, #9)."""

    CAPTURE_PLATFORM_COST_SNAPSHOT = "capture_platform_cost_snapshot"
    """Daily — platform-wide cost snapshot for billing reports."""

    CRON_DEPLOY_TICK = "cron_deploy_tick"
    """Every 1 min — re-reads RegisteredApp rows with
    trigger_mode='cron' and fires a deploy for each whose
    cron_expression matches the current minute (#296)."""


@dataclasses.dataclass(frozen=True, slots=True)
class ScheduleDefinition:
    """One row in the schedule catalog. Boot routine walks
    DEFAULT_SCHEDULES and calls Temporal to register each."""

    kind: ScheduleKind
    workflow_name: str
    """Temporal workflow name. Boot uses this to resolve the
    registered workflow class."""

    interval_seconds: int
    """How often to fire. Subject to MIN/MAX bounds."""

    schedule_id: str
    """Deterministic Temporal schedule ID. Re-registration with
    same ID is idempotent — Temporal updates rather than creates
    a duplicate."""

    description: str

    def __post_init__(self) -> None:
        if self.interval_seconds < MIN_INTERVAL_SECONDS:
            raise ScheduleRegistryError(
                f"schedule {self.kind.value} interval "
                f"{self.interval_seconds}s below minimum "
                f"{MIN_INTERVAL_SECONDS}s"
            )
        if self.interval_seconds > MAX_INTERVAL_SECONDS:
            raise ScheduleRegistryError(
                f"schedule {self.kind.value} interval "
                f"{self.interval_seconds}s exceeds maximum "
                f"{MAX_INTERVAL_SECONDS}s"
            )
        if not self.workflow_name:
            raise ScheduleRegistryError(f"schedule {self.kind.value} requires workflow_name")
        if not self.schedule_id:
            raise ScheduleRegistryError(f"schedule {self.kind.value} requires schedule_id")


def schedule_id_for(*, kind: ScheduleKind) -> str:
    """Deterministic schedule ID. ``astro-{kind}`` namespace
    so platform-owned schedules are easy to spot in the
    Temporal UI alongside any tenant-created ones."""
    return f"astro-{kind.value}"


# Spec 06 §5: the canonical schedule list. Locked.
DEFAULT_SCHEDULES: tuple[ScheduleDefinition, ...] = (
    ScheduleDefinition(
        kind=ScheduleKind.PREVIEW_GC,
        workflow_name="PreviewGarbageCollectWorkflow",
        interval_seconds=15 * 60,
        schedule_id=schedule_id_for(kind=ScheduleKind.PREVIEW_GC),
        description="Preview env GC: TTL eviction + max-active",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.POLL_SCHEDULED_JOB_RUNS,
        workflow_name="PollScheduledJobRunsWorkflow",
        interval_seconds=60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.POLL_SCHEDULED_JOB_RUNS,
        ),
        description="Discover k8s Jobs from tenant CronJobs",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.RECONCILE_CLUSTER_CAPABILITIES,
        workflow_name="ReconcileClusterCapabilitiesWorkflow",
        interval_seconds=10 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.RECONCILE_CLUSTER_CAPABILITIES,
        ),
        description="Refresh cluster capability snapshot",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.DRIFT_DETECTION,
        workflow_name="DriftDetectionWorkflow",
        interval_seconds=60 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.DRIFT_DETECTION,
        ),
        description="DB vs cluster state diff per deployment",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.REHEAL_WEBHOOK_SUBSCRIPTIONS,
        workflow_name="RehealWebhookSubscriptionsWorkflow",
        interval_seconds=5 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.REHEAL_WEBHOOK_SUBSCRIPTIONS,
        ),
        description="Re-enable backed-off webhook subs",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.PRUNE_AUDIT_LOG,
        workflow_name="PruneAuditLogWorkflow",
        interval_seconds=24 * 60 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.PRUNE_AUDIT_LOG,
        ),
        description="Delete audit log past retention",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.CAPTURE_PLATFORM_COST_SNAPSHOT,
        workflow_name="CapturePlatformCostSnapshotWorkflow",
        interval_seconds=24 * 60 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.CAPTURE_PLATFORM_COST_SNAPSHOT,
        ),
        description="Daily platform cost snapshot for billing",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.CRON_DEPLOY_TICK,
        workflow_name="CronDeployTickWorkflow",
        interval_seconds=60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.CRON_DEPLOY_TICK,
        ),
        description="Cron-triggered deploy dispatcher tick (#296)",
    ),
)


def get_schedule(*, kind: ScheduleKind) -> ScheduleDefinition:
    """Look up a single schedule by kind."""
    for s in DEFAULT_SCHEDULES:
        if s.kind == kind:
            return s
    raise ScheduleRegistryError(f"no schedule definition for {kind!r}")


# ---- registration plan ---------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class RegistrationDecision:
    """Per-schedule decision the boot loop emits — what action
    to take for each catalog entry given current Temporal state."""

    schedule_id: str
    action: str
    """One of: 'create' (no existing), 'update' (existing
    differs), 'skip' (existing matches, idempotent re-boot).
    Update vs skip avoids the 'noisy boot logs' problem of
    always claiming 'created N schedules' on every restart."""


def plan_registrations(
    *,
    catalog: Sequence[ScheduleDefinition],
    existing_ids: frozenset[str],
    existing_by_id: dict[str, ScheduleDefinition],
) -> tuple[RegistrationDecision, ...]:
    """For each catalog entry, decide what the boot loop should
    do. ``existing_by_id`` is a snapshot of what's already in
    Temporal (mapped to the same ScheduleDefinition shape via the
    activity layer); we compare on workflow_name + interval to
    detect drift.
    """
    out: list[RegistrationDecision] = []
    for entry in catalog:
        if entry.schedule_id not in existing_ids:
            out.append(
                RegistrationDecision(
                    schedule_id=entry.schedule_id,
                    action="create",
                )
            )
            continue
        existing = existing_by_id.get(entry.schedule_id)
        if existing is None or (
            existing.workflow_name != entry.workflow_name
            or existing.interval_seconds != entry.interval_seconds
        ):
            out.append(
                RegistrationDecision(
                    schedule_id=entry.schedule_id,
                    action="update",
                )
            )
            continue
        out.append(
            RegistrationDecision(
                schedule_id=entry.schedule_id,
                action="skip",
            )
        )
    return tuple(out)


# ---- orphan cleanup -----------------------------------------------


def orphaned_schedule_ids(
    *,
    catalog: Sequence[ScheduleDefinition],
    existing_ids: frozenset[str],
) -> tuple[str, ...]:
    """Return platform-owned schedules in Temporal that aren't
    in the current catalog. Boot loop deletes these so removing
    a schedule from the catalog actually stops it.

    Only considers IDs prefixed ``astro-`` so tenant-created
    schedules aren't touched.
    """
    catalog_ids = {s.schedule_id for s in catalog}
    return tuple(sid for sid in sorted(existing_ids) if sid.startswith("astro-") and sid not in catalog_ids)
