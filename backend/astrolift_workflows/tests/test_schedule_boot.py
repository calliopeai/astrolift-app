"""Tests for the schedule registrar (spec 37).

Covers the §Acceptance items:
* allowlist filtering — default Phase-3a set, env override (add a HOLD
  kind / remove a default one), empty env → none, unknown kind ignored;
* ``plan_registrations`` create/update/skip wiring — create_schedule
  called for the right ids with the right interval + workflow, and NOT
  called for HELD kinds;
* registrar-error-is-non-fatal — a raising client logs + the boot wrapper
  keeps the worker serving.

All Temporal SDK calls are mocked — no live Temporal in the harness.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import timedelta

import pytest

from astrolift_workflows import schedule_boot
from astrolift_workflows.schedule_boot import (
    ACTIVE_SCHEDULES_ENV,
    PHASE_3A_ACTIVE_KINDS,
    _allowlisted_catalog,
    register_schedules,
    resolve_active_kinds,
)
from astrolift_workflows.schedule_registry import (
    ScheduleKind,
    get_schedule,
    schedule_id_for,
)

# ---- log capture ---------------------------------------------------
#
# ``astrolift.worker`` is wired into the project's structured-logging
# config, which doesn't propagate to the root handler pytest's ``caplog``
# attaches to — so ``caplog.records`` comes back empty. Attach our own
# handler directly to the logger to assert on emitted messages reliably.


class _CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextlib.contextmanager
def _capture_worker_logs(level: int = logging.INFO):
    logger = logging.getLogger("astrolift.worker")
    handler = _CapturingHandler()
    prev_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(level)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)


# ---- fake Temporal client + schedule listing -----------------------


class _FakeIntervalSpec:
    def __init__(self, every: timedelta) -> None:
        self.every = every


class _FakeSpec:
    def __init__(self, interval_seconds: int) -> None:
        self.intervals = [_FakeIntervalSpec(timedelta(seconds=interval_seconds))]


class _FakeAction:
    def __init__(self, workflow: str) -> None:
        self.workflow = workflow


class _FakeSchedule:
    def __init__(self, workflow: str, interval_seconds: int) -> None:
        self.action = _FakeAction(workflow)
        self.spec = _FakeSpec(interval_seconds)


class _FakeListEntry:
    """Mirrors temporalio ``ScheduleListDescription`` (id + schedule)."""

    def __init__(self, schedule_id: str, workflow: str, interval_seconds: int) -> None:
        self.id = schedule_id
        self.schedule = _FakeSchedule(workflow, interval_seconds)


class _FakeAsyncIterator:
    def __init__(self, entries):
        self._entries = list(entries)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for e in self._entries:
            yield e


class _FakeHandle:
    def __init__(self, schedule_id: str, recorder: list) -> None:
        self._id = schedule_id
        self._recorder = recorder

    async def update(self, updater):
        # Exercise the real updater callback so the built Schedule is covered.
        result = updater(object())
        self._recorder.append((self._id, result))


class _FakeClient:
    """Minimal stand-in for ``temporalio.client.Client``.

    Records create_schedule / get_schedule_handle.update calls so tests can
    assert exactly which schedule ids were created/updated and with what.
    """

    def __init__(self, existing: list[_FakeListEntry] | None = None) -> None:
        self._existing = existing or []
        self.created: list[tuple[str, object]] = []
        self.updated: list[tuple[str, object]] = []

    async def list_schedules(self, *args, **kwargs):
        return _FakeAsyncIterator(self._existing)

    async def create_schedule(self, schedule_id, schedule):
        self.created.append((schedule_id, schedule))

    def get_schedule_handle(self, schedule_id):
        return _FakeHandle(schedule_id, self.updated)


def _interval_of(schedule) -> int:
    """Pull interval seconds out of a real temporalio Schedule we built."""
    return int(schedule.spec.intervals[0].every.total_seconds())


def _workflow_of(schedule) -> str:
    return schedule.action.workflow


def _task_queue_of(schedule) -> str:
    return schedule.action.task_queue


# ============ allowlist filtering ===================================


def test_default_allowlist_is_phase_3a_yes_set():
    """Unset env → the exact Phase-3a YES set from the spec table."""
    expected = {
        ScheduleKind.AGENT_RECONCILE_TICK,
        ScheduleKind.AGENT_CRON_TICK,
        ScheduleKind.SCALE_TICK,
        ScheduleKind.LOOP_TICK,
        ScheduleKind.RECONCILE_CLUSTER_CAPABILITIES,
        ScheduleKind.REHEAL_WEBHOOK_SUBSCRIPTIONS,
        ScheduleKind.DRIFT_DETECTION,
        ScheduleKind.SECRET_BUNDLE_REFRESH,
        # Uptime-monitoring increment: the synthetic probe joined the
        # default-on set alongside app.down/recovered alerting.
        ScheduleKind.UPTIME_PROBE,
        # Alerting-pipeline wire-up: the alert-rule evaluation tick ships
        # active as the sibling detector to uptime (safe-by-default —
        # default rules are PromQL/no-fire; fan-out no-ops unconfigured).
        ScheduleKind.ALERT_EVAL,
        # Run-status reconciler ships active — it's the only thing that
        # advances a finished Job's run row past RUNNING/PENDING (read-only
        # cluster reads, per-run try/except, terminal rows never re-touched).
        ScheduleKind.RUN_STATUS_RECONCILE,
        # Agent-box reaper ships active (#128) — held, an idle-reaped box
        # would free its node while the platform kept claiming the box was
        # attachable and left its Secret behind (read-only cluster reads,
        # per-box try/except, an unobservable box is left untouched).
        ScheduleKind.AGENT_BOX_REAP,
        # Cert-expiry monitor ships active (#155) — the failure it catches
        # (a cert that quietly stopped renewing) only shows up otherwise as
        # a production TLS outage. Read-only provider reads, per-domain
        # try/except, watermarked so a threshold pages once.
        ScheduleKind.CERT_EXPIRY,
    }
    assert PHASE_3A_ACTIVE_KINDS == expected
    assert resolve_active_kinds(None) == frozenset(expected)


def test_default_excludes_every_hold_kind():
    """Every HOLD kind from the spec table is absent from the default."""
    hold = {
        ScheduleKind.PRUNE_AUDIT_LOG,
        ScheduleKind.PRUNE_STALE_SESSIONS,
        ScheduleKind.PREVIEW_GC,
        ScheduleKind.EXPIRE_PENDING_APPROVAL_DEPLOYMENTS,
        ScheduleKind.CAPTURE_PLATFORM_COST_SNAPSHOT,
        ScheduleKind.CRON_DEPLOY_TICK,
        ScheduleKind.POLL_SCHEDULED_JOB_RUNS,
        # Outbound CI-workflow resync sweep ships HELD (#1211): it writes to
        # tenant repos, so it's opt-in via env / the resyncAll admin mutation.
        ScheduleKind.CI_WORKFLOW_RESYNC,
        # Per-quota usage snapshot ships HELD (#1182): only meaningful once
        # quota reconciliation populates current_usage — sibling to the cost
        # snapshot, opt-in via env.
        ScheduleKind.CAPTURE_QUOTA_USAGE_SNAPSHOT,
    }
    assert hold.isdisjoint(PHASE_3A_ACTIVE_KINDS)
    # Sanity: YES set + HOLD set together cover the whole enum.
    assert hold | PHASE_3A_ACTIVE_KINDS == set(ScheduleKind)


def test_env_override_exact_set():
    """A non-empty env yields exactly the listed kinds (whitespace ignored)."""
    raw = " agent_reconcile_tick , drift_detection "
    assert resolve_active_kinds(raw) == frozenset(
        {ScheduleKind.AGENT_RECONCILE_TICK, ScheduleKind.DRIFT_DETECTION}
    )


def test_env_override_can_add_a_hold_kind():
    """Operator opts a HOLD kind in (Phase 3b) by listing it."""
    raw = "agent_reconcile_tick,prune_audit_log"
    resolved = resolve_active_kinds(raw)
    assert ScheduleKind.PRUNE_AUDIT_LOG in resolved
    assert ScheduleKind.AGENT_RECONCILE_TICK in resolved


def test_env_override_can_remove_a_default_kind():
    """Listing only one default kind drops the rest of the default set."""
    resolved = resolve_active_kinds("drift_detection")
    assert resolved == frozenset({ScheduleKind.DRIFT_DETECTION})
    assert ScheduleKind.AGENT_RECONCILE_TICK not in resolved


def test_empty_env_activates_none():
    """Present-but-empty env is a deliberate 'turn everything off'."""
    assert resolve_active_kinds("") == frozenset()
    assert resolve_active_kinds("   ") == frozenset()
    assert resolve_active_kinds(",, ,") == frozenset()


def test_unknown_kind_ignored():
    """A typo'd kind is logged and skipped, not fatal, valid kinds still resolve."""
    with _capture_worker_logs(logging.WARNING) as handler:
        resolved = resolve_active_kinds("agent_reconcile_tick,not_a_real_kind")
    assert resolved == frozenset({ScheduleKind.AGENT_RECONCILE_TICK})
    assert any("not_a_real_kind" in r.getMessage() for r in handler.records)


def test_allowlisted_catalog_filters_to_active():
    """_allowlisted_catalog returns only the catalog entries in the set."""
    catalog = _allowlisted_catalog(frozenset({ScheduleKind.DRIFT_DETECTION}))
    assert {d.kind for d in catalog} == {ScheduleKind.DRIFT_DETECTION}


# ============ create / update / skip wiring =========================


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Each test controls the allowlist explicitly."""
    monkeypatch.delenv(ACTIVE_SCHEDULES_ENV, raising=False)


@pytest.mark.asyncio
async def test_create_path_default_set(monkeypatch):
    """Empty Temporal + default env → create exactly the Phase-3a YES ids,
    with the catalog interval + workflow + main task queue, and NOTHING for
    HELD kinds."""
    client = _FakeClient(existing=[])
    await register_schedules(client)

    created_ids = {sid for sid, _ in client.created}
    expected_ids = {schedule_id_for(kind=k) for k in PHASE_3A_ACTIVE_KINDS}
    assert created_ids == expected_ids
    assert client.updated == []

    # HELD kinds must NOT be created.
    held_ids = {schedule_id_for(kind=k) for k in (set(ScheduleKind) - PHASE_3A_ACTIVE_KINDS)}
    assert created_ids.isdisjoint(held_ids)
    # Spot-check a few HOLD ids explicitly.
    for k in (
        ScheduleKind.PRUNE_AUDIT_LOG,
        ScheduleKind.PREVIEW_GC,
        ScheduleKind.CRON_DEPLOY_TICK,
        ScheduleKind.CAPTURE_PLATFORM_COST_SNAPSHOT,
    ):
        assert schedule_id_for(kind=k) not in created_ids

    # Each created schedule carries the right workflow + interval + queue.
    created_by_id = dict(client.created)
    for k in PHASE_3A_ACTIVE_KINDS:
        defn = get_schedule(kind=k)
        sched = created_by_id[defn.schedule_id]
        assert _workflow_of(sched) == defn.workflow_name
        assert _interval_of(sched) == defn.interval_seconds
        # Task queue matches the worker's default (astrolift-main) queue.
        assert _task_queue_of(sched) == "astrolift-main"


@pytest.mark.asyncio
async def test_held_kind_not_created_even_when_present_in_temporal(monkeypatch):
    """Even if a HOLD schedule somehow exists in Temporal, the registrar with
    the default allowlist neither creates nor updates it (it's an orphan we
    leave alone — no deletion)."""
    audit = get_schedule(kind=ScheduleKind.PRUNE_AUDIT_LOG)
    client = _FakeClient(
        existing=[
            _FakeListEntry(audit.schedule_id, audit.workflow_name, audit.interval_seconds),
        ]
    )
    await register_schedules(client)

    assert all(sid != audit.schedule_id for sid, _ in client.created)
    assert all(sid != audit.schedule_id for sid, _ in client.updated)


@pytest.mark.asyncio
async def test_skip_path_when_all_exist(monkeypatch):
    """Re-boot: every active schedule already in Temporal with matching
    workflow + interval → skip (no create, no update)."""
    existing = [
        _FakeListEntry(
            get_schedule(kind=k).schedule_id,
            get_schedule(kind=k).workflow_name,
            get_schedule(kind=k).interval_seconds,
        )
        for k in PHASE_3A_ACTIVE_KINDS
    ]
    client = _FakeClient(existing=existing)
    await register_schedules(client)

    assert client.created == []
    assert client.updated == []


@pytest.mark.asyncio
async def test_update_path_on_interval_drift(monkeypatch):
    """An active schedule exists but with a drifted interval → update (not
    create), and the update carries the catalog interval."""
    reconcile = get_schedule(kind=ScheduleKind.AGENT_RECONCILE_TICK)
    # All other active kinds already match so they skip; reconcile drifts.
    existing = []
    for k in PHASE_3A_ACTIVE_KINDS:
        d = get_schedule(kind=k)
        interval = d.interval_seconds + 120 if k == ScheduleKind.AGENT_RECONCILE_TICK else d.interval_seconds
        existing.append(_FakeListEntry(d.schedule_id, d.workflow_name, interval))

    client = _FakeClient(existing=existing)
    await register_schedules(client)

    assert client.created == []
    assert len(client.updated) == 1
    upd_id, upd = client.updated[0]
    assert upd_id == reconcile.schedule_id
    # The updater returned a ScheduleUpdate with the catalog interval/workflow.
    assert _interval_of(upd.schedule) == reconcile.interval_seconds
    assert _workflow_of(upd.schedule) == reconcile.workflow_name


@pytest.mark.asyncio
async def test_update_path_on_workflow_name_drift(monkeypatch):
    """Workflow renamed in the catalog vs Temporal → update."""
    reconcile = get_schedule(kind=ScheduleKind.AGENT_RECONCILE_TICK)
    existing = []
    for k in PHASE_3A_ACTIVE_KINDS:
        d = get_schedule(kind=k)
        wf = "StaleOldWorkflow" if k == ScheduleKind.AGENT_RECONCILE_TICK else d.workflow_name
        existing.append(_FakeListEntry(d.schedule_id, wf, d.interval_seconds))

    client = _FakeClient(existing=existing)
    await register_schedules(client)

    assert client.created == []
    assert [sid for sid, _ in client.updated] == [reconcile.schedule_id]


@pytest.mark.asyncio
async def test_env_override_controls_what_is_created(monkeypatch):
    """ASTROLIFT_ACTIVE_SCHEDULES adds a HOLD kind + restricts the rest."""
    monkeypatch.setenv(ACTIVE_SCHEDULES_ENV, "agent_reconcile_tick,prune_audit_log")
    client = _FakeClient(existing=[])
    await register_schedules(client)

    created_ids = {sid for sid, _ in client.created}
    assert created_ids == {
        schedule_id_for(kind=ScheduleKind.AGENT_RECONCILE_TICK),
        schedule_id_for(kind=ScheduleKind.PRUNE_AUDIT_LOG),
    }


@pytest.mark.asyncio
async def test_empty_env_creates_nothing(monkeypatch):
    """Empty allowlist → register nothing at all."""
    monkeypatch.setenv(ACTIVE_SCHEDULES_ENV, "")
    client = _FakeClient(existing=[])
    await register_schedules(client)

    assert client.created == []
    assert client.updated == []


@pytest.mark.asyncio
async def test_no_deletion_orphans_only_logged(monkeypatch):
    """A platform (astro-) schedule not in the allowlisted catalog is left
    running and only logged — NEVER deleted (no delete API is touched)."""
    # An active reconcile (will skip) plus a stray astro- orphan.
    reconcile = get_schedule(kind=ScheduleKind.AGENT_RECONCILE_TICK)
    monkeypatch.setenv(ACTIVE_SCHEDULES_ENV, "agent_reconcile_tick")
    client = _FakeClient(
        existing=[
            _FakeListEntry(reconcile.schedule_id, reconcile.workflow_name, reconcile.interval_seconds),
            _FakeListEntry("astro-old-removed-schedule", "GoneWorkflow", 600),
        ]
    )
    # _FakeClient deliberately has no delete method — if the registrar tried
    # to delete, it would AttributeError.
    assert not hasattr(client, "delete_schedule")

    with _capture_worker_logs(logging.INFO) as handler:
        await register_schedules(client)

    assert client.created == []  # reconcile already exists → skip
    assert client.updated == []
    assert any(
        "orphaned" in r.getMessage() and "astro-old-removed-schedule" in r.getMessage()
        for r in handler.records
    )


# ============ registrar error is non-fatal ==========================


@pytest.mark.asyncio
async def test_register_schedules_propagates_to_caller(monkeypatch):
    """register_schedules itself does not swallow a hard client failure —
    it surfaces so the boot wrapper can log it (the wrapper is what makes it
    non-fatal). Here list_schedules raises."""

    class _ExplodingClient(_FakeClient):
        async def list_schedules(self, *a, **k):
            raise RuntimeError("temporal unreachable")

    with pytest.raises(RuntimeError, match="temporal unreachable"):
        await register_schedules(_ExplodingClient())


@pytest.mark.asyncio
async def test_boot_wrapper_swallows_registrar_error(monkeypatch):
    """The pattern wired into __main__:_run() — try/register/except/log —
    keeps the worker alive when the registrar raises."""

    async def _boom(_client):
        raise RuntimeError("registration blew up")

    monkeypatch.setattr(schedule_boot, "register_schedules", _boom)

    log = logging.getLogger("astrolift.worker")
    worker_continued = False
    with _capture_worker_logs(logging.ERROR) as handler:
        # This block mirrors __main__._run() exactly.
        try:
            await schedule_boot.register_schedules(object())
        except Exception:  # noqa: BLE001
            log.exception("schedule registrar failed; worker continuing without schedule sync")
        worker_continued = True  # reached only because the except swallowed it

    assert worker_continued is True
    assert any("schedule registrar failed" in r.getMessage() for r in handler.records)
