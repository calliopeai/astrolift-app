"""The cert-expiry monitor has to actually be scheduled and served.

``cert_expiry`` was a correct policy module with no caller: no schedule,
no workflow, no activity. A tick that exists but is absent from the
catalog never runs; one in the catalog but outside the boot allowlist is
registered nowhere; one whose activity is missing from ``ACTIVITIES``
fails the moment the run reaches it. These pin all four links.
"""

from __future__ import annotations

import pytest

from astrolift_workflows.activities.cert_expiry import (
    _check_cert_expiry_tick_sync,
    check_cert_expiry_tick,
)
from astrolift_workflows.schedule_boot import PHASE_3A_ACTIVE_KINDS, resolve_active_kinds
from astrolift_workflows.schedule_registry import ScheduleKind, get_schedule
from astrolift_workflows.worker import ACTIVITIES, WORKFLOWS


def test_cert_expiry_is_in_the_schedule_catalog():
    s = get_schedule(kind=ScheduleKind.CERT_EXPIRY)
    assert s.workflow_name == "CertExpiryTickWorkflow"
    assert s.interval_seconds == 24 * 60 * 60


def test_cert_expiry_is_active_by_default():
    """Held, the sweep is worthless: the failure it catches only surfaces
    otherwise as a production TLS outage."""
    assert ScheduleKind.CERT_EXPIRY in PHASE_3A_ACTIVE_KINDS
    assert ScheduleKind.CERT_EXPIRY in resolve_active_kinds(None)


def test_the_scheduled_workflow_name_is_served_by_a_worker():
    """The schedule names a workflow by string; a name no worker registers
    is a schedule that fires into nothing."""
    wanted = get_schedule(kind=ScheduleKind.CERT_EXPIRY).workflow_name
    registered = {getattr(w, "__temporal_workflow_definition").name for w in WORKFLOWS}
    assert wanted in registered


def test_the_tick_activity_is_registered():
    assert check_cert_expiry_tick in ACTIVITIES


def test_the_activity_returns_the_sweep_summary(monkeypatch):
    """The activity is the seam between Temporal and the sweep — assert it
    calls the sweep rather than reporting a hardcoded zero."""
    called: list[bool] = []

    def fake_sweep():
        called.append(True)
        return {"checked": 3, "reminders": 2, "escalations": 1}

    monkeypatch.setattr(
        "astrolift_operations.cert_expiry_monitor.run_cert_expiry_sweep",
        fake_sweep,
    )
    summary = _check_cert_expiry_tick_sync()

    assert called == [True]
    assert (summary.checked, summary.reminders, summary.escalations) == (3, 2, 1)


@pytest.mark.asyncio
async def test_the_async_activity_delegates_to_the_sync_sweep(monkeypatch):
    monkeypatch.setattr(
        "astrolift_operations.cert_expiry_monitor.run_cert_expiry_sweep",
        lambda: {"checked": 1, "reminders": 1, "escalations": 0},
    )
    summary = await check_cert_expiry_tick()

    assert summary.reminders == 1
