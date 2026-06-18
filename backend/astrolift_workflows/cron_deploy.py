"""
Cron-triggered deploy policy (#296, spec 06 §5).

Pure-Python policy used by the platform's cron-dispatch tick to
decide which ``RegisteredApp`` rows should fire a deploy this minute.

The dispatcher fires every minute via the schedule registry. On
each tick it:

1. Loads every active ``RegisteredApp`` with
   ``trigger_mode == 'cron' AND cron_expression != ''``.
2. Filters out rows that are soft-deleted or have ``cron_paused``
   set.
3. Matches each row's expression against the current minute via
   :func:`cron_matches`.
4. For every match, enqueues a ``StartDeployment`` (via the same
   ``start_workflow`` path the GraphQL mutation uses).

Matching is intentionally minute-granular — we don't aim to cover
the sub-minute fan-out cases. If two ticks land in the same minute
(e.g. backfill on a Temporal restart) the deploy mutation's
single-flight workflow id idempotency rejects the duplicate.

Matching the five-field cron grammar:
    minute hour day-of-month month day-of-week
where each field accepts ``*``, an integer, ``*/N``, ``A-B``, or a
comma-list of the above. Same shape :mod:`astrolift_registry.cron`
already validates at registration time.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime

from astrolift_registry.cron import CronValidationError, validate_cron_expression

# Mirror the bounds in astrolift_registry.cron (which validates the
# *shape*; this module evaluates the *value*).
_FIELD_BOUNDS: tuple[tuple[int, int], ...] = (
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day-of-month
    (1, 12),  # month
    (0, 6),  # day-of-week (0=Sun .. 6=Sat)
)

_INT_RE = re.compile(r"^\d+$")
_STEP_RE = re.compile(r"^\*/(\d+)$")
_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")


def _atom_matches(atom: str, value: int, lo: int, hi: int) -> bool:
    if atom == "*":
        return True
    if _INT_RE.match(atom):
        return int(atom) == value
    m = _STEP_RE.match(atom)
    if m is not None:
        step = int(m.group(1))
        # ``*/N`` ≡ {lo, lo+N, lo+2N, ...} within [lo, hi].
        return (value - lo) % step == 0
    m = _RANGE_RE.match(atom)
    if m is not None:
        start, end = int(m.group(1)), int(m.group(2))
        return start <= value <= end
    # Anything else means the expression was rejected by the validator
    # before it ever landed in the DB — defensively refuse to match.
    return False


def _field_matches(field: str, value: int, lo: int, hi: int) -> bool:
    for atom in field.split(","):
        atom = atom.strip()
        if _atom_matches(atom, value, lo, hi):
            return True
    return False


def cron_matches(expression: str, *, now: datetime) -> bool:
    """Does ``expression`` fire at ``now``?

    Accepts the same 5-field grammar as :func:`validate_cron_expression`.
    Naive datetimes are rejected — callers must pass tz-aware ``now``
    so the meaning is unambiguous (UTC is the platform convention).
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    try:
        normalized = validate_cron_expression(expression)
    except CronValidationError:
        return False

    fields = normalized.split()
    values = (
        now.minute,
        now.hour,
        now.day,
        now.month,
        # Python's weekday() is Monday=0..Sunday=6; cron weekday is
        # Sunday=0..Saturday=6. Convert.
        (now.weekday() + 1) % 7,
    )
    for field, value, (lo, hi) in zip(fields, values, _FIELD_BOUNDS, strict=True):
        if not _field_matches(field, value, lo, hi):
            return False
    return True


@dataclasses.dataclass(frozen=True, slots=True)
class CronDispatchCandidate:
    """One ``RegisteredApp`` row the dispatcher should consider this tick."""

    app_id: int
    app_slug: str
    app_guid: str
    cron_expression: str
    cron_paused: bool
    primary_environment_name: str | None


def select_matches(
    *,
    candidates: list[CronDispatchCandidate],
    now: datetime,
) -> list[CronDispatchCandidate]:
    """Filter ``candidates`` to those whose cron fires at ``now``.

    Skips:
      * rows with ``cron_paused=True``
      * rows whose ``primary_environment_name`` is None (no env to
        deploy to — surface upstream as an alert separately)
      * rows whose cron expression doesn't match the current minute
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    out: list[CronDispatchCandidate] = []
    for c in candidates:
        if c.cron_paused:
            continue
        if not c.primary_environment_name:
            continue
        if not cron_matches(c.cron_expression, now=now):
            continue
        out.append(c)
    return out


@dataclasses.dataclass(frozen=True, slots=True)
class AgentCronDispatchCandidate:
    """One agent ``Workload`` row the agent-cron tick should consider
    this tick (spec 33, PR-4).

    The Task-family / Schedule-mode counterpart to
    :class:`CronDispatchCandidate`. The agent-cron tick selects agent
    Workloads — not ``RegisteredApp`` rows — so this carries the
    workload identity instead of an app/env pair. An agent dispatches
    to the org's managed cluster (the ``dispatch_agent_task`` activity
    resolves it), so there is no per-app ``primary_environment_name``
    gate here; that's the one field the app selector needs and this one
    doesn't.
    """

    workload_id: int
    workload_slug: str
    workload_guid: str
    organization_id: int
    run_cron_expression: str
    run_paused: bool


def select_agent_matches(
    *,
    candidates: list[AgentCronDispatchCandidate],
    now: datetime,
) -> list[AgentCronDispatchCandidate]:
    """Filter agent ``candidates`` to those whose cron fires at ``now``.

    Parallel to :func:`select_matches` and reuses :func:`cron_matches`
    verbatim for the minute-grained evaluation. Skips:
      * rows with ``run_paused=True`` (operator kill-switch — a paused
        schedule agent must not dispatch)
      * rows whose ``run_cron_expression`` doesn't match the current
        minute (and malformed expressions, which ``cron_matches``
        defensively refuses)

    Deliberately has no environment gate: agent Tasks run on the org's
    managed cluster, resolved at dispatch time, not against an
    ``AppEnvironment`` the way an app deploy does.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    out: list[AgentCronDispatchCandidate] = []
    for c in candidates:
        if c.run_paused:
            continue
        if not cron_matches(c.run_cron_expression, now=now):
            continue
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# Loop dispatch (spec 33, PR-6)
# ---------------------------------------------------------------------------
#
# The continuous-re-dispatch sibling of the cron selector. A Loop agent
# (``run_family='task' AND run_mode='loop'``) should always have up to
# ``run_max_parallel`` Tasks in flight: as each run finishes (terminal), the
# next reconcile tick tops the agent back up to the cap. This selector is the
# pure-policy half — it computes, per agent, HOW MANY new Tasks to dispatch
# this tick from the cap + the current in-flight count. The activity layer
# supplies the in-flight count (a DB COUNT) and performs the dispatch + the
# per-agent row lock that makes the count-then-dispatch atomic.
#
# Default cap (``run_max_parallel is None``): Loop must NOT be truly
# unbounded — an uncapped continuous re-dispatch would spawn runs without
# limit. So a null cap means "use the platform default loop cap"
# (``DEFAULT_LOOP_MAX_PARALLEL``), not infinity. An explicit cap of 0 means
# "dispatch nothing" (a soft pause that keeps the agent in Loop mode).
#
# Mutually exclusive from the other three selectors by the same axes:
#   * cron-deploy   reads ``RegisteredApp.trigger_mode='cron'``;
#   * agent-cron    reads agent Workloads ``run_family='task', run_mode='schedule'``;
#   * scale-tick    reads agent Workloads ``run_family='service'``;
#   * THIS selector reads agent Workloads ``run_family='task', run_mode='loop'``.
# ``run_mode`` (loop vs schedule) makes the two Task selectors disjoint; an
# agent is in exactly one run_mode at a time.

# Platform default Loop concurrency cap when ``run_max_parallel is None``.
# Chosen conservatively: a Loop with no explicit cap keeps a single run in
# flight at a time (serial re-dispatch) rather than fanning out unbounded.
# Operators raise it explicitly via the run-spec ``run_max_parallel`` field.
DEFAULT_LOOP_MAX_PARALLEL = 1


def effective_loop_cap(run_max_parallel: int | None) -> int:
    """Resolve the concurrency cap for a Loop agent.

    ``None`` (the field's "uncapped" sentinel) maps to the platform default
    cap, NOT infinity — a Loop is never truly unbounded (see module note).
    An explicit value (including 0) is honoured verbatim.
    """
    if run_max_parallel is None:
        return DEFAULT_LOOP_MAX_PARALLEL
    return int(run_max_parallel)


@dataclasses.dataclass(frozen=True, slots=True)
class LoopDispatchCandidate:
    """One ``run_family='task', run_mode='loop'`` agent ``Workload`` row the
    loop tick should consider (spec 33, PR-6).

    Carries the run-spec cap (``run_max_parallel``; None = default cap) and
    the agent's current in-flight (non-terminal) Task count — supplied by the
    activity from a DB COUNT taken under the per-agent row lock — so the pure
    policy can decide how many fresh Tasks to dispatch without exceeding the
    cap.
    """

    workload_id: int
    workload_slug: str
    workload_guid: str
    organization_id: int
    run_paused: bool
    run_max_parallel: int | None
    in_flight: int


@dataclasses.dataclass(frozen=True, slots=True)
class LoopDispatchAction:
    """A decided loop dispatch for one agent this tick (spec 33, PR-6).

    ``to_dispatch`` is how many fresh Tasks the activity should create +
    enqueue for this agent — always ``>= 1`` (candidates that resolve to 0
    are dropped, so the activity never iterates a no-op) and never more than
    the headroom under the (resolved) cap.
    """

    workload_id: int
    workload_slug: str
    workload_guid: str
    organization_id: int
    to_dispatch: int


def select_loop_dispatches(
    *,
    candidates: list[LoopDispatchCandidate],
) -> list[LoopDispatchAction]:
    """Decide how many Tasks to dispatch per Loop agent.

    For each candidate:
      * a paused agent (``run_paused=True``) dispatches nothing — the
        operator kill-switch halts the loop (acceptance (3));
      * otherwise the headroom is ``effective_loop_cap(run_max_parallel) -
        in_flight``; clamped at 0 so an over-cap agent (more in-flight than
        the cap, e.g. just after the cap was lowered) dispatches nothing and
        drains naturally;
      * an agent with positive headroom yields an action to dispatch exactly
        that many — so the in-flight count after dispatch equals the cap and
        NEVER exceeds it (acceptance (1)).

    No clock here — Loop is not cron-gated; it reconciles to the cap every
    tick. The activity is responsible for taking the in-flight count under a
    per-agent lock so two concurrent ticks can't both see the same headroom
    and double-dispatch (the cap-enforcement race; see the activity).
    """
    out: list[LoopDispatchAction] = []
    for c in candidates:
        if c.run_paused:
            continue
        cap = effective_loop_cap(c.run_max_parallel)
        headroom = cap - int(c.in_flight)
        if headroom <= 0:
            continue
        out.append(
            LoopDispatchAction(
                workload_id=c.workload_id,
                workload_slug=c.workload_slug,
                workload_guid=c.workload_guid,
                organization_id=c.organization_id,
                to_dispatch=headroom,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Scheduled scaling (spec 33, PR-5)
# ---------------------------------------------------------------------------
#
# The third selector in this module, parallel to :func:`select_matches`
# (app deploys) and :func:`select_agent_matches` (agent Task dispatch).
# This one selects ``run_family='service'`` agent Workloads and decides a
# replica-patch action per tick: scale UP to ``scheduled_scale_to`` at a
# scale-up-cron match, or scale DOWN to 0 at a scale-down-cron match.
#
# Mutually exclusive from the other two selectors by construction:
#   * the deploy selector reads ``RegisteredApp.trigger_mode='cron'`` and
#     fires ``DeployAppWorkflow``;
#   * the agent-dispatch selector reads agent Workloads with
#     ``run_family='task' AND run_mode='schedule'`` and fires
#     ``DispatchAgentTaskWorkflow``;
#   * this selector reads agent Workloads with ``run_family='service'``
#     and issues a ``scale_workload`` replica patch — no dispatch, no
#     deploy.
# The ``run_family`` axis (task vs service) makes the two agent selectors
# disjoint: a Task agent is never scaled, a Service agent is never
# dispatched-as-task. An app has no ``run_family`` at all.


@dataclasses.dataclass(frozen=True, slots=True)
class ScaleTickCandidate:
    """One ``run_family='service'`` agent ``Workload`` row the scaling
    tick should consider (spec 33, PR-5).

    Carries the cron pair + the up-cron target (``scheduled_scale_to``)
    and the workload's current desired replica count + the env-resolved
    upper bound so the pure-policy :func:`select_scale_matches` can both
    clamp the target to the env max AND skip a redundant patch when the
    workload is already at the (clamped) target — keeping the activity
    layer a thin DB+driver shim.
    """

    workload_id: int
    workload_slug: str
    workload_guid: str
    organization_id: int
    scheduled_scale_to: int | None
    scale_up_cron: str
    scale_down_cron: str
    current_replicas: int
    max_replicas: int


@dataclasses.dataclass(frozen=True, slots=True)
class ScaleAction:
    """A decided replica-patch for one workload this tick.

    ``direction`` is ``'up'`` | ``'down'`` for logging / events;
    ``target_replicas`` is the already-clamped count to patch to (the
    activity passes it straight to ``scale_workload``)."""

    workload_id: int
    workload_slug: str
    workload_guid: str
    organization_id: int
    direction: str
    target_replicas: int


def select_scale_matches(
    *,
    candidates: list[ScaleTickCandidate],
    now: datetime,
) -> list[ScaleAction]:
    """Decide replica-patch actions for ``candidates`` at ``now``.

    For each Service agent:
      * if the **scale-down** cron matches ``now`` → action to 0;
      * else if the **scale-up** cron matches ``now`` → action to
        ``min(scheduled_scale_to, max_replicas)`` (clamped to the env
        ceiling so a misconfigured target never raises VALIDATION in
        the activity — acceptance (2));
      * else no action.

    Tie-break (acceptance: up-cron and down-cron match the SAME tick,
    e.g. both ``0 0 * * *``): **scale-DOWN wins** — it's the safer,
    cheaper outcome (never accidentally scale a fleet UP because two
    crons collided), and it's deterministic regardless of field order.
    We evaluate down first and ``continue`` so up can't override.

    Idempotency (acceptance (3)): an action is emitted only when the
    (clamped) target differs from ``current_replicas`` — so a workload
    already at target produces no action and the activity issues no
    redundant ``scale_workload`` patch.

    An up-cron candidate with no ``scheduled_scale_to`` set is skipped
    (there is no target to scale to); the down-cron path is unaffected
    (its target is always 0).
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    out: list[ScaleAction] = []
    for c in candidates:
        # Scale-down wins a same-tick collision: evaluate it first.
        if c.scale_down_cron and cron_matches(c.scale_down_cron, now=now):
            if c.current_replicas != 0:
                out.append(
                    ScaleAction(
                        workload_id=c.workload_id,
                        workload_slug=c.workload_slug,
                        workload_guid=c.workload_guid,
                        organization_id=c.organization_id,
                        direction="down",
                        target_replicas=0,
                    )
                )
            continue
        if c.scale_up_cron and cron_matches(c.scale_up_cron, now=now):
            if c.scheduled_scale_to is None:
                continue
            target = min(int(c.scheduled_scale_to), int(c.max_replicas))
            if c.current_replicas != target:
                out.append(
                    ScaleAction(
                        workload_id=c.workload_id,
                        workload_slug=c.workload_slug,
                        workload_guid=c.workload_guid,
                        organization_id=c.organization_id,
                        direction="up",
                        target_replicas=target,
                    )
                )
    return out
