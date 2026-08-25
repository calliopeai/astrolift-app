"""The four links between a ScheduleKind and a running sweep (#1602 step 6).

Copied in shape from `test_cert_expiry_tick.py`, because the failure this
guards is the one #1580 spent an epic on: each link can exist while the
next does not, and every gap fails silently. A schedule naming a workflow
nothing registers simply never runs, and Temporal reports nothing wrong.

The fifth assertion is the one that matters most here. The activity must
*delegate* to the sweep rather than report a hardcoded zero -- a tick that
returns `evicted=0` from a stub is indistinguishable from one that found
nothing to evict, which is the #1594 false zero arriving one layer up.
"""

from __future__ import annotations

import pytest

from astrolift_workflows.schedule_boot import PHASE_3A_ACTIVE_KINDS
from astrolift_workflows.schedule_registry import DEFAULT_SCHEDULES, ScheduleKind


def _definition():
    matches = [d for d in DEFAULT_SCHEDULES if d.kind == ScheduleKind.PRUNE_OBSERVABILITY_DATA]
    assert len(matches) == 1, "exactly one definition for the kind"
    return matches[0]


def test_the_kind_is_in_the_catalog():
    assert _definition().workflow_name == "ObservabilityRetentionTickWorkflow"


def test_it_ships_held_not_active():
    """The strongest HOLD case in the catalog: the only schedule whose
    purpose is deleting tenant data, and a tick that deletes a window
    cannot be undone by the next one."""
    assert ScheduleKind.PRUNE_OBSERVABILITY_DATA not in PHASE_3A_ACTIVE_KINDS


def test_the_interval_is_daily_and_the_timeout_fits_inside_it():
    """A timeout longer than the interval lets two passes overlap, and two
    concurrent passes issue the same deletes twice."""
    from astrolift_workflows.workflows.observability_retention_tick import _TICK_TIMEOUT

    interval = _definition().interval_seconds
    assert interval == 24 * 60 * 60
    assert _TICK_TIMEOUT.total_seconds() < interval


def test_the_workflow_name_is_served_by_a_registered_workflow():
    """A schedule naming a workflow nothing registers never runs, and
    Temporal reports nothing wrong."""
    from astrolift_workflows.worker import WORKFLOWS

    # `__temporal_workflow_definition` -- one trailing underscore, not two.
    # The dunder spelling returns None for every workflow, so the set comes
    # back empty and the assertion passes over nothing: a gate that would
    # have reported every unregistered workflow as registered.
    names = {getattr(w, "__temporal_workflow_definition").name for w in WORKFLOWS}

    assert _definition().workflow_name in names


def test_the_activity_is_registered_on_the_worker():
    from astrolift_workflows.activities import prune_observability_data
    from astrolift_workflows.worker import ACTIVITIES

    assert prune_observability_data in ACTIVITIES


def test_the_activity_delegates_to_the_sweep_rather_than_reporting_zero(monkeypatch):
    """The link that can exist and still be a lie.

    A stub returning zeroes satisfies every assertion above -- it is in the
    catalog, registered, and named correctly -- while reporting "nothing to
    evict" for ever. Indistinguishable from a working sweep with nothing to
    do, which is exactly the false zero this issue was partly filed about.
    """
    import astrolift_operations.observability_retention_sweep as sweep_mod
    from astrolift_workflows.activities.observability_retention import (
        _prune_observability_data_sync,
    )

    seen: dict = {}

    def fake_sweep(*, dry_run):
        seen["dry_run"] = dry_run
        return {
            "orgs": 3,
            "windows": 5,
            "evicted": 7,
            "held": 2,
            "skipped_unsupported": 1,
            "errors": 0,
        }

    monkeypatch.setattr(sweep_mod, "run_observability_retention_sweep", fake_sweep)

    summary = _prune_observability_data_sync()

    assert seen, "the activity did not call the sweep at all"
    assert summary.evicted == 7
    assert summary.skipped_unsupported == 1
    assert summary.held == 2


def test_a_real_tick_is_not_a_dry_run(monkeypatch):
    """The sweep defaults to dry-run so nothing deletes by accident. The
    deliberate call site has to opt in, or activating the schedule would
    produce a nightly report and never delete anything -- retention
    "enabled" and not enforced."""
    import astrolift_operations.observability_retention_sweep as sweep_mod
    from astrolift_workflows.activities.observability_retention import (
        _prune_observability_data_sync,
    )

    seen: dict = {}

    def fake_sweep(*, dry_run):
        seen["dry_run"] = dry_run
        return dict.fromkeys(("orgs", "windows", "evicted", "held", "skipped_unsupported", "errors"), 0)

    monkeypatch.setattr(sweep_mod, "run_observability_retention_sweep", fake_sweep)
    _prune_observability_data_sync()

    assert seen["dry_run"] is False


def test_it_is_not_the_same_thing_as_applying_a_retention_window():
    """Two kinds one letter apart in meaning, and confusing them would
    activate a delete on the reasoning that applied to a config change.
    APPLY sets a log group's window; PRUNE evicts past it."""
    assert ScheduleKind.APPLY_OBSERVABILITY_RETENTION != ScheduleKind.PRUNE_OBSERVABILITY_DATA

    apply_def = next(d for d in DEFAULT_SCHEDULES if d.kind == ScheduleKind.APPLY_OBSERVABILITY_RETENTION)
    assert apply_def.workflow_name != _definition().workflow_name


@pytest.mark.parametrize(
    "field",
    ["orgs", "windows", "evicted", "held", "skipped_unsupported", "errors"],
)
def test_the_summary_carries_every_count_the_sweep_produces(field):
    """A count the sweep produces and the summary drops is a count the
    operator never sees -- and `skipped_unsupported` in particular is the
    one that separates "not enforced" from "nothing to enforce"."""
    from astrolift_workflows.activities.observability_retention import (
        ObservabilityRetentionSummary,
    )

    assert field in ObservabilityRetentionSummary.__dataclass_fields__
