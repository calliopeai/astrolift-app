"""Schedule registrar — wires the dormant scheduler into worker boot (spec 37).

``astrolift_workflows.schedule_registry`` defines the catalog
(``DEFAULT_SCHEDULES``) and the pure create/update/skip policy
(``plan_registrations``), but nothing called them, so no Temporal
schedules were ever created and the reconcile + cron/loop/scale agent
run-modes never fired in prod.

This module is that missing caller. ``register_schedules(client)`` runs
once per worker boot (wired into ``__main__.py:_run()``), is idempotent
(create-or-skip; update only on drift), and **never deletes**.

**Phased activation (spec 37 §Risk).** Activating the whole catalog at
once would flip on sweeps that have never run in prod and do mutating /
external work (audit-log pruning, preview teardown, cost-API calls, …).
So the registrar only registers an **allowlisted subset**, env-driven
via ``ASTROLIFT_ACTIVE_SCHEDULES`` and defaulting to the safe Phase-3a
set encoded in ``PHASE_3A_ACTIVE_KINDS`` below. An unset env → that safe
default (not empty). The HOLD kinds are enabled later, one at a time, by
adding their kind to the env (a config change, no code).

Temporal SDK call shape mirrors the working
``astrolift_agents/services/workflow_triggers.py`` /
``astrolift_pipelines/schedule_sync.py`` create_schedule usage.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import TYPE_CHECKING

from astrolift_workflows.schedule_registry import (
    DEFAULT_SCHEDULES,
    ScheduleDefinition,
    ScheduleKind,
    plan_registrations,
)

if TYPE_CHECKING:
    from temporalio.client import Client

log = logging.getLogger("astrolift.worker")


# ---- env allowlist key ---------------------------------------------

ACTIVE_SCHEDULES_ENV = "ASTROLIFT_ACTIVE_SCHEDULES"
"""Comma-separated ``ScheduleKind`` values. Unset → ``PHASE_3A_ACTIVE_KINDS``."""


# Spec 37 §Risk table — the Phase-3a "Activate? YES" set. Encoded in code
# so an unset env is the *safe* default (the idempotent / read-only kinds),
# never the whole catalog and never empty. Each of these either re-applies
# idempotently (SSA), reconciles read-mostly state, or only acts on agents
# explicitly configured with the matching run-mode (≈zero prod blast radius).
# The HOLD kinds (deletes / teardowns / cost-API / auto-deploy / auto-fail)
# are deliberately absent — Phase 3b adds them one at a time via the env.
PHASE_3A_ACTIVE_KINDS: frozenset[ScheduleKind] = frozenset(
    {
        ScheduleKind.AGENT_RECONCILE_TICK,
        ScheduleKind.AGENT_CRON_TICK,
        ScheduleKind.SCALE_TICK,
        ScheduleKind.LOOP_TICK,
        ScheduleKind.RECONCILE_CLUSTER_CAPABILITIES,
        ScheduleKind.REHEAL_WEBHOOK_SUBSCRIPTIONS,
        ScheduleKind.DRIFT_DETECTION,
        ScheduleKind.SECRET_BUNDLE_REFRESH,
    }
)


# ---- task queue for schedule actions -------------------------------


def _schedule_task_queue() -> str:
    """Task queue the scheduled tick workflows are dispatched on.

    Matches the existing agent/workflow schedule path
    (``workflow_triggers._create_temporal_schedule``): the default
    ``TEMPORAL_TASK_QUEUE`` (``astrolift-main``), which the worker polls
    as ``default_queue`` in ``__main__.py:_run()``. The tick workflows
    are registered on every worker, so dispatching them to the main
    queue guarantees a poller picks them up.
    """
    from django.conf import settings

    return getattr(settings, "TEMPORAL_TASK_QUEUE", "astrolift-main")


# ---- allowlist resolution ------------------------------------------


def resolve_active_kinds(raw_env: str | None) -> frozenset[ScheduleKind]:
    """Resolve the active-schedule allowlist from the env value.

    * ``None`` (unset) → the safe ``PHASE_3A_ACTIVE_KINDS`` default.
    * non-empty → exactly the listed kinds (so an operator can both
      *add* a HOLD kind and *remove* a default one). Whitespace and
      empty fields are ignored; unknown kinds are logged and skipped
      (a typo must not silently activate nothing-or-everything).
    * present but empty / whitespace-only → empty set (activate none),
      a deliberate "turn everything off" override.
    """
    if raw_env is None:
        return PHASE_3A_ACTIVE_KINDS

    valid = {k.value: k for k in ScheduleKind}
    selected: set[ScheduleKind] = set()
    for field in raw_env.split(","):
        token = field.strip()
        if not token:
            continue
        kind = valid.get(token)
        if kind is None:
            log.warning(
                "schedule registrar: ignoring unknown schedule kind %r in %s",
                token,
                ACTIVE_SCHEDULES_ENV,
            )
            continue
        selected.add(kind)
    return frozenset(selected)


def _allowlisted_catalog(
    active_kinds: frozenset[ScheduleKind],
) -> tuple[ScheduleDefinition, ...]:
    """``DEFAULT_SCHEDULES`` filtered to the active allowlist."""
    return tuple(s for s in DEFAULT_SCHEDULES if s.kind in active_kinds)


# ---- existing-schedule snapshot ------------------------------------


def _interval_seconds_from_spec(schedule_spec) -> int | None:
    """Pull the first interval's period (seconds) from a ScheduleSpec.

    Platform schedules are all interval-based (``ScheduleIntervalSpec``).
    Returns ``None`` if the existing schedule carries no interval (e.g.
    a hand-edited cron schedule) — the caller then treats it as drift so
    it gets corrected back to the catalog interval.
    """
    intervals = getattr(schedule_spec, "intervals", None) or []
    if not intervals:
        return None
    every = getattr(intervals[0], "every", None)
    if not isinstance(every, timedelta):
        return None
    return int(every.total_seconds())


def _workflow_name_from_action(action) -> str:
    """Pull the workflow type name from a listed schedule action.

    ``client.list_schedules()`` yields ``ScheduleListDescription`` whose
    ``schedule.action`` is a ``ScheduleListActionStartWorkflow`` carrying
    a ``workflow`` (the registered workflow type name).
    """
    return getattr(action, "workflow", "") or ""


async def _existing_snapshot(
    client: Client,
) -> tuple[frozenset[str], dict[str, ScheduleDefinition]]:
    """List Temporal schedules and map them to the shape ``plan_registrations`` wants.

    Returns ``(existing_ids, existing_by_id)`` where ``existing_by_id``
    carries the *currently registered* workflow_name + interval per id so
    ``plan_registrations`` can detect drift (workflow renamed / interval
    bumped → update). Only ``workflow_name`` + ``interval_seconds`` are
    load-bearing for the drift comparison; ``kind`` / ``description`` are
    cosmetic, so unknown existing ids (orphans, tenant schedules) are
    still mapped best-effort with a placeholder kind.
    """
    existing_ids: set[str] = set()
    existing_by_id: dict[str, ScheduleDefinition] = {}

    by_id = {s.schedule_id: s for s in DEFAULT_SCHEDULES}

    async for entry in await client.list_schedules():
        sid = entry.id
        existing_ids.add(sid)

        schedule = getattr(entry, "schedule", None)
        action = getattr(schedule, "action", None) if schedule is not None else None
        spec = getattr(schedule, "spec", None) if schedule is not None else None

        workflow_name = _workflow_name_from_action(action) if action is not None else ""
        interval_seconds = _interval_seconds_from_spec(spec) if spec is not None else None

        # Reuse the catalog kind when the id matches a known schedule so
        # ScheduleDefinition validation (interval bounds) is satisfied;
        # otherwise fall back to the catalog entry's interval as a benign
        # placeholder. The drift comparison only reads workflow_name +
        # interval_seconds, both taken from the *live* schedule here.
        known = by_id.get(sid)
        kind = known.kind if known is not None else ScheduleKind.DRIFT_DETECTION
        if interval_seconds is None:
            # No interval exposed (e.g. cron-only schedule). Use a bound-safe
            # placeholder that differs from the catalog value so the entry is
            # treated as drift and corrected. Catalog interval - 1 stays > MIN.
            interval_seconds = (known.interval_seconds - 1) if known is not None else 60

        try:
            existing_by_id[sid] = ScheduleDefinition(
                kind=kind,
                workflow_name=workflow_name or "unknown",
                interval_seconds=interval_seconds,
                schedule_id=sid,
                description="(live Temporal snapshot)",
            )
        except Exception:  # noqa: BLE001 — never let one odd live schedule abort the snapshot
            log.warning(
                "schedule registrar: could not map existing schedule %s; "
                "treating as create/update target",
                sid,
            )

    return frozenset(existing_ids), existing_by_id


# ---- create / update apply -----------------------------------------


def _build_schedule(definition: ScheduleDefinition, task_queue: str):
    """Construct a Temporal ``Schedule`` for one catalog entry.

    Mirrors the create_schedule call shape used in
    ``astrolift_agents/services/workflow_triggers.py`` and
    ``astrolift_pipelines/schedule_sync.py`` — interval-based here per the
    catalog's ``interval_seconds`` (the existing call sites are cron-based).
    """
    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleIntervalSpec,
        ScheduleSpec,
    )

    return Schedule(
        action=ScheduleActionStartWorkflow(
            definition.workflow_name,
            id=f"{definition.schedule_id}-run",
            task_queue=task_queue,
        ),
        spec=ScheduleSpec(
            intervals=[ScheduleIntervalSpec(every=timedelta(seconds=definition.interval_seconds))]
        ),
    )


async def _apply_create(client: Client, definition: ScheduleDefinition, task_queue: str) -> None:
    await client.create_schedule(
        definition.schedule_id,
        _build_schedule(definition, task_queue),
    )


async def _apply_update(client: Client, definition: ScheduleDefinition, task_queue: str) -> None:
    """Update an existing schedule to the catalog's action + interval.

    Uses the handle's ``update(updater)`` callback: the updater receives
    the current ``ScheduleUpdateInput`` and returns a ``ScheduleUpdate``
    carrying a fresh ``Schedule`` with the catalog action + spec, leaving
    other fields (state, policy) as Temporal's defaults for this schedule.
    """
    from temporalio.client import ScheduleUpdate

    handle = client.get_schedule_handle(definition.schedule_id)

    def _updater(_input):
        return ScheduleUpdate(schedule=_build_schedule(definition, task_queue))

    await handle.update(_updater)


# ---- registrar -----------------------------------------------------


async def register_schedules(client: Client) -> None:
    """Register the allowlisted platform schedules in Temporal (spec 37).

    Idempotent and safe to call on every worker boot. Resolves the
    allowlist, snapshots existing Temporal schedules, asks
    ``plan_registrations`` for per-id create/update/skip decisions, and
    applies them. **Never deletes** — orphans are logged only.

    This function does not raise on Temporal errors at the call-site level
    (the caller in ``__main__`` also wraps it defensively), but it logs
    and lets unexpected errors propagate to that wrapper so a registration
    failure is visible without crashing the worker.
    """
    active_kinds = resolve_active_kinds(os.environ.get(ACTIVE_SCHEDULES_ENV))
    catalog = _allowlisted_catalog(active_kinds)
    held_kinds = set(ScheduleKind) - active_kinds
    task_queue = _schedule_task_queue()

    existing_ids, existing_by_id = await _existing_snapshot(client)

    decisions = plan_registrations(
        catalog=catalog,
        existing_ids=existing_ids,
        existing_by_id=existing_by_id,
    )
    by_id = {s.schedule_id: s for s in catalog}

    created = updated = skipped = 0
    for decision in decisions:
        definition = by_id[decision.schedule_id]
        if decision.action == "create":
            await _apply_create(client, definition, task_queue)
            created += 1
        elif decision.action == "update":
            await _apply_update(client, definition, task_queue)
            updated += 1
        else:  # "skip"
            skipped += 1

    # NO deletion (spec 37 §Out of scope) — log orphans only. An orphan is
    # a platform-owned (astro-) schedule that is in Temporal but not in the
    # *allowlisted* catalog: either a HOLD kind that was previously active,
    # or a kind removed from the catalog. We leave it running and surface it.
    catalog_ids = set(by_id.keys())
    orphans = sorted(
        sid for sid in existing_ids if sid.startswith("astro-") and sid not in catalog_ids
    )
    if orphans:
        log.info(
            "schedule registrar: %d orphaned platform schedule(s) left untouched "
            "(no deletion this phase): %s",
            len(orphans),
            ",".join(orphans),
        )

    log.info(
        "schedule registrar: created=%d updated=%d skipped=%d held=%d "
        "active_kinds=[%s] queue=%s",
        created,
        updated,
        skipped,
        len(held_kinds),
        ",".join(sorted(k.value for k in active_kinds)),
        task_queue,
    )
