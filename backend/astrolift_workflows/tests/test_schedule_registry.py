"""Tests for scheduled workflow registration policy (#129, spec 06 §5)."""

from __future__ import annotations

import pytest

from astrolift_workflows.schedule_registry import (
    DEFAULT_SCHEDULES,
    MAX_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    ScheduleDefinition,
    ScheduleKind,
    ScheduleRegistryError,
    get_schedule,
    orphaned_schedule_ids,
    plan_registrations,
    schedule_id_for,
)

# ---- bounds locked -------------------------------------------------


def test_min_max_interval_locked():
    """Lock the bounds — changes require code review."""
    assert MIN_INTERVAL_SECONDS == 60
    assert MAX_INTERVAL_SECONDS == 24 * 60 * 60


# ---- schedule definition validation --------------------------------


def test_schedule_definition_below_min_rejected():
    """30s would be a hot loop — refuse."""
    with pytest.raises(ScheduleRegistryError, match="below minimum"):
        ScheduleDefinition(
            kind=ScheduleKind.PREVIEW_GC,
            workflow_name="X",
            interval_seconds=30,
            schedule_id="x",
            description="d",
        )


def test_schedule_definition_above_max_rejected():
    """48h is probably operator typo for 'every 2 days' (use cron)."""
    with pytest.raises(ScheduleRegistryError, match="exceeds maximum"):
        ScheduleDefinition(
            kind=ScheduleKind.DRIFT_DETECTION,
            workflow_name="X",
            interval_seconds=48 * 60 * 60,
            schedule_id="x",
            description="d",
        )


def test_schedule_definition_requires_workflow_name():
    with pytest.raises(ScheduleRegistryError):
        ScheduleDefinition(
            kind=ScheduleKind.PREVIEW_GC,
            workflow_name="",
            interval_seconds=900,
            schedule_id="x",
            description="d",
        )


def test_schedule_definition_requires_id():
    with pytest.raises(ScheduleRegistryError):
        ScheduleDefinition(
            kind=ScheduleKind.PREVIEW_GC,
            workflow_name="X",
            interval_seconds=900,
            schedule_id="",
            description="d",
        )


# ---- schedule ID convention ----------------------------------------


def test_schedule_id_namespace():
    """Platform-owned schedules use astro- prefix."""
    assert schedule_id_for(kind=ScheduleKind.PREVIEW_GC) == "astro-preview_gc"


def test_schedule_id_deterministic():
    """Same kind → same ID. Boot re-registration idempotent."""
    a = schedule_id_for(kind=ScheduleKind.DRIFT_DETECTION)
    b = schedule_id_for(kind=ScheduleKind.DRIFT_DETECTION)
    assert a == b


# ---- catalog -------------------------------------------------------


def test_default_schedules_complete():
    """Spec 06 §5 listed 7 schedules; #296 added the cron-deploy
    dispatcher tick; #365 added the secret-bundle refresh sweep;
    #498 added the stale-session prune; #779 added the pending-approval
    expiry sweep; spec 33 PR-4 added the agent-cron dispatcher tick;
    spec 33 PR-5 added the scheduled-scaling tick.
    Lock the count so future additions stay visible in a diff."""
    assert len(DEFAULT_SCHEDULES) == 13


def test_default_schedules_include_all_kinds():
    """Every ScheduleKind enum value should appear in catalog."""
    catalog_kinds = {s.kind for s in DEFAULT_SCHEDULES}
    enum_kinds = set(ScheduleKind)
    assert catalog_kinds == enum_kinds


def test_preview_gc_interval_15_min():
    s = get_schedule(kind=ScheduleKind.PREVIEW_GC)
    assert s.interval_seconds == 15 * 60


def test_poll_jobs_interval_1_min():
    s = get_schedule(kind=ScheduleKind.POLL_SCHEDULED_JOB_RUNS)
    assert s.interval_seconds == 60


def test_drift_detection_interval_1_hour():
    s = get_schedule(kind=ScheduleKind.DRIFT_DETECTION)
    assert s.interval_seconds == 60 * 60


def test_audit_log_interval_daily():
    s = get_schedule(kind=ScheduleKind.PRUNE_AUDIT_LOG)
    assert s.interval_seconds == 24 * 60 * 60


def test_get_schedule_unknown_kind():
    """Defensive — caller can't look up a kind we don't have."""

    class Fake:
        value = "fake"

    with pytest.raises(ScheduleRegistryError):
        get_schedule(kind=Fake())  # type: ignore[arg-type]


# ---- registration planning -----------------------------------------


def test_plan_creates_when_existing_empty():
    """Boot against empty Temporal: create all (one decision per
    catalog entry)."""
    decisions = plan_registrations(
        catalog=DEFAULT_SCHEDULES,
        existing_ids=frozenset(),
        existing_by_id={},
    )
    assert len(decisions) == len(DEFAULT_SCHEDULES)
    assert all(d.action == "create" for d in decisions)


def test_plan_skips_when_existing_matches():
    """Boot re-registration: if Temporal state matches catalog,
    no work — avoid noisy 'created N schedules' logs on every
    restart."""
    existing = {s.schedule_id: s for s in DEFAULT_SCHEDULES}
    decisions = plan_registrations(
        catalog=DEFAULT_SCHEDULES,
        existing_ids=frozenset(existing.keys()),
        existing_by_id=existing,
    )
    assert all(d.action == "skip" for d in decisions)


def test_plan_updates_when_interval_changed():
    """Operator bumped GC interval; existing Temporal config has
    old value. Boot updates."""
    catalog_def = get_schedule(kind=ScheduleKind.PREVIEW_GC)
    drifted = ScheduleDefinition(
        kind=catalog_def.kind,
        workflow_name=catalog_def.workflow_name,
        interval_seconds=30 * 60,  # different from 15
        schedule_id=catalog_def.schedule_id,
        description=catalog_def.description,
    )
    decisions = plan_registrations(
        catalog=(catalog_def,),
        existing_ids=frozenset({catalog_def.schedule_id}),
        existing_by_id={catalog_def.schedule_id: drifted},
    )
    assert len(decisions) == 1
    assert decisions[0].action == "update"


def test_plan_updates_when_workflow_name_changed():
    """Workflow renamed in catalog → update."""
    catalog_def = get_schedule(kind=ScheduleKind.PREVIEW_GC)
    drifted = ScheduleDefinition(
        kind=catalog_def.kind,
        workflow_name="OldGcWorkflow",
        interval_seconds=catalog_def.interval_seconds,
        schedule_id=catalog_def.schedule_id,
        description=catalog_def.description,
    )
    decisions = plan_registrations(
        catalog=(catalog_def,),
        existing_ids=frozenset({catalog_def.schedule_id}),
        existing_by_id={catalog_def.schedule_id: drifted},
    )
    assert decisions[0].action == "update"


# ---- orphan cleanup ------------------------------------------------


def test_orphaned_schedules_returns_extras():
    """Old astro- schedule that's no longer in catalog → cleanup."""
    existing = frozenset({s.schedule_id for s in DEFAULT_SCHEDULES} | {"astro-old-removed-schedule"})
    orphans = orphaned_schedule_ids(
        catalog=DEFAULT_SCHEDULES,
        existing_ids=existing,
    )
    assert orphans == ("astro-old-removed-schedule",)


def test_orphaned_schedules_skips_tenant_owned():
    """Tenant-created schedules don't have astro- prefix; never
    touch them even if not in the platform catalog."""
    existing = frozenset(
        {s.schedule_id for s in DEFAULT_SCHEDULES} | {"customer-cronjob-1", "customer-cronjob-2"}
    )
    orphans = orphaned_schedule_ids(
        catalog=DEFAULT_SCHEDULES,
        existing_ids=existing,
    )
    assert orphans == ()


def test_orphaned_schedules_no_orphans():
    existing = frozenset({s.schedule_id for s in DEFAULT_SCHEDULES})
    orphans = orphaned_schedule_ids(
        catalog=DEFAULT_SCHEDULES,
        existing_ids=existing,
    )
    assert orphans == ()
